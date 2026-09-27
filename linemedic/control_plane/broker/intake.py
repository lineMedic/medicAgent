"""제안 접수와 백그라운드 브로커 (W09, spec 03 §4, spec 06 §1·§7·§10, docs/04 §3).

동기 접수 `submit`(`POST /tools/proposals`), 한 트랜잭션:
  principal 범위(token·body의 run·incident·work·attempt) → 멱등 키(같은 키·같은 본문은 저장된
  응답) → B01 시작 알림 ACCEPTED·Issue 현재 scope·상태·deadline·제출 예산 → B02 엄격한 JSON·
  schema(category↔action·근거 개수)·등록 설비·매뉴얼 → 제출 1회 소비 → proposal RECEIVED,
  incident INVESTIGATING→VALIDATING → 202 `{proposal_id, decision: RECEIVED}`
  B02 실패(422 `INVALID_PROPOSAL`)도 제출 1회로 센다. 예산을 다 쓴 실패는 이관한다.
  B01 실패(403·409)는 세지 않는다. 202는 접수일 뿐 허용·실행 성공이 아니다.

백그라운드 `Broker.run`(같은 프로세스 루프) → `process_pending`: RECEIVED → CHECKING →
  B03 증거 범위 → B04 기존 실행 → B05 민감 값·허용 채널 → B06 상태·version·catalog 재확인 → 액션
  - create_work_order_draft: 승인 템플릿 초안, execution DRAFT_WORK_ORDER,
    WORK_ORDER_DRAFTED/HANDED_OFF, HANDOFF_DRAFTED
  - escalate: ESCALATED/BLOCKED(reason), WORK_BLOCKED(blocker report)
  - create_pr: 패치 검사(W10) 전이라 REJECTED(PROTECTION_UNAVAILABLE). 가짜 통과 경로를 두지 않는다
  거절은 제출 예산이 남으면 VALIDATING→INVESTIGATING(수정 1회, 같은 attempt·deadline),
  아니면 ESCALATED/BLOCKED(VALIDATION_FAILED).
"""

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from linemedic.common.canonical_json import (
    StrictJSONError,
    canonical_dumps,
    loads_strict,
    sha256_hex,
)
from linemedic.common.ids import new_id
from linemedic.common.sanitize import mask_secrets
from linemedic.control_plane import audit, idempotency
from linemedic.control_plane.app import AppContext, safe_validation_errors
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.broker.proposals import Proposal, ProposalReceipt
from linemedic.control_plane.broker.work_order import build_draft
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.errors import ApiError, error_body, success_body
from linemedic.control_plane.idempotency import Outcome
from linemedic.control_plane.knowledge import ManualTemplate
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.state import Actor, coupled_transition, transition_incident
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.symptoms import observed_symptom

PROPOSALS_PATH = "/tools/proposals"
RETRY_AFTER_APPROVAL = "운영자가 새 generation을 승인한 뒤"
# 단어 경계를 두지 않는다(한글 등 앞 글자에 붙은 URL도 잡는다). scheme은 http·ftp·file 등 모두.
_URL = re.compile(r"(?i)[a-z][a-z0-9+.-]*://|www\.")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

Failure = tuple[str, dict[str, Any]]  # (검사 코드, 세부)


# ── 동기 접수 ─────────────────────────────────────────────────


def _principal_scope(tx: Tx, agent: AgentPrincipal) -> tuple[Any, Any]:
    """token의 run·incident·work·attempt가 지금 사건·work와 맞는지. 아니면 403."""
    incident = tx.one(
        "SELECT * FROM incidents WHERE id = ? AND run_id = ?", (agent.incident_id, agent.run_id)
    )
    work = tx.one(
        "SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?",
        (agent.run_id, agent.incident_id),
    )
    if (
        incident is None
        or work is None
        or incident["attempt_id"] != agent.attempt_id
        or work["id"] != agent.work_id
        or work["attempt_id"] != agent.attempt_id
    ):
        raise ApiError("FORBIDDEN_SCOPE")
    return incident, work


