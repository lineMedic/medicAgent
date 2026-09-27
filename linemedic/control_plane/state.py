"""incident·work 상태 전이 (W06, docs/03 §2·§3, spec 04 §3·§4).

- 전이 허용 표(`INCIDENT_TRANSITIONS`, `WORK_TRANSITIONS`)와
  결합 표(`COUPLED_WORK_STATUS`)를 데이터로 둔다.
  표에 없는 전이와 표에 없는 주체는 `TransitionDenied`다.
- 모든 전이는 `store.cas_update`(기대 version·상태가 맞을 때만)로 version을 1 올리고,
  같은 트랜잭션에서 `audit_events`에 남긴다.
- 주체(`Actor`)는 서버 내부 코드가 정한다. 요청 body의 actor·role·status 문자열로 만들지 않는다.
  `Actor.VERIFIER`는 `control_plane/verifier.py`에서만 쓴다(INV-01). 정적 검사 테스트가 확인한다.

spec 04 §4의 "dispatcher"는 docs/03 표를 따라 `SUPERVISOR`로 부른다.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from linemedic.control_plane import audit
from linemedic.control_plane.store import StateConflict, Tx, cas_update


class Actor(StrEnum):
    ROUTER = "router"
    ISSUE_SYNC = "issue_sync"
    SUPERVISOR = "supervisor"
    NOTIFIER = "notifier"
    BROKER = "broker"
    RELEASE_EXECUTOR = "release_executor"
    OPERATOR = "operator"
    VERIFIER = "verifier"
    RECONCILER = "reconciler"


INCIDENT_STATUSES = (
    "NEW",
    "INVESTIGATING",
    "VALIDATING",
    "PR_OPENED",
    "DEPLOYING",
    "VERIFYING",
    "WORK_ORDER_DRAFTED",
    "RESOLVED",
    "ESCALATED",
    "EXECUTION_UNKNOWN",
)
WORK_STATUSES = (
    "WAITING_APPROVAL",
    "WAITING_NOTIFICATION",
    "READY",
    "RUNNING",
    "WAITING_REVIEW",
    "WAITING_VERIFICATION",
    "HANDED_OFF",
    "SUCCEEDED",
    "BLOCKED",
    "CANCELLED",
    "EXECUTION_UNKNOWN",
)
TERMINAL_WORK_STATUSES = frozenset({"HANDED_OFF", "SUCCEEDED", "BLOCKED", "CANCELLED"})

_A = Actor

# docs/03 §2 Incident 전이 권한. 이 표에 없는 전이는 전부 거부한다.
INCIDENT_TRANSITIONS: Mapping[tuple[str, str], frozenset[Actor]] = MappingProxyType(
    {
        ("NEW", "INVESTIGATING"): frozenset({_A.SUPERVISOR}),
        ("NEW", "ESCALATED"): frozenset({_A.ROUTER, _A.SUPERVISOR}),
        ("NEW", "EXECUTION_UNKNOWN"): frozenset({_A.ROUTER}),
        ("INVESTIGATING", "VALIDATING"): frozenset({_A.BROKER}),
        ("INVESTIGATING", "ESCALATED"): frozenset({_A.SUPERVISOR, _A.BROKER}),
        ("VALIDATING", "INVESTIGATING"): frozenset({_A.BROKER}),
        ("VALIDATING", "PR_OPENED"): frozenset({_A.BROKER}),
        ("VALIDATING", "WORK_ORDER_DRAFTED"): frozenset({_A.BROKER}),
        ("VALIDATING", "ESCALATED"): frozenset({_A.BROKER}),
        ("VALIDATING", "EXECUTION_UNKNOWN"): frozenset({_A.BROKER}),
        ("PR_OPENED", "DEPLOYING"): frozenset({_A.RELEASE_EXECUTOR}),
        ("PR_OPENED", "ESCALATED"): frozenset({_A.OPERATOR}),
        ("DEPLOYING", "VERIFYING"): frozenset({_A.RELEASE_EXECUTOR}),
        ("DEPLOYING", "ESCALATED"): frozenset({_A.RELEASE_EXECUTOR}),
        ("DEPLOYING", "EXECUTION_UNKNOWN"): frozenset({_A.RELEASE_EXECUTOR}),
        ("VERIFYING", "RESOLVED"): frozenset({_A.VERIFIER}),
        ("VERIFYING", "ESCALATED"): frozenset({_A.VERIFIER}),
        ("EXECUTION_UNKNOWN", "NEW"): frozenset({_A.RECONCILER}),
        ("EXECUTION_UNKNOWN", "PR_OPENED"): frozenset({_A.RECONCILER}),
        ("EXECUTION_UNKNOWN", "VERIFYING"): frozenset({_A.RECONCILER}),
        ("EXECUTION_UNKNOWN", "ESCALATED"): frozenset({_A.RECONCILER}),
    }
)

# docs/03 §3 Work 전이. (생성) → WAITING_APPROVAL은 전이가 아니라 work 생성(W24·W25)이다.
WORK_TRANSITIONS: Mapping[tuple[str, str], frozenset[Actor]] = MappingProxyType(
    {
        ("WAITING_APPROVAL", "WAITING_NOTIFICATION"): frozenset({_A.OPERATOR, _A.ROUTER}),
        ("WAITING_APPROVAL", "BLOCKED"): frozenset({_A.ROUTER, _A.OPERATOR}),
        ("WAITING_APPROVAL", "CANCELLED"): frozenset({_A.OPERATOR}),
        ("WAITING_NOTIFICATION", "READY"): frozenset({_A.NOTIFIER, _A.SUPERVISOR}),
        ("WAITING_NOTIFICATION", "BLOCKED"): frozenset({_A.SUPERVISOR}),
        ("WAITING_NOTIFICATION", "CANCELLED"): frozenset({_A.OPERATOR}),
        ("READY", "RUNNING"): frozenset({_A.SUPERVISOR}),
        ("READY", "BLOCKED"): frozenset({_A.SUPERVISOR}),
        ("READY", "CANCELLED"): frozenset({_A.SUPERVISOR}),
        ("RUNNING", "WAITING_REVIEW"): frozenset({_A.BROKER}),
        ("RUNNING", "HANDED_OFF"): frozenset({_A.BROKER}),
        ("RUNNING", "BLOCKED"): frozenset({_A.BROKER, _A.SUPERVISOR}),
        ("RUNNING", "EXECUTION_UNKNOWN"): frozenset({_A.BROKER}),
        ("WAITING_REVIEW", "WAITING_VERIFICATION"): frozenset({_A.RELEASE_EXECUTOR}),
        ("WAITING_REVIEW", "BLOCKED"): frozenset({_A.OPERATOR}),
        ("WAITING_VERIFICATION", "SUCCEEDED"): frozenset({_A.VERIFIER}),
        ("WAITING_VERIFICATION", "BLOCKED"): frozenset({_A.VERIFIER, _A.RELEASE_EXECUTOR}),
        ("WAITING_VERIFICATION", "EXECUTION_UNKNOWN"): frozenset({_A.RELEASE_EXECUTOR}),
        ("EXECUTION_UNKNOWN", "WAITING_REVIEW"): frozenset({_A.RECONCILER}),
        ("EXECUTION_UNKNOWN", "WAITING_VERIFICATION"): frozenset({_A.RECONCILER}),
        ("EXECUTION_UNKNOWN", "BLOCKED"): frozenset({_A.RECONCILER}),
    }
)

# docs/03 §3 결합 전이: incident 도착 상태 → 같은 트랜잭션에서 맞춰야 할 work 도착 상태.
COUPLED_WORK_STATUS: Mapping[str, str] = MappingProxyType(
    {
        "INVESTIGATING": "RUNNING",
        "PR_OPENED": "WAITING_REVIEW",
        "WORK_ORDER_DRAFTED": "HANDED_OFF",
        "DEPLOYING": "WAITING_VERIFICATION",
        "VERIFYING": "WAITING_VERIFICATION",
        "RESOLVED": "SUCCEEDED",
        "ESCALATED": "BLOCKED",
        "EXECUTION_UNKNOWN": "EXECUTION_UNKNOWN",
    }
)

REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

Kind = Literal["incident", "work"]
_TABLES: dict[str, tuple[str, Mapping[tuple[str, str], frozenset[Actor]], tuple[str, ...]]] = {
    "incident": ("incidents", INCIDENT_TRANSITIONS, INCIDENT_STATUSES),
    "work": ("work_items", WORK_TRANSITIONS, WORK_STATUSES),
}


class TransitionDenied(RuntimeError):
    """허용 표에 없는 전이이거나 그 주체에게 허용되지 않은 전이. API에서는 409 `STATE_CONFLICT`."""

    def __init__(self, kind: str, from_status: str | None, to_status: str, actor: str, why: str):
        super().__init__(f"{kind} {from_status} → {to_status} ({actor}): {why}")
        self.kind = kind
        self.from_status = from_status
        self.to_status = to_status
        self.actor = actor


def allowed_actors(kind: Kind, from_status: str, to_status: str) -> frozenset[Actor]:
    return _TABLES[kind][1].get((from_status, to_status), frozenset())


def check_transition(kind: Kind, from_status: str, to_status: str, actor: Actor) -> None:
    if not isinstance(actor, Actor):
        raise TypeError("actor는 state.Actor여야 한다(요청 문자열로 만들지 않는다)")
    statuses = _TABLES[kind][2]
    if to_status not in statuses:
        raise TransitionDenied(kind, from_status, to_status, actor, "알 수 없는 상태")
    actors = allowed_actors(kind, from_status, to_status)
    if not actors:
        raise TransitionDenied(kind, from_status, to_status, actor, "허용 표에 없는 전이")
    if actor not in actors:
        raise TransitionDenied(
            kind, from_status, to_status, actor, "이 주체에게 허용되지 않은 전이"
        )


def _check_reason(reason: str | None) -> None:
    if reason is not None and not REASON_CODE_RE.fullmatch(reason):
        raise ValueError(f"reason 코드 형식이 아니다: {reason!r}")


def _transition(
    tx: Tx,
    kind: Kind,
    row_id: str,
    expected_version: int,
    to: str,
    actor: Actor,
    reason: str | None,
    details: Mapping[str, Any] | None,
    fields: dict[str, Any],
) -> int:
    table = _TABLES[kind][0]
    incident_column = "id" if kind == "incident" else "incident_id"
    row = tx.one(
        f"SELECT run_id, {incident_column} AS incident_id, status, version"
        f" FROM {table} WHERE id = ?",
        (row_id,),
    )
    if row is None:
        raise StateConflict(table, row_id, expected_version, None, None, None)
    if row["version"] != expected_version:
        raise StateConflict(table, row_id, expected_version, None, row["status"], row["version"])
    check_transition(kind, row["status"], to, actor)
    _check_reason(reason)
    if reason is not None:
        fields["reason_code"] = reason
    if kind == "work":
        fields["updated_at"] = tx.now
    new_version = cas_update(tx, table, row_id, expected_version, row["status"], to, **fields)
    payload: dict[str, Any] = {
        f"{kind}_id": row_id,
        "from": row["status"],
        "to": to,
        "version": new_version,
        "reason_code": reason,
    }
    if details:
        payload["details"] = dict(details)
    audit.append(
        tx, row["run_id"], row["incident_id"], actor, f"{kind.upper()}_TRANSITION", payload
    )
    return new_version


def transition_incident(
    tx: Tx,
    incident_id: str,
    expected_version: int,
    to: str,
    actor: Actor,
    reason: str | None = None,
    *,
    details: Mapping[str, Any] | None = None,
    **fields: Any,
) -> int:
    """incident 하나를 전이한다. 새 version을 돌려준다."""
    return _transition(
        tx, "incident", incident_id, expected_version, to, actor, reason, details, dict(fields)
    )


def transition_work(
    tx: Tx,
    work_id: str,
    expected_version: int,
    to: str,
    actor: Actor,
    reason: str | None = None,
    *,
    details: Mapping[str, Any] | None = None,
    **fields: Any,
) -> int:
    """work 하나를 전이한다. `updated_at`은 트랜잭션 시각으로 바꾼다. 새 version을 돌려준다."""
    return _transition(
        tx, "work", work_id, expected_version, to, actor, reason, details, dict(fields)
    )


@dataclass(frozen=True)
class CoupledResult:
    incident_version: int
    work_version: int
    outbox_event: str | None  # 같은 트랜잭션에서 넣어야 할 알림 event_type


def coupled_outbox_event(work_from: str, work_to: str) -> str | None:
    """docs/03 §3 결합 표의 outbox event. 검증 대기 중 차단은 복구 미검증 알림이다."""
    if work_to == "BLOCKED":
        return "RECOVERY_NOT_VERIFIED" if work_from == "WAITING_VERIFICATION" else "WORK_BLOCKED"
    return {
        "WAITING_REVIEW": "PR_READY",
        "HANDED_OFF": "HANDOFF_DRAFTED",
        "SUCCEEDED": "RECOVERY_VERIFIED",
    }.get(work_to)


def coupled_transition(
    tx: Tx,
    *,
    incident_id: str,
    expected_incident_version: int,
    incident_to: str,
    work_id: str,
    expected_work_version: int,
    work_to: str,
    actor: Actor,
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> CoupledResult:
    """incident와 그 work를 같은 트랜잭션에서 결합 표대로 함께 전이한다.

    호출자는 돌려받은 `outbox_event`가 있으면 같은 트랜잭션에서 outbox에 넣는다.
    """
    if COUPLED_WORK_STATUS.get(incident_to) != work_to:
        raise TransitionDenied(
            "coupled", None, f"{incident_to}+{work_to}", actor, "결합 표에 없는 조합"
        )
    incident = tx.one("SELECT run_id FROM incidents WHERE id = ?", (incident_id,))
    work = tx.one("SELECT run_id, incident_id, status FROM work_items WHERE id = ?", (work_id,))
    if incident is None or work is None:
        raise StateConflict("work_items", work_id, expected_work_version, None, None, None)
    if (work["run_id"], work["incident_id"]) != (incident["run_id"], incident_id):
        raise TransitionDenied(
            "coupled", work["status"], work_to, actor, "work가 이 incident에 속하지 않음"
        )
    incident_version = transition_incident(
        tx, incident_id, expected_incident_version, incident_to, actor, reason, details=details
    )
    work_version = transition_work(
        tx, work_id, expected_work_version, work_to, actor, reason, details=details
    )
    return CoupledResult(
        incident_version, work_version, coupled_outbox_event(work["status"], work_to)
    )
