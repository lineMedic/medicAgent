"""에이전트 도구 `/tools/*` (W07: get_incident·search_logs·get_deploys, spec 03 §2).

W08: query_equipment_metrics·get_knowledge. W09: submit_proposal·get_proposal.
나머지는 W27·W28(search_cases·get_bound_issue)에서 더한다.

- agent token의 run·incident·attempt·work가 지금 사건과 맞고 work가 RUNNING일 때만 답한다.
  아니면 없는 사건과 같은 404다(T-AUTH-02).
- 응답에 시나리오 이름·정답 category·기대 fixture·평가 입력을 넣지 않는다. `features`는 힌트다.
- `search_logs`의 `q`는 대소문자를 무시하는 부분 문자열이다. 정규식·shell을 쓰지 않는다.
- 로그·증거는 비신뢰 데이터다. 안의 지시문·Issue 번호·URL을 해석하지 않는다.
- `submit_proposal`의 202는 접수일 뿐 허용·실행 성공이 아니다. 결과는 `get_proposal`로 본다.
"""

import json
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from linemedic.common.clock import from_rfc3339, to_rfc3339
from linemedic.common.ids import is_valid_entity_id
from linemedic.common.sanitize import disable_urls
from linemedic.control_plane import evidence
from linemedic.control_plane.app import (
    AppContext,
    context,
    idempotency_key,
    principal,
    read_raw_body,
    reject_unknown_query,
    request_id,
)
from linemedic.control_plane.auth import AgentPrincipal, load_visible_incident
from linemedic.control_plane.broker import intake
from linemedic.control_plane.broker.proposals import ProposalStatus
from linemedic.control_plane.deploys import deploy_records
from linemedic.control_plane.errors import ApiError, success_body
from linemedic.control_plane.symptoms import observed_symptom

router = APIRouter()
LOG_QUERY_MAX_CHARS = 200
ENVELOPE_RESERVE_BYTES = 2048


def _agent(request: Request) -> AgentPrincipal:
    current = principal(request)
    if not isinstance(current, AgentPrincipal):
        raise ApiError("FORBIDDEN_SCOPE")
    return current


def _related_services(ctx: AppContext, service: str) -> frozenset[str]:
    """배포 기록을 볼 서비스: 같은 라인의 등록 서비스. catalog가 없으면 사건 서비스만."""
    if ctx.catalog is None:
        return frozenset({service})
    return ctx.catalog.related_services(service)


def _shift(value: str, **delta: float) -> str:
    return to_rfc3339(from_rfc3339(value) + timedelta(**delta))


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
        history = deploy_records(tx, run_id, _related_services(ctx, service), since="")
        evidence_ids = evidence.evidence_ids(tx, run_id, incident_id)
    first_seen = incident["first_seen"]
    details = json.loads(incident["details_json"] or "{}")
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
        "symptom": observed_symptom(details),  # 관찰 사실만, 원인 추정 없음
        "features": {
            "recent_deploy": any(recent_from <= d["observed_at"] <= first_seen for d in history),
            "scope": "equipment" if "metric" in details else "service",
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
        services = _related_services(ctx, incident["service"])
        history = deploy_records(tx, incident["run_id"], services, since="")
    recent = [d for d in history if d["observed_at"] >= since]
    current = next((d["base_sha"] for d in reversed(history) if d.get("base_sha")), None)
    return {
        "service": incident["service"],
        "services": sorted(services),
        "window_hours": ctx.deploys_window_hours,
        "deploys": recent,
        "current_base_sha": current,
    }


@router.get("/tools/incidents/{incident_id}/deploys")
async def get_deploys(incident_id: str, request: Request) -> JSONResponse:
    reject_unknown_query(request)
    data = await run_in_threadpool(_deploys, context(request), _agent(request), incident_id)
    return JSONResponse(content=success_body(request_id(request), data))


def _equipment_metrics(
    ctx: AppContext, agent: AgentPrincipal, incident_id: str, equipment_id: str
) -> dict:
    if ctx.catalog is None or ctx.metrics_store is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, incident_id)
    equipment = ctx.catalog.equipment_of(incident["service"]).get(equipment_id)
    if equipment is None:
        raise ApiError("RESOURCE_NOT_FOUND")  # 미등록 설비·다른 서비스 설비
    end = incident["last_seen"]
    start = _shift(end, minutes=-ctx.metrics_max_minutes)
    samples = ctx.metrics_store.read(incident["run_id"], equipment_id, start, end)
    samples = samples[-ctx.metrics_max_samples :]
    return {
        "equipment_id": equipment_id,
        "service": equipment.service,
        "line_id": equipment.line_id,
        "window": {"from": start, "to": end},
        "baseline": {"brightness": samples[-1].baseline_brightness if samples else None},
        "samples": [
            {"ts": s.ts, "brightness": s.brightness, "confidence": s.confidence} for s in samples
        ],
        "quality": {
            "sample_count": len(samples),
            "max_samples": ctx.metrics_max_samples,
            "window_minutes": ctx.metrics_max_minutes,
        },
    }


