"""attempt 시험 세계(W13·W15): 승인된 work의 시작 댓글이 접수돼 READY인 상태와 가짜 adapter."""

import json
from pathlib import Path

from linemedic.agent.adapter import AttemptResult
from linemedic.common.config import load_settings
from linemedic.control_plane import supervisor
from linemedic.control_plane.auth import TokenRegistry
from linemedic.control_plane.notifications.github_comment import GitHubCommentAdapter
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import ROUTE_ID, RUN
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run

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
