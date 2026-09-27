"""W13 attempt 실행: 시작 게이트 뒤에만 workspace·context·token을 만들고 adapter를 부른다.

- workspace는 base commit의 파일만(git 이력 없음), context에는 서버가 확인한 문맥만(credential 없음)
- token은 그 attempt 범위로 발급해 adapter에게만 주고, 끝나면(멈춰도) 폐기한다
- adapter가 제안 없이 끝나면 attempt를 닫는다: 거절 → VALIDATION_FAILED, 제안 없음 →
  INSUFFICIENT_EVIDENCE, 오류 → MODEL_UNAVAILABLE, deadline → BUDGET_EXCEEDED
  (멈춘 adapter는 기다리지 않는다)
- 게이트가 열리지 않았거나 base가 없으면 workspace·token·adapter 호출이 없다
- 재시작 때 끊긴 attempt, deadline이 지난 attempt, adapter가 끝난 뒤 조사로 돌아온 attempt를 닫는다
"""

import json
import threading
from pathlib import Path

import pytest

from linemedic.agent.adapter import AttemptResult
from linemedic.common.config import load_settings
from linemedic.control_plane import supervisor
from linemedic.control_plane.attempts import attempt_origin
from linemedic.control_plane.auth import AgentPrincipal, TokenRegistry
from linemedic.control_plane.notifications.github_comment import GitHubCommentAdapter
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.control_plane.state import transition_incident
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import ROUTE_ID, RUN
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run
from linemedic.tests.helpers.pr_world import build_seed_mirror

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO, REPO_ID, BOT = "demo-team/l3-mes-api", 100001, 900001
CONFIG = load_settings(
    REPO_ROOT / "config" / "linemedic.toml",
    {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)},
).config


class FakeAdapter:
    name = "fake"
    origin = "manual_integration"

    def __init__(self, tokens: TokenRegistry, behavior=None) -> None:
        self.tokens = tokens
        self.behavior = behavior or (lambda call: AttemptResult("no_proposal", "fake", self.origin))
        self.calls: list[dict] = []

    def run_agent(self, run_id, incident_id, work_id, attempt_id, deadline, workspace_ref,
                  context_ref, *, credential):  # fmt: skip
        call = {
            "ids": (run_id, incident_id, work_id, attempt_id),
            "deadline": deadline,
            "workspace": workspace_ref,
            "context": context_ref,
            "credential": credential,
            "principal": self.tokens.resolve(credential),
        }
        self.calls.append(call)
        return self.behavior(call)


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


class World:
    """승인된 work의 시작 댓글이 접수돼 READY인 상태(실제 시작 게이트)."""

    def __init__(self, store, conn, clock, seed, tmp_path, *, baseline=True):
        mirror, self.base = seed
        self.store, self.conn, self.clock = store, conn, clock
        insert_run(conn, RUN)
        manifest = {"runtime_env": {"baseline_commit": self.base}} if baseline else {}
        conn.execute(
            "UPDATE demo_runs SET config_json = ? WHERE id = ?", (json.dumps(manifest), RUN)
        )
        self.github = FakeGitHub(REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=True)
        issue = self.github.add_issue(title="요청", author_id=200001)
        insert_issue(conn, issue["number"])
        self.incident_id = insert_incident(conn, RUN, "NEW")
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (self.incident_id,))
            mirror_row = tx.one("SELECT * FROM github_issues")
            work, _ = supervisor.ensure_work(tx, incident, mirror_row, authorization={"b": 1})
            supervisor.approve(
                tx,
                work["id"],
                work["version"],
                mirror_row["snapshot_sha256"],
                principal="operator:host-operator",
                note="승인",
                route_id=ROUTE_ID,
            )
        self.work_id = work["id"]
        OutboxWorker(
            store,
            adapters={"github_comment": GitHubCommentAdapter(self.github)},
            config=CONFIG,
            repo=REPO,
            clock=clock,
        ).process_pending()
        clock.advance(3)
        self.tokens = TokenRegistry()
        self.adapter = FakeAdapter(self.tokens)
        self.runs_dir = tmp_path / "runs"
        self.runtime = supervisor.AttemptRuntime(
            adapter=self.adapter,
            tokens=self.tokens,
            mirror=mirror,
            runs_dir=self.runs_dir,
            tools_base_url="http://127.0.0.1:8080",
            grace_seconds=0,
            poll_seconds=0.01,
        )
        self.sup = supervisor.Supervisor(store, config=CONFIG, clock=clock, runtime=self.runtime)

    def work(self):
        return self.conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (self.work_id,)
        ).fetchone()

    def incident(self):
        return self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (self.incident_id,)
        ).fetchone()

    def blocked(self) -> dict:
        (row,) = self.conn.execute(
            "SELECT payload_json FROM notifications WHERE event_type = 'WORK_BLOCKED'"
        ).fetchall()
        return json.loads(row[0])

    def audit(self, event_type: str) -> list[dict]:
        return [
            json.loads(r[0])
            for r in self.conn.execute(
                "SELECT payload_json FROM audit_events WHERE event_type = ? ORDER BY seq",
                (event_type,),
            )
        ]


