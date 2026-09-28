"""work 선점·승인·재시도·취소·시작 게이트 (W25, spec 04 §3·§7·§8, spec 15 §1·§6·§8, docs/05 ④·⑧).

트랜잭션 함수(API가 멱등 기록과 같은 트랜잭션에서 부른다):
- `ensure_work`: 같은 `(routing_scope, repo, issue)`에 활성 work가 있으면 그것을 돌려주고,
  다른 incident면 그 incident를 work에 잇는다(작업 복제 없음).
  없으면 generation = 마지막 + 1로 `WAITING_APPROVAL`.
  unique 위반은 삼키지 않고 기존 행을 다시 읽는다.
- `approve`: 현재 Issue snapshot hash가 요청 값과 같을 때만
  `WAITING_APPROVAL → WAITING_NOTIFICATION`과 `WORK_STARTING` outbox intent를 같은 트랜잭션에 쓴다.
  자동 승인 정책(trusted author)도 이 함수를 쓴다.
- `retry`: terminal(BLOCKED·HANDED_OFF) work만. 새 incident(`reopened_from`)·새 generation
  `WAITING_APPROVAL`. 이미 다음 generation이 있으면 거절한다
  (중복 승인으로 generation이 둘 생기지 않는다).
- `cancel`: 시작 전 → CANCELLED + incident ESCALATED + `WORK_CANCELLED` intent. READY·RUNNING →
  `cancel_requested=1`(안전 경계에서 멈춘다). 외부 결과 불명·PR 이후는 거절한다.
- `recheck_scope`: Issue open·승인 snapshot 그대로·취소 요청 없음.

`Supervisor`(루프·hook용): `auto_approve`(W23 새 Issue), `on_scope_changed`(W23 snapshot 변경),
`start_attempt`(시작 게이트: 시작 알림 ACCEPTED·scope 재확인·`one_running_work` 슬롯 →
`READY → RUNNING`과 incident `NEW → INVESTIGATING`, attempt 발급·deadline을 한 트랜잭션에).
**attempt ID는 `start_attempt`만 만든다.** 슬롯이 차 있으면 기다린다(실패 아님).
늦게 온 receipt로 BLOCKED work를 되살리지 않는다.

attempt 실행(W13, `AttemptRuntime`이 있을 때): 게이트를 통과한 뒤에만 workspace(base 파일 사본)와
context(`context.json`, credential 없음)를 만들고, attempt 범위 agent token을 발급해 adapter를
부른다.
adapter는 별도 thread에서 돌리고 deadline(+유예)이 지나면 기다리지 않는다. 끝나면 token을 폐기하고
`ATTEMPT_FINISHED`를 남긴다. 제안 없이 조사 중(INVESTIGATING·RUNNING)이면 attempt를 닫는다
(ESCALATED/BLOCKED: deadline → BUDGET_EXCEEDED, 제안 거절 → VALIDATION_FAILED, 제안 없음 →
INSUFFICIENT_EVIDENCE, 실행 오류 → MODEL_UNAVAILABLE). 재시작 때 끊긴 attempt는 새 세션 없이 닫는다.

sandbox 모드(W15, D88): attempt마다 sandbox를 준비하고 그 안에서 같은 adapter를 부른 뒤 닫는다.
준비하지 못하면 local로 바꿔 돌리지 않는다(MODEL_UNAVAILABLE). identity·정책 hash·effective policy·
보호 확인 결과는 `SANDBOX_PREPARED`와 trace에 남고, `sandbox_verified`는 host가 정한다.
attempt 전후 규칙 묶음 hash가 다르면 `AGENT_RULES_CHANGED`로 남긴다(N10).

초기 사례 검색(W28, D90): workspace를 만들기 전에 host가 한 번 검색한다(`requested_by=supervisor`).
결과(mode·snapshot·retrieval·상태·note ID·projection evidence ID·비신뢰 표시)는 context와 trace에
들어간다. 검색할 수 없으면(UNAVAILABLE) run policy `memory.on_unavailable`이 stop일 때 attempt를
시작하지 않고(LOOKUP_INCOMPLETE), proceed면 `history_status=UNAVAILABLE`을 표시한 채 진행한다.
"""

import json
import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from linemedic.agent import trace
from linemedic.agent.adapter import AgentAdapter, AttemptResult
from linemedic.agent.rules import forbidden_findings, install_rules, prompt_sha256
from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock, from_rfc3339, to_rfc3339
from linemedic.common.config import LineMedicConfig
from linemedic.common.ids import new_id
from linemedic.control_plane import audit
from linemedic.control_plane.auth import AgentPrincipal, TokenRegistry
from linemedic.control_plane.broker.candidate import CandidateError, prepare_workspace
from linemedic.control_plane.errors import ApiError
from linemedic.control_plane.memory.search import TRUST_NOTICE, CaseSearch
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.state import (
    TERMINAL_WORK_STATUSES,
    Actor,
    coupled_transition,
    transition_incident,
    transition_work,
)
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.symptoms import observed_symptom
from linemedic.integrations.sandbox import (
    POLICY_DIR,
    REQUIRED_PROTECTIONS,
    SandboxPort,
    SandboxSession,
    SandboxUnavailable,
    UnconfiguredSandbox,
    policy_dir_sha256,
    save_effective_policy,
    verification,
)

HUMAN_WORK_IN_PROGRESS = "HUMAN_WORK_IN_PROGRESS"
PROPOSAL_ACTIONS = ("create_pr", "create_work_order_draft", "escalate")
RETRYABLE_STATUSES = ("BLOCKED", "HANDED_OFF")
NOT_STARTED = ("WAITING_APPROVAL", "WAITING_NOTIFICATION")


# ── 조회 도우미 ───────────────────────────────────────────────


def active_work(tx: Tx, routing_scope: str, repository_id: int, issue_number: int) -> Any:
    placeholders = ", ".join("?" for _ in TERMINAL_WORK_STATUSES)
    return tx.one(
        "SELECT * FROM work_items WHERE routing_scope = ? AND repository_id = ?"
        f" AND issue_number = ? AND status NOT IN ({placeholders})"
        " ORDER BY generation DESC LIMIT 1",
        (routing_scope, repository_id, issue_number, *sorted(TERMINAL_WORK_STATUSES)),
    )


def _work(tx: Tx, work_id: str) -> Any:
    work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
    if work is None:
        raise ApiError("RESOURCE_NOT_FOUND")
    return work


def _issue(tx: Tx, work: Any) -> Any:
    return tx.one(
        "SELECT * FROM github_issues WHERE repository_id = ? AND issue_number = ?",
        (work["repository_id"], work["issue_number"]),
    )


def _incident(tx: Tx, work: Any) -> Any:
    return tx.one("SELECT * FROM incidents WHERE id = ?", (work["incident_id"],))


def _expect(work: Any, expected_version: int, allowed: Iterable[str]) -> None:
    if work["version"] != expected_version:
        raise ApiError(
            "STATE_CONFLICT",
            {"current_status": work["status"], "current_version": work["version"]},
        )
    if work["status"] not in allowed:
        raise ApiError("STATE_CONFLICT", {"current_status": work["status"]})


def _audit(tx: Tx, work: Any, actor: str, event: str, payload: Mapping[str, Any]) -> None:
    audit.append(tx, work["run_id"], work["incident_id"], actor, event, payload)


