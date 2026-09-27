"""운영 API `/ops/*` (W06: 사건 조회·중단 기록. 다른 endpoint는 카드별로 추가).

- GET `/ops/incidents/{id}`: 사건과 work·감사·proposal·execution·verification 연결 (역할 `read`)
- POST `/ops/incidents/{id}/escalate`: 운영자 중단 기록 (역할 `operate`). 허용 표상 operator는
  `PR_OPENED → ESCALATED`만 할 수 있고, work가 있으면 `WAITING_REVIEW → BLOCKED`와 `WORK_BLOCKED`
  알림 intent를 같은 트랜잭션에서 기록한다. `RESOLVED` 전이는 없다.
- POST `/ops/integrations/github/sync`: 등록 repo Issue 조회 1회 (역할 `integration`, W23).
  repo·URL은 지정할 수 없다
- GET `/ops/issues/candidates/{incident_id}`: binding 후보·match 근거·조회 완전성 (역할 `read`, W24)
- POST `/ops/incidents/{id}/issue-binding`: 등록 repo의 Issue 번호를 명시적으로 연결
  (역할 `triage`, basis OPERATOR, W24)
- GET `/ops/work-items/{id}`: work·현재 Issue snapshot·시작 알림 (역할 `read`, W25)
- POST `/ops/work-items/{id}/approve`·`/retry`(역할 `authorize`), `/cancel`(역할 `operate`) (W25).
  version CAS·멱등 키를 거치고, 상태 변경과 멱등 기록을 한 트랜잭션에 쓴다
- GET `/ops/notifications`: outbox·receipt·실패 상태 (역할 `read`, W26). 수신 주소를 보이지 않는다
- POST `/ops/notifications/{id}/reconcile`: UNKNOWN 알림을 외부 조회로만 조정
  (역할 `reconcile`, W26). 다시 보내지 않는다

force-resolve·임의 상태 PATCH는 만들지 않는다.
"""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from linemedic.common.ids import is_valid_entity_id
from linemedic.control_plane import idempotency, supervisor
from linemedic.control_plane.app import (
    AppContext,
    context,
    idempotency_key,
    read_json_body,
    reject_unknown_query,
    request_id,
    require_operator_role,
)
from linemedic.control_plane.auth import OperatorPrincipal, load_visible_incident
from linemedic.control_plane.codes import RUN_ID_PATTERN, BlockerCode
from linemedic.control_plane.errors import ApiError, error_body, success_body
from linemedic.control_plane.idempotency import Outcome
from linemedic.control_plane.notifications import outbox, templates
from linemedic.control_plane.state import Actor, coupled_transition, transition_incident

router = APIRouter()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GitHubSyncRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    run_id: Annotated[str, Field(pattern=RUN_ID_PATTERN)]


class IssueBindingRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    run_id: Annotated[str, Field(pattern=RUN_ID_PATTERN)]
    issue_number: Annotated[int, Field(ge=1)]
    expected_incident_version: Annotated[int, Field(ge=0)]
    decision_note: Annotated[str, Field(min_length=1, max_length=2000)]


class ApproveRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    expected_work_version: Annotated[int, Field(ge=0)]
    expected_issue_snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    approval_note: Annotated[str, Field(min_length=1, max_length=2000)]


class RetryRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    expected_work_version: Annotated[int, Field(ge=0)]
    blocker_resolution_note: Annotated[str, Field(min_length=1, max_length=2000)]


class CancelRequest(_Body):
    schema_version: Literal["linemedic.v4"]
    expected_work_version: Annotated[int, Field(ge=0)]
    cancel_note: Annotated[str, Field(min_length=1, max_length=2000)]


class ReconcileRequest(_Body):
    schema_version: Literal["linemedic.v4"]


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


GITHUB_SYNC_PATH = "/ops/integrations/github/sync"


