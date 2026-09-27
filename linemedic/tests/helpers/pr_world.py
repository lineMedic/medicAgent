"""W11 봇 PR 시험 도우미(테스트 전용). 운영 코드에서 쓰지 않는다.

실제 seed mirror·git candidate + scripted runner(R0/R1/R2 통과) + FakeGitHub·FakePusher로
제안 접수부터 PR·조정까지를 한 world에서 돌린다.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from linemedic.common.clock import FakeClock
from linemedic.common.config import load_settings
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.broker.github_pr import PrOpener
from linemedic.control_plane.broker.intake import Broker
from linemedic.control_plane.broker.patch_gate import PatchGate
from linemedic.control_plane.broker.patch_policy import load_policy
from linemedic.control_plane.broker.reconcile import ExecutionReconciler
from linemedic.control_plane.broker.runner import Runner
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.deploys import record_deploy_observed
from linemedic.control_plane.evidence import add_evidence
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.notifications import outbox
from linemedic.integrations.git_push import FakePusher
from linemedic.integrations.github import FakeGitHub
from linemedic.scripts.seed_demo_repo import build_seed_repo
from linemedic.tests.helpers.api import ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.db_rows import NOW, insert_incident, insert_run, insert_work
from linemedic.tests.helpers.runner import Scripted, profile, scripted_docker

REPO_ROOT = Path(__file__).resolve().parents[3]
PATCHES = REPO_ROOT / "linemedic" / "tests" / "fixtures" / "patches"
CONFIG = load_settings(REPO_ROOT / "config" / "linemedic.toml", {}).config
REPO, REPO_ID, BOT = "demo-team/l3-mes-api", 100001, 900001
ATTEMPT = "ATT-00000000000A"
DEADLINE = "2026-09-27T00:20:00.000000Z"
NEW_TEST = "tests/repro/test_missing_inspector.py"
GIT_ENV = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
PASSING = {
    "R0": Scripted(exit_code=0, junit="r0_regression_passed"),
    "R1": Scripted(exit_code=1, junit="r1_keyerror"),
    "R2": Scripted(exit_code=0, junit="r2_all_passed"),
}


def build_seed_mirror(root: Path) -> tuple[Path, str]:
    """결정적 시드로 만든 신뢰 mirror와 base SHA."""
    built = build_seed_repo(root / "seed")
    mirror = root / "mirror" / "l3-mes-api.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(root / "seed"), str(mirror)],
        env=GIT_ENV,
        check=True,
    )
    return mirror, built["commit"]


class PrWorld:
    """승인된 mes-api work가 RUNNING이고 시작 알림이 ACCEPTED인 사건 + 등록 repo(FakeGitHub)."""

    def __init__(
        self,
        store,
        conn,
        clock: FakeClock,
        seed: tuple[Path, str],
        runs_dir: Path,
        *,
        write_enabled: bool = True,
        assignees: tuple[int, ...] = (),
    ) -> None:
        mirror, self.base = seed
        self.store, self.conn, self.clock = store, conn, clock
        insert_run(conn, RUN)
        manifest = {"runtime_env": {"baseline_commit": self.base}}
        conn.execute(
            "UPDATE demo_runs SET config_json = ? WHERE id = ?", (json.dumps(manifest), RUN)
        )
        self.github = FakeGitHub(
            REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=write_enabled
        )
        issue = self.github.add_issue(title="집계 오류", author_id=200001, assignees=assignees)
        self.issue_number = issue["number"]
        self.baseline = f"baseline/{RUN}"
        self.github.branches[self.baseline] = self.base
        catalog = Catalog.from_config(CONFIG)
        self.sync = IssueSync(
            store,
            self.github,
            run_id=RUN,
            config=CONFIG,
            catalog=catalog,
            clock=clock,
            routing_scope=f"eval:{RUN}",
        )
        with store.tx() as tx:
            _, mirror_row, _ = self.sync.upsert_mirror(tx, issue)
        self.incident = insert_incident(
            conn,
            RUN,
            "INVESTIGATING",
            attempt_id=ATTEMPT,
            service="mes-api",
            attempt_deadline=DEADLINE,
            details_json=json.dumps(
                {"signature": {"endpoint": "/defects/summary", "error_type": "KeyError:x"}}
            ),
        )
        self.work = insert_work(
            conn,
            RUN,
            self.incident,
            self.issue_number,
            "RUNNING",
            attempt_id=ATTEMPT,
            issue_snapshot_sha256=mirror_row["snapshot_sha256"],
        )
        with store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (self.work,))
            self.notice = outbox.enqueue(
                tx, work, "WORK_STARTING", {"event_type": "WORK_STARTING"}, ROUTE_ID
            )
            self.evidence = add_evidence(
                tx,
                run_id=RUN,
                incident_id=self.incident,
                kind="log_error",
                observed_at=NOW,
                source_identity="test:1",
                payload={"i": 1},
            )
            record_deploy_observed(
                tx,
                run_id=RUN,
                service="mes-api",
                base_sha=self.base,
                image_id="sha256:" + "1" * 64,
                container="mes",
                container_id="c" * 64,
                actor="test",
            )
        conn.execute(
            "UPDATE notifications SET status = 'ACCEPTED', accepted_at = ? WHERE id = ?",
            (NOW, self.notice),
        )
        conn.execute(
            "UPDATE work_items SET start_notification_id = ? WHERE id = ?", (self.notice, self.work)
        )
        knowledge = KnowledgeBase()
        self.pusher = FakePusher(self.github)
        self.opener = PrOpener(store, self.github, self.pusher, self.sync, route_id=ROUTE_ID)
        self.reconciler = ExecutionReconciler(store, opener=self.opener, route_id=ROUTE_ID)
        self.api = make_api(
            store,
            conn,
            catalog=catalog,
            knowledge=knowledge,
            execution_reconciler=self.reconciler,
        )
        self.docker = scripted_docker(PASSING)
        gate = PatchGate(
            policy=load_policy(),
            runner=Runner(self.docker, profile(), clock),
            mirror=mirror,
            runs_dir=runs_dir,
        )
        self.broker = Broker(
            store,
            catalog,
            load_manual_templates(knowledge),
            ROUTE_ID,
            patch_gate=gate,
            pr_opener=self.opener,
        )
        self.principal = AgentPrincipal(RUN, self.incident, self.work, ATTEMPT)
        self.headers = self.api.agent(self.principal)

    def submit(self, key: str = "pr-1", **action: Any) -> str:
        body = {
            "schema_version": "linemedic.v4",
            "run_id": RUN,
            "incident_id": self.incident,
            "work_id": self.work,
            "attempt_id": ATTEMPT,
            "category": "code_bug",
            "summary": "inspector_id가 없는 record에서 KeyError가 난다",
            "evidence_ids": [self.evidence],
            "action": {
                "type": "create_pr",
                "base_sha": self.base,
                "root_cause_hypothesis": "필수라고 가정한 필드를 직접 조회한다",
                "diff": (PATCHES / "fix_missing_inspector.patch").read_text(encoding="utf-8"),
                "new_test_path": NEW_TEST,
                **action,
            },
        }
        response = self.api.client.post(
            "/tools/proposals", json=body, headers={**self.headers, "Idempotency-Key": key}
        )
        assert response.status_code == 202, response.text
        return response.json()["data"]["proposal_id"]

    def run(self, key: str = "pr-1", **action: Any) -> str:
        proposal_id = self.submit(key, **action)
        self.broker.process_pending()
        return proposal_id

    def proposal(self, proposal_id: str) -> tuple[Any, dict]:
        row = self.conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return row, json.loads(row["checks_json"])

    def execution(self) -> Any:
        return self.conn.execute(
            "SELECT * FROM executions WHERE operation = 'CREATE_PR'"
        ).fetchone()

    def incident_row(self) -> Any:
        return self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (self.incident,)
        ).fetchone()

    def work_row(self) -> Any:
        return self.conn.execute("SELECT * FROM work_items WHERE id = ?", (self.work,)).fetchone()

    def notifications(self, event_type: str) -> list[Any]:
        return self.conn.execute(
            "SELECT * FROM notifications WHERE event_type = ?", (event_type,)
        ).fetchall()

    def pull_posts(self) -> int:
        return sum(
            1 for r in self.github.requests if r.method == "POST" and r.path.endswith("/pulls")
        )
