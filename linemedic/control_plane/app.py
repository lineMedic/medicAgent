"""Control API app factory (W06, spec 03 §1, 07 §3).

- 모든 요청은 `Authorization: Bearer <token>`으로 principal을 얻는다. 없거나 모르는 token은 401.
- `/ops/*`는 operator, `/tools/*`는 agent principal만 쓴다. 서로 바꿔 쓰면 403.
  라우팅 전에 prefix 단위로 검사하므로 없는 경로도 먼저 인증을 거친다. 그 밖의 경로는 404.
- 변경 요청 body: 크기 제한 → `loads_strict`(중복 key·NaN 거부) → pydantic strict·extra=forbid 검증.
- 모든 POST는 `Idempotency-Key` 헤더가 필요하다.
- 응답·오류는 spec 03 외피를 쓴다. 공개 API 문서(`/docs`·`/openapi.json`)는 열지 않는다.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from linemedic.common.canonical_json import StrictJSONError, loads_strict
from linemedic.common.clock import Clock
from linemedic.common.ids import new_id
from linemedic.common.sanitize import mask_secrets
from linemedic.control_plane import idempotency
from linemedic.control_plane.auth import (
    AgentPrincipal,
    OperatorPrincipal,
    Principal,
    TokenRegistry,
    bearer_token,
)
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.errors import ApiError, error_response
from linemedic.control_plane.issue_router import IssueRouter
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.knowledge import KnowledgeBase
from linemedic.control_plane.log_store import LogStore
from linemedic.control_plane.metrics_store import MetricsStore
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.control_plane.state import TransitionDenied
from linemedic.control_plane.store import StateConflict, Store, StoreBusy

if TYPE_CHECKING:  # broker.reconcile·release → intake → app 순환을 피한다
    from linemedic.control_plane.broker.reconcile import ExecutionReconciler
    from linemedic.control_plane.memory.search import CaseSearch
    from linemedic.control_plane.release import ReleaseExecutor

DEFAULT_MAX_BODY_BYTES = 131072  # docs/07 proposal.max_bytes (128 KiB)
MAX_ERROR_ITEMS = 20


@dataclass(frozen=True)
class AppContext:
    store: Store
    tokens: TokenRegistry
    clock: Clock
    notification_route_id: str
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    log_store: LogStore | None = None
    logs_window_minutes: int = 30  # docs/07 tools.logs.window_minutes
    logs_max_bytes: int = 65536  # docs/07 tools.logs.max_bytes
    deploys_window_hours: int = 24  # docs/07 tools.deploys.window_hours
    catalog: Catalog | None = None  # 등록 서비스·설비(W08)
    metrics_store: MetricsStore | None = None
    metrics_max_minutes: int = 30  # docs/07 tools.metrics.max_minutes
    metrics_max_samples: int = 60  # docs/07 tools.metrics.max_samples
    knowledge: KnowledgeBase | None = None
    max_submissions: int = 2  # docs/07 agent.max_submissions (attempt당 서로 다른 제출 합산)
    issue_sync: IssueSync | None = None  # 등록 repo Issue 조회(W23). G2 전에는 없음
    issue_router: IssueRouter | None = None  # 로그 incident → Issue 연결(W24)
    outbox_worker: OutboxWorker | None = None  # 알림 발송·조정(W26)
    execution_reconciler: "ExecutionReconciler | None" = None  # 결과 불명 execution 조정(W11)
    release_executor: "ReleaseExecutor | None" = None  # 승인한 exact SHA 배포(W12)
    case_search: "CaseSearch | None" = None  # 사례 검색(W27). cold_start면 DISABLED를 기록한다


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class PrefixAuthGuard:
    """라우팅 전에 token과 경로 prefix를 대조한다. body를 읽기 전에 거부한다."""

    def __init__(self, app: ASGIApp, tokens: TokenRegistry) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_id("REQ")
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        principal = self.tokens.resolve(bearer_token(Headers(scope=scope).get("authorization")))
        path = scope["path"]
        code: str | None
        if principal is None:
            code = "UNAUTHENTICATED"
        elif _under(path, "/ops"):
            code = None if isinstance(principal, OperatorPrincipal) else "FORBIDDEN_SCOPE"
        elif _under(path, "/tools"):
            code = None if isinstance(principal, AgentPrincipal) else "FORBIDDEN_SCOPE"
        else:
            code = "RESOURCE_NOT_FOUND"
        if code is not None:
            response = error_response(request_id, code)
            if code == "UNAUTHENTICATED":
                response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        state["principal"] = principal
        await self.app(scope, receive, send)


# ── 요청 도우미 ───────────────────────────────────────────────


def context(request: Request) -> AppContext:
    return request.app.state.ctx


def request_id(request: Request) -> str:
    return request.state.request_id


def principal(request: Request) -> Principal:
    return request.state.principal


def require_operator_role(role: str):
    """operator principal이고 해당 역할이 있을 때만 통과하는 FastAPI dependency."""

    def dependency(request: Request) -> OperatorPrincipal:
        current = principal(request)
        if not isinstance(current, OperatorPrincipal) or role not in current.roles:
            raise ApiError("FORBIDDEN_SCOPE")
        return current

    return dependency


def reject_unknown_query(request: Request, allowed: frozenset[str] = frozenset()) -> None:
    """정의하지 않은 query parameter는 무시하지 않고 거부한다."""
    unknown = set(request.query_params) - allowed
    if unknown:
        raise ApiError("INVALID_REQUEST", {"unknown_query": sorted(name[:64] for name in unknown)})


def idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if not idempotency.valid_key(key):
        raise ApiError("INVALID_REQUEST", {"reason": "idempotency_key_required"})
    assert key is not None
    return key


def safe_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """검증 오류의 위치·종류만 돌려준다. 입력 값은 넣지 않고 위치(key 이름)의 비밀 형태는 가린다."""
    items = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = [mask_secrets(str(part)[:64]) for part in error["loc"]]
        items.append({"loc": location, "type": error["type"]})
    return items[:MAX_ERROR_ITEMS]


async def read_raw_body(request: Request) -> bytes:
    """content-type(JSON)과 크기 제한만 확인하고 body 바이트를 읽는다."""
    limit = context(request).max_body_bytes
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise ApiError("INVALID_REQUEST", {"reason": "content_type_must_be_json"})
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise ApiError("INVALID_REQUEST", {"reason": "invalid_content_length"})
        if int(declared) > limit:
            raise ApiError("PAYLOAD_TOO_LARGE", {"max_bytes": limit})
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise ApiError("PAYLOAD_TOO_LARGE", {"max_bytes": limit})
    return bytes(body)


async def read_json_body[M: BaseModel](request: Request, model: type[M]) -> M:
    """크기 제한 → 엄격한 JSON 파싱 → pydantic 모델 검증 순서로 body를 읽는다."""
    limit = context(request).max_body_bytes
    body = await read_raw_body(request)
    try:
        data = loads_strict(body, max_bytes=limit)
    except StrictJSONError:
        raise ApiError("INVALID_REQUEST", {"reason": "invalid_json"}) from None
    if not isinstance(data, dict):
        raise ApiError("INVALID_REQUEST", {"reason": "body_must_be_object"})
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ApiError("INVALID_REQUEST", {"errors": safe_validation_errors(exc)}) from None


# ── 예외 → 오류 외피 ─────────────────────────────────────────


def _request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_id("REQ")


async def _api_error(request: Request, exc: ApiError):
    return error_response(_request_id_of(request), exc.code, exc.details)


async def _state_conflict(request: Request, exc: StateConflict):
    details: dict[str, Any] = {}
    if exc.current_status is not None:
        details = {"current_status": exc.current_status, "current_version": exc.current_version}
    return error_response(_request_id_of(request), "STATE_CONFLICT", details)


async def _transition_denied(request: Request, exc: TransitionDenied):
    details = {"current_status": exc.from_status} if exc.from_status else {}
    return error_response(_request_id_of(request), "STATE_CONFLICT", details)


async def _store_busy(request: Request, exc: StoreBusy):
    return error_response(_request_id_of(request), "DEPENDENCY_UNAVAILABLE")


async def _validation_error(request: Request, exc: RequestValidationError):
    errors = [
        {"loc": [str(part)[:64] for part in error.get("loc", ())], "type": error.get("type")}
        for error in exc.errors()
    ][:MAX_ERROR_ITEMS]
    return error_response(_request_id_of(request), "INVALID_REQUEST", {"errors": errors})


async def _http_error(request: Request, exc: StarletteHTTPException):
    code = "RESOURCE_NOT_FOUND" if exc.status_code in (404, 405) else "INVALID_REQUEST"
    return error_response(_request_id_of(request), code)


async def _unexpected(request: Request, exc: Exception):
    return error_response(_request_id_of(request), "INTERNAL_ERROR")


def create_app(ctx: AppContext) -> FastAPI:
    from linemedic.control_plane import ops_api, tools_api

    app = FastAPI(title="LineMedic Control API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.add_middleware(PrefixAuthGuard, tokens=ctx.tokens)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StateConflict, _state_conflict)
    app.add_exception_handler(TransitionDenied, _transition_denied)
    app.add_exception_handler(StoreBusy, _store_busy)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)
    app.include_router(ops_api.router)
    app.include_router(tools_api.router)
    return app