def _github_sync(
    ctx: AppContext, operator: OperatorPrincipal, key: str, body: GitHubSyncRequest, rid: str
) -> tuple[int, dict]:
    """등록 repo Issue 조회 1회(W23). repo·URL은 요청으로 정하지 않는다.

    외부 조회는 트랜잭션 밖에서 한다.
    """
    sync = ctx.issue_sync
    if sync is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    if body.run_id != sync.run_id:
        raise ApiError("STATE_CONFLICT", {"reason": "run_mismatch"})
    scope = {
        "principal_scope": operator.scope,
        "method": "POST",
        "path": GITHUB_SYNC_PATH,
        "run_id": body.run_id,
        "key": key,
    }
    with ctx.store.tx() as tx:
        started = idempotency.begin(
            tx, **scope, body_sha256=idempotency.body_sha256(body.model_dump(mode="json"))
        )
    if started.outcome is Outcome.REPLAY:
        assert started.response is not None
        return started.response["status_code"], started.response["body"]
    if started.outcome is Outcome.CONFLICT:
        raise ApiError("IDEMPOTENCY_CONFLICT")
    if started.outcome is Outcome.IN_FLIGHT:
        raise ApiError("STATE_CONFLICT", {"reason": "request_in_progress_or_unknown"})
    result = sync.poll_once()
    if result.mode == "busy":  # 아무것도 하지 않았다: 같은 키로 다시 시도할 수 있게 지운다
        with ctx.store.tx() as tx:
            idempotency.abandon(tx, **scope)
        raise ApiError("STATE_CONFLICT", {"reason": "sync_in_progress"})
    if result.mode == "backoff" or result.error == "RateLimited":
        status, response = 429, error_body(rid, "RATE_LIMITED", {"retry_after": result.retry_after})
    elif result.error:
        details = {"error": result.error, "pages": result.pages, "mode": result.mode}
        status, response = 503, error_body(rid, "LOOKUP_INCOMPLETE", details)
    else:
        status, response = 200, success_body(rid, result.as_dict())
    with ctx.store.tx() as tx:
        idempotency.complete(tx, **scope, status_code=status, body=response)
    return status, response


@router.post(GITHUB_SYNC_PATH)
async def github_sync(
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("integration"))],
) -> JSONResponse:
    key = idempotency_key(request)
    body = await read_json_body(request, GitHubSyncRequest)
    status_code, payload = await run_in_threadpool(
        _github_sync, context(request), operator, key, body, request_id(request)
    )
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/ops/issues/candidates/{incident_id}")
async def issue_candidates(
    incident_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("read"))],
) -> JSONResponse:
    ctx = context(request)
    if ctx.issue_router is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    router_ = ctx.issue_router

    def load() -> dict:
        with ctx.store.read() as tx:
            load_visible_incident(tx, operator, incident_id)  # 형식·범위 밖은 404
        return router_.candidates(incident_id)

    data = await run_in_threadpool(load)
    return JSONResponse(content=success_body(request_id(request), data))


def _issue_binding(
    ctx: AppContext,
    operator: OperatorPrincipal,
    incident_id: str,
    path: str,
    key: str,
    body: IssueBindingRequest,
    rid: str,
) -> tuple[int, dict]:
    """운영자 연결. GitHub 재조회는 트랜잭션 밖에서 하고, 거절되면 멱등 기록을 지운다."""
    if ctx.issue_router is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    with ctx.store.read() as tx:
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
    with ctx.store.tx() as tx:
        started = idempotency.begin(
            tx, **scope, body_sha256=idempotency.body_sha256(body.model_dump(mode="json"))
        )
    if started.outcome is Outcome.REPLAY:
        assert started.response is not None
        return started.response["status_code"], started.response["body"]
    if started.outcome is Outcome.CONFLICT:
        raise ApiError("IDEMPOTENCY_CONFLICT")
    if started.outcome is Outcome.IN_FLIGHT:
        raise ApiError("STATE_CONFLICT", {"reason": "request_in_progress_or_unknown"})
    try:
        data = ctx.issue_router.operator_bind(
            incident_id,
            body.issue_number,
            body.expected_incident_version,
            body.decision_note,
            operator.scope,
        )
    except ApiError:
        # 연결이 기록되지 않았다: 같은 키로 다시 시도할 수 있게 멱등 기록을 지운다
        with ctx.store.tx() as tx:
            idempotency.abandon(tx, **scope)
        raise
    response = success_body(rid, data)
    with ctx.store.tx() as tx:
        idempotency.complete(tx, **scope, status_code=200, body=response)
    return 200, response


@router.post("/ops/incidents/{incident_id}/issue-binding")
async def issue_binding(
    incident_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("triage"))],
) -> JSONResponse:
    key = idempotency_key(request)
    body = await read_json_body(request, IssueBindingRequest)
    status_code, payload = await run_in_threadpool(
        _issue_binding,
        context(request),
        operator,
        incident_id,
        request.url.path,
        key,
        body,
        request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload)


# ── work (W25) ────────────────────────────────────────────────


