"""W07 통합 테스트: 에이전트 조회 도구 get_incident·search_logs·get_deploys (실제 SQLite + app).

사건은 감지기로 만들고, supervisor의 시작(W25·W28)은 테스트에서 상태를 직접 준비해 흉내 낸다.
T-AUTH-02를 실제 `/tools` endpoint로 다시 확인한다.
"""

import json
import re

import pytest

from linemedic.common.clock import FakeClock
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.deploys import deploy_records, record_deploy_observed
from linemedic.control_plane.detector import Detector, DetectorSettings
from linemedic.control_plane.log_store import MemoryLogStore
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.factory_sim import scenarios
from linemedic.tests.helpers.api import RUN, make_api
from linemedic.tests.helpers.db_rows import insert_issue, insert_run, insert_work

SOURCE = "container:linemedic-mes-test"
BASE_SHA = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
HOLDOUT_LOT = "L3-HOLDOUT-201"
MAX_RESPONSE_BYTES = 65536


def error_line(n: int, **changes) -> str:
    event = {
        "level": "ERROR",
        "event": "request_failed",
        "request_id": f"{n:032x}",
        "lot_id": "L3-0927-118",
        "path": "/defects/summary",
        "status": 500,
        "error_type": "KeyError",
        "error_field": "inspector_id",
        "top_frame": "app.defects:summarize",
    }
    event.update(changes)
    return json.dumps(event)


def info_line(lot_id: str) -> str:
    return json.dumps(
        {
            "level": "INFO",
            "event": "request_completed",
            "lot_id": lot_id,
            "path": "/defects/summary",
        }
    )


class World:
    def __init__(self, store, conn, clock: FakeClock):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        self.logs = MemoryLogStore()
        self.api = make_api(store, conn, log_store=self.logs)
        self.detector = Detector(
            store,
            DetectorSettings(
                run_id=RUN,
                routing_scope=f"eval:{RUN}",
                service="mes-api",
                line_id="L3",
                repository_id=100001,
            ),
            clock,
            self.logs,
            eval_identifiers(),
        )
        self.issue = 1

    def deploy(self, base_sha: str | None = BASE_SHA) -> None:
        with self.store.tx() as tx:
            record_deploy_observed(
                tx,
                run_id=RUN,
                service="mes-api",
                base_sha=base_sha,
                image_id=IMAGE_ID,
                container="linemedic-mes-test",
                container_id="c" * 64,
                actor="trusted_harness",
            )

    def observe(self, *lines: str) -> None:
        for line in lines:
            self.clock.advance(1)
            self.detector.observe_line(line, SOURCE)

    def incident(self, **changes) -> str:
        """오류 3회로 사건을 만든다."""
        self.observe(*(error_line(i, **changes) for i in range(3)))
        return self.conn.execute("SELECT id FROM incidents ORDER BY rowid DESC").fetchone()[0]

    def start(self, incident_id: str, attempt: str = "ATT-00000000000A") -> AgentPrincipal:
        """supervisor의 READY → RUNNING·attempt 발급을 흉내 낸다(테스트 전용 직접 준비)."""
        self.issue += 1
        insert_issue(self.conn, self.issue)
        self.conn.execute(
            "UPDATE incidents SET status = 'INVESTIGATING', attempt_id = ? WHERE id = ?",
            (attempt, incident_id),
        )
        work = insert_work(self.conn, RUN, incident_id, self.issue, "RUNNING", attempt_id=attempt)
        return AgentPrincipal(RUN, incident_id, work, attempt)

    def get(self, path: str, principal: AgentPrincipal, **params):
        return self.api.client.get(path, params=params, headers=self.api.agent(principal))


@pytest.fixture
def world(store, conn, fake_clock):
    return World(store, conn, fake_clock)


@pytest.fixture
def s1(world):
    """배포 관찰 → 정상 요청 1건·holdout 요청 1건 → 오류 3회로 사건 생성 → attempt 시작."""
    world.deploy()
    world.observe(info_line("L3-0927-101"), info_line(HOLDOUT_LOT))
    incident = world.incident()
    return incident, world.start(incident)


