"""N01 스파이크: Nemotron 접근과 도구 호출 형식 확인 (W02).

흐름
1. chat completions에 가짜 `get_incident` 도구 1개를 정의해 호출한다.
2. 모델이 tool call을 내는지 확인한다.
3. 가짜 사건 결과를 tool 메시지로 다시 넣는다.
4. 최종 응답이 구조화된 제안(JSON)인지 확인한다.

endpoint가 OpenAI 호환 형식인지는 실제 응답 구조로 판단한다. HTTP는 `httpx`만 쓴다.
판정은 PASS / FAIL / INCONCLUSIVE / NOT_CONFIGURED(필수 env 없음)이고,
종료 코드는 PASS 0, FAIL·INCONCLUSIVE 1, NOT_CONFIGURED 2다.
API 키는 Authorization 헤더에만 쓰고 출력·파일에 남기지 않는다.

실행 예:
    python -m linemedic.scripts.spikes.n01_model_tool_call \
        --output evidence/spikes/N01-model-tool-call.json
"""

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from linemedic.common.canonical_json import StrictJSONError, canonical_dumps, loads_strict
from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_ENV_FILE, process_env

REQUIRED_ENV = ("NVIDIA_BASE_URL", "NVIDIA_MODEL_ID", "NVIDIA_API_KEY")
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TOOL_ROUNDS = 2
MAX_ERROR_BODY_CHARS = 500

FAKE_INCIDENT_ID = "INC-000000000001"

# get_incident 모양의 가짜 도구. 실제 /tools 계약(spec 03 §2)과 이름만 같고 서버에 연결되지 않는다.
GET_INCIDENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_incident",
        "description": "현재 사건의 증상·특징·배포 정보와 증거 ID를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {"incident_id": {"type": "string", "description": "사건 ID"}},
            "required": ["incident_id"],
            "additionalProperties": False,
        },
    },
}

# 합성 값이다. 시나리오 이름·정답 category·기대 fixture를 넣지 않는다(spec 03 §2).
FAKE_INCIDENT_RESULT: dict[str, Any] = {
    "schema_version": "linemedic.v4",
    "data": {
        "id": FAKE_INCIDENT_ID,
        "service": "mes-api",
        "line_id": "L3",
        "status": "INVESTIGATING",
        "symptom": "불량 집계 요청에서 예외 반복",
        "features": {"recent_deploy": True, "scope": "service"},
        "base_sha": "1" * 40,
        "observed_at": "2026-09-27T00:00:00.000000Z",
    },
    "evidence_ids": ["EV-000000000001", "EV-000000000002"],
}

SYSTEM_PROMPT = (
    "너는 합성 공장 IT 환경의 장애 조사 에이전트다. "
    "반드시 get_incident 도구로 사건을 먼저 조회한다. "
    "도구 결과를 읽은 뒤에는 설명 없이 JSON 객체 하나만 출력한다. 필드는 다음과 같다: "
    '"category"(code_bug, equipment, config, infra, external_dependency, unknown 중 하나), '
    '"summary"(한두 문장), "evidence_ids"(도구 결과에 있는 증거 ID 배열), '
    '"action"(객체 하나: {"type": "escalate", "reason": "INSUFFICIENT_EVIDENCE", '
    '"open_questions": [문자열]}). confidence 필드나 actions 배열은 쓰지 않는다.'
)
USER_PROMPT = f"사건 {FAKE_INCIDENT_ID}을 조사하고 최종 제안을 JSON으로 내라."

CATEGORIES = {"code_bug", "equipment", "config", "infra", "external_dependency", "unknown"}
ACTION_TYPES = {"create_pr", "create_work_order_draft", "escalate"}
FORBIDDEN_PROPOSAL_FIELDS = {"confidence", "actions", "actor", "role", "status", "model"}
REQUEST_ID_HEADERS = ("x-request-id", "nvcf-reqid", "request-id")

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class SpikeFailure(Exception):
    def __init__(self, verdict: str, reason: str) -> None:
        super().__init__(reason)
        self.verdict = verdict
        self.reason = reason