def _visible_work(tx: Any, operator: OperatorPrincipal, work_id: str) -> Any:
    """형식 오류·없음·범위 밖은 모두 같은 404다."""
    if not is_valid_entity_id(work_id, "WORK"):
        raise ApiError("RESOURCE_NOT_FOUND")
    work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
    if work is None:
        raise ApiError("RESOURCE_NOT_FOUND")
    load_visible_incident(tx, operator, work["incident_id"])
    return work


@router.get("/ops/work-items/{work_id}")
async def get_work_item(
    work_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("read"))],
) -> JSONResponse:
    ctx = context(request)

    def load() -> dict:
        with ctx.store.read() as tx:
            work = _visible_work(tx, operator, work_id)
            issue = tx.one(
                "SELECT state, snapshot_sha256, updated_at FROM github_issues"
                " WHERE repository_id = ? AND issue_number = ?",
                (work["repository_id"], work["issue_number"]),
            )
            notice = None
            if work["start_notification_id"]:
                notice = tx.one(
                    "SELECT id, status, receipt_id FROM notifications WHERE id = ?",
                    (work["start_notification_id"],),
                )
        data = {k: work[k] for k in work.keys() if k not in ("details_json", "authorization_json")}
        data["details"] = _json(work["details_json"])
        data["authorization"] = _json(work["authorization_json"])
        data["issue"] = dict(issue) if issue is not None else None
        data["start_notification"] = dict(notice) if notice is not None else None
        return data

    data = await run_in_threadpool(load)
    return JSONResponse(content=success_body(request_id(request), data))


def _work_command(
    ctx: AppContext,
    operator: OperatorPrincipal,
    work_id: str,
    path: str,
    key: str,
    body: _Body,
    rid: str,
    action: Any,
) -> tuple[int, dict]:
    """version CAS·멱등 키. 상태 변경과 멱등 기록을 한 트랜잭션에 쓴다(거절이면 둘 다 취소)."""
    with ctx.store.tx() as tx:
        work = _visible_work(tx, operator, work_id)
        scope = {
            "principal_scope": operator.scope,
            "method": "POST",
            "path": path,
            "run_id": work["run_id"],
            "key": key,
        }
        started = idempotency.begin(
            tx, **scope, body_sha256=idempotency.body_sha256(body.model_dump(mode="json"))
        )
        if started.outcome is Outcome.REPLAY:
            assert started.response is not None
            return started.response["status_code"], started.response["body"]
        if started.outcome is Outcome.CONFLICT:
            raise ApiError("IDEMPOTENCY_CONFLICT")
        if started.outcome is Outcome.IN_FLIGHT:
            raise ApiError("STATE_CONFLICT", {"reason": "request_in_progress_or_unknown"})
        response = success_body(rid, action(tx))
        idempotency.complete(tx, **scope, status_code=200, body=response)
    return 200, response


@router.post("/ops/work-items/{work_id}/approve")
async def approve_work(
    work_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("authorize"))],
) -> JSONResponse:
    ctx = context(request)
    key = idempotency_key(request)
    body = await read_json_body(request, ApproveRequest)

    def action(tx: Any) -> dict:
        return supervisor.approve(
            tx,
            work_id,
            body.expected_work_version,
            body.expected_issue_snapshot_sha256,
            principal=operator.scope,
            note=body.approval_note,
            route_id=ctx.notification_route_id,
        )

    status_code, payload = await run_in_threadpool(
        _work_command,
        ctx,
        operator,
        work_id,
        request.url.path,
        key,
        body,
        request_id(request),
        action,
    )
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/ops/work-items/{work_id}/retry")
async def retry_work(
    work_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("authorize"))],
) -> JSONResponse:
    ctx = context(request)
    key = idempotency_key(request)
    body = await read_json_body(request, RetryRequest)

    def action(tx: Any) -> dict:
        return supervisor.retry(
            tx,
            work_id,
            body.expected_work_version,
            body.blocker_resolution_note,
            principal=operator.scope,
        )

    status_code, payload = await run_in_threadpool(
        _work_command,
        ctx,
        operator,
        work_id,
        request.url.path,
        key,
        body,
        request_id(request),
        action,
    )
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/ops/work-items/{work_id}/cancel")
async def cancel_work(
    work_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("operate"))],
) -> JSONResponse:
    ctx = context(request)
    key = idempotency_key(request)
    body = await read_json_body(request, CancelRequest)

    def action(tx: Any) -> dict:
        return supervisor.cancel(
            tx,
            work_id,
            body.expected_work_version,
            body.cancel_note,
            principal=operator.scope,
            route_id=ctx.notification_route_id,
        )

    status_code, payload = await run_in_threadpool(
        _work_command,
        ctx,
        operator,
        work_id,
        request.url.path,
        key,
        body,
        request_id(request),
        action,
    )
    return JSONResponse(status_code=status_code, content=payload)


