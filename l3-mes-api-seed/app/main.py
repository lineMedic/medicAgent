"""L3 라인 합성 MES API."""

import uuid

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.data import InvalidLotId, LotNotFound, load_lot
from app.defects import summarize
from app.logging_json import exception_fields, log_event

app = FastAPI(title="L3 MES API")


@app.middleware("http")
async def json_request_log(request: Request, call_next):
    request_id = uuid.uuid4().hex
    lot_id = request.query_params.get("lot_id")
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as exc:
        log_event(
            "ERROR",
            "request_failed",
            request_id=request_id,
            lot_id=lot_id,
            path=path,
            status=500,
            **exception_fields(exc),
        )
        return JSONResponse(
            status_code=500, content={"error": "internal_error", "request_id": request_id}
        )
    level = "ERROR" if response.status_code >= 500 else "INFO"
    log_event(
        level,
        "request_completed",
        request_id=request_id,
        lot_id=lot_id,
        path=path,
        status=response.status_code,
    )
    response.headers["x-request-id"] = request_id
    return response


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/defects/summary")
def defects_summary(lot_id: str = Query(..., min_length=1, max_length=64)):
    try:
        records = load_lot(lot_id)
    except InvalidLotId:
        return JSONResponse(status_code=400, content={"error": "invalid_lot_id"})
    except LotNotFound:
        return JSONResponse(status_code=404, content={"error": "lot_not_found", "lot_id": lot_id})
    return summarize(lot_id, records)
