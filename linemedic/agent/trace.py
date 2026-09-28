"""attempt trace (W14, spec 05 §1·§7·§9, spec 12 §3.1, D87).

attempt가 끝나면 `runs/<run>/traces/<attempt>.json`에 남긴다(같은 파일이 있으면 덮어쓰지 않는다).

- runtime·runtime_version·model_id·agent_mode·prompt_sha256(규칙 묶음 hash)
- tool trace: 서버 측 `/tools` 호출(감사 `TOOL_CALL`의 순서·도구·예산 제외 여부)과 runtime이 관측한
  로컬 도구(adapter 보고). 둘을 섞지 않는다
- token 사용량: 입력·출력을 모두 관측하면 observed, 일부만 partial, 없으면 null
- 시작·끝 시각, attempt 결과 상태
- sandbox 모드면 identity·정책 hash·effective policy·보호 확인(W15)
- attempt 전후 규칙 묶음 hash(N10)
prompt 본문·도구 응답 본문·모델의 숨은 사고과정은 남기지 않는다.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.control_plane.store import Tx

TRACE_SCHEMA = "linemedic.attempt-trace.v1"
TOOL_CALL_EVENT = "TOOL_CALL"
TOOL_REFUSED_EVENT = "TOOL_CALL_REFUSED"


def server_calls(tx: Tx, run_id: str, incident_id: str, attempt_id: str) -> list[dict[str, Any]]:
    """서버가 받은 이 attempt의 도구 호출(예산 초과로 거절한 호출 포함), 받은 순서."""
    calls = []
    for row in tx.all(
        "SELECT event_type, created_at, payload_json FROM audit_events WHERE run_id = ?"
        " AND incident_id = ? AND event_type IN (?, ?) ORDER BY seq",
        (run_id, incident_id, TOOL_CALL_EVENT, TOOL_REFUSED_EVENT),
    ):
        payload = json.loads(row["payload_json"] or "{}")
        if payload.get("attempt_id") != attempt_id:
            continue
        calls.append(
            {
                "at": row["created_at"],
                "tool": payload.get("tool"),
                "result": "refused_budget" if row["event_type"] == TOOL_REFUSED_EVENT else "served",
                "budget_exempt": bool(payload.get("budget_exempt")),
            }
        )
    return calls


def usage_record(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """token 사용량. 관측하지 못한 값은 null로 두고 지어내지 않는다."""
    usage = usage or {}
    tokens = {key: usage.get(key) for key in ("input_tokens", "output_tokens")}
    known = [value for value in tokens.values() if isinstance(value, int) and value >= 0]
    status = "observed" if len(known) == 2 else ("partial" if known else "null")
    return {**tokens, "status": status}


def build_trace(
    *,
    attempt_id: str,
    run_id: str,
    incident_id: str,
    agent_mode: str,
    prompt_sha256: str | None,
    started_at: str | None,
    ended_at: str,
    result: Mapping[str, Any],
    server: list[dict[str, Any]],
    sandbox: Mapping[str, Any] | None = None,
    rules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    local = [
        {key: item.get(key) for key in ("tool", "status", "error")}
        for item in result.get("local_tools") or []
        if isinstance(item, Mapping)
    ]
    counted = [call for call in server if not call["budget_exempt"] and call["result"] == "served"]
    return {
        "schema_version": TRACE_SCHEMA,
        "run_id": run_id,
        "incident_id": incident_id,
        "attempt_id": attempt_id,
        "adapter": result.get("adapter"),
        "origin": result.get("origin"),
        "runtime_version": result.get("runtime_version"),
        "model_id": result.get("model_id"),
        "agent_mode": agent_mode,
        "prompt_sha256": prompt_sha256,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": result.get("status"),
        "proposal_ids": list(result.get("proposal_ids") or []),
        "tool_calls": {
            "server": server,
            "runtime_local": local,
            "counted": len(counted),
            "local_observed": "observed" if local else "null",  # runtime이 보고하지 않으면 모른다
        },
        "tokens": usage_record(result.get("usage")),
        "sandbox": dict(sandbox) if sandbox is not None else None,  # local 모드는 null
        "rules": dict(rules) if rules is not None else None,
    }


def write_trace(runs_dir: Path, run_id: str, attempt_id: str, trace: Mapping[str, Any]) -> Path:
    """배타 생성으로 쓴다. 이미 있으면 그대로 두고 그 경로를 돌려준다."""
    path = runs_dir / run_id / "traces" / f"{attempt_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(canonical_dumps(dict(trace)) + "\n")
    except FileExistsError:
        pass
    return path


def summarize(server: list[dict[str, Any]]) -> list[str]:
    """화면용 도구 순서: 연속한 같은 도구는 `이름 ×N`으로 줄인다."""
    summary: list[str] = []
    last, count = None, 0
    for call in server:
        name = call["tool"] + (" (예산 초과 거절)" if call["result"] == "refused_budget" else "")
        if name == last:
            count += 1
            summary[-1] = f"{name} ×{count}"
        else:
            last, count = name, 1
            summary.append(name)
    return summary