def missing_env(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV if not env.get(name)]


def _request_id(response: httpx.Response, body: Any) -> str | None:
    for header in REQUEST_ID_HEADERS:
        if response.headers.get(header):
            return response.headers[header]
    if isinstance(body, dict) and isinstance(body.get("id"), str):
        return body["id"]
    return None


def _usage(body: dict[str, Any]) -> dict[str, int] | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    picked = {key: usage[key] for key in fields if isinstance(usage.get(key), int)}
    return picked or None


def _chat(
    client: httpx.Client,
    env: Mapping[str, str],
    messages: list[dict[str, Any]],
    clock: Clock,
    step: str,
    calls: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = env["NVIDIA_BASE_URL"].rstrip("/") + "/chat/completions"
    payload = {
        "model": env["NVIDIA_MODEL_ID"],
        "messages": messages,
        "tools": [GET_INCIDENT_TOOL],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 1024,
    }
    headers = {"Authorization": f"Bearer {env['NVIDIA_API_KEY']}", "Accept": "application/json"}
    started = clock.monotonic()
    response = client.post(url, headers=headers, json=payload)
    call: dict[str, Any] = {
        "step": step,
        "status_code": response.status_code,
        "latency_ms": round((clock.monotonic() - started) * 1000),
    }
    calls.append(call)  # 실패 응답도 request ID·오류 본문이 남도록 판정 전에 붙인다
    try:
        body = response.json()
    except ValueError:
        body = None
    call["request_id"] = _request_id(response, body)
    if response.status_code != 200:
        call["error_body"] = response.text[:MAX_ERROR_BODY_CHARS]
        transient = response.status_code == 429 or response.status_code >= 500
        raise SpikeFailure("INCONCLUSIVE" if transient else "FAIL", f"HTTP_{response.status_code}")
    if not isinstance(body, dict):
        call["error_body"] = response.text[:MAX_ERROR_BODY_CHARS]
        raise SpikeFailure("INCONCLUSIVE", "NOT_OPENAI_COMPATIBLE: 응답이 JSON 객체가 아님")
    call["usage"] = _usage(body)
    call["response_model"] = body.get("model") if isinstance(body.get("model"), str) else None
    return body, call


def _first_message(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise SpikeFailure("INCONCLUSIVE", "NOT_OPENAI_COMPATIBLE: choices 배열이 없음")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise SpikeFailure("INCONCLUSIVE", "NOT_OPENAI_COMPATIBLE: choices[0].message가 없음")
    return message, choices[0].get("finish_reason")


def _parse_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise SpikeFailure("FAIL", "BAD_TOOL_CALLS: tool_calls가 배열이 아님")
    parsed = []
    for raw in raw_calls:
        function = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(function, dict) or not isinstance(raw.get("id"), str):
            raise SpikeFailure("FAIL", "BAD_TOOL_CALLS: id 또는 function이 없음")
        name, arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, str):
            raise SpikeFailure("FAIL", "BAD_TOOL_CALLS: name 또는 arguments 형식 오류")
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            raise SpikeFailure("FAIL", "BAD_TOOL_ARGUMENTS: arguments가 JSON이 아님") from None
        if not isinstance(args, dict):
            raise SpikeFailure("FAIL", "BAD_TOOL_ARGUMENTS: arguments가 객체가 아님")
        parsed.append({"id": raw["id"], "name": name, "arguments": args})
    return parsed


def parse_proposal(content: Any) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: 최종 응답이 비어 있음")
    text = content.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        proposal = loads_strict(text.encode("utf-8"))
    except StrictJSONError as exc:
        raise SpikeFailure("FAIL", f"INVALID_PROPOSAL: JSON이 아님 ({exc})") from None
    if not isinstance(proposal, dict):
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: JSON 객체가 아님")
    forbidden = sorted(FORBIDDEN_PROPOSAL_FIELDS & set(proposal))
    if forbidden:
        raise SpikeFailure("FAIL", f"INVALID_PROPOSAL: 금지 필드 {', '.join(forbidden)}")
    if proposal.get("category") not in CATEGORIES:
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: category가 허용 값이 아님")
    summary = proposal.get("summary")
    if not isinstance(summary, str) or not 1 <= len(summary) <= 2000:
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: summary는 1~2000자 문자열이어야 함")
    evidence = proposal.get("evidence_ids", [])
    if (
        not isinstance(evidence, list)
        or len(evidence) > 20
        or not all(isinstance(item, str) for item in evidence)
        or len(set(evidence)) != len(evidence)
    ):
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: evidence_ids 형식 오류")
    action = proposal.get("action")
    if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
        raise SpikeFailure("FAIL", "INVALID_PROPOSAL: action.type이 허용 값이 아님")
    return proposal


def run_spike(
    env: Mapping[str, str],
    client: httpx.Client | None = None,
    clock: Clock | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    record: dict[str, Any] = {
        "spike": "N01",
        "schema_version": "linemedic.v4",
        "started_at": to_rfc3339(clock.utc_now()),
        "model_id": env.get("NVIDIA_MODEL_ID") or None,
        "response_model": None,  # 응답 본문의 model. model_id와 같은지 evidence로 확인한다
        "base_url": env.get("NVIDIA_BASE_URL") or None,
        "calls": [],
        "final_proposal": None,
        "verdict": None,
        "reason": None,
    }
    missing = missing_env(env)
    if missing:
        record.update(verdict="NOT_CONFIGURED", reason="필수 env 미설정", missing_env=missing)
        return record

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]
    try:
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            step = "tool_request" if round_index == 0 else f"after_tool_result_{round_index}"
            body, call = _chat(client, env, messages, clock, step, record["calls"])
            record["response_model"] = record["response_model"] or call["response_model"]
            message, finish_reason = _first_message(body)
            call["finish_reason"] = finish_reason
            tool_calls = _parse_tool_calls(message)
            call["tool_calls"] = [
                {"name": c["name"], "arguments": c["arguments"]} for c in tool_calls
            ]
            if tool_calls:
                if round_index == MAX_TOOL_ROUNDS:
                    raise SpikeFailure(
                        "INCONCLUSIVE", "TOOL_LOOP: 도구 호출이 반복되어 최종 제안이 없음"
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": message["tool_calls"],
                    }
                )
                for tool_call in tool_calls:
                    if tool_call["name"] != "get_incident":
                        raise SpikeFailure("FAIL", f"UNKNOWN_TOOL: {tool_call['name']}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": canonical_dumps(FAKE_INCIDENT_RESULT),
                        }
                    )
                continue
            if round_index == 0:
                raise SpikeFailure("FAIL", "NO_TOOL_CALL: 모델이 첫 응답에서 도구를 호출하지 않음")
            record["final_proposal"] = parse_proposal(message.get("content"))
            record.update(verdict="PASS", reason="tool call → 결과 재입력 → 구조화 제안 성공")
            return record
    except SpikeFailure as exc:
        record.update(verdict=exc.verdict, reason=exc.reason)
    except httpx.HTTPError as exc:
        record.update(verdict="INCONCLUSIVE", reason=f"HTTP_ERROR: {type(exc).__name__}")
    finally:
        if owns_client:
            client.close()
    return record


def exit_code_for(verdict: str | None) -> int:
    if verdict == "PASS":
        return 0
    if verdict == "NOT_CONFIGURED":
        return 2
    return 1


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="n01_model_tool_call")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로 (비밀 값 없음)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    env = process_env(args.env_file) if env is None else env
    record = run_spike(env, client=client, timeout=args.timeout)
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"N01 판정: {record['verdict']} — {record['reason']}", file=sys.stderr)
    return exit_code_for(record["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