def _close_unsent_notice(tx: Tx, work: Any, error: str) -> None:
    """work의 PENDING 시작 알림을 보내지 않은 채 FAILED로 닫는다(감사 NOTIFICATION_FAILED)."""
    notice = tx.one(
        "SELECT * FROM notifications WHERE id = ? AND status = 'PENDING'",
        (work["start_notification_id"],),
    )
    if notice is None:
        return
    record = {**json.loads(notice["result_json"] or "{}"), "last": {"error": error}}
    tx.execute(
        "UPDATE notifications SET status = 'FAILED', next_attempt_at = NULL, updated_at = ?,"
        " result_json = ? WHERE id = ? AND status = 'PENDING'",
        (tx.now, canonical_dumps(record), notice["id"]),
    )
    _audit(
        tx,
        work,
        Actor.SUPERVISOR,
        "NOTIFICATION_FAILED",
        {"notification_id": notice["id"], "event_type": notice["event_type"], "error": error},
    )


def work_authorization(row: Any) -> dict[str, Any]:
    return json.loads(row["authorization_json"])


# ── work 생성 ─────────────────────────────────────────────────


def ensure_work(
    tx: Tx, incident: Any, issue: Any, *, authorization: Mapping[str, Any]
) -> tuple[Any, bool]:
    """incident를 Issue의 활성 work에 잇는다. (work 행, 새로 만들었는가)를 돌려준다.

    활성 work가 다른 incident의 것이면 그 work의 `linked_incident_ids`에 이 incident를 남긴다.
    이 incident의 증거는 그대로 두고, 조사는 활성 work 하나가 한다.
    """
    scope, repository_id, number = (
        incident["routing_scope"],
        issue["repository_id"],
        issue["issue_number"],
    )
    existing = active_work(tx, scope, repository_id, number)
    if existing is None:
        last = tx.one(
            "SELECT MAX(generation) AS g FROM work_items WHERE routing_scope = ?"
            " AND repository_id = ? AND issue_number = ?",
            (scope, repository_id, number),
        )
        work_id = new_id("WORK")
        try:
            tx.execute(
                "INSERT INTO work_items(id, run_id, incident_id, routing_scope, repository_id,"
                " issue_number, generation, status, issue_snapshot_sha256, authorization_json,"
                " created_at, updated_at, details_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'WAITING_APPROVAL', ?, ?, ?, ?, ?)",
                (
                    work_id,
                    incident["run_id"],
                    incident["id"],
                    scope,
                    repository_id,
                    number,
                    (last["g"] or 0) + 1,
                    issue["snapshot_sha256"],
                    canonical_dumps(dict(authorization)),
                    tx.now,
                    tx.now,
                    canonical_dumps({"issue_node_id": issue["node_id"]}),
                ),
            )
            return tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,)), True
        except sqlite3.IntegrityError:
            existing = active_work(tx, scope, repository_id, number)  # 경합: 기존 행을 다시 읽는다
            if existing is None:
                raise
    if existing["incident_id"] != incident["id"]:
        details = json.loads(existing["details_json"] or "{}")
        linked = details.setdefault("linked_incident_ids", [])
        if incident["id"] not in linked:
            linked.append(incident["id"])
            tx.execute(
                "UPDATE work_items SET details_json = ?, updated_at = ? WHERE id = ?",
                (canonical_dumps(details), tx.now, existing["id"]),
            )
            _audit(
                tx, existing, Actor.SUPERVISOR, "INCIDENT_LINKED", {"incident_id": incident["id"]}
            )
            existing = tx.one("SELECT * FROM work_items WHERE id = ?", (existing["id"],))
    return existing, False


# ── 사람 작업·scope ───────────────────────────────────────────


def _references(text: str | None, number: int) -> bool:
    return bool(text) and re.search(rf"(?<![\w/&#])#{number}(?!\d)", text) is not None


def check_human_work(
    issue: Mapping[str, Any], open_pulls: Iterable[Mapping[str, Any]], bot_id: int | None
) -> str | None:
    """사람이 이미 이 Issue를 맡았거나 PR을 열었으면 `HUMAN_WORK_IN_PROGRESS`."""
    for assignee in issue.get("assignees") or []:
        if isinstance(assignee, Mapping) and assignee.get("id") != bot_id:
            return HUMAN_WORK_IN_PROGRESS
    number = issue["number"]
    for pull in open_pulls:
        author = pull.get("user") or {}
        if author.get("id") == bot_id:
            continue  # 우리 봇의 PR은 같은 work의 산출물이다
        if _references(pull.get("title"), number) or _references(pull.get("body"), number):
            return HUMAN_WORK_IN_PROGRESS
    return None


def recheck_scope(tx: Tx, work: Any) -> str | None:
    """시작 직전 재확인. 통과하면 None, 아니면 issue_closed·snapshot_changed·cancel_requested."""
    issue = _issue(tx, work)
    if issue is None or issue["state"] != "open":
        return "issue_closed"
    if issue["snapshot_sha256"] != work["issue_snapshot_sha256"]:
        return "snapshot_changed"  # 본문·담당자·권한 label이 바뀌었다
    if work["cancel_requested"]:
        return "cancel_requested"
    return None


# ── 승인 ──────────────────────────────────────────────────────


def approve(
    tx: Tx,
    work_id: str,
    expected_version: int,
    expected_snapshot: str,
    *,
    principal: str,
    note: str,
    route_id: str,
    actor: Actor = Actor.OPERATOR,
) -> dict[str, Any]:
    """정확한 Issue snapshot을 승인한다: `WAITING_NOTIFICATION`과 `WORK_STARTING` intent를 같이."""
    work = _work(tx, work_id)
    _expect(work, expected_version, ("WAITING_APPROVAL",))
    issue = _issue(tx, work)
    if issue is None or issue["state"] != "open":
        raise ApiError("STATE_CONFLICT", {"reason": "issue_not_open"})
    if issue["snapshot_sha256"] != expected_snapshot:
        raise ApiError("ISSUE_SCOPE_CHANGED", {"current_snapshot_sha256": issue["snapshot_sha256"]})
    incident = _incident(tx, work)
    if incident is None or incident["status"] != "NEW":
        raise ApiError("STATE_CONFLICT", {"reason": "incident_not_new"})
    approval = {
        **work_authorization(work),
        "approved": {"by": principal, "at": tx.now, "note": note, "snapshot": expected_snapshot},
    }
    payload = {
        "schema_version": "linemedic.v4",
        "event_type": "WORK_STARTING",
        "run_id": work["run_id"],
        "incident_id": work["incident_id"],
        "work_id": work["id"],
        "generation": work["generation"],
        "repository_id": work["repository_id"],
        "issue_number": work["issue_number"],
        "issue_snapshot_sha256": expected_snapshot,
        "approved_by": principal,
        "scope": {
            "service": incident["service"],
            "line_id": incident["line_id"],
            "allowed_actions": list(PROPOSAL_ACTIONS),
        },
        "observed_at": tx.now,
    }
    notification_id = outbox.enqueue(tx, work, "WORK_STARTING", payload, route_id)
    version = transition_work(
        tx,
        work_id,
        expected_version,
        "WAITING_NOTIFICATION",
        actor,
        details={"approved_by": principal},
        issue_snapshot_sha256=expected_snapshot,
        authorization_json=canonical_dumps(approval),
        start_notification_id=notification_id,
    )
    _audit(tx, work, actor, "WORK_APPROVED", {"work_id": work_id, "by": principal})
    return {
        "work_id": work_id,
        "status": "WAITING_NOTIFICATION",
        "version": version,
        "start_notification": {"id": notification_id, "status": "PENDING"},
    }


