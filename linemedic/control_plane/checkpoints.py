"""외부 입력 처리 위치(checkpoint) 저장 (W07: detect-once, W23: Issue polling이 다시 쓴다).

`integration_state(integration_id, state_key)` 한 행에 마지막으로 처리한 위치를 둔다.
처리 결과와 checkpoint는 호출자가 정한 트랜잭션 경계로 기록한다.
"""

import json
from collections.abc import Mapping
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.control_plane.store import Tx


def get(tx: Tx, integration_id: str, state_key: str) -> dict[str, Any] | None:
    row = tx.one(
        "SELECT payload_json FROM integration_state WHERE integration_id = ? AND state_key = ?",
        (integration_id, state_key),
    )
    return json.loads(row["payload_json"]) if row is not None else None


def put(tx: Tx, integration_id: str, state_key: str, payload: Mapping[str, Any]) -> None:
    tx.execute(
        "INSERT INTO integration_state(integration_id, state_key, version, updated_at,"
        " payload_json) VALUES (?, ?, 0, ?, ?) ON CONFLICT(integration_id, state_key) DO UPDATE SET"
        " version = version + 1, updated_at = excluded.updated_at,"
        " payload_json = excluded.payload_json",
        (integration_id, state_key, tx.now, canonical_dumps(dict(payload))),
    )