def _b01(tx: Tx, incident: Any, work: Any, max_submissions: int) -> None:
    """시작 알림 ACCEPTED·Issue 현재 scope·상태·deadline·제출 예산."""
    notice = None
    if work["start_notification_id"]:
        notice = tx.one(
            "SELECT status, event_type, work_id FROM notifications WHERE id = ?",
            (work["start_notification_id"],),
        )
    if (
        notice is None
        or notice["event_type"] != "WORK_STARTING"
        or notice["work_id"] != work["id"]
        or notice["status"] != "ACCEPTED"
    ):
        raise ApiError("START_NOTICE_UNCONFIRMED")
    issue = tx.one(
        "SELECT state, snapshot_sha256 FROM github_issues"
        " WHERE repository_id = ? AND issue_number = ?",
        (work["repository_id"], work["issue_number"]),
    )
    if (
        issue is None
        or issue["state"] != "open"
        or issue["snapshot_sha256"] != work["issue_snapshot_sha256"]
    ):
        raise ApiError("ISSUE_SCOPE_CHANGED")
    if work["status"] != "RUNNING" or incident["status"] != "INVESTIGATING":
        raise ApiError("STATE_CONFLICT", {"current_status": incident["status"]})
    if incident["attempt_deadline"] and tx.now > incident["attempt_deadline"]:
        raise ApiError("STATE_CONFLICT", {"reason": "attempt_deadline_passed"})
    if incident["submissions"] >= max_submissions:
        raise ApiError("STATE_CONFLICT", {"reason": "submission_budget_exhausted"})


def _parse(raw: bytes, max_bytes: int) -> tuple[Proposal | None, dict[str, Any] | None]:
    try:
        data = loads_strict(raw, max_bytes=max_bytes)
    except StrictJSONError:
        return None, {"reason": "invalid_json"}
    if not isinstance(data, dict):
        return None, {"reason": "body_must_be_object"}
    try:
        return Proposal.model_validate(data), None
    except ValidationError as exc:
        return None, {"reason": "schema", "errors": safe_validation_errors(exc)}


def _catalog_problem(catalog: Catalog, service: str, proposal: Proposal) -> dict | None:
    """정비 초안은 사건 서비스에 등록된 설비와 그 설비에 허용된 매뉴얼만 쓴다."""
    action = proposal.action
    if action.type != "create_work_order_draft":
        return None
    equipment = catalog.equipment_of(service).get(action.equipment_id)
    if equipment is None:
        return {"reason": "unregistered_equipment"}
    if action.manual_ref_id not in equipment.manual_ref_ids:
        return {"reason": "manual_ref_not_allowed"}
    return None


def _block(
    tx: Tx,
    route_id: str,
    incident: Any,
    work: Any,
    *,
    reason: str,
    stage: str,
    reason_detail: str,
    evidence_ids: list[str],
    details: dict[str, Any],
    attempted: list[str],
    agent_summary: str | None = None,
    missing: list[str] | None = None,
    next_steps: list[str] | None = None,
    retry_condition: str | None = None,
) -> None:
    """incident ESCALATED·work BLOCKED와 WORK_BLOCKED 알림 intent를 같은 트랜잭션에 기록한다.

    `symptom_impact`는 사건 details의 관찰 사실로 host가 채운다(모델 요약은 `agent_summary`로 따로).
    `evidence_ids`는 호출자가 이 run·사건에서 확인한 ID만 넘긴다.
    """
    observed = observed_symptom(json.loads(incident["details_json"] or "{}"))
    result = coupled_transition(
        tx,
        incident_id=incident["id"],
        expected_incident_version=incident["version"],
        incident_to="ESCALATED",
        work_id=work["id"],
        expected_work_version=work["version"],
        work_to="BLOCKED",
        actor=Actor.BROKER,
        reason=reason,
        details=details,
    )
    report = blocker_report(
        blocker_code=reason,
        stage=stage,
        incident=incident,
        work=work,
        symptom_impact=observed or f"{incident['service']} 사건(관찰 요약을 만들 수 없음)",
        evidence_ids=evidence_ids,
        owner_route_id=route_id,
        observed_at=tx.now,
        attempted_actions=attempted,
        missing_requirements=missing or [],
        operator_next_step=next_steps or [],
        retry_condition=retry_condition or RETRY_AFTER_APPROVAL,
        reason_detail=reason_detail,
        agent_summary=agent_summary,
    )
    assert result.outbox_event == "WORK_BLOCKED"
    outbox.enqueue(tx, work, result.outbox_event, report, route_id)