def test_get_incident_returns_scoped_view(world, s1):
    incident, agent = s1
    response = world.get(f"/tools/incidents/{incident}", agent)
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert (data["id"], data["run_id"], data["attempt_id"]) == (incident, RUN, agent.attempt_id)
    assert (data["status"], data["service"], data["line_id"]) == ("INVESTIGATING", "mes-api", "L3")
    assert data["category"] is None
    assert data["symptom"] == "/defects/summary 요청에서 KeyError 오류 반복 관찰"
    assert data["features"] == {"recent_deploy": True, "scope": "service"}
    assert data["base_sha"] == BASE_SHA
    assert data["count"] == 3
    assert (data["work_id"], data["work_status"]) == (agent.work_id, "RUNNING")
    assert data["issue"]["repository_id"] == 100001
    assert data["start_notification"] is None
    stored = [r[0] for r in world.conn.execute("SELECT id FROM evidence ORDER BY observed_at")]
    assert body["evidence_ids"] == stored and len(stored) == 3


def test_tool_responses_hide_scenario_and_eval_data(world, s1):
    incident, agent = s1
    texts = [
        world.get(f"/tools/incidents/{incident}", agent).text,
        world.get(f"/tools/incidents/{incident}/logs", agent).text,
        world.get(f"/tools/incidents/{incident}/deploys", agent).text,
    ]
    for text in texts:
        assert not re.search(r"(?i)\bs1b?\b", text)
        assert "expected_category" not in text and "scenario" not in text.lower()
        assert HOLDOUT_LOT not in text and "HOLDOUT" not in text and "I-03" not in text
    assert "[REDACTED:eval]" in texts[1]


def test_search_logs_returns_window_lines_in_order(world, s1):
    incident, agent = s1
    data = world.get(f"/tools/incidents/{incident}/logs", agent).json()["data"]
    lines = data["lines"]
    assert len(lines) == 5 and data["truncated"] is False
    assert [line["observed_at"] for line in lines] == sorted(line["observed_at"] for line in lines)
    assert {line["source"] for line in lines} == {SOURCE}
    assert data["limit"] == 20 and data["q"] is None
    assert data["window"]["from"] < lines[0]["observed_at"] < data["window"]["to"]


def test_search_logs_query_is_literal_and_case_insensitive(world, s1):
    incident, agent = s1
    world.observe("literal .* marker", "regex would match this too")
    literal = world.get(f"/tools/incidents/{incident}/logs", agent, q=".*").json()["data"]
    assert [line["line"] for line in literal["lines"]] == ["literal .* marker"]
    errors = world.get(f"/tools/incidents/{incident}/logs", agent, q="KEYERROR").json()["data"]
    assert len(errors["lines"]) == 3


