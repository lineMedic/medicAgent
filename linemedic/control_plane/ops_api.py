"""운영 API `/ops/*` (W06: 사건 조회·중단 기록. 다른 endpoint는 카드별로 추가).

- GET `/ops/incidents/{id}`: 사건과 work·감사·proposal·execution·verification 연결 (역할 `read`)
- POST `/ops/incidents/{id}/escalate`: 운영자 중단 기록 (역할 `operate`). 허용 표상 operator는
  `PR_OPENED → ESCALATED`만 할 수 있고, work가 있으면 `WAITING_REVIEW → BLOCKED`와 `WORK_BLOCKED`
  알림 intent를 같은 트랜잭션에서 기록한다. `RESOLVED` 전이는 없다.

force-resolve·임의 상태 PATCH는 만들지 않는다.
"""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from linemedic.control_plane import idempotency
from linemedic.control_plane.app import (
    AppContext,
    context,
    idempotency_key,
    read_json_body,
    request_id,
    require_operator_role,
)
from linemedic.control_plane.auth import OperatorPrincipal, load_visible_incident
from linemedic.control_plane.errors import ApiError, success_body
from linemedic.control_plane.idempotency import Outcome
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.state import Actor, coupled_transition, transition_incident

router = APIRouter()

# docs/03 §5 blocker_code 14개
BlockerCode = Literal[
    "UNSUPPORTED_ACTION",
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_REQUIREMENTS",
    "PERMISSION_REQUIRED",
    "HUMAN_WORK_IN_PROGRESS",
    "LOOKUP_INCOMPLETE",
    "START_NOTICE_UNCONFIRMED",
    "MODEL_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "VALIDATION_FAILED",
    "SOURCE_CHANGED",
    "EXTERNAL_RESULT_UNKNOWN",
    "VERIFICATION_FAILED",
    "OBSERVATION_INCONCLUSIVE",
]
RUN_ID_PATTERN = r"^r-\d{8}-\d{6}-[0-9a-f]{4}$"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EscalateRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    run_id: Annotated[str, Field(pattern=RUN_ID_PATTERN)]
    expected_incident_version: Annotated[int, Field(ge=0)]
    reason_code: BlockerCode
    note: Annotated[str, Field(min_length=1, max_length=2000)]


def _json(value: str | None) -> Any:
    return json.loads(value) if value else None


def _incident_view(ctx: AppContext, principal: OperatorPrincipal, incident_id: str) -> dict:
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, principal, incident_id)
        key = (incident["run_id"], incident_id)
        work = tx.one("SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?", key)
        audit_rows = tx.all(
            "SELECT seq, actor, event_type, created_at, payload_json FROM audit_events"
            " WHERE run_id = ? AND incident_id = ? ORDER BY seq",
            key,
        )
        proposals = tx.all(
            "SELECT id, work_id, attempt_id, decision, received_at FROM proposals"
            " WHERE run_id = ? AND incident_id = ? ORDER BY received_at, id",
            key,
        )
        executions = tx.all(
            "SELECT id, work_id, proposal_id, operation, status, stage, intended_at, updated_at"
            " FROM executions WHERE run_id = ? AND incident_id = ? ORDER BY intended_at, id",
            key,
        )
        verifications = tx.all(
            "SELECT id, execution_id, origin, verdict, contract_id, contract_sha256, started_at,"
            " ended_at FROM verifications WHERE run_id = ? AND incident_id = ?"
            " ORDER BY started_at, id",
            key,
        )
    incident_data = {k: incident[k] for k in incident.keys() if k != "details_json"}
    incident_data["details"] = _json(incident["details_json"])
    work_data = None
    if work is not None:
        work_data = {
            k: work[k] for k in work.keys() if k not in ("details_json", "authorization_json")
        }
        work_data["details"] = _json(work["details_json"])
        work_data["authorization"] = _json(work["authorization_json"])
    return {
        "incident": incident_data,
        "work": work_data,
        "audit_events": [
            {
                "seq": row["seq"],
                "actor": row["actor"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "payload": _json(row["payload_json"]),
            }
            for row in audit_rows
        ],
        "proposals": [dict(row) for row in proposals],
        "executions": [dict(row) for row in executions],
        "verifications": [dict(row) for row in verifications],
    }


@router.get("/ops/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("read"))],
) -> dict:
    data = await run_in_threadpool(_incident_view, context(request), operator, incident_id)
    return success_body(request_id(request), data)


def _escalate(
    ctx: AppContext,
    operator: OperatorPrincipal,
    incident_id: str,
    path: str,
    key: str,
    body: EscalateRequest,
    rid: str,
) -> tuple[int, dict]:
    body_hash = idempotency.body_sha256(body.model_dump(mode="json"))
    with ctx.store.tx() as tx:
        incident = load_visible_incident(tx, operator, incident_id)
        if incident["run_id"] != body.run_id:
            raise ApiError("RESOURCE_NOT_FOUND")
        scope = {
            "principal_scope": operator.scope,
            "method": "POST",
            "path": path,
            "run_id": body.run_id,
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

        details = {"requested_by": operator.scope, "note": body.note}
        work = tx.one(
            "SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?",
            (incident["run_id"], incident_id),
        )
        work_data = notification = None
        if work is None:
            incident_version = transition_incident(
                tx,
                incident_id,
                body.expected_incident_version,
                "ESCALATED",
                Actor.OPERATOR,
                body.reason_code,
                details=details,
            )
        else:
            result = coupled_transition(
                tx,
                incident_id=incident_id,
                expected_incident_version=body.expected_incident_version,
                incident_to="ESCALATED",
                work_id=work["id"],
                expected_work_version=work["version"],
                work_to="BLOCKED",
                actor=Actor.OPERATOR,
                reason=body.reason_code,
                details=details,
            )
            incident_version = result.incident_version
            work_data = {"work_id": work["id"], "status": "BLOCKED", "version": result.work_version}
            assert result.outbox_event is not None
            payload = {
                "schema_version": "linemedic.v4",
                "event_type": result.outbox_event,
                "run_id": incident["run_id"],
                "incident_id": incident_id,
                "work_id": work["id"],
                "generation": work["generation"],
                "attempt_id": work["attempt_id"],
                "repository_id": work["repository_id"],
                "issue_number": work["issue_number"],
                "blocker_code": body.reason_code,
                "incident_status_before": incident["status"],
                "work_status_before": work["status"],
                "observed_at": tx.now,
                "operator_note": body.note,
                "requested_by": operator.scope,
            }
            notification_id = outbox.enqueue(
                tx, work, result.outbox_event, payload, ctx.notification_route_id
            )
            notification = {
                "notification_id": notification_id,
                "event_type": result.outbox_event,
                "status": "PENDING",
            }
        data = {
            "incident_id": incident_id,
            "status": "ESCALATED",
            "version": incident_version,
            "work": work_data,
            "notification": notification,
        }
        response = success_body(rid, data)
        idempotency.complete(tx, **scope, status_code=200, body=response)
    return 200, response


@router.post("/ops/incidents/{incident_id}/escalate")
async def escalate_incident(
    incident_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("operate"))],
) -> JSONResponse:
    key = idempotency_key(request)
    body = await read_json_body(request, EscalateRequest)
    status_code, payload = await run_in_threadpool(
        _escalate,
        context(request),
        operator,
        incident_id,
        request.url.path,
        key,
        body,
        request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload)
