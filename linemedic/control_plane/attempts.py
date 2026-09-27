"""attempt 기록 조회 (W13, docs/05 ④, D83).

attempt는 supervisor `start_attempt`만 만든다(INV-12). 시작 감사(`ATTEMPT_STARTED`)에 adapter와
origin을 남기고, 봇 PR 본문(W11)과 배포 검증(W12)이 그 origin을 읽는다. 사람이 미리 쓴 제안
(`manual_integration`)을 에이전트 산출물처럼 표시하지 않고, 에이전트 성과 집계에서 빼기 위해서다.
adapter 없이 만든 attempt(origin 기록 없음)는 기본값 `agent_release`로 본다.
"""

import json
from typing import Any

from linemedic.control_plane.store import Tx

ORIGIN_AGENT = "agent_release"
ORIGIN_MANUAL = "manual_integration"
ATTEMPT_ORIGINS = frozenset({ORIGIN_AGENT, ORIGIN_MANUAL})


def attempt_started(
    tx: Tx, run_id: str, incident_id: str, attempt_id: str
) -> dict[str, Any] | None:
    """그 attempt의 `ATTEMPT_STARTED` 감사 payload(없으면 None)."""
    for row in tx.all(
        "SELECT payload_json FROM audit_events WHERE run_id = ? AND incident_id = ?"
        " AND event_type = 'ATTEMPT_STARTED' ORDER BY seq DESC",
        (run_id, incident_id),
    ):
        payload = json.loads(row["payload_json"] or "{}")
        if isinstance(payload, dict) and payload.get("attempt_id") == attempt_id:
            return payload
    return None


def attempt_origin(
    tx: Tx, run_id: str, incident_id: str, attempt_id: str, default: str = ORIGIN_AGENT
) -> str:
    origin = (attempt_started(tx, run_id, incident_id, attempt_id) or {}).get("origin")
    return origin if origin in ATTEMPT_ORIGINS else default