def test_search_logs_limit_keeps_latest_lines(world, s1):
    incident, agent = s1
    data = world.get(f"/tools/incidents/{incident}/logs", agent, limit=2).json()["data"]
    assert len(data["lines"]) == 2 and data["truncated"] is True
    assert all("KeyError" in line["line"] for line in data["lines"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 21}, {"limit": 0}, {"limit": "x"}, {"q": "q" * 201}, {"regex": "1"}],
)
def test_search_logs_rejects_bad_parameters(world, s1, params):
    incident, agent = s1
    response = world.get(f"/tools/incidents/{incident}/logs", agent, **params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_search_logs_response_stays_under_64_kib(world, s1):
    incident, agent = s1
    world.observe(*("L" + str(i) + "x" * 9000 for i in range(20)))
    response = world.get(f"/tools/incidents/{incident}/logs", agent)
    assert response.status_code == 200
    assert len(response.content) <= MAX_RESPONSE_BYTES
    assert response.json()["data"]["truncated"] is True


def test_search_logs_excludes_lines_outside_window(world):
    world.observe("early line before the incident")
    world.clock.advance(40 * 60)
    incident = world.incident()
    agent = world.start(incident)
    lines = world.get(f"/tools/incidents/{incident}/logs", agent).json()["data"]["lines"]
    assert "early line before the incident" not in [line["line"] for line in lines]
    assert len(lines) == 3


def test_get_deploys_lists_recent_and_current_base(world, s1):
    incident, agent = s1
    data = world.get(f"/tools/incidents/{incident}/deploys", agent).json()["data"]
    assert (data["service"], data["window_hours"], data["current_base_sha"]) == (
        "mes-api",
        24,
        BASE_SHA,
    )
    assert [(d["source"], d["base_sha"], d["image_id"]) for d in data["deploys"]] == [
        ("deploy_observed", BASE_SHA, IMAGE_ID)
    ]


def test_old_deploy_is_not_recent_but_still_current_base(world):
    world.deploy()
    world.clock.advance(25 * 3600)
    incident = world.incident()
    agent = world.start(incident)
    deploys = world.get(f"/tools/incidents/{incident}/deploys", agent).json()["data"]
    assert deploys["deploys"] == [] and deploys["current_base_sha"] == BASE_SHA
    view = world.get(f"/tools/incidents/{incident}", agent).json()["data"]
    assert view["features"]["recent_deploy"] is False


def without_request_id(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != "request_id"}


def test_t_auth_02_other_incident_is_hidden_on_every_tool(world, s1):
    incident, agent = s1
    other = world.incident(error_field="defect_id")  # 같은 run의 다른 사건(전역 RUNNING은 1개)
    for suffix in ("", "/logs", "/deploys"):
        hidden = world.get(f"/tools/incidents/{other}{suffix}", agent)
        missing = world.get(f"/tools/incidents/INC-FFFFFFFFFFFF{suffix}", agent)
        assert hidden.status_code == missing.status_code == 404
        assert without_request_id(hidden.json()) == without_request_id(missing.json())
    assert world.get(f"/tools/incidents/{incident}", agent).status_code == 200


def test_finished_attempt_cannot_read_tools(world, s1):
    incident, agent = s1
    world.conn.execute("UPDATE work_items SET status = 'BLOCKED' WHERE id = ?", (agent.work_id,))
    response = world.get(f"/tools/incidents/{incident}", agent)
    assert response.status_code == 404


def test_search_logs_without_log_store_is_503(store, conn, fake_clock):
    world = World(store, conn, fake_clock)
    incident = world.incident()
    agent = world.start(incident)
    api = make_api(store, conn)  # log_store 없음
    response = api.client.get(f"/tools/incidents/{incident}/logs", headers=api.agent(agent))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_inject_s1_records_deploy_observed(store, conn, tmp_path, fake_clock):
    insert_run(conn, RUN)

    def fake_run(argv):
        if argv[:3] == ["docker", "image", "inspect"]:
            return scenarios.CommandResult(0, IMAGE_ID + "\n", "")
        if argv[:2] == ["docker", "run"]:
            return scenarios.CommandResult(0, "d" * 64 + "\n", "")
        if argv[:2] == ["docker", "exec"]:
            return scenarios.CommandResult(0, "200\n", "")
        return scenarios.CommandResult(0, "", "")

    result = scenarios.inject_s1(
        RUN,
        runs_dir=tmp_path,
        run=fake_run,
        clock=fake_clock,
        sleep=lambda s: None,
        store=store,
        base_sha=BASE_SHA,
    )
    assert result["deploy_recorded"] is True
    with store.read() as tx:
        records = deploy_records(tx, RUN, "mes-api", since="")
    assert records == [
        {
            "observed_at": "2026-09-27T00:00:00.000000Z",
            "source": "deploy_observed",
            "service": "mes-api",
            "base_sha": BASE_SHA,
            "image_id": IMAGE_ID,
            "container": f"linemedic-mes-{RUN}",
        }
    ]
    actor = conn.execute("SELECT actor FROM audit_events WHERE event_type = 'DEPLOY_OBSERVED'")
    assert actor.fetchone()[0] == "trusted_harness"
