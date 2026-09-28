"""에이전트 `/tools/*` HTTP client (W14, spec 03 §2, docs/04 §2, D87).

runtime adapter가 모델이 고른 도구를 이 client로 부른다.

- 9개 도구를 메서드로 둔다. token은 client 헤더에만 둔다(prompt·context·trace에 넣지 않는다)
- 모델이 준 경로 인자(사건·설비·제안 ID)는 형식을 먼저 확인한다. 형식이 아니면 요청을 보내지 않는다
- 자동 재시도하지 않는다(전송 재시도와 도구 호출을 섞지 않는다)
- 호출마다 trace 항목(도구·HTTP 상태·오류 코드·걸린 시간·request_id)을 남긴다. 본문은 남기지 않는다
- submit_proposal의 202는 접수일 뿐이다. 결정은 get_proposal로 본다
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

INCIDENT_RE = re.compile(r"^INC-[0-9A-F]{12}$")
PROPOSAL_RE = re.compile(r"^PROP-[0-9A-F]{12}$")
EQUIPMENT_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
TOOL_NAMES = (
    "get_incident",
    "search_logs",
    "get_deploys",
    "get_knowledge",
    "query_equipment_metrics",
    "get_bound_issue",
    "search_cases",
    "submit_proposal",
    "get_proposal",
)


@dataclass(frozen=True)
class ToolResult:
    tool: str
    status: int  # HTTP 상태. 요청을 보내지 않았으면 0
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (200, 202)

    @property
    def data(self) -> Any:
        return self.body.get("data")

    @property
    def evidence_ids(self) -> list[str]:
        return [e for e in self.body.get("evidence_ids") or [] if isinstance(e, str)]

    @property
    def error_code(self) -> str | None:
        error = self.body.get("error")
        return error.get("code") if isinstance(error, dict) else None


class ToolsClient:
    def __init__(
        self,
        base_url: str,
        credential: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {credential}"},
        )
        self._monotonic = monotonic
        self.calls: list[dict[str, Any]] = []  # runtime이 관측한 도구 호출(본문 없음)

    def __repr__(self) -> str:  # credential을 보이지 않는다
        return f"ToolsClient({self._http.base_url!s})"

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ToolsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _refuse(self, tool: str, reason: str) -> ToolResult:
        self.calls.append({"tool": tool, "status": 0, "error": reason, "elapsed_ms": 0})
        return ToolResult(tool, 0, {"error": {"code": "INVALID_ARGUMENT", "reason": reason}})

    def _call(
        self,
        tool: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ToolResult:
        started = self._monotonic()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = self._http.request(method, path, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            elapsed = round((self._monotonic() - started) * 1000)
            self.calls.append({"tool": tool, "status": 0, "error": type(exc).__name__,
                               "elapsed_ms": elapsed})  # fmt: skip
            return ToolResult(
                tool, 0, {"error": {"code": "TRANSPORT", "reason": type(exc).__name__}}
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        result = ToolResult(tool, response.status_code, body if isinstance(body, dict) else {})
        self.calls.append(
            {
                "tool": tool,
                "status": response.status_code,
                "error": result.error_code,
                "elapsed_ms": round((self._monotonic() - started) * 1000),
                "request_id": result.body.get("request_id"),
            }
        )
        return result

    @staticmethod
    def _incident(incident_id: str) -> str | None:
        return f"/tools/incidents/{incident_id}" if INCIDENT_RE.fullmatch(incident_id) else None

    # 도구 9개

    def get_incident(self, incident_id: str) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("get_incident", "invalid_incident_id")
        return self._call("get_incident", "GET", path)

    def search_logs(
        self, incident_id: str, q: str | None = None, limit: int | None = None
    ) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("search_logs", "invalid_incident_id")
        return self._call("search_logs", "GET", f"{path}/logs", params={"q": q, "limit": limit})

    def get_deploys(self, incident_id: str) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("get_deploys", "invalid_incident_id")
        return self._call("get_deploys", "GET", f"{path}/deploys")

    def get_knowledge(self, incident_id: str, q: str | None = None) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("get_knowledge", "invalid_incident_id")
        return self._call("get_knowledge", "GET", f"{path}/knowledge", params={"q": q})

    def query_equipment_metrics(self, incident_id: str, equipment_id: str) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("query_equipment_metrics", "invalid_incident_id")
        if not EQUIPMENT_RE.fullmatch(equipment_id):
            return self._refuse("query_equipment_metrics", "invalid_equipment_id")
        return self._call(
            "query_equipment_metrics", "GET", f"{path}/equipment/{equipment_id}/metrics"
        )

    def get_bound_issue(self, incident_id: str) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("get_bound_issue", "invalid_incident_id")
        return self._call("get_bound_issue", "GET", f"{path}/issue")

    def search_cases(
        self, incident_id: str, q: str | None = None, limit: int | None = None
    ) -> ToolResult:
        path = self._incident(incident_id)
        if path is None:
            return self._refuse("search_cases", "invalid_incident_id")
        return self._call(
            "search_cases", "GET", f"{path}/cases/search", params={"q": q, "limit": limit}
        )

    def submit_proposal(self, proposal: dict[str, Any], idempotency_key: str) -> ToolResult:
        """202는 접수다(허용·실행 성공이 아니다)."""
        return self._call(
            "submit_proposal",
            "POST",
            "/tools/proposals",
            json=proposal,
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_proposal(self, proposal_id: str) -> ToolResult:
        if not PROPOSAL_RE.fullmatch(proposal_id):
            return self._refuse("get_proposal", "invalid_proposal_id")
        return self._call("get_proposal", "GET", f"/tools/proposals/{proposal_id}")