@pytest.fixture
def world(store, conn, fake_clock, seed, tmp_path):
    return World(store, conn, fake_clock, seed, tmp_path)


def test_attempt_gets_base_workspace_context_and_a_scoped_token(world):
    assert world.work()["status"] == "READY"
    (result,) = world.sup.run_ready()
    (call,) = world.adapter.calls
    attempt_id = world.work()["attempt_id"]
    assert call["ids"] == (RUN, world.incident_id, world.work_id, attempt_id)
    assert call["principal"] == AgentPrincipal(RUN, world.incident_id, world.work_id, attempt_id)
    assert world.tokens.resolve(call["credential"]) is None  # 끝나면 폐기
    root = world.runs_dir / RUN / "workspaces" / attempt_id
    assert call["workspace"] == root / "repo" and call["context"] == root / "context.json"
    defects = (root / "repo" / "app" / "defects.py").read_text(encoding="utf-8")
    assert 'row["inspector_id"]' in defects  # base(버그) 코드
    assert not (root / "repo" / ".git").exists() and not (root / "source").exists()
    context = json.loads(call["context"].read_text(encoding="utf-8"))
    assert context["base"] == {"sha": world.base, "service": "mes-api"}
    notice = world.conn.execute(
        "SELECT id, receipt_id FROM notifications WHERE id = ?",
        (world.work()["start_notification_id"],),
    ).fetchone()
    assert (context["start_notice"]["notification_id"], context["start_notice"]["receipt_id"]) == (
        notice["id"],
        notice["receipt_id"],
    )
    assert context["issue"]["number"] == world.work()["issue_number"]
    assert context["memory"]["mode"] == "cold_start" and context["origin"] == "manual_integration"
    assert call["credential"] not in call["context"].read_text(encoding="utf-8")
    (started,) = world.audit("ATTEMPT_STARTED")
    assert (started["adapter"], started["origin"]) == ("fake", "manual_integration")
    with world.store.read() as tx:
        assert attempt_origin(tx, RUN, world.incident_id, attempt_id) == "manual_integration"
    (finished,) = world.audit("ATTEMPT_FINISHED")
    assert (finished["attempt_id"], finished["status"]) == (attempt_id, "no_proposal")
    assert (result["adapter_status"], result["blocked"]) == ("no_proposal", "INSUFFICIENT_EVIDENCE")
    assert (world.incident()["status"], world.work()["status"]) == ("ESCALATED", "BLOCKED")
    report = world.blocked()
    assert (report["blocker_code"], report["stage"]) == ("INSUFFICIENT_EVIDENCE", "agent")


@pytest.mark.parametrize(
    ("result", "blocker"),
    [
        (
            AttemptResult("decided", "fake", "manual_integration", decision="REJECTED"),
            "VALIDATION_FAILED",
        ),  # fmt: skip
        (AttemptResult("error", "fake", "manual_integration", detail="x"), "MODEL_UNAVAILABLE"),
        (AttemptResult("deadline_exceeded", "fake", "manual_integration"), "BUDGET_EXCEEDED"),
        (AttemptResult("closed", "fake", "manual_integration"), "MODEL_UNAVAILABLE"),
    ],
)
def test_attempt_that_ends_while_investigating_is_closed(world, result, blocker):
    world.adapter.behavior = lambda call: result
    (summary,) = world.sup.run_ready()
    assert summary["blocked"] == blocker
    assert world.incident()["reason_code"] == blocker and world.work()["status"] == "BLOCKED"
    assert world.blocked()["blocker_code"] == blocker


def test_adapter_exception_is_recorded_and_closes_the_attempt(world):
    def boom(call):
        raise RuntimeError("adapter crashed")

    world.adapter.behavior = boom
    (summary,) = world.sup.run_ready()
    (finished,) = world.audit("ATTEMPT_FINISHED")
    assert (finished["status"], finished["detail"]) == ("error", "RuntimeError")
    assert summary["blocked"] == "MODEL_UNAVAILABLE"
    assert world.tokens.resolve(world.adapter.calls[0]["credential"]) is None


def test_attempt_whose_proposal_moved_on_is_not_closed(world):
    def submit(call):  # 제안이 접수돼 브로커가 검사 중이다(INVESTIGATING → VALIDATING)
        with world.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (world.incident_id,))
            transition_incident(
                tx, incident["id"], incident["version"], "VALIDATING", supervisor.Actor.BROKER
            )
        return AttemptResult("deadline_exceeded", "fake", "manual_integration")

    world.adapter.behavior = submit
    (summary,) = world.sup.run_ready()
    assert summary["blocked"] is None
    assert (world.incident()["status"], world.work()["status"]) == ("VALIDATING", "RUNNING")