# ── 재시도·취소 ───────────────────────────────────────────────


def retry(
    tx: Tx, work_id: str, expected_version: int, note: str, *, principal: str
) -> dict[str, Any]:
    """terminal work를 운영자 승인으로 다시 시작한다: 새 incident·새 generation(승인 대기부터)."""
    work = _work(tx, work_id)
    _expect(work, expected_version, RETRYABLE_STATUSES)
    successor = tx.one(
        "SELECT id FROM work_items WHERE routing_scope = ? AND repository_id = ?"
        " AND issue_number = ? AND generation > ? ORDER BY generation LIMIT 1",
        (work["routing_scope"], work["repository_id"], work["issue_number"], work["generation"]),
    )
    if successor is not None:  # 중복 승인: generation을 하나만 만든다
        raise ApiError("STATE_CONFLICT", {"reason": "already_retried", "work_id": successor["id"]})
    issue = _issue(tx, work)
    if issue is None or issue["state"] != "open":
        raise ApiError("STATE_CONFLICT", {"reason": "issue_not_open"})  # 닫힌 Issue를 열지 않는다
    old = _incident(tx, work)
    incident_id = new_id("INC")
    tx.execute(
        "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, status, category, service, line_id, count,"
        " first_seen, last_seen, reopened_from, details_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', NULL, ?, ?, 0, ?, ?, ?, ?)",
        (
            incident_id,
            old["run_id"],
            old["routing_scope"],
            old["repository_id"],
            old["fingerprint"],
            old["fingerprint_version"],
            old["source_kind"],
            old["service"],
            old["line_id"],
            tx.now,
            tx.now,
            old["id"],
            old["details_json"],
        ),
    )
    incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    authorization = {
        "policy": "operator",
        "basis": "operator_retry",
        "by": principal,
        "previous_work_id": work_id,
        "blocker_resolution_note": note,
        "auto_start_eligible": False,
    }
    new_work, created = ensure_work(tx, incident, issue, authorization=authorization)
    assert created, "활성 work가 없을 때만 retry한다"
    payload = {"previous_work_id": work_id, "work_id": new_work["id"], "by": principal}
    audit.append(tx, work["run_id"], old["id"], Actor.OPERATOR, "WORK_RETRIED", payload)
    audit.append(tx, work["run_id"], incident_id, Actor.OPERATOR, "WORK_RETRIED", payload)
    return {
        "previous_work_id": work_id,
        "work_id": new_work["id"],
        "incident_id": incident_id,
        "generation": new_work["generation"],
        "status": "WAITING_APPROVAL",
    }


def _cancel_before_start(tx: Tx, work: Any, actor: Actor, note: str, route_id: str) -> int:
    """시작 전 work → CANCELLED, incident NEW → ESCALATED, `WORK_CANCELLED` intent."""
    version = transition_work(
        tx,
        work["id"],
        work["version"],
        "CANCELLED",
        actor,
        "WORK_CANCELLED",
        details={"note": note},
    )
    incident = _incident(tx, work)
    if incident is not None and incident["status"] == "NEW":
        transition_incident(
            tx,
            incident["id"],
            incident["version"],
            "ESCALATED",
            Actor.SUPERVISOR,
            "WORK_CANCELLED",
            details={"work_id": work["id"]},
        )
    payload = {
        "schema_version": "linemedic.v4",
        "event_type": "WORK_CANCELLED",
        "run_id": work["run_id"],
        "incident_id": work["incident_id"],
        "work_id": work["id"],
        "generation": work["generation"],
        "repository_id": work["repository_id"],
        "issue_number": work["issue_number"],
        "work_status_before": work["status"],
        "note": note,
        "side_effect_state": "NONE",  # 시작 전: attempt·외부 쓰기 없음
        "observed_at": tx.now,
    }
    outbox.enqueue(tx, work, "WORK_CANCELLED", payload, route_id)
    return version


def cancel(
    tx: Tx, work_id: str, expected_version: int, note: str, *, principal: str, route_id: str
) -> dict[str, Any]:
    work = _work(tx, work_id)
    if work["version"] != expected_version:
        raise ApiError(
            "STATE_CONFLICT",
            {"current_status": work["status"], "current_version": work["version"]},
        )
    status = work["status"]
    if status in NOT_STARTED:
        version = _cancel_before_start(tx, work, Actor.OPERATOR, note, route_id)
        _audit(tx, work, Actor.OPERATOR, "WORK_CANCELLED", {"work_id": work_id, "by": principal})
        return {"work_id": work_id, "status": "CANCELLED", "version": version}
    if status in ("READY", "RUNNING"):
        if not work["cancel_requested"]:
            tx.execute(
                "UPDATE work_items SET cancel_requested = 1, version = version + 1,"
                " updated_at = ? WHERE id = ? AND version = ?",
                (tx.now, work_id, expected_version),
            )
            _audit(
                tx,
                work,
                Actor.OPERATOR,
                "WORK_CANCEL_REQUESTED",
                {"work_id": work_id, "by": principal},
            )
        current = _work(tx, work_id)
        return {
            "work_id": work_id,
            "status": status,
            "version": current["version"],
            "cancel_requested": True,  # supervisor가 안전 경계에서 멈춘다
        }
    if status == "EXECUTION_UNKNOWN":
        raise ApiError("STATE_CONFLICT", {"reason": "external_result_unknown"})
    if status in ("WAITING_REVIEW", "WAITING_VERIFICATION"):
        raise ApiError("STATE_CONFLICT", {"reason": "use_incident_escalate"})
    raise ApiError("STATE_CONFLICT", {"current_status": status})


# ── 시작 알림 게이트 (W26) ─────────────────────────────────────


def start_wait_exceeded(created_at: str | None, now: str, start_wait_seconds: float) -> bool:
    """시작 알림을 만든 뒤 `start_wait_seconds`가 지났는가(D83 ⑥).

    발송 전(outbox)·receipt 저장·만료 검사가 같은 기준을 쓴다. supervisor 루프가 attempt 실행으로
    멈춰 있어도 60초 게이트가 지켜지게 한다.
    """
    if created_at is None:
        return True
    elapsed = (from_rfc3339(now) - from_rfc3339(created_at)).total_seconds()
    return elapsed > start_wait_seconds


