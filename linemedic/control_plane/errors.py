"""API 오류 외피와 코드 → HTTP 매핑 (W06, spec 03 §1·§6, docs/03 §5).

메시지는 코드별 고정 문장이다. 요청 값·token·환경 변수·다른 사건 내용을 넣지 않는다.
`details`에는 호출자가 이미 볼 수 있는 값(현재 상태 등)만 넣는다.
"""

from typing import Any

from fastapi.responses import JSONResponse

SCHEMA_VERSION = "linemedic.v4"

# 코드 → (HTTP 상태, 자동 재시도 가능 여부, 고정 메시지)
ERROR_CODES: dict[str, tuple[int, bool, str]] = {
    "UNAUTHENTICATED": (401, False, "인증 정보가 없거나 유효하지 않습니다."),
    "FORBIDDEN_SCOPE": (403, False, "이 token으로는 이 요청을 할 수 없습니다."),
    "RESOURCE_NOT_FOUND": (404, False, "요청한 대상을 찾을 수 없습니다."),
    "STATE_CONFLICT": (409, False, "현재 상태에서 이 요청을 처리할 수 없습니다."),
    "IDEMPOTENCY_CONFLICT": (409, False, "같은 Idempotency-Key로 다른 요청 본문이 들어왔습니다."),
    "SOURCE_CHANGED": (409, False, "검사한 source가 바뀌었습니다."),
    "ISSUE_SCOPE_CHANGED": (409, False, "Issue 내용이나 승인 범위가 바뀌었습니다."),
    "START_NOTICE_UNCONFIRMED": (409, False, "시작 알림 접수가 확인되지 않았습니다."),
    "PAYLOAD_TOO_LARGE": (413, False, "요청 본문이 허용 크기를 넘었습니다."),
    "INVALID_PROPOSAL": (422, False, "제안 형식이 올바르지 않습니다."),
    "INVALID_REQUEST": (422, False, "요청 형식이 올바르지 않습니다."),
    "RATE_LIMITED": (429, True, "요청이 너무 많습니다."),
    "PROTECTION_UNAVAILABLE": (503, False, "필수 보호 조건을 확인할 수 없습니다."),
    "DEPENDENCY_UNAVAILABLE": (503, False, "필요한 외부 의존성을 쓸 수 없습니다."),
    "LOOKUP_INCOMPLETE": (503, False, "조회가 완전하지 않습니다."),
    "INTERNAL_ERROR": (500, False, "요청을 처리하지 못했습니다."),
}


class ApiError(Exception):
    """오류 외피로 응답할 예외. 메시지는 코드별 고정 문장을 쓴다."""

    def __init__(self, code: str, details: dict[str, Any] | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"알 수 없는 오류 코드: {code}")
        super().__init__(code)
        self.code = code
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return ERROR_CODES[self.code][0]


def error_body(request_id: str, code: str, details: dict[str, Any] | None = None) -> dict:
    _, retryable, message = ERROR_CODES[code]
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    }


def error_response(
    request_id: str, code: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_CODES[code][0], content=error_body(request_id, code, details)
    )


def success_body(request_id: str, data: Any, evidence_ids: list[str] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "data": data,
        "evidence_ids": evidence_ids or [],
    }
