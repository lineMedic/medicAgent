"""W02 N01 스파이크 스크립트의 단위 테스트 (네트워크 없음, httpx.MockTransport 사용).

실제 NVIDIA endpoint 호출은 `linemedic/tests/live/test_model_toolcall.py`(live_model)에서 한다.
"""

import json

import httpx

from linemedic.common.clock import FakeClock
from linemedic.scripts.spikes import n01_model_tool_call as n01

SECRET_KEY = "nvapi-UNIT-TEST-KEY-DO-NOT-PRINT"
ENV = {
    "NVIDIA_BASE_URL": "https://inference.example.test/v1",
    "NVIDIA_MODEL_ID": "example/model-id",
    "NVIDIA_API_KEY": SECRET_KEY,
}

FINAL_PROPOSAL = {
    "category": "unknown",
    "summary": "증거가 부족해 안전하게 조치를 고를 수 없습니다.",
    "evidence_ids": ["EV-000000000001"],
    "action": {
        "type": "escalate",
        "reason": "INSUFFICIENT_EVIDENCE",
        "open_questions": ["재현 요청의 전체 stack을 확인하지 못함"],
    },
}


def tool_call_response() -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "req-tool-1"},
        json={
            "id": "chatcmpl-1",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "get_incident",
                                    "arguments": json.dumps({"incident_id": n01.FAKE_INCIDENT_ID}),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        },
    )


def final_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "req-final-1"},
        json={
            "id": "chatcmpl-2",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


def run_with(handler) -> dict:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return n01.run_spike(ENV, client=client, clock=FakeClock())


def test_not_configured_without_env_and_no_secret_output(capsys):
    exit_code = n01.main([], env={"NVIDIA_API_KEY": SECRET_KEY})
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "NOT_CONFIGURED" in output
    assert "NVIDIA_BASE_URL" in output and "NVIDIA_MODEL_ID" in output
    assert SECRET_KEY not in output


def test_pass_when_tool_call_roundtrip_and_structured_proposal():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request, body))
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == f"Bearer {SECRET_KEY}"
        assert body["model"] == ENV["NVIDIA_MODEL_ID"]
        assert body["tools"][0]["function"]["name"] == "get_incident"
        if len(seen) == 1:
            return tool_call_response()
        tool_messages = [m for m in body["messages"] if m["role"] == "tool"]
        assert tool_messages and tool_messages[0]["tool_call_id"] == "call-1"
        assert n01.FAKE_INCIDENT_ID in tool_messages[0]["content"]
        return final_response(
            "```json\n" + json.dumps(FINAL_PROPOSAL, ensure_ascii=False) + "\n```"
        )

    record = run_with(handler)
    assert record["verdict"] == "PASS", record["reason"]
    assert len(record["calls"]) == 2
    assert record["calls"][0]["request_id"] == "req-tool-1"
    assert record["calls"][0]["usage"]["total_tokens"] == 140
    assert record["calls"][1]["usage"] is None
    assert record["calls"][0]["tool_calls"][0]["name"] == "get_incident"
    assert record["final_proposal"]["action"]["type"] == "escalate"
    assert SECRET_KEY not in json.dumps(record, ensure_ascii=False)


def test_fail_when_model_does_not_call_tool():
    record = run_with(lambda request: final_response(json.dumps(FINAL_PROPOSAL)))
    assert record["verdict"] == "FAIL"
    assert record["reason"].startswith("NO_TOOL_CALL")


def test_inconclusive_when_response_is_not_openai_compatible():
    record = run_with(lambda request: httpx.Response(200, json={"result": "hello"}))
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["reason"].startswith("NOT_OPENAI_COMPATIBLE")


def test_fail_on_auth_error_without_leaking_key():
    record = run_with(lambda request: httpx.Response(401, json={"detail": "unauthorized"}))
    assert record["verdict"] == "FAIL"
    assert record["reason"] == "HTTP_401"
    assert SECRET_KEY not in json.dumps(record, ensure_ascii=False)


def test_rate_limit_is_inconclusive():
    record = run_with(lambda request: httpx.Response(429, json={"detail": "slow down"}))
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["reason"] == "HTTP_429"


def test_fail_when_final_answer_is_not_a_valid_proposal():
    responses = iter([tool_call_response(), final_response("조치를 판단할 수 없습니다.")])
    record = run_with(lambda request: next(responses))
    assert record["verdict"] == "FAIL"
    assert record["reason"].startswith("INVALID_PROPOSAL")


def test_fail_when_proposal_uses_forbidden_confidence_field():
    bad = dict(FINAL_PROPOSAL, confidence=0.9)
    responses = iter([tool_call_response(), final_response(json.dumps(bad))])
    record = run_with(lambda request: next(responses))
    assert record["verdict"] == "FAIL"
    assert "confidence" in record["reason"]