def on_start_notice_accepted(tx: Tx, notification: Any, start_wait_seconds: float) -> str:
    """필수 route의 시작 알림 receipt가 저장됐다. 그 work가 아직 알림 대기면 READY로 옮긴다.

    - 이미 BLOCKED·CANCELLED 등으로 끝난 work는 되살리지 않는다(늦게 온 receipt)
    - 대기 시간(`start_wait_seconds`)이 지난 뒤 저장된 receipt도 READY로 올리지 않는다. work는
      알림 대기에 남고 다음 `expire_start_notices`가 멈춘다(만료 검사가 늦게 돌아도 게이트 유지)
    """
    work = tx.one("SELECT * FROM work_items WHERE id = ?", (notification["work_id"],))
    if work is None or work["start_notification_id"] != notification["id"]:
        return "not_start_notice"
    late = None
    if work["status"] != "WAITING_NOTIFICATION":
        late = "work_not_waiting"
    elif start_wait_exceeded(notification["created_at"], tx.now, start_wait_seconds):
        late = "start_wait_exceeded"
    if late is not None:
        _audit(
            tx,
            work,
            Actor.NOTIFIER,
            "LATE_START_RECEIPT",
            {
                "notification_id": notification["id"],
                "work_status": work["status"],
                "reason": late,
            },
        )
        return "late"
    transition_work(
        tx,
        work["id"],
        work["version"],
        "READY",
        Actor.NOTIFIER,
        details={"notification_id": notification["id"], "receipt_id": notification["receipt_id"]},
    )
    return "ready"


# ── supervisor ────────────────────────────────────────────────


@dataclass(frozen=True)
class AttemptRuntime:
    """attempt를 실제로 돌리는 host 자원(W13). 없으면 supervisor는 attempt를 만들기만 한다(W25)."""

    adapter: AgentAdapter
    tokens: TokenRegistry
    mirror: Path  # 신뢰 mirror(base 파일을 꺼낸다)
    runs_dir: Path
    tools_base_url: str
    grace_seconds: float = 5.0  # deadline 뒤 adapter를 더 기다리는 시간
    poll_seconds: float = 1.0
    eval_terms: tuple[str, ...] = ()  # workspace 금지 자료 검사의 평가 식별자(W14)
    sandbox: SandboxPort | None = None  # sandbox 모드의 실행 환경(W15). 없으면 준비를 거절한다
    sandbox_policy_dir: Path = POLICY_DIR  # 고정한 sandbox 정책 파일(G5 뒤)
    case_search: CaseSearch | None = None  # 초기 사례 검색(W28). 없으면 cold_start만 진행한다


MAX_BLOCKER_EVIDENCE = 5


@dataclass(frozen=True)
class AttemptStart:
    status: str  # started | waiting_slot | not_ready | blocked | cancelled
    work_id: str
    attempt_id: str | None = None
    deadline: str | None = None
    tool_call_budget: int | None = None
    reason: str | None = None


