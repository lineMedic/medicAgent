"""W14 `/tools` HTTP client: 9개 도구의 경로·인자, token은 헤더에만, 잘못된 경로 인자는 보내지 않음,
자동 재시도 없음, trace에 본문 없음 (D87).
"""

import json

import httpx
import pytest

from linemedic.agent.tools_client import TOOL_NAMES, ToolsClient

INC = "INC-0000000000A1"
PROP = "PROP-0000000000A1"
TOKEN = "attempt-token-" + "x" * 32


def client_with(handler):
    return ToolsClient("http://127.0.0.1:8080", TOKEN, transport=httpx.MockTransport(handler))


def test_each_tool_calls_its_documented_endpoint():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        status = 202 if request.method == "POST" else 200
        return httpx.Response(status, json={"request_id": "REQ-1", "data": {}, "evidence_ids": []})

    with client_with(handler) as client:
        results = [
            client.get_incident(INC),
            client.search_logs(INC, q="KeyError", limit=5),
            client.get_deploys(INC),
            client.get_knowledge(INC, q="점검"),
            client.query_equipment_metrics(INC, "L3-CAM-2"),
            client.get_bound_issue(INC),
            client.search_cases(INC, limit=3),
            client.submit_proposal({"schema_version": "linemedic.v4"}, "key-1"),
            client.get_proposal(PROP),
        ]
    assert [r.tool for r in results] == list(TOOL_NAMES)
    assert all(r.ok for r in results)
    assert seen == [
        ("GET", f"/tools/incidents/{INC}", {}),
        ("GET", f"/tools/incidents/{INC}/logs", {"q": "KeyError", "limit": "5"}),
        ("GET", f"/tools/incidents/{INC}/deploys", {}),
        ("GET", f"/tools/incidents/{INC}/knowledge", {"q": "점검"}),
        ("GET", f"/tools/incidents/{INC}/equipment/L3-CAM-2/metrics", {}),
        ("GET", f"/tools/incidents/{INC}/issue", {}),
        ("GET", f"/tools/incidents/{INC}/cases/search", {"limit": "3"}),
        ("POST", "/tools/proposals", {}),
        ("GET", f"/tools/proposals/{PROP}", {}),
    ]


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_incident("../ops/dashboard"),
        lambda c: c.search_logs("INC-1"),
        lambda c: c.query_equipment_metrics(INC, "../../x"),
        lambda c: c.get_proposal("PROP-../x"),
    ],
)
def test_invalid_path_arguments_are_not_sent(call):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("요청을 보내면 안 된다")

    with client_with(handler) as client:
        result = call(client)
    assert (result.status, result.error_code, result.ok) == (0, "INVALID_ARGUMENT", False)
    assert client.calls[-1]["status"] == 0


def test_submission_is_only_accepted_and_carries_the_idempotency_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"] == "attempt-1"
        assert json.loads(request.content) == {"a": 1}
        return httpx.Response(202, json={"data": {"proposal_id": PROP, "decision": "RECEIVED"}})

    with client_with(handler) as client:
        result = client.submit_proposal({"a": 1}, "attempt-1")
    assert result.status == 202 and result.data["decision"] == "RECEIVED"


def test_trace_has_no_bodies_or_token_and_errors_are_kept():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"request_id": "REQ-9", "error": {"code": "RATE_LIMITED"}})

    with client_with(handler) as client:
        result = client.search_logs(INC, q="비밀 문장")
    assert (result.status, result.error_code) == (429, "RATE_LIMITED")
    (entry,) = client.calls
    assert entry["tool"] == "search_logs" and entry["error"] == "RATE_LIMITED"
    assert entry["request_id"] == "REQ-9"
    assert "비밀 문장" not in json.dumps(entry, ensure_ascii=False)
    assert TOKEN not in repr(client) and TOKEN not in json.dumps(client.calls)


def test_transport_error_is_reported_once_without_retry():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        raise httpx.ConnectError("down")

    with client_with(handler) as client:
        result = client.get_incident(INC)
    assert attempts == [f"/tools/incidents/{INC}"]  # 한 번만 보냈다
    assert (result.status, result.error_code) == (0, "TRANSPORT")
