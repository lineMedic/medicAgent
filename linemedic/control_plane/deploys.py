"""배포 관찰 기록 (W07, D59).

배포 기록 테이블이 DDL에 없으므로 `audit_events(event_type='DEPLOY_OBSERVED')`에 남긴다.
`get_deploys`는 이것과 DEPLOY execution(W12)을 함께 읽는다.
"""

import json
from typing import Any

from linemedic.control_plane import audit
from linemedic.control_plane.store import Tx

DEPLOY_EVENT = "DEPLOY_OBSERVED"


def record_deploy_observed(
    tx: Tx,
    *,
    run_id: str,
    service: str,
    base_sha: str | None,
    image_id: str,
    container: str,
    container_id: str | None,
    actor: str,
) -> int:
    """관찰한 배포 한 건을 감사 기록으로 남긴다. base SHA를 모르면 null로 둔다(추정하지 않음)."""
    return audit.append(
        tx,
        run_id,
        None,
        actor,
        DEPLOY_EVENT,
        {
            "service": service,
            "base_sha": base_sha,
            "image_id": image_id,
            "container": container,
            "container_id": container_id,
        },
    )


def _loads(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value) if value else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def deploy_records(tx: Tx, run_id: str, service: str, since: str) -> list[dict[str, Any]]:
    """run·service의 배포 기록을 시각 순서로 돌려준다(`since` 이후)."""
    records = []
    for row in tx.all(
        "SELECT created_at, payload_json FROM audit_events"
        " WHERE run_id = ? AND event_type = ? AND created_at >= ? ORDER BY seq",
        (run_id, DEPLOY_EVENT, since),
    ):
        payload = _loads(row["payload_json"])
        if payload.get("service") != service:
            continue
        records.append(
            {
                "observed_at": row["created_at"],
                "source": "deploy_observed",
                "base_sha": payload.get("base_sha"),
                "image_id": payload.get("image_id"),
                "container": payload.get("container"),
            }
        )
    for row in tx.all(
        "SELECT id, updated_at, request_json, result_json FROM executions"
        " WHERE run_id = ? AND operation = 'DEPLOY' AND status = 'SUCCEEDED' AND updated_at >= ?"
        " ORDER BY updated_at, id",
        (run_id, since),
    ):
        request, result = _loads(row["request_json"]), _loads(row["result_json"])
        if request.get("service", service) != service:
            continue
        records.append(
            {
                "observed_at": row["updated_at"],
                "source": "execution",
                "execution_id": row["id"],
                "base_sha": request.get("approved_merge_sha"),
                "image_id": result.get("image_id"),
            }
        )
    return sorted(records, key=lambda record: record["observed_at"])