def _invalid(
    tx: Tx,
    ctx: AppContext,
    incident: Any,
    work: Any,
    problem: dict[str, Any],
    used: int,
    rid: str,
) -> tuple[int, dict]:
    """B02 실패: 제출 1회를 쓴 422. 예산을 다 썼으면 VALIDATION_FAILED로 이관한다."""
    details = {**problem, "submissions_used": used, "max_submissions": ctx.max_submissions}
    audit.append(tx, incident["run_id"], incident["id"], Actor.BROKER, "PROPOSAL_INVALID", details)
    if used >= ctx.max_submissions:
        _block(
            tx,
            ctx.notification_route_id,
            incident,
            work,
            reason="VALIDATION_FAILED",
            stage="validation",
            reason_detail="제안이 형식·정책 검사를 통과하지 못했고 제출 예산을 모두 썼다",
            evidence_ids=[],
            details={"reason": "submission_budget_exhausted"},
            attempted=[f"submit_proposal → INVALID_PROPOSAL({problem['reason']})"],
            next_steps=["제안과 검사 결과를 검토한 뒤 새 작업 승인 여부를 판단"],
        )
        details["escalated"] = True
    return 422, error_body(rid, "INVALID_PROPOSAL", details)


def _accept(
    tx: Tx,
    agent: AgentPrincipal,
    incident: Any,
    proposal: Proposal,
    key: str,
    body_hash: str,
    used: int,
    rid: str,
) -> tuple[int, dict]:
    proposal_id = new_id("PROP")
    version = transition_incident(
        tx,
        incident["id"],
        incident["version"],
        "VALIDATING",
        Actor.BROKER,
        details={"proposal_id": proposal_id},
    )
    record = {
        "intake_incident_version": version,
        "checks": [{"check": "B01", "result": "PASS"}, {"check": "B02", "result": "PASS"}],
    }
    tx.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id,"
        " idempotency_key, body_sha256, decision, received_at, payload_json, checks_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?, ?)",
        (
            proposal_id,
            agent.run_id,
            agent.incident_id,
            agent.work_id,
            agent.attempt_id,
            key,
            body_hash,
            tx.now,
            canonical_dumps(proposal.model_dump(mode="json")),
            canonical_dumps(record),
        ),
    )
    audit.append(
        tx,
        agent.run_id,
        agent.incident_id,
        Actor.BROKER,
        "PROPOSAL_RECEIVED",
        {
            "proposal_id": proposal_id,
            "action_type": proposal.action.type,
            "category": proposal.category,
            "submissions_used": used,
        },
    )
    receipt = ProposalReceipt(proposal_id=proposal_id, decision="RECEIVED")
    return 202, success_body(rid, receipt.model_dump(mode="json"))


def submit(
    ctx: AppContext, agent: AgentPrincipal, key: str, raw: bytes, rid: str
) -> tuple[int, dict]:
    """제안 한 건을 접수한다. (HTTP 상태, 응답 외피)를 돌려준다."""
    proposal, problem = _parse(raw, ctx.max_body_bytes)
    if proposal is not None:
        body_hash = sha256_hex(proposal.model_dump(mode="json"))
    else:
        body_hash = hashlib.sha256(raw).hexdigest()
    with ctx.store.tx() as tx:
        incident, work = _principal_scope(tx, agent)
        if proposal is not None and (
            proposal.run_id,
            proposal.incident_id,
            proposal.work_id,
            proposal.attempt_id,
        ) != (agent.run_id, agent.incident_id, agent.work_id, agent.attempt_id):
            raise ApiError("FORBIDDEN_SCOPE")
        scope = {
            "principal_scope": agent.scope,
            "method": "POST",
            "path": PROPOSALS_PATH,
            "run_id": agent.run_id,
            "key": key,
        }
        started = idempotency.begin(tx, **scope, body_sha256=body_hash)
        if started.outcome is Outcome.REPLAY:
            assert started.response is not None
            return started.response["status_code"], started.response["body"]
        if started.outcome is Outcome.CONFLICT:
            raise ApiError("IDEMPOTENCY_CONFLICT")
        if started.outcome is Outcome.IN_FLIGHT:
            raise ApiError("STATE_CONFLICT", {"reason": "request_in_progress_or_unknown"})
        _b01(tx, incident, work, ctx.max_submissions)
        if proposal is not None and proposal.action.type == "create_work_order_draft":
            if ctx.catalog is None:
                raise ApiError("DEPENDENCY_UNAVAILABLE")
            problem = _catalog_problem(ctx.catalog, incident["service"], proposal)

        used = incident["submissions"] + 1
        tx.execute("UPDATE incidents SET submissions = ? WHERE id = ?", (used, incident["id"]))
        if problem is not None:
            status, body = _invalid(tx, ctx, incident, work, problem, used, rid)
        else:
            assert proposal is not None
            status, body = _accept(tx, agent, incident, proposal, key, body_hash, used, rid)
        idempotency.complete(tx, **scope, status_code=status, body=body)
    return status, body