class Supervisor:
    def __init__(
        self,
        store: Store,
        *,
        config: LineMedicConfig,
        clock: Clock,
        route_id: str | None = None,
        runtime: AttemptRuntime | None = None,
        run_id: str | None = None,
    ) -> None:
        self.store = store
        # 주면 이 run의 work만 다룬다(W19: reset 뒤 과거 run 작업을 이어받지 않게)
        self.run_id = run_id
        self.clock = clock
        self.agent = config.agent
        self.route_id = route_id or config.notifications.required_start_route_id
        self.start_wait_seconds = config.notifications.start_wait_seconds
        self.runtime = runtime
        # 설정이 없으면 local이다. local 결과는 평가 집계에 넣지 않는다
        self.agent_mode = config.agent.mode or "local"
        self.memory = config.memory

    def auto_approve(self, work_id: str) -> None:
        """승인된 작성자(W23 `auto_start_eligible`)의 work를 등록 정책으로 승인한다."""
        with self.store.tx() as tx:
            work = _work(tx, work_id)
            if not work_authorization(work).get("auto_start_eligible"):
                return
            approve(
                tx,
                work_id,
                work["version"],
                work["issue_snapshot_sha256"],
                principal="policy:trusted_authors",
                note="승인된 작성자의 새 Issue(등록 정책)",
                route_id=self.route_id,
                actor=Actor.ROUTER,
            )

    def on_scope_changed(self, work_id: str) -> None:
        """승인 snapshot이 바뀌었다(W23). 시작 전이면 멈추고, 실행 중이면 취소를 요청한다.

        `WAITING_APPROVAL`은 그대로 둔다: 승인할 때 현재 snapshot을 다시 요구한다(재승인 대기).
        """
        with self.store.tx() as tx:
            work = _work(tx, work_id)
            if work["status"] in ("WAITING_NOTIFICATION", "READY"):
                self._block(
                    tx, work, "ISSUE_SCOPE_CHANGED", "승인한 뒤 Issue 내용·담당자·권한이 바뀌었다"
                )
            elif work["status"] == "RUNNING" and not work["cancel_requested"]:
                tx.execute(
                    "UPDATE work_items SET cancel_requested = 1, version = version + 1,"
                    " updated_at = ? WHERE id = ?",
                    (tx.now, work_id),
                )
                _audit(
                    tx, work, Actor.SUPERVISOR, "WORK_CANCEL_REQUESTED", {"reason": "scope_changed"}
                )

    def expire_start_notices(self) -> list[str]:
        """시작 알림이 `start_wait_seconds` 안에 접수되지 않았거나 명확히 실패한 work를 멈춘다.

        BLOCKED(`START_NOTICE_UNCONFIRMED`) + incident ESCALATED + `WORK_BLOCKED` intent.
        UNKNOWN도 기다리는 시간이 지나면 멈춘다. 나중에 FOUND가 와도 되살리지 않는다.
        아직 보내지 않은(PENDING) 시작 알림은 같은 트랜잭션에서 `FAILED(expired_before_send)`로
        닫는다. 시작하지 않을 work에 "작업 시작 예정" 댓글이 나중에 달리지 않게 한다.
        SENDING·UNKNOWN은 이미 나갔을 수 있으므로 그대로 두고 조정 결과는 감사만 남긴다.
        대기 시간이 지난 뒤 저장된 receipt(ACCEPTED인데 work가 아직 알림 대기)도 멈춘다. 제시간
        receipt는 저장과 같은 트랜잭션에서 READY가 되므로 여기 남은 ACCEPTED는 늦은 것뿐이다(D83 ⑥).
        """
        blocked = []
        now = self.clock.utc_now()
        with self.store.tx() as tx:
            rows = tx.all(
                "SELECT w.id AS work_id, n.status AS notice_status,"
                " n.created_at AS notice_created_at"
                " FROM work_items w LEFT JOIN notifications n ON n.id = w.start_notification_id"
                " WHERE w.status = 'WAITING_NOTIFICATION' AND (? IS NULL OR w.run_id = ?)"
                " ORDER BY w.created_at",
                (self.run_id, self.run_id),
            )
            for row in rows:
                expired = start_wait_exceeded(
                    row["notice_created_at"], to_rfc3339(now), self.start_wait_seconds
                )
                if not expired and row["notice_status"] != "FAILED":
                    continue
                work = _work(tx, row["work_id"])
                reason = {
                    "FAILED": "시작 알림 실패",
                    "ACCEPTED": "대기 시간이 지난 뒤 시작 알림 접수",
                }.get(row["notice_status"] or "", "시작 알림 대기 시간 초과")
                self._block(
                    tx,
                    work,
                    "START_NOTICE_UNCONFIRMED",
                    f"{reason}(상태 {row['notice_status']}): 코드 작업을 시작하지 않았다",
                    blocker_code="START_NOTICE_UNCONFIRMED",
                )
                if row["notice_status"] == "PENDING":
                    _close_unsent_notice(tx, work, "expired_before_send")
                blocked.append(work["id"])
        return blocked

    def start_attempt(self, work_id: str) -> AttemptStart:
        """시작 게이트. 이 함수만 attempt를 만든다(INV-12)."""
        with self.store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
            if work is None or work["status"] != "READY":
                return AttemptStart(
                    "not_ready", work_id, reason=work["status"] if work else "not_found"
                )
            notice = None
            if work["start_notification_id"]:
                notice = tx.one(
                    "SELECT status, event_type, work_id, route_id, accepted_at, result_json"
                    " FROM notifications WHERE id = ?",
                    (work["start_notification_id"],),
                )
            if (
                notice is None
                or notice["event_type"] != "WORK_STARTING"
                or notice["work_id"] != work_id  # 같은 work·generation의 시작 알림
                or notice["route_id"] != self.route_id  # 필수 route
                or notice["status"] != "ACCEPTED"
            ):
                return AttemptStart("not_ready", work_id, reason="start_notice_unconfirmed")
            reason = recheck_scope(tx, work)
            if reason == "cancel_requested":
                _cancel_before_start(tx, work, Actor.SUPERVISOR, "시작 전 취소 요청", self.route_id)
                return AttemptStart("cancelled", work_id, reason=reason)
            if reason is not None:
                self._block(tx, work, "ISSUE_SCOPE_CHANGED", f"시작 직전 재확인 실패: {reason}")
                return AttemptStart("blocked", work_id, reason=reason)
            running = tx.one("SELECT id FROM work_items WHERE status = 'RUNNING'")
            if running is not None:  # one_running_work: 기다린다(실패 아님)
                return AttemptStart("waiting_slot", work_id, reason=running["id"])
            incident = _incident(tx, work)
            if incident is None or incident["status"] != "NEW":
                return AttemptStart("not_ready", work_id, reason="incident_not_new")
            attempt_id = new_id("ATT")
            deadline = to_rfc3339(
                self.clock.utc_now() + timedelta(seconds=self.agent.deadline_seconds)
            )
            transition_incident(
                tx,
                incident["id"],
                incident["version"],
                "INVESTIGATING",
                Actor.SUPERVISOR,
                details={"work_id": work_id, "attempt_id": attempt_id},
                attempt_id=attempt_id,
                attempt_deadline=deadline,
                submissions=0,
            )
            transition_work(
                tx,
                work_id,
                work["version"],
                "RUNNING",
                Actor.SUPERVISOR,
                details={"attempt_id": attempt_id},
                attempt_id=attempt_id,
            )
            _audit(
                tx,
                work,
                Actor.SUPERVISOR,
                "ATTEMPT_STARTED",
                {
                    "attempt_id": attempt_id,
                    "deadline": deadline,
                    "tool_call_budget": self.agent.tool_call_budget,
                    "max_submissions": self.agent.max_submissions,
                    "agent_mode": self.agent_mode,
                    # provider 시각과, 이 audit와 같은 시계로 receipt를 저장한 시각(receipt ≤ 시작)
                    "start_notice_accepted_at": notice["accepted_at"],
                    "start_notice_recorded_at": json.loads(notice["result_json"] or "{}").get(
                        "recorded_at"
                    ),
                    # 사람 제안(manual_integration)을 에이전트 산출물로 표시하지 않게 남긴다(W13)
                    **(
                        {
                            "adapter": self.runtime.adapter.name,
                            "origin": self.runtime.adapter.origin,
                        }
                        if self.runtime is not None
                        else {}
                    ),
                },
            )
        return AttemptStart("started", work_id, attempt_id, deadline, self.agent.tool_call_budget)

    # ── attempt 실행 (W13) ─────────────────────────────────────

    def run_ready(self, limit: int = 5) -> list[dict[str, Any]]:
        """READY work를 오래된 순서로 시작하고, 시작하면 adapter를 끝까지 부른다(한 번에 하나)."""
        if self.runtime is None:
            raise RuntimeError("attempt runtime이 없다")
        with self.store.read() as tx:
            ids = [
                row["id"]
                for row in tx.all(
                    "SELECT id FROM work_items WHERE status = 'READY'"
                    " AND (? IS NULL OR run_id = ?) ORDER BY updated_at, id LIMIT ?",
                    (self.run_id, self.run_id, limit),
                )
            ]
        results: list[dict[str, Any]] = []
        for work_id in ids:
            started = self.start_attempt(work_id)
            if started.status != "started":
                results.append({"work_id": work_id, "status": started.status})
                if started.status == "waiting_slot":
                    break
                continue
            results.append(self.run_attempt(started))
        return results

    def run_attempt(self, started: AttemptStart) -> dict[str, Any]:
        """게이트를 통과한 attempt 하나: workspace·context·token → adapter → 정리."""
        runtime = self.runtime
        if runtime is None or started.attempt_id is None or started.deadline is None:
            raise RuntimeError("시작한 attempt와 runtime이 필요하다")
        with self.store.read() as tx:
            work = _work(tx, started.work_id)
            incident = _incident(tx, work)
            notice = tx.one(
                "SELECT id, receipt_id, accepted_at FROM notifications WHERE id = ?",
                (work["start_notification_id"],),
            )
            run = tx.one("SELECT config_json FROM demo_runs WHERE id = ?", (work["run_id"],))
        manifest = json.loads(run["config_json"]) if run is not None else {}
        base_sha = (manifest.get("runtime_env") or {}).get("baseline_commit")
        root = runtime.runs_dir / work["run_id"] / "workspaces" / started.attempt_id
        adapter = runtime.adapter
        memory = self._initial_memory(work, incident)
        if memory["status"] == "UNAVAILABLE" and self.memory.on_unavailable == "stop":
            failed = AttemptResult(
                "error",
                adapter.name,
                adapter.origin,
                detail=f"memory:unavailable({memory.get('reason') or 'unknown'})",
            )
            return self.finish_attempt(started, failed)
        try:
            if not base_sha:
                raise CandidateError("run_baseline_unconfigured")
            repo = prepare_workspace(mirror=runtime.mirror, root=root / "work", base_sha=base_sha)
        except CandidateError as exc:
            failed = AttemptResult(
                "error", adapter.name, adapter.origin, detail=f"workspace:{exc.reason}"
            )
            return self.finish_attempt(started, failed)
        workspace = repo.parent  # work/{repo,output}: 에이전트가 쓰는 곳
        (workspace / "output").mkdir()
        rules_dir = root / "agent_rules"
        prompt_sha256 = install_rules(rules_dir)  # 읽기 전용 prompt·skill·도구 설명
        terms = runtime.eval_terms
        findings = [
            *forbidden_findings(workspace, terms=terms),
            *forbidden_findings(rules_dir, terms=terms, rules=True),
        ]
        if findings:  # 넣지 않을 자료가 보이면 시작하지 않는다(위치·종류만 남긴다)
            listed = ", ".join(str(f) for f in findings[:5])
            failed = AttemptResult(
                "error", adapter.name, adapter.origin, detail=f"workspace:forbidden({listed})"
            )
            return self.finish_attempt(started, failed)
        session: SandboxSession | None = None
        port: SandboxPort = runtime.sandbox or UnconfiguredSandbox()
        if self.agent_mode == "sandbox":  # local로 바꿔 돌리지 않는다
            try:
                session = port.prepare(
                    run_id=work["run_id"],
                    attempt_id=started.attempt_id,
                    workspace=workspace,
                    rules_dir=rules_dir,
                )
            except Exception as exc:  # noqa: BLE001 — 준비 실패는 attempt 결과로 남긴다
                reason = exc.reason if isinstance(exc, SandboxUnavailable) else type(exc).__name__
                failed = AttemptResult(
                    "error", adapter.name, adapter.origin, detail=f"sandbox:{reason}"
                )
                return self.finish_attempt(started, failed)
        try:
            sandbox = (
                None if session is None else self._record_sandbox(started, work, session, port)
            )
            tools_base_url = (session.tools_base_url if session else None) or runtime.tools_base_url
            context = {
                "schema_version": "linemedic.v4",
                "run_id": work["run_id"],
                "incident_id": incident["id"],
                "work_id": work["id"],
                "attempt_id": started.attempt_id,
                "deadline": started.deadline,
                "issue": {
                    "repository_id": work["repository_id"],
                    "number": work["issue_number"],
                    "snapshot_sha256": work["issue_snapshot_sha256"],
                },
                "start_notice": {
                    "notification_id": notice["id"] if notice is not None else None,
                    "receipt_id": notice["receipt_id"] if notice is not None else None,
                    "accepted_at": notice["accepted_at"] if notice is not None else None,
                },
                "base": {"sha": base_sha, "service": incident["service"]},
                "memory": memory,  # 초기 사례 검색(W28). 사례 본문은 비신뢰 자료다
                "budget": {
                    "tool_calls": self.agent.tool_call_budget,
                    "max_submissions": self.agent.max_submissions,
                },
                "tools": {"base_url": tools_base_url},
                "adapter": adapter.name,
                "origin": adapter.origin,
                "agent_mode": self.agent_mode,
                "prompt_sha256": prompt_sha256,
                # local은 host 경로, sandbox는 안에서 보이는 경로
                # (/sandbox/work·/agent_rules, 읽기 전용)
                "rules_dir": session.rules_path if session else str(rules_dir),
                "sandbox": None
                if session is None
                else {
                    "identity": session.identity,
                    "workspace": session.workspace_path,
                    "rules_dir": session.rules_path,
                },
            }
            context_ref = root / "context.json"
            context_ref.write_text(
                json.dumps(context, ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
            principal = AgentPrincipal(
                work["run_id"], incident["id"], work["id"], started.attempt_id
            )
            token = runtime.tokens.issue_agent_token(principal)
            try:
                result = self._call_adapter(started, work, incident, workspace, context_ref, token)
                if (
                    result.retryable
                    and not result.proposal_ids
                    and self._before(started.deadline)
                    and self._nothing_submitted(started, work)
                ):
                    # 외부 변경 없는 조사 단계의 모델 일시 오류: 같은 deadline 안에서 한 번만 다시
                    self._audit_retry(started, result)
                    result = self._call_adapter(
                        started, work, incident, workspace, context_ref, token
                    )
            finally:
                runtime.tokens.revoke_attempt(started.attempt_id)
        finally:
            if session is not None:  # adapter가 끝나면(실패해도) 그 sandbox를 닫는다
                self._close_sandbox(started, work, port, session)
        rules = self._check_rules(started, work, rules_dir, prompt_sha256)
        trace_ref = self._record_trace(
            started, work, prompt_sha256, result, sandbox=sandbox, rules=rules, memory=memory
        )
        return self.finish_attempt(started, result, trace_ref=trace_ref)

    def _initial_memory(self, work: Any, incident: Any) -> dict[str, Any]:
        """시작 게이트 뒤 host가 한 번 하는 사례 검색. cold_start는 DISABLED로 기록된다."""
        runtime = self.runtime
        assert runtime is not None
        search = runtime.case_search
        if search is None:  # 검색기를 주지 않은 조립(기록 없음): cold_start만 그대로 진행한다
            status = "DISABLED" if self.memory.mode == "cold_start" else "UNAVAILABLE"
            data: dict[str, Any] = {"mode": self.memory.mode, "snapshot_id": None,
                                    "retrieval_id": None, "status": status, "hits": []}  # fmt: skip
            if status == "UNAVAILABLE":
                data["reason"] = "search_not_configured"
            evidence_ids: list[str] = []
        else:
            result = search.search(
                run_id=work["run_id"],
                incident_id=incident["id"],
                work_id=work["id"],
                requested_by="supervisor",
            )
            data, evidence_ids = result.data, result.evidence_ids
        hits = data.get("hits") or []
        return {
            "mode": data["mode"],
            "snapshot_id": data.get("snapshot_id"),
            "retrieval_id": data.get("retrieval_id"),
            "status": data["status"],
            "history_status": data["status"],
            "reason": data.get("reason"),
            "note_ids": [hit["note_id"] for hit in hits],
            "evidence_ids": list(evidence_ids),
            "hits": hits,
            "trust": TRUST_NOTICE,
        }

    def _record_sandbox(
        self, started: AttemptStart, work: Any, session: SandboxSession, port: SandboxPort
    ) -> dict[str, Any]:
        """sandbox identity·정책 hash·effective policy·보호 확인을 남긴다.

        확인 여부(`verified`)는 host가 정한다.
        """
        runtime = self.runtime
        assert runtime is not None
        try:
            policy_sha256 = policy_dir_sha256(runtime.sandbox_policy_dir)
        except ValueError:  # symlink 등 고정할 수 없는 정책은 확인하지 못한 것이다
            policy_sha256 = None
        stored = save_effective_policy(runtime.runs_dir, work["run_id"], session.effective_policy)
        verified, unverified = verification(session.checks, policy_sha256=policy_sha256)
        record = {
            "attempt_id": started.attempt_id,
            "sandbox": port.name,
            "identity": session.identity,
            "version": session.version,
            "policy_sha256": policy_sha256,
            "effective_policy_sha256": stored.sha256,
            "effective_policy_ref": stored.ref,
            "effective_policy_masked": stored.masked,
            "checks": {name: session.checks.get(name, "NOT_RUN") for name in REQUIRED_PROTECTIONS},
            "verified": verified,
            "unverified": unverified,
        }
        with self.store.tx() as tx:
            _audit(tx, _work(tx, started.work_id), Actor.SUPERVISOR, "SANDBOX_PREPARED", record)
        return record

    def _close_sandbox(
        self, started: AttemptStart, work: Any, port: SandboxPort, session: SandboxSession
    ) -> None:
        """그 attempt의 sandbox를 닫는다. 닫지 못했으면 숨기지 않고 남긴다."""
        try:
            port.close(session)
            result = "closed"
        except Exception as exc:  # noqa: BLE001 — 정리 실패도 기록한다
            result = f"error:{type(exc).__name__}"
        with self.store.tx() as tx:
            _audit(
                tx,
                _work(tx, started.work_id),
                Actor.SUPERVISOR,
                "SANDBOX_CLOSED",
                {"attempt_id": started.attempt_id, "identity": session.identity, "result": result},
            )

    def _check_rules(
        self, started: AttemptStart, work: Any, rules_dir: Path, before: str
    ) -> dict[str, Any]:
        """attempt 뒤 규칙 묶음 hash를 다시 잰다. 다르면(읽지 못해도) 변경으로 남긴다(N10)."""
        try:
            after: str | None = prompt_sha256(rules_dir)
        except OSError:
            after = None
        rules = {"before": before, "after": after, "changed": after != before}
        if rules["changed"]:
            with self.store.tx() as tx:
                _audit(
                    tx,
                    _work(tx, started.work_id),
                    Actor.SUPERVISOR,
                    "AGENT_RULES_CHANGED",
                    {"attempt_id": started.attempt_id, "before": before, "after": after},
                )
        return rules

    def _before(self, deadline: str) -> bool:
        return self.clock.utc_now() < from_rfc3339(deadline)

    def _nothing_submitted(self, started: AttemptStart, work: Any) -> bool:
        """재시도 전에 서버 기록으로 외부 변경이 없었는지 본다(adapter 보고만 믿지 않는다).

        사건이 아직 같은 attempt로 조사 중이고, 이 attempt의 제안 행과 제출 호출 기록이 없어야 한다.
        제출 직후 timeout으로 adapter가 제안 ID를 돌려주지 못한 경우도 여기서 막는다.
        """
        run_id, incident_id = work["run_id"], work["incident_id"]
        with self.store.read() as tx:
            incident = tx.one(
                "SELECT status, attempt_id FROM incidents WHERE id = ?", (incident_id,)
            )
            if (
                incident is None
                or incident["status"] != "INVESTIGATING"
                or incident["attempt_id"] != started.attempt_id
            ):
                return False
            proposals = tx.one(
                "SELECT COUNT(*) FROM proposals WHERE run_id = ? AND incident_id = ?"
                " AND attempt_id = ?",
                (run_id, incident_id, started.attempt_id),
            )[0]
            submit_calls = tx.one(
                "SELECT COUNT(*) FROM audit_events WHERE run_id = ? AND incident_id = ?"
                " AND event_type IN ('TOOL_CALL', 'TOOL_CALL_REFUSED')"
                " AND json_extract(payload_json, '$.attempt_id') = ?"
                " AND json_extract(payload_json, '$.tool') = 'submit_proposal'",
                (run_id, incident_id, started.attempt_id),
            )[0]
        return proposals == 0 and submit_calls == 0

    def _audit_retry(self, started: AttemptStart, result: AttemptResult) -> None:
        with self.store.tx() as tx:
            work = _work(tx, started.work_id)
            _audit(
                tx,
                work,
                Actor.SUPERVISOR,
                "ATTEMPT_RETRY",
                {"attempt_id": started.attempt_id, "detail": result.detail},
            )

    def _record_trace(
        self,
        started: AttemptStart,
        work: Any,
        prompt_sha256: str,
        result: AttemptResult,
        *,
        sandbox: Mapping[str, Any] | None,
        rules: Mapping[str, Any],
        memory: Mapping[str, Any],
    ) -> str | None:
        """attempt trace를 `runs/<run>/traces/<attempt>.json`에 쓰고 상대 경로를 돌려준다."""
        runtime = self.runtime
        assert runtime is not None and started.attempt_id is not None
        with self.store.read() as tx:
            server = trace.server_calls(tx, work["run_id"], work["incident_id"], started.attempt_id)
            started_row = tx.one(
                "SELECT created_at FROM audit_events WHERE run_id = ? AND incident_id = ?"
                " AND event_type = 'ATTEMPT_STARTED' AND json_extract(payload_json,"
                " '$.attempt_id') = ?",
                (work["run_id"], work["incident_id"], started.attempt_id),
            )
        record = trace.build_trace(
            attempt_id=started.attempt_id,
            run_id=work["run_id"],
            incident_id=work["incident_id"],
            agent_mode=self.agent_mode,
            prompt_sha256=prompt_sha256,
            started_at=started_row["created_at"] if started_row is not None else None,
            ended_at=to_rfc3339(self.clock.utc_now()),
            result=result.record(),
            server=server,
            sandbox=sandbox,
            rules=rules,
            memory=memory,
        )
        path = trace.write_trace(runtime.runs_dir, work["run_id"], started.attempt_id, record)
        return path.relative_to(runtime.runs_dir).as_posix()

    def _call_adapter(
        self,
        started: AttemptStart,
        work: Any,
        incident: Any,
        workspace: Path,
        context_ref: Path,
        token: str,
    ) -> AttemptResult:
        """adapter를 별도 thread에서 돌린다. deadline + 유예가 지나면 더 기다리지 않는다."""
        runtime = self.runtime
        assert runtime is not None and started.attempt_id and started.deadline
        adapter = runtime.adapter
        box: dict[str, AttemptResult] = {}

        def target() -> None:
            try:
                box["result"] = adapter.run_agent(
                    work["run_id"],
                    incident["id"],
                    work["id"],
                    started.attempt_id,
                    started.deadline,
                    workspace,
                    context_ref,
                    credential=token,
                )
            except Exception as exc:  # noqa: BLE001 — adapter 오류도 attempt 결과로 남긴다
                box["result"] = AttemptResult(
                    "error", adapter.name, adapter.origin, detail=type(exc).__name__
                )

        thread = threading.Thread(target=target, name=f"attempt-{started.attempt_id}", daemon=True)
        thread.start()
        until = from_rfc3339(started.deadline) + timedelta(seconds=runtime.grace_seconds)
        while thread.is_alive():
            thread.join(timeout=runtime.poll_seconds)
            if thread.is_alive() and self.clock.utc_now() >= until:
                return AttemptResult(
                    "deadline_exceeded", adapter.name, adapter.origin, detail="adapter_timeout"
                )
        return box.get("result") or AttemptResult(
            "error", adapter.name, adapter.origin, detail="no_result"
        )

    def finish_attempt(
        self, started: AttemptStart, result: AttemptResult, *, trace_ref: str | None = None
    ) -> dict[str, Any]:
        """adapter가 끝났다. 제안 없이 조사 중이면 attempt를 닫는다."""
        blocker = None
        with self.store.tx() as tx:
            work = _work(tx, started.work_id)
            incident = _incident(tx, work)
            _audit(
                tx,
                work,
                Actor.SUPERVISOR,
                "ATTEMPT_FINISHED",
                {"attempt_id": started.attempt_id, **result.record(), "trace": trace_ref},
            )
            if _open_attempt(work, incident, started.attempt_id):
                blocker, detail = _attempt_blocker(tx, incident, result)
                attempted = [f"adapter {result.adapter} → {result.status}"] + [
                    f"submit_proposal {pid}" for pid in result.proposal_ids
                ]
                self._close_attempt(tx, work, incident, blocker, detail, attempted)
        return {
            "work_id": started.work_id,
            "attempt_id": started.attempt_id,
            "status": "finished",
            "adapter_status": result.status,
            "blocked": blocker,
        }

    def expire_attempts(self) -> list[str]:
        """제안 없이 조사 중인데 deadline이 지났거나 adapter가 이미 끝난 attempt를 닫는다.

        브로커가 제안을 거절해 조사 단계로 되돌렸지만 adapter가 더 제출하지 않는 경우도
        여기서 닫는다.
        """
        closed = []
        with self.store.tx() as tx:
            for row in tx.all(
                "SELECT w.id AS work_id FROM work_items w JOIN incidents i ON i.id = w.incident_id"
                " WHERE w.status = 'RUNNING' AND i.status = 'INVESTIGATING'"
                " AND i.attempt_id = w.attempt_id AND (? IS NULL OR w.run_id = ?)"
                " ORDER BY w.updated_at",
                (self.run_id, self.run_id),
            ):
                work = _work(tx, row["work_id"])
                incident = _incident(tx, work)
                if incident["attempt_deadline"] and tx.now >= incident["attempt_deadline"]:
                    blocker, detail = (
                        "BUDGET_EXCEEDED",
                        "attempt deadline이 지났고 조사 중 제안이 없다",
                    )
                elif _attempt_finished(tx, work):
                    blocker = "VALIDATION_FAILED"
                    detail = "adapter가 끝난 뒤 제안이 조사 단계로 돌아왔고 더 제출되지 않았다"
                else:
                    continue
                self._close_attempt(
                    tx, work, incident, blocker, detail, [f"attempt {work['attempt_id']}"]
                )
                closed.append(work["id"])
        if self.runtime is not None:
            for work_id in closed:
                with self.store.read() as tx:
                    attempt = _work(tx, work_id)["attempt_id"]
                self.runtime.tokens.revoke_attempt(attempt)
        return closed

    def recover_attempts(self) -> list[str]:
        """재시작 때 조사 중인 attempt를 닫는다.

        이 프로세스에는 그 attempt의 adapter가 없다. 새 세션을 자동으로 만들지 않는다.
        """
        closed = []
        with self.store.tx() as tx:
            for row in tx.all(
                "SELECT w.id AS work_id FROM work_items w JOIN incidents i ON i.id = w.incident_id"
                " WHERE w.status = 'RUNNING' AND i.status = 'INVESTIGATING'"
                " AND i.attempt_id = w.attempt_id AND (? IS NULL OR w.run_id = ?)"
                " ORDER BY w.updated_at",
                (self.run_id, self.run_id),
            ):
                work = _work(tx, row["work_id"])
                incident = _incident(tx, work)
                self._close_attempt(
                    tx,
                    work,
                    incident,
                    "MODEL_UNAVAILABLE",
                    "프로세스가 다시 시작돼 attempt가 끊겼다. 새 세션을 자동으로 만들지 않는다",
                    [f"attempt {work['attempt_id']} → 재시작으로 중단"],
                )
                closed.append(work["id"])
        return closed

    def _close_attempt(
        self,
        tx: Tx,
        work: Any,
        incident: Any,
        blocker: str,
        detail: str,
        attempted: list[str],
    ) -> None:
        """조사 중 attempt를 닫는다: incident ESCALATED·work BLOCKED + WORK_BLOCKED(차단 보고)."""
        result = coupled_transition(
            tx,
            incident_id=incident["id"],
            expected_incident_version=incident["version"],
            incident_to="ESCALATED",
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to="BLOCKED",
            actor=Actor.SUPERVISOR,
            reason=blocker,
            details={"attempt_id": work["attempt_id"], "reason_detail": detail},
        )
        evidence_ids = [
            row["id"]
            for row in tx.all(
                "SELECT id FROM evidence WHERE run_id = ? AND incident_id = ?"
                " ORDER BY observed_at, id LIMIT ?",
                (incident["run_id"], incident["id"], MAX_BLOCKER_EVIDENCE),
            )
        ]
        observed = observed_symptom(json.loads(incident["details_json"] or "{}"))
        report = blocker_report(
            blocker_code=blocker,
            stage="agent",
            incident=incident,
            work=work,
            symptom_impact=observed or f"{incident['service']} 사건(관찰 요약을 만들 수 없음)",
            evidence_ids=evidence_ids,
            owner_route_id=self.route_id,
            observed_at=tx.now,
            attempted_actions=attempted,
            operator_next_step=[
                "attempt 기록·제안·검사 결과를 확인한 뒤 새 generation 승인 여부를 판단"
            ],
            retry_condition="운영자가 원인을 확인하고 새 generation을 승인한 뒤",
            reason_detail=detail,
        )
        assert result.outbox_event == "WORK_BLOCKED"
        outbox.enqueue(tx, work, result.outbox_event, report, self.route_id)

    def _block(
        self, tx: Tx, work: Any, reason: str, symptom: str, blocker_code: str = "SOURCE_CHANGED"
    ) -> None:
        incident = _incident(tx, work)
        result = coupled_transition(
            tx,
            incident_id=incident["id"],
            expected_incident_version=incident["version"],
            incident_to="ESCALATED",
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to="BLOCKED",
            actor=Actor.SUPERVISOR,
            reason=reason,
            details={"work_id": work["id"]},
        )
        report = blocker_report(
            blocker_code=blocker_code,  # Issue 변경은 14종 중 SOURCE_CHANGED
            stage="preflight",
            incident=incident,
            work=work,
            symptom_impact=symptom,
            evidence_ids=[],
            owner_route_id=self.route_id,
            observed_at=tx.now,
            missing_requirements=["바뀐 Issue 내용·담당자·권한에 대한 운영자 확인"],
            operator_next_step=["Issue 변경을 확인한 뒤 retry로 새 generation을 승인할지 결정"],
            retry_condition="운영자가 바뀐 Issue를 확인하고 새 generation을 승인한 뒤",
            reason_detail=f"{reason}: {symptom}",
        )
        assert result.outbox_event == "WORK_BLOCKED"
        outbox.enqueue(tx, work, result.outbox_event, report, self.route_id)


def _open_attempt(work: Any, incident: Any, attempt_id: str | None) -> bool:
    """그 attempt가 아직 제안 없이 조사 중인가(브로커 검사·PR·이관으로 넘어가지 않았다)."""
    return (
        work["status"] == "RUNNING"
        and work["attempt_id"] == attempt_id
        and incident["status"] == "INVESTIGATING"
        and incident["attempt_id"] == attempt_id
    )


def _attempt_blocker(tx: Tx, incident: Any, result: AttemptResult) -> tuple[str, str]:
    """조사 중에 끝난 attempt의 blocker 코드와 사유."""
    deadline = incident["attempt_deadline"]
    if result.status == "deadline_exceeded" or (deadline and tx.now >= deadline):
        return "BUDGET_EXCEEDED", "attempt deadline 안에 통과한 제안이 없다"
    if result.status == "decided" and result.decision == "REJECTED":
        return (
            "VALIDATION_FAILED",
            "제안이 브로커 검사를 통과하지 못했고 adapter가 더 제출하지 않았다",
        )
    if result.status == "no_proposal":
        return "INSUFFICIENT_EVIDENCE", f"adapter가 제안을 내지 않았다({result.detail})"
    if (result.detail or "").startswith("memory:unavailable"):
        return (
            "LOOKUP_INCOMPLETE",
            f"초기 사례 검색을 할 수 없어 시작하지 않았다(run policy stop, {result.detail})",
        )
    return (
        "MODEL_UNAVAILABLE",
        f"에이전트 실행이 결과 없이 끝났다({result.status}: {result.detail})",
    )


def _attempt_finished(tx: Tx, work: Any) -> bool:
    for row in tx.all(
        "SELECT payload_json FROM audit_events WHERE run_id = ? AND incident_id = ?"
        " AND event_type = 'ATTEMPT_FINISHED'",
        (work["run_id"], work["incident_id"]),
    ):
        if json.loads(row["payload_json"] or "{}").get("attempt_id") == work["attempt_id"]:
            return True
    return False