def test_hanging_adapter_is_abandoned_after_the_deadline(world):
    release = threading.Event()

    def hang(call):
        world.clock.advance(300)  # deadline(240초)을 넘겼는데 돌아오지 않는다
        release.wait(10)
        return AttemptResult("decided", "fake", "manual_integration", decision="ALLOWED")

    world.adapter.behavior = hang
    try:
        (summary,) = world.sup.run_ready()
        assert (summary["adapter_status"], summary["blocked"]) == (
            "deadline_exceeded",
            "BUDGET_EXCEEDED",
        )
        assert world.tokens.resolve(world.adapter.calls[0]["credential"]) is None
        (finished,) = world.audit("ATTEMPT_FINISHED")
        assert finished["detail"] == "adapter_timeout"
    finally:
        release.set()


def test_no_workspace_or_token_without_the_start_gate(world):
    world.conn.execute(
        "UPDATE notifications SET route_id = 'ops-mail' WHERE id = ?",
        (world.work()["start_notification_id"],),
    )
    assert world.sup.run_ready() == [{"work_id": world.work_id, "status": "not_ready"}]
    assert world.adapter.calls == [] and not (world.runs_dir / RUN / "workspaces").exists()
    assert world.work()["attempt_id"] is None


def test_missing_run_baseline_closes_without_calling_the_adapter(
    store, conn, fake_clock, seed, tmp_path
):
    world = World(store, conn, fake_clock, seed, tmp_path, baseline=False)
    (summary,) = world.sup.run_ready()
    assert world.adapter.calls == []
    (finished,) = world.audit("ATTEMPT_FINISHED")
    assert finished["detail"] == "workspace:run_baseline_unconfigured"
    assert summary["blocked"] == "MODEL_UNAVAILABLE"


def test_waiting_slot_stops_the_scan(world, conn):
    other = insert_incident(conn, RUN, "INVESTIGATING")
    insert_issue(conn, 99)
    conn.execute(
        "INSERT INTO work_items(id, run_id, incident_id, routing_scope, repository_id,"
        " issue_number, generation, status, issue_snapshot_sha256, authorization_json,"
        " created_at, updated_at, details_json) VALUES ('WORK-0000000000FF', ?, ?, 'eval:x',"
        " ?, 99, 1, 'RUNNING', ?, '{}', '2026-01-01T00:00:00.000000Z',"
        " '2026-01-01T00:00:00.000000Z', '{}')",
        (RUN, other, REPO_ID, "a" * 64),
    )
    assert world.sup.run_ready() == [{"work_id": world.work_id, "status": "waiting_slot"}]
    assert world.adapter.calls == []


def test_expired_or_finished_attempts_are_closed(world):
    started = world.sup.start_attempt(world.work_id)  # adapter 없이 attempt만 있다
    assert world.sup.expire_attempts() == []
    world.clock.advance(241)
    assert world.sup.expire_attempts() == [world.work_id]
    assert world.incident()["reason_code"] == "BUDGET_EXCEEDED"
    assert started.status == "started"


def test_attempt_back_to_investigating_after_the_adapter_finished_is_closed(world):
    started = world.sup.start_attempt(world.work_id)
    with world.store.tx() as tx:  # 제안 접수 → 브로커 검사 중
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (world.incident_id,))
        transition_incident(
            tx, incident["id"], incident["version"], "VALIDATING", supervisor.Actor.BROKER
        )
    finished = world.sup.finish_attempt(
        started, AttemptResult("deadline_exceeded", "fake", "manual_integration")
    )
    assert finished["blocked"] is None  # 검사 중에는 닫지 않는다
    assert world.sup.expire_attempts() == []
    with world.store.tx() as tx:  # 브로커가 거절하고 수정 1회를 허용했지만 adapter는 끝났다
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (world.incident_id,))
        transition_incident(
            tx, incident["id"], incident["version"], "INVESTIGATING", supervisor.Actor.BROKER
        )
    assert world.sup.expire_attempts() == [world.work_id]
    assert world.incident()["reason_code"] == "VALIDATION_FAILED"


def test_restart_closes_the_interrupted_attempt(world):
    world.sup.start_attempt(world.work_id)  # adapter 실행 중 프로세스가 죽었다
    assert world.sup.recover_attempts() == [world.work_id]
    assert (world.incident()["status"], world.incident()["reason_code"]) == (
        "ESCALATED",
        "MODEL_UNAVAILABLE",
    )
    assert world.blocked()["stage"] == "agent"
    assert world.sup.recover_attempts() == []
