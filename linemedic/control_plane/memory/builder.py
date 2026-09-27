"""사례 기억: case builder (W27, spec 17 §1~§3·§7, docs/03 §6, docs/05 사례 기억 흐름, D84).

host가 DB의 원본 event를 읽어 사례 노트를 만든다. agent가 outcome을 쓰는 경로는 없다.

입력 event(`source_event_key`로 한 번만 처리):
- 거절된 제안 `proposal:<PROP>:rejected` → BLOCKED, phase validation
  (runner 회귀 FAIL도 여기다. 운영 적용의 VERIFIED_FAILURE와 섞지 않는다)
- 봇 PR 생성 `pr:<EXE>:opened` → UNVERIFIED, phase review
- 정비 요청 초안 `draft:<EXE>:drafted` → HANDOFF, phase handoff
- 차단 보고 `blocked:<NOT>` → BLOCKED, phase = 보고의 stage
- 업무 검증 최종 결과 `verification:<VER>:final` → PASS(도메인 검사 통과만) VERIFIED_SUCCESS,
  FAIL VERIFIED_FAILURE, INCONCLUSIVE 또는 대상 identity를 확인하지 못한 PASS → INCONCLUSIVE

series는 work(없으면 incident)마다 하나이고, 같은 series의 다음 결과는
revision + 1(`supersedes_id`)이다. 사실 필드는 DB 원본에서만, 문장은 고정 템플릿으로 만든다
(LLM 요약 없음). 자유 텍스트(가설 등)는 정제하고 출처를 밝힌다. 정제가 비밀·평가 식별자를
가렸으면 사람이 보기 전 게시하지 않는다(DRAFT, 색인 없음). PUBLISHED면 같은 트랜잭션에서
FTS 색인에 넣는다. 잘못된 노트는 RETRACTED로 바꾸고 이유를 남긴다(삭제 없음). 업무 검증
실패 조건에는 기대값·실제 값을 넣지 않는다(holdout 보호).
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.sanitize import disable_urls, mask_secrets
from linemedic.control_plane import audit
from linemedic.control_plane.attempts import ORIGIN_MANUAL, attempt_origin
from linemedic.control_plane.memory.text import search_text
from linemedic.control_plane.redaction import clean_text, eval_identifiers
from linemedic.control_plane.store import (
    FTS5_MIGRATION_MARK,
    MIGRATIONS_DIR,
    Store,
    Tx,
    fts5_available,
    split_statements,
)
from linemedic.control_plane.symptoms import observed_symptom
from linemedic.control_plane.verifier import DEFAULT_CONTRACT, load_contract

SCHEMA_VERSION = "linemedic.case.v4"
CASE_ACTOR = "case_builder"
FAILURE_OUTCOMES = frozenset({"VERIFIED_FAILURE", "BLOCKED", "INCONCLUSIVE"})
MAX_TEXT_CHARS = 1500
SUMMARY_MAX_CHARS = 2000
PUBLISH_METADATA = frozenset({"publish_status", "publish_check", "retraction"})
RESULT_TEXT = {
    "VERIFIED_SUCCESS": "업무 계약 검사 PASS(관찰 범위 안)",
    "VERIFIED_FAILURE": "업무 계약 검사 FAIL",
    "UNVERIFIED": "PR·검사 준비, 업무 검증 없음",
    "BLOCKED": "진행 불가(틀린 코드라는 뜻이 아님)",
    "INCONCLUSIVE": "결론 미확인",
    "HANDOFF": "정비 요청 초안(실제 정비·복구 아님)",
}
LIMITATIONS = {
    "VERIFIED_SUCCESS": [
        "합성 입력과 고정된 업무 계약의 관찰 범위에서만 확인했다",
        "다른 입력·버전에 쓰려면 현재 source·contract로 다시 검증해야 한다",
    ],
    "VERIFIED_FAILURE": ["이 대상 버전·입력에서 관찰한 실패다. 다른 버전의 결과가 아니다"],
    "UNVERIFIED": ["PR·검사만 준비됐다. 업무 복구는 확인하지 않았다"],
    "BLOCKED": [
        "진행 불가 기록이다. 틀린 코드라는 뜻이 아니다",
        "권한 부족은 현재 권한을 다시 확인할 신호이지 영구 금지가 아니다",
    ],
    "INCONCLUSIVE": ["결론을 확인하지 못했다. 성공·실패의 근거로 쓰지 않는다"],
    "HANDOFF": ["정비 요청 초안이다. 실제 정비·복구를 확인하지 않았다"],
}
VERIFICATION_OUTCOME = {"PASS": "VERIFIED_SUCCESS", "FAIL": "VERIFIED_FAILURE"}
ORIGIN_NEGATIVE = "human_injected_negative"
ORIGIN_OPERATOR = "operator_note"


def _loads(value: str | None) -> dict[str, Any]:
    data = json.loads(value) if value else {}
    return data if isinstance(data, dict) else {}


def fts_enabled(tx: Tx) -> bool:
    """`case_search` 색인을 쓸 수 있는가: 테이블이 있고 이 SQLite에 FTS5가 있다.

    FTS5 없는 환경(migration이 색인을 만들지 않음)이면 keyword_fallback이다.
    """
    exists = tx.one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'case_search'")
    return exists is not None and fts5_available(tx)


def content_sha256(payload: dict[str, Any]) -> str:
    return sha256_hex({k: v for k, v in payload.items() if k not in PUBLISH_METADATA})


def series_id_for(work_id: str | None, incident_id: str) -> str:
    key = work_id or f"incident:{incident_id}"
    return "CASE-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()


def held_out_case_ids(path: Path = DEFAULT_CONTRACT) -> frozenset[str]:
    """기대값이 eval에만 있는 case(`fixture_ref`). 노트에는 이 case의 ID도 남기지 않는다."""
    contract, _ = load_contract(path)
    return frozenset(case.id for case in contract.cases if case.fixture_ref is not None)


@dataclass
class CaseEvent:
    """원본 event에서 뽑은 사실(정제 전)."""

    key: str
    run_id: str
    incident_id: str
    work_id: str | None
    attempt_id: str | None
    observed_at: str
    outcome: str
    phase: str
    origin: str
    hypothesis: str | None = None
    attempted: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    failure_conditions: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    artifact_refs: dict[str, Any] = field(default_factory=dict)
    applicability: dict[str, Any] = field(default_factory=dict)


# ── 원본 event 읽기 ───────────────────────────────────────────

PENDING_QUERIES = (
    (
        "proposal",
        "SELECT p.id, p.received_at AS at FROM proposals p WHERE p.decision = 'REJECTED'"
        " AND NOT EXISTS (SELECT 1 FROM case_notes c"
        " WHERE c.source_event_key = 'proposal:' || p.id || ':rejected')",
    ),
    (
        "pr",
        "SELECT e.id, e.updated_at AS at FROM executions e WHERE e.operation = 'CREATE_PR'"
        " AND e.status = 'SUCCEEDED' AND NOT EXISTS (SELECT 1 FROM case_notes c"
        " WHERE c.source_event_key = 'pr:' || e.id || ':opened')",
    ),
    (
        "draft",
        "SELECT e.id, e.updated_at AS at FROM executions e"
        " WHERE e.operation = 'DRAFT_WORK_ORDER' AND e.status = 'SUCCEEDED'"
        " AND NOT EXISTS (SELECT 1 FROM case_notes c"
        " WHERE c.source_event_key = 'draft:' || e.id || ':drafted')",
    ),
    (
        "blocked",
        "SELECT n.id, n.created_at AS at FROM notifications n WHERE n.event_type = 'WORK_BLOCKED'"
        " AND NOT EXISTS (SELECT 1 FROM case_notes c"
        " WHERE c.source_event_key = 'blocked:' || n.id)",
    ),
    (
        "verification",
        "SELECT v.id, v.ended_at AS at FROM verifications v"
        " WHERE v.verdict IN ('PASS', 'FAIL', 'INCONCLUSIVE') AND NOT EXISTS"
        " (SELECT 1 FROM case_notes c"
        " WHERE c.source_event_key = 'verification:' || v.id || ':final')",
    ),
)


def pending_events(tx: Tx) -> list[tuple[str, str, str]]:
    """아직 노트가 없는 event (시각, 종류, 원본 ID)를 시각 순서로."""
    found = []
    for kind, sql in PENDING_QUERIES:
        found.extend((row["at"] or "", kind, row["id"]) for row in tx.all(sql))
    return sorted(found)


def _proposal_facts(proposal: Any) -> tuple[str | None, list[str], list[str], dict[str, Any]]:
    """(가설, 시도한 변경, 검사 요약, 적용 조건) — 제안 원본과 브로커 검사 기록에서."""
    payload, record = _loads(proposal["payload_json"]), _loads(proposal["checks_json"])
    action = payload.get("action") or {}
    hypothesis = action.get("root_cause_hypothesis") or action.get("probable_cause")
    checks = record.get("checks") or []
    policy = next((c for c in checks if c.get("check") == "PATCH_POLICY"), {})
    attempted = [
        f"{f.get('path')} (+{f.get('additions')}/-{f.get('deletions')})"
        for f in policy.get("files") or []
    ]
    if action.get("type") == "create_pr" and action.get("new_test_path"):
        attempted.append(f"재현 테스트 {action['new_test_path']}")
    summary = [
        f"{c.get('check')}: {c.get('result')}" + (f"({c['reason']})" if c.get("reason") else "")
        for c in checks
        if c.get("check")
    ]
    candidate = record.get("candidate") or {}
    applicability = {
        key: candidate.get(key)
        for key in ("base_sha", "candidate_sha", "candidate_tree", "patch_sha256")
        if candidate.get(key)
    }
    if not applicability.get("base_sha") and action.get("base_sha"):
        applicability["base_sha"] = action["base_sha"]
    return hypothesis, attempted, summary, applicability


class CaseBuilder:
    def __init__(
        self,
        store: Store,
        *,
        terms: Iterable[str] | None = None,
        held_out: Iterable[str] | None = None,
    ) -> None:
        self.store = store
        self.terms = tuple(eval_identifiers() if terms is None else terms)
        self.held_out = frozenset(held_out_case_ids() if held_out is None else held_out)

    # 공개

    def process_pending(self, limit: int = 50) -> list[str]:
        """노트가 없는 event를 시각 순서로 처리한다. 만든 note ID를 돌려준다."""
        with self.store.read() as tx:
            pending = pending_events(tx)[:limit]
        created = []
        for _, kind, source_id in pending:
            with self.store.tx() as tx:
                event = self._event(tx, kind, source_id)
                if event is None or tx.one(
                    "SELECT 1 FROM case_notes WHERE source_event_key = ?", (event.key,)
                ):
                    continue
                created.append(self._record(tx, event))
        return created

    # event → 사실

    def _event(self, tx: Tx, kind: str, source_id: str) -> CaseEvent | None:
        return {
            "proposal": self._rejected_proposal,
            "pr": self._pr_opened,
            "draft": self._draft,
            "blocked": self._blocked,
            "verification": self._verification,
        }[kind](tx, source_id)

    def _origin(self, tx: Tx, run_id: str, incident_id: str, attempt_id: str | None) -> str:
        if attempt_id is None:
            return attempt_origin(tx, run_id, incident_id, "")
        return attempt_origin(tx, run_id, incident_id, attempt_id)

    def _rejected_proposal(self, tx: Tx, proposal_id: str) -> CaseEvent | None:
        proposal = tx.one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        if proposal is None:
            return None
        hypothesis, attempted, checks, applicability = _proposal_facts(proposal)
        record = _loads(proposal["checks_json"])
        failed = [c for c in record.get("checks") or [] if c.get("result") not in (None, "PASS")]
        last = failed[-1] if failed else {}
        decided = tx.one(
            "SELECT created_at FROM audit_events WHERE run_id = ? AND incident_id = ?"
            " AND event_type = 'PROPOSAL_REJECTED' AND payload_json LIKE ? ORDER BY seq DESC",
            (proposal["run_id"], proposal["incident_id"], f'%"{proposal_id}"%'),
        )
        code = record.get("decision_reason") or last.get("result") or "REJECTED"
        condition = f"브로커 검사 {last.get('check', '?')} 단계에서 {code}"
        if last.get("reason"):
            condition += f"({last['reason']})"
        payload = _loads(proposal["payload_json"])
        return CaseEvent(
            key=f"proposal:{proposal_id}:rejected",
            run_id=proposal["run_id"],
            incident_id=proposal["incident_id"],
            work_id=proposal["work_id"],
            attempt_id=proposal["attempt_id"],
            observed_at=decided["created_at"] if decided else proposal["received_at"],
            outcome="BLOCKED",
            phase="validation",
            origin=self._origin(
                tx, proposal["run_id"], proposal["incident_id"], proposal["attempt_id"]
            ),
            hypothesis=hypothesis,
            attempted=attempted,
            checks=checks,
            failure_conditions=[condition],
            evidence_ids=list(payload.get("evidence_ids") or []),
            artifact_refs={"proposal_id": proposal_id},
            applicability=applicability,
        )

    def _pr_opened(self, tx: Tx, execution_id: str) -> CaseEvent | None:
        execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
        proposal = tx.one("SELECT * FROM proposals WHERE id = ?", (execution["proposal_id"],))
        if proposal is None:
            return None
        hypothesis, attempted, checks, applicability = _proposal_facts(proposal)
        result = _loads(execution["result_json"])
        payload = _loads(proposal["payload_json"])
        return CaseEvent(
            key=f"pr:{execution_id}:opened",
            run_id=execution["run_id"],
            incident_id=execution["incident_id"],
            work_id=execution["work_id"],
            attempt_id=proposal["attempt_id"],
            observed_at=execution["updated_at"],
            outcome="UNVERIFIED",
            phase="review",
            origin=self._origin(
                tx, proposal["run_id"], proposal["incident_id"], proposal["attempt_id"]
            ),
            hypothesis=hypothesis,
            attempted=attempted,
            checks=checks,
            evidence_ids=list(payload.get("evidence_ids") or []),
            artifact_refs={
                "proposal_id": proposal["id"],
                "execution_id": execution_id,
                "pr_number": result.get("pr_number"),
                "pr_head_sha": result.get("head_sha"),
            },
            applicability=applicability,
        )

    def _draft(self, tx: Tx, execution_id: str) -> CaseEvent | None:
        execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
        proposal = tx.one("SELECT * FROM proposals WHERE id = ?", (execution["proposal_id"],))
        if proposal is None:
            return None
        action = _loads(proposal["payload_json"]).get("action") or {}
        return CaseEvent(
            key=f"draft:{execution_id}:drafted",
            run_id=execution["run_id"],
            incident_id=execution["incident_id"],
            work_id=execution["work_id"],
            attempt_id=proposal["attempt_id"],
            observed_at=execution["updated_at"],
            outcome="HANDOFF",
            phase="handoff",
            origin=self._origin(
                tx, proposal["run_id"], proposal["incident_id"], proposal["attempt_id"]
            ),
            hypothesis=action.get("probable_cause"),
            attempted=[
                f"정비 요청 초안: {action.get('equipment_id')} / {action.get('manual_ref_id')}"
            ],
            evidence_ids=list(_loads(proposal["payload_json"]).get("evidence_ids") or []),
            artifact_refs={"proposal_id": proposal["id"], "execution_id": execution_id},
            applicability={"equipment_id": action.get("equipment_id")},
        )

    def _blocked(self, tx: Tx, notification_id: str) -> CaseEvent | None:
        notice = tx.one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        report = _loads(notice["payload_json"])
        work = (
            tx.one("SELECT * FROM work_items WHERE id = ?", (notice["work_id"],))
            if notice["work_id"]
            else None
        )
        attempt_id = report.get("attempt_id") or (work["attempt_id"] if work else None)
        code = report.get("blocker_code")
        operator = "requested_by" in report  # 운영자 중단(POST /ops/incidents/{id}/escalate)
        detail = report.get("operator_note") if operator else report.get("reason_detail")
        conditions = [f"{code}: {detail}" if detail else str(code)]
        missing = [str(item) for item in report.get("missing_requirements") or []]
        if missing:
            conditions.append("부족한 조건: " + ", ".join(missing))
        return CaseEvent(
            key=f"blocked:{notification_id}",
            run_id=notice["run_id"],
            incident_id=notice["incident_id"],
            work_id=notice["work_id"],
            attempt_id=attempt_id,
            observed_at=report.get("observed_at") or notice["created_at"],
            outcome="BLOCKED",
            phase="operator" if operator else str(report.get("stage") or "unknown"),
            origin=(
                ORIGIN_OPERATOR
                if operator
                else self._origin(tx, notice["run_id"], notice["incident_id"], attempt_id)
            ),
            hypothesis=report.get("agent_summary"),
            attempted=[str(a) for a in report.get("attempted_actions") or []],
            checks=[f"blocker {code}"],
            failure_conditions=conditions,
            evidence_ids=[str(e) for e in report.get("evidence_ids") or []],
            artifact_refs={"notification_id": notification_id},
        )

    def _verification(self, tx: Tx, verification_id: str) -> CaseEvent | None:
        row = tx.one("SELECT * FROM verifications WHERE id = ?", (verification_id,))
        result = _loads(row["result_json"])
        execution = (
            tx.one("SELECT * FROM executions WHERE id = ?", (row["execution_id"],))
            if row["execution_id"]
            else None
        )
        request = _loads(execution["request_json"]) if execution is not None else {}
        target = result.get("target") or {}
        outcome = VERIFICATION_OUTCOME.get(row["verdict"], "INCONCLUSIVE")
        conditions = [f"판정 사유 {result.get('reason')}"]
        if row["verdict"] == "PASS":
            problem = success_problem(tx, row, result, execution)
            if problem is not None:  # 대상 identity를 확인하지 못한 PASS는 성공으로 올리지 않는다
                outcome = "INCONCLUSIVE"
                conditions = [f"성공으로 올리지 않음: {problem}"]
        for failed in result.get("failed_assertions") or []:  # 기대값·실제 값은 넣지 않는다
            case_id = str(failed.get("case_id"))
            held = case_id in self.held_out or "held" in case_id
            case = "held-out case" if held else case_id
            conditions.append(f"{case}: {failed.get('assertion')} 불일치({failed.get('field')})")
        work = tx.one(
            "SELECT id, attempt_id FROM work_items WHERE run_id = ? AND incident_id = ?",
            (row["run_id"], row["incident_id"]),
        )
        # 배포한 제안의 가설·변경을 같이 남긴다(무엇을 시도해 성공·실패했는가)
        proposal = (
            tx.one("SELECT * FROM proposals WHERE id = ?", (request["proposal_id"],))
            if request.get("proposal_id")
            else None
        )
        hypothesis, attempted, _, _ = (
            _proposal_facts(proposal) if proposal is not None else (None, [], [], {})
        )
        if row["origin"] == ORIGIN_NEGATIVE:
            attempted = ["사람이 주입한 거짓 정상 구현(S1b, 에이전트 산출물 아님)"]
        return CaseEvent(
            key=f"verification:{verification_id}:final",
            run_id=row["run_id"],
            incident_id=row["incident_id"],
            work_id=work["id"] if work else None,
            attempt_id=work["attempt_id"] if work else None,
            observed_at=row["ended_at"] or row["started_at"],
            outcome=outcome,
            phase="verification",
            origin=row["origin"],
            hypothesis=hypothesis,
            attempted=attempted,
            checks=[f"verification {row['verdict']} ({result.get('reason')})"],
            failure_conditions=conditions if outcome != "VERIFIED_SUCCESS" else [],
            evidence_ids=(
                list(_loads(proposal["payload_json"]).get("evidence_ids") or [])
                if proposal is not None
                else []
            ),
            artifact_refs={
                "verification_id": verification_id,
                "execution_id": row["execution_id"],
                "proposal_id": request.get("proposal_id"),
                "pr_number": request.get("pr_number"),
            },
            applicability={
                "contract_id": row["contract_id"],
                "contract_sha256": row["contract_sha256"],
                "fixture_sha256": result.get("fixture_sha256"),
                "image_id": target.get("image_id"),
                "container_id": target.get("container_id"),
                "approved_merge_sha": request.get("approved_merge_sha"),
                "approved_tree": request.get("approved_tree"),
                "base_sha": request.get("base_sha"),
                "candidate_sha": request.get("candidate_sha"),
            },
        )

    # 사실 → 노트

    def _clean(self, text: str | None, problems: set[str]) -> str | None:
        """비밀·평가 식별자를 가리고 URL을 무력화한다. 가린 것이 있으면 `problems`에 적는다.

        내부 저장이라 HTML 이스케이프는 하지 않는다(공개 채널로 보낼 때 그 경로가 정제한다).
        """
        if not text:
            return None
        if mask_secrets(text) != text:
            problems.add("secret_masked")
        if any(term in text for term in self.terms):
            problems.add("eval_identifier")
        return disable_urls(clean_text(text, self.terms, MAX_TEXT_CHARS))

    def _record(self, tx: Tx, event: CaseEvent) -> str:
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (event.incident_id,))
        work = (
            tx.one("SELECT * FROM work_items WHERE id = ?", (event.work_id,))
            if event.work_id
            else None
        )
        details = _loads(incident["details_json"])
        signature = details.get("signature") if isinstance(details.get("signature"), dict) else {}
        problems: set[str] = set()
        symptom = self._clean(
            observed_symptom(details) or f"{incident['service']} 사건(관찰 요약 없음)", problems
        )
        hypothesis = self._clean(event.hypothesis, problems)
        attempted = [self._clean(item, problems) or "" for item in event.attempted]
        conditions = [self._clean(item, problems) or "" for item in event.failure_conditions]
        checks = [self._clean(item, problems) or "" for item in event.checks]
        series_id = series_id_for(event.work_id, event.incident_id)
        last = tx.one(
            "SELECT id, revision FROM case_notes WHERE series_id = ? ORDER BY revision DESC",
            (series_id,),
        )
        revision = last["revision"] + 1 if last is not None else 1
        note_id = f"{series_id}-R{revision}"
        attempted_text = "; ".join(item for item in attempted if item) or None
        summary = (
            f"[{event.outcome}·{event.phase}] {symptom}."
            f" 시도: {attempted_text or '코드 변경 없음'}. 결과: {RESULT_TEXT[event.outcome]}"
        )
        if conditions and event.outcome in FAILURE_OUTCOMES:
            summary += " — " + "; ".join(conditions[:3])
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "note_id": note_id,
            "series_id": series_id,
            "revision": revision,
            "supersedes_id": last["id"] if last is not None else None,
            "repository_id": incident["repository_id"],
            "service_id": incident["service"],
            "problem_fingerprint": incident["fingerprint"],
            "signature": {
                key: signature.get(key) for key in ("error_type", "top_frame", "endpoint")
            },
            "source_run_id": event.run_id,
            "source_incident_id": event.incident_id,
            "work_id": event.work_id,
            "issue_number": work["issue_number"] if work is not None else None,
            "attempt_id": event.attempt_id,
            "source_event_key": event.key,
            "outcome": event.outcome,
            "phase": event.phase,
            "origin": event.origin,
            "seed": False,
            "symptom": symptom,
            "hypothesis": hypothesis,
            "hypothesis_by": (
                ("사람이 미리 작성한 제안" if event.origin == ORIGIN_MANUAL else "에이전트")
                + "(검증되지 않은 가설)"
                if hypothesis
                else None
            ),
            "attempted_change": attempted_text,
            "checks": checks,
            "failure_conditions": conditions,
            "evidence_ids": event.evidence_ids,
            "artifact_refs": {k: v for k, v in event.artifact_refs.items() if v is not None},
            "applicability": {k: v for k, v in event.applicability.items() if v is not None},
            "limitations": LIMITATIONS[event.outcome],
            "summary": summary[:SUMMARY_MAX_CHARS],
            "observed_at": event.observed_at,
            "created_at": tx.now,
        }
        digest = content_sha256(payload)
        status = "DRAFT" if problems else "PUBLISHED"
        payload["publish_status"] = status
        if problems:
            payload["publish_check"] = {"problems": sorted(problems)}
        tx.execute(
            "INSERT INTO case_notes(id, series_id, revision, supersedes_id, repository_id, service,"
            " problem_fingerprint, source_run_id, source_incident_id, work_id, source_event_key,"
            " outcome, phase, origin, publish_status, observed_at, created_at, content_sha256,"
            " payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                series_id,
                revision,
                payload["supersedes_id"],
                incident["repository_id"],
                incident["service"],
                incident["fingerprint"],
                event.run_id,
                event.incident_id,
                event.work_id,
                event.key,
                event.outcome,
                event.phase,
                event.origin,
                status,
                event.observed_at,
                tx.now,
                digest,
                canonical_dumps(payload),
            ),
        )
        if status == "PUBLISHED" and fts_enabled(tx):
            tx.execute(
                "INSERT INTO case_search(note_id, search_text) VALUES (?, ?)",
                (note_id, search_text(payload)),
            )
        audit.append(
            tx,
            event.run_id,
            event.incident_id,
            CASE_ACTOR,
            "CASE_NOTE_CREATED",
            {
                "note_id": note_id,
                "source_event_key": event.key,
                "outcome": event.outcome,
                "phase": event.phase,
                "origin": event.origin,
                "publish_status": status,
            },
        )
        return note_id


def success_problem(tx: Tx, row: Any, result: dict[str, Any], execution: Any) -> str | None:
    """VERIFIED_SUCCESS 도메인 검사: PASS·관찰 완료·대상 image·contract·배포한 코드 identity."""
    if row["verdict"] != "PASS":
        return "not_pass"
    if not result.get("observation_complete") or (
        result.get("samples_completed") != result.get("samples_required")
    ):
        return "observation_incomplete"
    target = result.get("target") or {}
    if not (target.get("image_id") and target.get("container_id")):
        return "target_unconfirmed"
    if not row["contract_sha256"]:
        return "contract_unconfirmed"
    if execution is None or execution["operation"] != "DEPLOY":
        return "code_unconfirmed"
    if execution["status"] != "SUCCEEDED":
        return "deploy_unconfirmed"
    deployed = (_loads(execution["result_json"]).get("target") or {}).get("image_id")
    if deployed != target.get("image_id"):
        return "image_mismatch"
    if not _loads(execution["request_json"]).get("approved_merge_sha"):
        return "code_unconfirmed"
    return None


# ── 운영 ──────────────────────────────────────────────────────


def retract(tx: Tx, note_id: str, *, reason: str, principal: str) -> dict[str, Any]:
    """잘못된 노트를 철회한다(삭제 없음). 검색 색인에서 빼고 이유를 남긴다."""
    note = tx.one("SELECT * FROM case_notes WHERE id = ?", (note_id,))
    if note is None:
        raise LookupError(note_id)
    if note["publish_status"] == "RETRACTED":
        return {"note_id": note_id, "publish_status": "RETRACTED", "changed": False}
    payload = _loads(note["payload_json"])
    payload["publish_status"] = "RETRACTED"
    payload["retraction"] = {"reason": reason[:500], "by": principal, "at": tx.now}
    tx.execute(
        "UPDATE case_notes SET publish_status = 'RETRACTED', payload_json = ? WHERE id = ?",
        (canonical_dumps(payload), note_id),
    )
    if fts_enabled(tx):
        tx.execute("DELETE FROM case_search WHERE note_id = ?", (note_id,))
    audit.append(
        tx,
        note["source_run_id"],
        note["source_incident_id"],
        CASE_ACTOR,
        "CASE_NOTE_RETRACTED",
        {"note_id": note_id, "reason": reason[:500], "by": principal},
    )
    return {"note_id": note_id, "publish_status": "RETRACTED", "changed": True}


def _create_index(tx: Tx) -> None:
    """FTS5 migration 파일의 문장으로 `case_search`를 만든다(DDL을 두 곳에 두지 않는다)."""
    for path in sorted(MIGRATIONS_DIR.glob(f"*{FTS5_MIGRATION_MARK}*.sql")):
        for statement in split_statements(path.read_text(encoding="utf-8")):
            tx.execute(statement)


def rebuild_index(tx: Tx) -> dict[str, Any]:
    """PUBLISHED revision 전부로 색인을 다시 만든다(outcome·노트는 바꾸지 않는다).

    색인은 파생물이라 지우고 새로 만든다. 손상된 색인(읽기·DELETE가 실패), 지워진 색인, FTS5 없는
    SQLite에서 만든 DB도 이렇게 복구한다. FTS5가 없으면 아무것도 하지 않고 keyword_fallback이라고
    돌려준다.
    """
    if not fts5_available(tx):
        return {"engine": "keyword_fallback", "indexed": 0}
    tx.execute("DROP TABLE IF EXISTS case_search")
    _create_index(tx)
    count = 0
    for note in tx.all(
        "SELECT id, payload_json FROM case_notes WHERE publish_status = 'PUBLISHED' ORDER BY id"
    ):
        tx.execute(
            "INSERT INTO case_search(note_id, search_text) VALUES (?, ?)",
            (note["id"], search_text(_loads(note["payload_json"]))),
        )
        count += 1
    return {"engine": "sqlite_fts5", "indexed": count}