# ── 백그라운드 브로커 ─────────────────────────────────────────


def _action_texts(proposal: Proposal) -> list[str]:
    action = proposal.action
    if action.type == "create_pr":
        return [action.root_cause_hypothesis, action.diff]
    if action.type == "create_work_order_draft":
        return [action.symptom, action.probable_cause, *action.open_questions]
    return [
        *action.open_questions,
        *(action.missing_requirements or []),
        action.retry_condition or "",
    ]


@dataclass
class _Case:
    """처리 중인 제안 한 건과 같은 트랜잭션에서 읽은 사건·work."""

    row: dict[str, Any]
    proposal: Proposal
    incident: Any
    work: Any
    record: dict[str, Any]  # checks_json

    def still_validating(self) -> bool:
        """접수 때 만든 VALIDATING 상태·version이 그대로이고 같은 attempt의 work가 RUNNING인가."""
        return (
            self.incident is not None
            and self.work is not None
            and self.incident["status"] == "VALIDATING"
            and self.incident["version"] == self.record.get("intake_incident_version")
            and self.work["status"] == "RUNNING"
            and self.work["attempt_id"] == self.row["attempt_id"]
        )


def _scoped_evidence(tx: Tx, case: _Case) -> list[str]:
    """제안이 인용한 증거 중 이 run·사건에 실제로 있는 ID만(인용 순서 유지)."""
    return [
        evidence_id
        for evidence_id in case.proposal.evidence_ids
        if tx.one(
            "SELECT 1 FROM evidence WHERE id = ? AND run_id = ? AND incident_id = ?",
            (evidence_id, case.row["run_id"], case.row["incident_id"]),
        )
        is not None
    ]


