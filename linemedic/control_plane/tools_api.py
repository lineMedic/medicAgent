"""에이전트 조회 도구 `/tools/*` (W07: get_incident·search_logs·get_deploys, spec 03 §2).

나머지 도구는 W08(get_knowledge·query_equipment_metrics), W09(submit_proposal·get_proposal),
W27·W28(search_cases·get_bound_issue)에서 더한다.

- agent token의 run·incident·attempt·work가 지금 사건과 맞고 work가 RUNNING일 때만 답한다.
  아니면 없는 사건과 같은 404다(T-AUTH-02).
- 응답에 시나리오 이름·정답 category·기대 fixture·평가 입력을 넣지 않는다. `features`는 힌트다.
- `search_logs`의 `q`는 대소문자를 무시하는 부분 문자열이다. 정규식·shell을 쓰지 않는다.
- 로그·증거는 비신뢰 데이터다. 안의 지시문·Issue 번호·URL을 해석하지 않는다.
"""

import json
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from linemedic.common.clock import from_rfc3339, to_rfc3339
from linemedic.control_plane import evidence
from linemedic.control_plane.app import (
    AppContext,
    context,
    principal,
    reject_unknown_query,
    request_id,
)
from linemedic.control_plane.auth import AgentPrincipal, load_visible_incident
from linemedic.control_plane.deploys import deploy_records
from linemedic.control_plane.errors import ApiError, success_body

router = APIRouter()
LOG_QUERY_MAX_CHARS = 200
ENVELOPE_RESERVE_BYTES = 2048


def _agent(request: Request) -> AgentPrincipal:
    current = principal(request)
    if not isinstance(current, AgentPrincipal):
        raise ApiError("FORBIDDEN_SCOPE")
    return current


def _shift(value: str, **delta: float) -> str:
    return to_rfc3339(from_rfc3339(value) + timedelta(**delta))


def _symptom(details: dict[str, Any]) -> str | None:
    sig = details.get("signature")
    if not isinstance(sig, dict) or not sig.get("endpoint") or not sig.get("error_type"):
        return None
    error_type = str(sig["error_type"]).split(":", 1)[0]
    return f"{sig['endpoint']} 요청에서 {error_type} 오류 반복 관찰"


def _incident_data(ctx: AppContext, agent: AgentPrincipal, incident_id: str) -> tuple[dict, list]:
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, incident_id)
        run_id, service = incident["run_id"], incident["service"]
        work = tx.one(
            "SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?", (run_id, incident_id)
        )
        notice = None
        if work is not None and work["start_notification_id"]:
            notice = tx.one(
                "SELECT id, status, receipt_id FROM notifications WHERE id = ? AND work_id = ?",
                (work["start_notification_id"], work["id"]),
            )
        history = deploy_records(tx, run_id, service, since="")
        evidence_ids = evidence.evidence_ids(tx, run_id, incident_id)
    first_seen = incident["first_seen"]
    recent_from = _shift(first_seen, hours=-ctx.deploys_window_hours)
    base_sha = next((d["base_sha"] for d in reversed(history) if d.get("base_sha")), None)
    data = {
        "id": incident["id"],
        "run_id": run_id,
        "attempt_id": incident["attempt_id"],
        "version": incident["version"],
        "status": incident["status"],
        "service": service,
        "line_id": incident["line_id"],
        "category": incident["category"],
        "symptom": _symptom(json.loads(incident["details_json"] or "{}")),
        "features": {
            "recent_deploy": any(recent_from <= d["observed_at"] <= first_seen for d in history),
            "scope": "service",
        },
        "base_sha": base_sha,
        "observed_at": first_seen,
        "last_seen": incident["last_seen"],
        "count": incident["count"],
        "work_id": work["id"] if work is not None else None,
        "work_status": work["status"] if work is not None else None,
        "issue": (
            {
                "repository_id": work["repository_id"],
                "number": work["issue_number"],
                "snapshot_sha256": work["issue_snapshot_sha256"],
            }
            if work is not None
            else None
        ),
        "start_notification": (
            {"id": notice["id"], "status": notice["status"], "receipt_id": notice["receipt_id"]}
            if notice is not None
            else None
        ),
    }
    return data, evidence_ids


@router.get("/tools/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request) -> JSONResponse:
    reject_unknown_query(request)
    data, evidence_ids = await run_in_threadpool(
        _incident_data, context(request), _agent(request), incident_id
    )
    return JSONResponse(content=success_body(request_id(request), data, evidence_ids))


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _search_logs(
    ctx: AppContext, agent: AgentPrincipal, incident_id: str, q: str | None, limit: int, rid: str
) -> dict:
    if ctx.log_store is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, incident_id)
    start = _shift(incident["first_seen"], minutes=-ctx.logs_window_minutes)
    end = _shift(incident["last_seen"], minutes=ctx.logs_window_minutes)
    records = ctx.log_store.read(incident["run_id"], incident["service"], start, end)
    if q:
        needle = q.casefold()
        records = [record for record in records if needle in record.line.casefold()]
    lines = [
        {"observed_at": r.observed_at, "source": r.source, "line": r.line} for r in records[-limit:]
    ]
    truncated = len(records) > len(lines)
    body = success_body(
        rid,
        {
            "window": {"from": start, "to": end},
            "q": q,
            "limit": limit,
            "lines": lines,
            "truncated": truncated,
        },
    )
    # 응답 전체가 64 KiB를 넘지 않게 오래된 줄부터 뺀다.
    while lines and _encoded_size(body) > ctx.logs_max_bytes:
        lines.pop(0)
        body["data"]["truncated"] = True
    return body


@router.get("/tools/incidents/{incident_id}/logs")
async def search_logs(
    incident_id: str,
    request: Request,
    q: Annotated[str | None, Query(max_length=LOG_QUERY_MAX_CHARS)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> JSONResponse:
    reject_unknown_query(request, frozenset({"q", "limit"}))
    body = await run_in_threadpool(
        _search_logs, context(request), _agent(request), incident_id, q, limit, request_id(request)
    )
    return JSONResponse(content=body)


def _deploys(ctx: AppContext, agent: AgentPrincipal, incident_id: str) -> dict:
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, incident_id)
        since = _shift(incident["first_seen"], hours=-ctx.deploys_window_hours)
        history = deploy_records(tx, incident["run_id"], incident["service"], since="")
    recent = [d for d in history if d["observed_at"] >= since]
    current = next((d["base_sha"] for d in reversed(history) if d.get("base_sha")), None)
    return {
        "service": incident["service"],
        "window_hours": ctx.deploys_window_hours,
        "deploys": recent,
        "current_base_sha": current,
    }


@router.get("/tools/incidents/{incident_id}/deploys")
async def get_deploys(incident_id: str, request: Request) -> JSONResponse:
    reject_unknown_query(request)
    data = await run_in_threadpool(_deploys, context(request), _agent(request), incident_id)
    return JSONResponse(content=success_body(request_id(request), data))