@router.get("/tools/incidents/{incident_id}/equipment/{equipment_id}/metrics")
async def query_equipment_metrics(
    incident_id: str, equipment_id: str, request: Request
) -> JSONResponse:
    reject_unknown_query(request)
    data = await run_in_threadpool(
        _equipment_metrics, context(request), _agent(request), incident_id, equipment_id
    )
    return JSONResponse(content=success_body(request_id(request), data))


def _knowledge(ctx: AppContext, agent: AgentPrincipal, incident_id: str, q: str | None) -> dict:
    if ctx.catalog is None or ctx.knowledge is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, incident_id)
    allowed = ctx.catalog.manuals_for(incident["service"])
    found = ctx.knowledge.search(allowed, q)
    return {
        "q": q,
        "results": [
            {
                "manual_ref_id": section.manual_ref_id,
                "section_id": section.section_id,
                "title": section.title,
                "text": disable_urls(section.text),
                "disclaimer": manual.disclaimer,
            }
            for manual, section in found
        ],
    }


@router.get("/tools/incidents/{incident_id}/knowledge")
async def get_knowledge(
    incident_id: str,
    request: Request,
    q: Annotated[str | None, Query(max_length=LOG_QUERY_MAX_CHARS)] = None,
) -> JSONResponse:
    reject_unknown_query(request, frozenset({"q"}))
    data = await run_in_threadpool(_knowledge, context(request), _agent(request), incident_id, q)
    return JSONResponse(content=success_body(request_id(request), data))


@router.post(intake.PROPOSALS_PATH)
async def submit_proposal(request: Request) -> JSONResponse:
    reject_unknown_query(request)
    agent = _agent(request)
    key = idempotency_key(request)
    raw = await read_raw_body(request)
    status_code, body = await run_in_threadpool(
        intake.submit, context(request), agent, key, raw, request_id(request)
    )
    return JSONResponse(status_code=status_code, content=body)


def _proposal(ctx: AppContext, agent: AgentPrincipal, proposal_id: str) -> dict:
    if not is_valid_entity_id(proposal_id, "PROP"):
        raise ApiError("RESOURCE_NOT_FOUND")
    with ctx.store.read() as tx:
        incident = load_visible_incident(tx, agent, agent.incident_id)
        row = tx.one(
            "SELECT * FROM proposals WHERE id = ? AND run_id = ? AND incident_id = ?"
            " AND attempt_id = ?",
            (proposal_id, agent.run_id, agent.incident_id, agent.attempt_id),
        )
    if row is None:
        raise ApiError("RESOURCE_NOT_FOUND")  # 다른 사건·attempt의 제안도 없는 것과 같다
    payload = json.loads(row["payload_json"])
    record = json.loads(row["checks_json"])
    revision_allowed = (
        row["decision"] == "REJECTED"
        and incident["status"] == "INVESTIGATING"
        and incident["submissions"] < ctx.max_submissions
    )
    status = ProposalStatus.model_validate(
        {
            "proposal_id": row["id"],
            "incident_id": row["incident_id"],
            "category": payload["category"],
            "action_type": payload["action"]["type"],
            "decision": row["decision"],
            "decision_reason": record.get("decision_reason"),
            "checks": record["checks"],
            "received_at": row["received_at"],
            "submissions_used": incident["submissions"],
            "max_submissions": ctx.max_submissions,
            "revision_allowed": revision_allowed,
        }
    )
    return status.model_dump(mode="json")


@router.get(intake.PROPOSALS_PATH + "/{proposal_id}")
async def get_proposal(proposal_id: str, request: Request) -> JSONResponse:
    reject_unknown_query(request)
    data = await run_in_threadpool(_proposal, context(request), _agent(request), proposal_id)
    return JSONResponse(content=success_body(request_id(request), data))