@dataclass
class Broker:
    store: Store
    catalog: Catalog
    templates: dict[str, ManualTemplate]
    route_id: str
    max_submissions: int = 2

    def run(self, stop: threading.Event, interval_seconds: float = 1.0) -> None:
        """같은 프로세스의 백그라운드 루프(W13 `make start`가 thread로 띄운다. 프로세스당 하나).

        시작할 때 CHECKING을 복구하고, `stop`이 설정될 때까지 RECEIVED 제안을 처리한다.
        """
        self.recover_checking()
        while True:
            self.process_pending(keep_going=True)
            if stop.wait(interval_seconds):
                return

    def process_pending(self, limit: int = 20, *, keep_going: bool = False) -> list[str]:
        """RECEIVED 제안을 오래된 순서로 처리한다. 처리를 마친 proposal ID를 돌려준다.

        `keep_going`이면 한 제안의 오류로 멈추지 않는다. 그 제안은 CHECKING에 남고(트랜잭션 취소)
        감사 기록 `PROPOSAL_CHECK_ERROR`에 예외 종류만 남긴다. 다음 시작 때 다시 검사한다.
        """
        done = []
        for _ in range(limit):
            row = self._claim()
            if row is None:
                break
            try:
                self._process(row)
            except Exception as exc:
                if not keep_going:
                    raise
                with self.store.tx() as tx:
                    audit.append(
                        tx,
                        row["run_id"],
                        row["incident_id"],
                        Actor.BROKER,
                        "PROPOSAL_CHECK_ERROR",
                        {"proposal_id": row["id"], "error": type(exc).__name__},
                    )
                continue
            done.append(row["id"])
        return done

    def recover_checking(self) -> int:
        """재시작 때 CHECKING에 남은 제안: 외부 실행 intent가 없으면 다시 검사하도록 RECEIVED로.

        intent가 있는 제안은 그대로 둔다. 그 execution은 UNKNOWN 규칙(reconcile)을 따른다.
        """
        with self.store.tx() as tx:
            reset = 0
            for row in tx.all("SELECT id FROM proposals WHERE decision = 'CHECKING'"):
                if tx.one("SELECT 1 FROM executions WHERE proposal_id = ?", (row["id"],)) is None:
                    tx.execute(
                        "UPDATE proposals SET decision = 'RECEIVED' WHERE id = ?"
                        " AND decision = 'CHECKING'",
                        (row["id"],),
                    )
                    reset += 1
            return reset

    def _claim(self) -> dict[str, Any] | None:
        with self.store.tx() as tx:
            row = tx.one(
                "SELECT * FROM proposals WHERE decision = 'RECEIVED'"
                " ORDER BY received_at, rowid LIMIT 1"
            )
            if row is None:
                return None
            tx.execute(
                "UPDATE proposals SET decision = 'CHECKING' WHERE id = ? AND decision = 'RECEIVED'",
                (row["id"],),
            )
            return dict(row)

    def _process(self, row: dict[str, Any]) -> None:
        with self.store.tx() as tx:
            case = _Case(
                row=row,
                proposal=Proposal.model_validate(json.loads(row["payload_json"])),
                incident=tx.one("SELECT * FROM incidents WHERE id = ?", (row["incident_id"],)),
                work=tx.one("SELECT * FROM work_items WHERE id = ?", (row["work_id"],)),
                record=json.loads(row["checks_json"]),
            )
            checks = (
                ("B03", self._b03_evidence_scope),
                ("B04", self._b04_existing_execution),
                ("B05", self._b05_sensitive_content),
                ("B06", self._b06_state),
            )
            for name, check in checks:
                failure = check(tx, case)
                if failure is not None:
                    code, detail = failure
                    case.record["checks"].append({"check": name, "result": code, **detail})
                    self._reject(tx, case, code)
                    return
                case.record["checks"].append({"check": name, "result": "PASS"})
            action_type = case.proposal.action.type
            if action_type == "create_pr":
                self._reject(tx, case, "PROTECTION_UNAVAILABLE")  # W10에서 패치 검사로 교체
            elif action_type == "create_work_order_draft":
                self._draft(tx, case)
            else:
                self._escalate(tx, case)

    # B03~B06: 통과하면 None, 실패하면 (검사 코드, 세부)

    def _b03_evidence_scope(self, tx: Tx, case: _Case) -> Failure | None:
        found = set(_scoped_evidence(tx, case))
        for evidence_id in case.proposal.evidence_ids:
            if evidence_id not in found:
                return "EVIDENCE_SCOPE_MISMATCH", {"evidence_id": evidence_id}
        return None

    def _b04_existing_execution(self, tx: Tx, case: _Case) -> Failure | None:
        existing = tx.one(
            "SELECT id FROM executions WHERE work_id = ?"
            " AND status IN ('INTENDED', 'RUNNING', 'UNKNOWN', 'SUCCEEDED')",
            (case.row["work_id"],),
        )
        if existing is not None:
            return "STATE_CONFLICT", {"reason": "existing_execution"}
        return None

    def _b05_sensitive_content(self, tx: Tx, case: _Case) -> Failure | None:
        proposal = case.proposal
        texts = [proposal.summary, *_action_texts(proposal)]
        if any(mask_secrets(text) != text for text in texts):
            return "SENSITIVE_CONTENT", {"reason": "secret_pattern"}
        # 정비 초안·이관은 알림·초안으로 사람에게 간다. URL·수신 주소는 허용 채널이 아니다.
        if proposal.action.type != "create_pr" and any(
            _URL.search(text) or _EMAIL.search(text) for text in texts
        ):
            return "SENSITIVE_CONTENT", {"reason": "url_or_address"}
        return None

    def _b06_state(self, tx: Tx, case: _Case) -> Failure | None:
        if not case.still_validating():
            return "STATE_CONFLICT", {"reason": "state_changed"}
        action = case.proposal.action
        if action.type == "create_work_order_draft":
            problem = _catalog_problem(self.catalog, case.incident["service"], case.proposal)
            if problem is not None:
                return "STATE_CONFLICT", {"reason": "catalog_changed"}
        return None

    # 결과

    def _decide(self, tx: Tx, case: _Case, decision: str, event: str, code: str) -> None:
        case.record["decision_reason"] = code
        tx.execute(
            "UPDATE proposals SET decision = ?, checks_json = ?"
            " WHERE id = ? AND decision = 'CHECKING'",
            (decision, canonical_dumps(case.record), case.row["id"]),
        )
        audit.append(
            tx,
            case.row["run_id"],
            case.row["incident_id"],
            Actor.BROKER,
            event,
            {"proposal_id": case.row["id"], "code": code},
        )

    def _reject(self, tx: Tx, case: _Case, code: str) -> None:
        self._decide(tx, case, "REJECTED", "PROPOSAL_REJECTED", code)
        if not case.still_validating():
            return  # 상태가 이미 바뀌었으면 전이하지 않는다
        incident = case.incident
        if incident["submissions"] < self.max_submissions:
            transition_incident(  # 수정 1회: 같은 attempt·원래 deadline
                tx,
                incident["id"],
                incident["version"],
                "INVESTIGATING",
                Actor.BROKER,
                details={"proposal_id": case.row["id"], "rejected": code},
            )
            return
        _block(
            tx,
            self.route_id,
            incident,
            case.work,
            reason="VALIDATION_FAILED",
            stage="validation",
            reason_detail=f"제안이 브로커 검사({code})를 통과하지 못했고 제출 예산을 모두 썼다",
            evidence_ids=_scoped_evidence(tx, case),
            details={"proposal_id": case.row["id"], "rejected": code},
            attempted=[f"submit_proposal {case.row['id']} → REJECTED({code})"],
            next_steps=["거절 사유와 원본 제안을 검토한 뒤 새 작업 승인 여부를 판단"],
        )

    def _draft(self, tx: Tx, case: _Case) -> None:
        action = case.proposal.action
        row, work = case.row, case.work
        template = self.templates.get(action.manual_ref_id)
        if template is None:  # 승인 문구 없이 모델 문장으로 초안을 만들지 않는다
            case.record["checks"].append({"check": "TEMPLATE", "result": "PROTECTION_UNAVAILABLE"})
            return self._reject(tx, case, "PROTECTION_UNAVAILABLE")
        draft = build_draft(action, list(case.proposal.evidence_ids), template)
        execution_id = new_id("EXE")
        tx.execute(
            "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
            " logical_key, idempotency_key, request_sha256, status, stage, intended_at,"
            " updated_at, request_json, result_json) VALUES (?, ?, ?, ?, ?, 'DRAFT_WORK_ORDER',"
            " ?, ?, ?, 'SUCCEEDED', 'drafted', ?, ?, ?, ?)",
            (
                execution_id,
                row["run_id"],
                row["incident_id"],
                row["work_id"],
                row["id"],
                f"draft:{row['work_id']}",
                row["idempotency_key"],
                row["body_sha256"],
                tx.now,
                tx.now,
                canonical_dumps(
                    {
                        "proposal_id": row["id"],
                        "equipment_id": action.equipment_id,
                        "manual_ref_id": action.manual_ref_id,
                    }
                ),
                canonical_dumps(draft),
            ),
        )
        case.record["execution_id"] = execution_id
        self._decide(tx, case, "ALLOWED", "PROPOSAL_ALLOWED", "WORK_ORDER_DRAFTED")
        result = coupled_transition(
            tx,
            incident_id=case.incident["id"],
            expected_incident_version=case.incident["version"],
            incident_to="WORK_ORDER_DRAFTED",
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to="HANDED_OFF",
            actor=Actor.BROKER,
            details={"proposal_id": row["id"], "execution_id": execution_id},
        )
        assert result.outbox_event == "HANDOFF_DRAFTED"
        payload = {
            "schema_version": "linemedic.v4",
            "event_type": "HANDOFF_DRAFTED",
            "run_id": row["run_id"],
            "incident_id": row["incident_id"],
            "work_id": work["id"],
            "generation": work["generation"],
            "repository_id": work["repository_id"],
            "issue_number": work["issue_number"],
            "proposal_id": row["id"],
            "execution_id": execution_id,
            "work_order": draft,
        }
        outbox.enqueue(tx, work, result.outbox_event, payload, self.route_id)

    def _escalate(self, tx: Tx, case: _Case) -> None:
        action = case.proposal.action
        self._decide(tx, case, "ALLOWED", "PROPOSAL_ALLOWED", "ESCALATED")
        _block(
            tx,
            self.route_id,
            case.incident,
            case.work,
            reason=action.reason,
            stage="agent",
            reason_detail="에이전트가 이관을 제안했고 브로커 검사를 통과했다",
            agent_summary=case.proposal.summary,
            evidence_ids=_scoped_evidence(tx, case),
            details={"proposal_id": case.row["id"]},
            attempted=[f"submit_proposal {case.row['id']} → escalate({action.reason})"],
            missing=list(action.missing_requirements or []),
            next_steps=list(action.open_questions)
            or ["에이전트가 남긴 근거를 검토한 뒤 새 작업 승인 여부를 판단"],
            retry_condition=action.retry_condition,
        )
