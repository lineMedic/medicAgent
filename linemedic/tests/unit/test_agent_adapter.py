"""W13 adapter 계약: ScriptedAdapter가 사람 제안을 제출하고 브로커 결정을 조회한다.

- 제안 파일의 schema 필드만 옮기고(설명 필드 제외) 서버가 준 ID·base·근거를 채운다
- token은 Authorization 헤더로만 가고 제안·context에 없다. 멱등 키는 attempt마다 고정이다
- 결정(ALLOWED·REJECTED) → decided, work가 끝나 도구가 404 → closed, 근거 없음 → no_proposal,
  deadline → deadline_exceeded, 도구 오류 → error. 어떤 경우에도 client를 닫는다
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from linemedic.agent.adapter import ORIGIN_MANUAL, ScriptedAdapter, http_tools_client
from linemedic.common.clock import FakeClock
from linemedic.control_plane.broker.proposals import Proposal
from linemedic.control_plane.main import DEFAULT_MANUAL_PROPOSAL

RUN = "r-20260927-000000-abcd"
INC, WORK, ATT = "INC-0000000000AA", "WORK-0000000000AA", "ATT-0000000000AA"
BASE = "b" * 40
TOKEN = "test-agent-token-" + "x" * 20  # 테스트 전용 가짜 값
DEADLINE = "2026-09-27T00:04:00.000000Z"


class Tools:
    """가짜 `/tools/*`: 요청을 기록하고 준비한 응답을 돌려준다."""

    def __init__(self, evidence=("EV-0000000000A1", "EV-0000000000A2"), decisions=("ALLOWED",)):
        self.evidence = list(evidence)
        self.decisions = list(decisions)
        self.requests: list[httpx.Request] = []
        self.closed = 0
        self.submit_status = 202

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == f"/tools/incidents/{INC}":
            return httpx.Response(200, json={"data": {}, "evidence_ids": self.evidence})
        if path == "/tools/proposals" and request.method == "POST":
            if self.submit_status != 202:
                error = {"error": {"code": "INVALID_PROPOSAL"}}
                return httpx.Response(self.submit_status, json=error)
            return httpx.Response(202, json={"data": {"proposal_id": "PROP-0000000000AA"}})
        if path == "/tools/proposals/PROP-0000000000AA":
            decision = self.decisions.pop(0) if self.decisions else "CHECKING"
            if decision in (404, 500):
                return httpx.Response(decision, json={"error": {"code": "X"}})
            return httpx.Response(
                200, json={"data": {"decision": decision, "decision_reason": "PR_OPENED"}}
            )
        return httpx.Response(500)

    def factory(self, credential: str) -> httpx.Client:
        tools = self

        class Client(httpx.Client):
            def close(self) -> None:
                tools.closed += 1
                super().close()

        return Client(
            base_url="http://control",
            transport=httpx.MockTransport(self.handler),
            headers={"Authorization": f"Bearer {credential}"},
        )


def context_file(tmp_path: Path) -> Path:
    path = tmp_path / "context.json"
    path.write_text(json.dumps({"base": {"sha": BASE}, "run_id": RUN}), encoding="utf-8")
    return path


def run(adapter: ScriptedAdapter, tmp_path: Path):
    return adapter.run_agent(
        RUN, INC, WORK, ATT, DEADLINE, tmp_path / "repo", context_file(tmp_path), credential=TOKEN
    )


def clock() -> FakeClock:
    return FakeClock(start=datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC))


def test_submits_schema_fields_with_server_ids_and_reads_the_decision(tmp_path):
    tools = Tools(decisions=["CHECKING", "ALLOWED"])
    waits: list[float] = []
    adapter = ScriptedAdapter(
        DEFAULT_MANUAL_PROPOSAL, tools.factory, clock(), poll_seconds=2, wait=waits.append
    )
    result = run(adapter, tmp_path)
    assert (result.status, result.decision, result.proposal_ids) == (
        "decided",
        "ALLOWED",
        ("PROP-0000000000AA",),
    )
    assert (result.adapter, result.origin) == ("scripted", ORIGIN_MANUAL) and waits == [2]
    post = next(r for r in tools.requests if r.method == "POST")
    body = json.loads(post.content)
    Proposal.model_validate(body)  # 브로커 schema 그대로 통과한다
    assert (body["run_id"], body["incident_id"], body["work_id"], body["attempt_id"]) == (
        RUN,
        INC,
        WORK,
        ATT,
    )
    assert body["action"]["base_sha"] == BASE and body["evidence_ids"] == tools.evidence
    assert "note" not in body and TOKEN not in post.content.decode()
    assert post.headers["idempotency-key"] == f"scripted-{ATT}-1"
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in tools.requests)
    assert tools.closed == 1


def test_fields_outside_the_schema_are_not_forwarded(tmp_path):
    template = json.loads(DEFAULT_MANUAL_PROPOSAL.read_text(encoding="utf-8"))
    template["reviewer_memo"] = "사람 메모"
    template["action"]["author_note"] = "사람이 쓴 설명"
    template["action"]["base_sha"] = "0" * 40  # 파일의 base는 쓰지 않는다(서버 context 값)
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    tools = Tools()
    assert run(ScriptedAdapter(path, tools.factory, clock()), tmp_path).status == "decided"
    body = json.loads(next(r for r in tools.requests if r.method == "POST").content)
    Proposal.model_validate(body)
    assert "reviewer_memo" not in body and "author_note" not in body["action"]
    assert body["action"]["base_sha"] == BASE


def test_attempt_closed_when_the_work_moves_on(tmp_path):
    tools = Tools(decisions=[404])  # PR이 열려 work가 RUNNING이 아니다
    result = run(ScriptedAdapter(DEFAULT_MANUAL_PROPOSAL, tools.factory, clock()), tmp_path)
    assert (result.status, result.detail) == ("closed", "attempt_closed")


def test_no_evidence_means_no_proposal(tmp_path):
    tools = Tools(evidence=())
    result = run(ScriptedAdapter(DEFAULT_MANUAL_PROPOSAL, tools.factory, clock()), tmp_path)
    assert (result.status, result.detail) == ("no_proposal", "no_evidence")
    assert not [r for r in tools.requests if r.method == "POST"]


def test_deadline_stops_waiting(tmp_path):
    fake = clock()
    tools = Tools(decisions=[])  # 계속 CHECKING
    adapter = ScriptedAdapter(DEFAULT_MANUAL_PROPOSAL, tools.factory, fake, poll_seconds=60)
    result = run(adapter, tmp_path)
    assert result.status == "deadline_exceeded" and result.proposal_ids == ("PROP-0000000000AA",)
    assert fake.utc_now() >= datetime(2026, 9, 27, 0, 4, tzinfo=UTC)


@pytest.mark.parametrize(
    ("setup", "detail"),
    [
        (lambda t: setattr(t, "submit_status", 422), "submit_422:INVALID_PROPOSAL"),
        (lambda t: setattr(t, "decisions", [500]), "get_500"),
        (lambda t: setattr(t, "evidence", None), "get_incident_500"),
    ],
)
def test_tool_errors_are_reported_as_error(tmp_path, setup, detail):
    tools = Tools()
    setup(tools)
    if tools.evidence is None:
        original = tools.handler
        tools.handler = lambda request: (  # type: ignore[method-assign]
            httpx.Response(500) if "/incidents/" in request.url.path else original(request)
        )
    result = run(ScriptedAdapter(DEFAULT_MANUAL_PROPOSAL, tools.factory, clock()), tmp_path)
    assert (result.status, result.detail) == ("error", detail)
    assert tools.closed == 1


def test_missing_proposal_file_is_an_error_and_client_is_closed(tmp_path):
    tools = Tools()
    adapter = ScriptedAdapter(tmp_path / "missing.json", tools.factory, clock())
    result = run(adapter, tmp_path)
    assert (result.status, result.detail) == ("error", "FileNotFoundError")
    assert tools.closed == 1


def test_http_tools_client_puts_the_token_only_in_the_header():
    client = http_tools_client("http://127.0.0.1:8080")(TOKEN)
    try:
        assert client.headers["authorization"] == f"Bearer {TOKEN}"
        assert str(client.base_url) == "http://127.0.0.1:8080"
    finally:
        client.close()
