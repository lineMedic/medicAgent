"""감사 기록 (W06, spec 04 §7·§9). JSONL export는 W19.

`append`는 호출자의 트랜잭션 안에서 `audit_events`에 한 줄을 넣는다.
상태 변경과 감사 기록이 같이 커밋되거나 같이 취소된다. payload 문자열의 비밀 형태는 가려서 저장한다.
"""

import re
from collections.abc import Mapping
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.sanitize import redact_json
from linemedic.control_plane.store import Tx

EVENT_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def append(
    tx: Tx,
    run_id: str,
    incident_id: str | None,
    actor: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> int:
    """감사 이벤트를 넣고 seq를 돌려준다."""
    if not EVENT_TYPE_RE.fullmatch(event_type):
        raise ValueError(f"event_type 형식이 아니다: {event_type!r}")
    if not actor:
        raise ValueError("actor가 비어 있다")
    cursor = tx.execute(
        "INSERT INTO audit_events(run_id, incident_id, actor, event_type, created_at, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            run_id,
            incident_id,
            str(actor),
            event_type,
            tx.now,
            canonical_dumps(redact_json(dict(payload))),
        ),
    )
    return int(cursor.lastrowid)
