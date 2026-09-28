"""runtime 에이전트 adapter 계약 (W13, spec 05 §1·§10, docs/05 ④, D53).

`run_agent(run_id, incident_id, work_id, attempt_id, deadline, workspace_ref, context_ref)`는
LineMedic 내부 계약이다(외부 SDK 함수명이 아니다). host supervisor만 시작 게이트를 통과한 work에서
부른다.

- `workspace_ref`: attempt의 쓰기 가능한 base 사본(`RUNS_DIR/<run>/workspaces/<attempt>/repo`)
- `context_ref`: supervisor가 확인해 쓴 문맥 JSON(work·Issue snapshot·시작 receipt·run/attempt·base·
  memory mode). credential은 넣지 않는다
- `credential`: attempt 범위 agent token. tool client에만 주고 파일·로그에 쓰지 않는다
- `AttemptResult`는 adapter의 보고일 뿐이다. 상태는 브로커·supervisor가 DB로 판단한다

`ScriptedAdapter`(G4 전, origin `manual_integration`): 사람이 미리 쓴 제안 파일
(`linemedic/eval/manual_proposals/`)을 읽어 `/tools/proposals`에 제출하고 브로커 결정을 조회한다.
모델을 부르지 않는다. 제안 파일의 설명 필드는 보내지 않고 schema 필드만 옮긴다.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from linemedic.common.clock import Clock, from_rfc3339

ORIGIN_MANUAL = "manual_integration"
ORIGIN_AGENT = "agent_release"
MAX_EVIDENCE = 3
FINAL_DECISIONS = frozenset({"ALLOWED", "REJECTED"})
PROPOSAL_FIELDS = ("category", "summary")
ACTION_FIELDS = {
    "create_pr": (
        "type",
        "root_cause_hypothesis",
        "diff",
        "new_test_path",
        "local_test_observation",
    ),
    "create_work_order_draft": (
        "type",
        "equipment_id",
        "symptom",
        "probable_cause",
        "manual_ref_id",
        "open_questions",
    ),
    "escalate": ("type", "reason", "open_questions", "missing_requirements", "retry_condition"),
}


class ToolsClient(Protocol):
    """`/tools/*`를 부르는 HTTP client(httpx.Client 호환).

    base URL·Authorization은 만들 때 정한다.
    """

    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...

    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class AttemptResult:
    """adapter의 attempt 보고.

    - decided: 브로커가 제안을 ALLOWED·REJECTED로 결정했다
    - closed: 제안을 낸 뒤 work가 RUNNING이 아니게 됐다(PR·초안·이관 등, 도구 조회가 404)
    - no_proposal: 제안을 내지 않았다(근거 없음 등)
    - deadline_exceeded: 결정 전에 deadline이 지났다
    - error: 도구 호출·제안 파일 오류

    runtime이 알면 채우는 값(W14 trace): model_id·runtime_version, token 사용량(`usage`, 모르면
    None), runtime이 관측한 로컬 도구(`local_tools`: 도구 이름·결과만). `retryable`은 외부 변경
    없는 조사 단계의 모델 일시 오류(429·timeout)라 host가 같은 deadline 안에서 한 번 다시 부를 수
    있다는 뜻이다.
    """

    status: Literal["decided", "closed", "no_proposal", "deadline_exceeded", "error"]
    adapter: str
    origin: str
    proposal_ids: tuple[str, ...] = ()
    decision: str | None = None
    decision_reason: str | None = None
    detail: str | None = None
    retryable: bool = False
    model_id: str | None = None
    runtime_version: str | None = None
    usage: dict[str, Any] | None = None
    local_tools: tuple[dict[str, Any], ...] = ()

    def record(self) -> dict[str, Any]:
        data = asdict(self)
        data["proposal_ids"] = list(self.proposal_ids)
        data["local_tools"] = [dict(item) for item in self.local_tools]
        return data


class AgentAdapter(Protocol):
    name: str
    origin: str  # verification origin: agent_release(실제 에이전트) 또는 manual_integration

    def run_agent(
        self,
        run_id: str,
        incident_id: str,
        work_id: str,
        attempt_id: str,
        deadline: str,
        workspace_ref: Path,
        context_ref: Path,
        *,
        credential: str,
    ) -> AttemptResult: ...


def load_context(context_ref: Path) -> dict[str, Any]:
    data = json.loads(context_ref.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("context는 JSON object여야 한다")
    return data


class ScriptedAdapter:
    name = "scripted"
    origin = ORIGIN_MANUAL

    def __init__(
        self,
        proposal_path: Path,
        client_factory: Callable[[str], ToolsClient],
        clock: Clock,
        *,
        poll_seconds: float = 1.0,
        wait: Callable[[float], None] | None = None,
    ) -> None:
        self.proposal_path = proposal_path
        self.client_factory = client_factory
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.wait = wait or clock.sleep

    def proposal_body(
        self,
        *,
        run_id: str,
        incident_id: str,
        work_id: str,
        attempt_id: str,
        base_sha: str,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        """제안 파일의 schema 필드만 옮기고 서버가 준 ID·base·근거를 채운다."""
        template = json.loads(self.proposal_path.read_text(encoding="utf-8"))
        action_in = template["action"]
        fields = ACTION_FIELDS[action_in["type"]]
        action = {key: action_in[key] for key in fields if key in action_in}
        if action["type"] == "create_pr":
            action["base_sha"] = base_sha
        return {
            "schema_version": "linemedic.v4",
            "run_id": run_id,
            "incident_id": incident_id,
            "work_id": work_id,
            "attempt_id": attempt_id,
            **{key: template[key] for key in PROPOSAL_FIELDS},
            "evidence_ids": evidence_ids[:MAX_EVIDENCE],
            "action": action,
        }

    def _result(self, status: str, **fields: Any) -> AttemptResult:
        return AttemptResult(status, self.name, self.origin, **fields)  # type: ignore[arg-type]

    def run_agent(
        self,
        run_id: str,
        incident_id: str,
        work_id: str,
        attempt_id: str,
        deadline: str,
        workspace_ref: Path,
        context_ref: Path,
        *,
        credential: str,
    ) -> AttemptResult:
        client = self.client_factory(credential)
        try:
            context = load_context(context_ref)
            return self._run(client, run_id, incident_id, work_id, attempt_id, deadline, context)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            return self._result("error", detail=type(exc).__name__)
        finally:
            client.close()

    def _run(
        self,
        client: ToolsClient,
        run_id: str,
        incident_id: str,
        work_id: str,
        attempt_id: str,
        deadline: str,
        context: dict[str, Any],
    ) -> AttemptResult:
        incident = client.get(f"/tools/incidents/{incident_id}")
        if incident.status_code != 200:
            return self._result("error", detail=f"get_incident_{incident.status_code}")
        evidence = [e for e in incident.json().get("evidence_ids") or [] if isinstance(e, str)]
        if not evidence:
            return self._result("no_proposal", detail="no_evidence")
        body = self.proposal_body(
            run_id=run_id,
            incident_id=incident_id,
            work_id=work_id,
            attempt_id=attempt_id,
            base_sha=context["base"]["sha"],
            evidence_ids=evidence,
        )
        submitted = client.post(
            "/tools/proposals", json=body, headers={"Idempotency-Key": f"scripted-{attempt_id}-1"}
        )
        if submitted.status_code != 202:
            code = (submitted.json().get("error") or {}).get("code")
            return self._result("error", detail=f"submit_{submitted.status_code}:{code}")
        proposal_id = submitted.json()["data"]["proposal_id"]
        until = from_rfc3339(deadline)
        while True:
            status = client.get(f"/tools/proposals/{proposal_id}")
            if status.status_code == 404:  # work가 RUNNING이 아니다(PR·초안·이관): attempt 끝
                return self._result("closed", proposal_ids=(proposal_id,), detail="attempt_closed")
            if status.status_code != 200:
                return self._result(
                    "error", proposal_ids=(proposal_id,), detail=f"get_{status.status_code}"
                )
            data = status.json()["data"]
            if data["decision"] in FINAL_DECISIONS:
                return self._result(
                    "decided",
                    proposal_ids=(proposal_id,),
                    decision=data["decision"],
                    decision_reason=data.get("decision_reason"),
                )
            if self.clock.utc_now() >= until:
                return self._result("deadline_exceeded", proposal_ids=(proposal_id,))
            self.wait(self.poll_seconds)


def http_tools_client(base_url: str, timeout: float = 30.0) -> Callable[[str], ToolsClient]:
    """Control API의 `/tools/*`를 부르는 client factory(`make start`). token은 헤더에만 둔다."""

    def factory(credential: str) -> ToolsClient:
        return httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {credential}"},
        )

    return factory