# ── 알림 (W26) ────────────────────────────────────────────────

NOTIFICATION_STATUSES = ("PENDING", "SENDING", "ACCEPTED", "FAILED", "UNKNOWN")


@router.get("/ops/notifications")
async def list_notifications(
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("read"))],
    status: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    reject_unknown_query(request, frozenset({"status", "limit"}))
    if status is not None and status not in NOTIFICATION_STATUSES:
        raise ApiError("INVALID_REQUEST", {"reason": "unknown_status"})
    if not 1 <= limit <= 200:
        raise ApiError("INVALID_REQUEST", {"reason": "limit_out_of_range"})
    ctx = context(request)
    routes = ctx.catalog.routes if ctx.catalog is not None else {}

    def load() -> list[dict]:
        where, params = ("WHERE status = ?", [status]) if status else ("", [])
        with ctx.store.read() as tx:
            rows = tx.all(
                "SELECT id, run_id, incident_id, work_id, event_type, route_id, status,"
                " attempt_count, next_attempt_at, receipt_id, accepted_at, created_at, updated_at,"
                f" result_json FROM notifications {where}"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, limit),
            )
        items = []
        for row in rows:
            route = routes.get(row["route_id"])
            last = (_json(row["result_json"]) or {}).get("last") or {}
            items.append(
                {
                    **{k: row[k] for k in row.keys() if k != "result_json"},
                    "status_label": templates.status_label(
                        row["status"], route.adapter if route else ""
                    ),
                    "last_error": last.get("error") or last.get("observation"),
                }
            )
        return items

    items = await run_in_threadpool(load)
    return JSONResponse(content=success_body(request_id(request), {"notifications": items}))


def _reconcile_notification(
    ctx: AppContext,
    operator: OperatorPrincipal,
    notification_id: str,
    path: str,
    key: str,
    body: ReconcileRequest,
    rid: str,
) -> tuple[int, dict]:
    if ctx.outbox_worker is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    if not is_valid_entity_id(notification_id, "NOT"):
        raise ApiError("RESOURCE_NOT_FOUND")
    with ctx.store.read() as tx:
        row = tx.one(
            "SELECT run_id, incident_id FROM notifications WHERE id = ?", (notification_id,)
        )
        if row is None:
            raise ApiError("RESOURCE_NOT_FOUND")
        load_visible_incident(tx, operator, row["incident_id"])
    scope = {
        "principal_scope": operator.scope,
        "method": "POST",
        "path": path,
        "run_id": row["run_id"],
        "key": key,
    }
    with ctx.store.tx() as tx:
        started = idempotency.begin(
            tx, **scope, body_sha256=idempotency.body_sha256(body.model_dump(mode="json"))
        )
    if started.outcome is Outcome.REPLAY:
        assert started.response is not None
        return started.response["status_code"], started.response["body"]
    if started.outcome is Outcome.CONFLICT:
        raise ApiError("IDEMPOTENCY_CONFLICT")
    if started.outcome is Outcome.IN_FLIGHT:
        raise ApiError("STATE_CONFLICT", {"reason": "request_in_progress_or_unknown"})
    data = ctx.outbox_worker.reconcile(notification_id)  # 외부 조회는 트랜잭션 밖
    response = success_body(rid, data)
    with ctx.store.tx() as tx:
        idempotency.complete(tx, **scope, status_code=200, body=response)
    return 200, response


@router.post("/ops/notifications/{notification_id}/reconcile")
async def reconcile_notification(
    notification_id: str,
    request: Request,
    operator: Annotated[OperatorPrincipal, Depends(require_operator_role("reconcile"))],
) -> JSONResponse:
    key = idempotency_key(request)
    body = await read_json_body(request, ReconcileRequest)
    status_code, payload = await run_in_threadpool(
        _reconcile_notification,
        context(request),
        operator,
        notification_id,
        request.url.path,
        key,
        body,
        request_id(request),
    )
    return JSONResponse(status_code=status_code, content=payload)
