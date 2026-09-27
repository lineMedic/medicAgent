"""알림 outbox 기록 (W06: 같은 트랜잭션의 intent 기록. worker·adapter·재시도·reconcile은 W26).

`enqueue`는 상태 전이와 같은 트랜잭션에서 PENDING 알림 한 건을 남긴다. 발송하지 않는다.
logical key는 서버가 만든다(docs/03 §8, 클라이언트가 지정하지 않음).
같은 key·같은 payload → 기존 알림 ID, 같은 key·다른 payload → `OutboxConflict`.
"""

import re
from collections.abc import Mapping
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.ids import new_id
from linemedic.common.sanitize import redact_json
from linemedic.control_plane.store import Tx

EVENT_TYPES = frozenset(
    {
        "WORK_STARTING",
        "WORK_BLOCKED",
        "PR_READY",
        "HANDOFF_DRAFTED",
        "RECOVERY_VERIFIED",
        "RECOVERY_NOT_VERIFIED",
        "WORK_CANCELLED",
    }
)
ROUTE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class OutboxConflict(RuntimeError):
    """같은 logical key에 다른 payload가 이미 있다."""


def logical_key(
    *,
    run_id: str,
    incident_id: str,
    work: Mapping[str, Any] | None,
    event_type: str,
    event_revision: int,
    route_id: str,
) -> str:
    if work is not None:
        return f"notify:{work['id']}:{work['generation']}:{event_type}:{event_revision}:{route_id}"
    return f"notify:intake:{run_id}:{incident_id}:{event_type}:{event_revision}:{route_id}"


def enqueue(
    tx: Tx,
    work: Mapping[str, Any] | None,
    event_type: str,
    payload: Mapping[str, Any],
    route_id: str,
    event_revision: int = 1,
    *,
    run_id: str | None = None,
    incident_id: str | None = None,
) -> str:
    """PENDING 알림을 넣고 알림 ID를 돌려준다. work가 없으면 run_id·incident_id가 필요하다."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"알 수 없는 알림 event_type: {event_type}")
    if not ROUTE_ID_RE.fullmatch(route_id):
        raise ValueError(f"route_id 형식이 아니다: {route_id!r}")
    if event_revision < 1:
        raise ValueError("event_revision은 1 이상이다")
    if work is not None:
        run_id, incident_id = work["run_id"], work["incident_id"]
    elif event_type == "WORK_STARTING":
        raise ValueError("work 없는 WORK_STARTING은 만들 수 없다(spec 04 §6)")
    if not run_id or not incident_id:
        raise ValueError("work가 없으면 run_id·incident_id가 필요하다")

    key = logical_key(
        run_id=run_id,
        incident_id=incident_id,
        work=work,
        event_type=event_type,
        event_revision=event_revision,
        route_id=route_id,
    )
    clean = redact_json(dict(payload))
    payload_sha256 = sha256_hex(clean)
    existing = tx.one("SELECT id, payload_sha256 FROM notifications WHERE logical_key = ?", (key,))
    if existing is not None:
        if existing["payload_sha256"] != payload_sha256:
            raise OutboxConflict(f"같은 알림 key에 다른 payload: {key}")
        return str(existing["id"])
    notification_id = new_id("NOT")
    tx.execute(
        "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
        " logical_key, payload_sha256, status, attempt_count, created_at, updated_at,"
        " payload_json, result_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, '{}')",
        (
            notification_id,
            run_id,
            incident_id,
            work["id"] if work is not None else None,
            event_type,
            route_id,
            key,
            payload_sha256,
            tx.now,
            tx.now,
            canonical_dumps(clean),
        ),
    )
    return notification_id
