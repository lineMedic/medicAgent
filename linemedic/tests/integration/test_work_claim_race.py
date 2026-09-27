"""W25 경합 테스트: 여러 스레드가 각자 DB 연결로 같은 Issue를 동시에 선점·승인·시작한다.

- T-STATE-01 / T-ISS-04: 동시 claim·중복 poll·동시 로그 → 활성 work 1,
  `WORK_STARTING` 알림 1, attempt 최대 1
- `one_running_work`: 다른 Issue의 READY work 둘이 동시에 시작해도 RUNNING은 하나(나머지는 대기)
"""

import json
import threading
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane import supervisor
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import (
    Detector,
    problem_fingerprint,
    settings_for_run,
    signature,
)
from linemedic.control_plane.errors import ApiError
from linemedic.control_plane.issue_router import IssueRouter
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.log_store import MemoryLogStore
from linemedic.control_plane.state import Actor, transition_work
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import ROUTE_ID, RUN
from linemedic.tests.helpers.db_rows import count, insert_incident, insert_issue, insert_run

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "linemedic.toml"
REPO, REPO_ID = "demo-team/l3-mes-api", 100001
TRUSTED, BOT = 200001, 900001
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "ISSUE_TRUSTED_AUTHOR_IDS": str(TRUSTED),
    "ISSUE_INTAKE_ENABLED": "true",
}
CONFIG = load_settings(CONFIG_PATH, ENV).config


def run_threads(n: int, target) -> list:
    """모든 스레드가 barrier에서 같이 출발한다. 각 결과(또는 예외)를 돌려준다."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = target(i)
        except Exception as exc:  # 결과로 모아 검사한다
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def accept_start(store, work_id: str) -> None:
    """W26 notifier가 시작 알림 receipt를 확인한 것처럼 한다(테스트 전용)."""
    with store.tx() as tx:
        work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
        tx.execute(
            "UPDATE notifications SET status = 'ACCEPTED', receipt_id = 'receipt-1',"
            " accepted_at = ? WHERE id = ?",
            (tx.now, work["start_notification_id"]),
        )
        transition_work(tx, work_id, work["version"], "READY", Actor.NOTIFIER)


@pytest.mark.parametrize("n", [2, 4, 8])
def test_concurrent_ensure_and_approve_make_one_claim(store, conn, n):
    insert_run(conn, RUN)
    insert_issue(conn, 9)
    incidents = [insert_incident(conn, RUN, "NEW", fingerprint=f"fp-race-{i}") for i in range(n)]

    def claim(i: int):
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incidents[i],))
            issue = tx.one("SELECT * FROM github_issues WHERE issue_number = 9")
            work, _ = supervisor.ensure_work(tx, incident, issue, authorization={"basis": "race"})
        try:
            with store.tx() as tx:
                return supervisor.approve(
                    tx,
                    work["id"],
                    work["version"],
                    issue["snapshot_sha256"],
                    principal=f"operator:{i}",
                    note="동시 승인",
                    route_id=ROUTE_ID,
                )
        except ApiError as exc:
            return exc.code

    results = run_threads(n, claim)
    assert not [r for r in results if isinstance(r, Exception) and not isinstance(r, ApiError)]
    (work,) = conn.execute("SELECT * FROM work_items").fetchall()  # 활성 work 1
    assert work["status"] == "WAITING_NOTIFICATION"
    starts = conn.execute("SELECT * FROM notifications WHERE event_type = 'WORK_STARTING'")
    assert len(starts.fetchall()) == 1  # 시작 알림 1
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(r == "STATE_CONFLICT" for r in results if not isinstance(r, dict))
    linked = json.loads(work["details_json"]).get("linked_incident_ids", [])
    assert sorted(linked + [work["incident_id"]]) == sorted(incidents)  # 나머지 incident는 연결만


@pytest.mark.parametrize("n", [2, 8])
def test_concurrent_start_attempt_issues_at_most_one_attempt(store, conn, fake_clock, n):
    insert_run(conn, RUN)
    insert_issue(conn, 9)
    incident_id = insert_incident(conn, RUN, "NEW")
    with store.tx() as tx:
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        issue = tx.one("SELECT * FROM github_issues WHERE issue_number = 9")
        work, _ = supervisor.ensure_work(tx, incident, issue, authorization={"basis": "race"})
        supervisor.approve(
            tx,
            work["id"],
            work["version"],
            issue["snapshot_sha256"],
            principal="operator:1",
            note="승인",
            route_id=ROUTE_ID,
        )
    accept_start(store, work["id"])
    sup = supervisor.Supervisor(store, config=CONFIG, clock=fake_clock)
    results = run_threads(n, lambda i: sup.start_attempt(work["id"]))
    started = [r for r in results if getattr(r, "status", None) == "started"]
    assert len(started) == 1  # attempt 최대 1
    assert all(getattr(r, "status", None) in ("started", "not_ready") for r in results)
    incident = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    assert (incident["status"], incident["attempt_id"]) == ("INVESTIGATING", started[0].attempt_id)


def test_two_ready_works_of_different_issues_share_one_running_slot(store, conn, fake_clock):
    insert_run(conn, RUN)
    works = []
    for number in (9, 10):
        insert_issue(conn, number)
        incident_id = insert_incident(conn, RUN, "NEW")
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            issue = tx.one("SELECT * FROM github_issues WHERE issue_number = ?", (number,))
            work, _ = supervisor.ensure_work(tx, incident, issue, authorization={"basis": "race"})
            supervisor.approve(
                tx,
                work["id"],
                work["version"],
                issue["snapshot_sha256"],
                principal="operator:1",
                note="승인",
                route_id=ROUTE_ID,
            )
        accept_start(store, work["id"])
        works.append(work["id"])
    sup = supervisor.Supervisor(store, config=CONFIG, clock=fake_clock)
    results = run_threads(2, lambda i: sup.start_attempt(works[i]))
    assert sorted(r.status for r in results) == ["started", "waiting_slot"]  # 대기는 실패가 아님
    assert count(conn, "work_items") == 2
    statuses = sorted(r["status"] for r in conn.execute("SELECT status FROM work_items"))
    assert statuses == ["READY", "RUNNING"]


def test_duplicate_polls_and_concurrent_log_route_start_one_work(store, conn, fake_clock):
    """같은 Issue에 polling(중복)과 로그 router가 동시에 도착한다."""
    insert_run(conn, RUN)
    scope = f"eval:{RUN}"
    intake = CONFIG.issue_intake.model_copy(update={"per_page": 10, "max_pages": 3})
    config = CONFIG.model_copy(update={"issue_intake": intake})
    catalog = Catalog.from_config(config)
    github = FakeGitHub(REPO_ID, REPO, clock=fake_clock, bot_id=BOT, write_enabled=True)
    sup = supervisor.Supervisor(store, config=config, clock=fake_clock)
    sync = IssueSync(
        store,
        github,
        run_id=RUN,
        config=config,
        catalog=catalog,
        clock=fake_clock,
        routing_scope=scope,
        auto_approve=sup.auto_approve,
        on_scope_changed=sup.on_scope_changed,
    )
    router = IssueRouter(store, github, sync, catalog=catalog, clock=fake_clock, wait_seconds=10)
    detector = Detector(
        store, settings_for_run(config, RUN, scope, "mes-api"), fake_clock, MemoryLogStore()
    )
    sync.poll_once()
    fake_clock.advance(61)
    event = {
        "level": "ERROR",
        "service": "mes-api",
        "path": "/defects/summary",
        "error_type": "KeyError",
        "error_field": "inspector_id",
        "top_frame": "app.defects:summarize",
    }
    fp = f"fp-v1:{problem_fingerprint(signature(event, 'mes-api'))}"
    github.add_issue(
        title="보고",
        body=f"### 서비스\n\nmes-api\n\n### 오류 signature\n\n{fp}\n",
        author_id=TRUSTED,
    )
    for n in range(3):
        outcome = detector.observe_line(json.dumps({**event, "request_id": f"{n:032x}"}), "c")
    log_incident = outcome.incident_id

    def act(i: int):
        return router.route(log_incident) if i == 0 else sync.poll_once(wait_seconds=10)

    results = run_threads(4, act)
    assert not [r for r in results if isinstance(r, Exception)]
    sync.poll_once(wait_seconds=10)
    active = conn.execute(
        "SELECT * FROM work_items WHERE status NOT IN ('BLOCKED', 'CANCELLED', 'HANDED_OFF',"
        " 'SUCCEEDED')"
    ).fetchall()
    assert len(active) == 1  # 활성 work 1
    starts = conn.execute("SELECT COUNT(*) FROM notifications WHERE event_type = 'WORK_STARTING'")
    assert starts.fetchone()[0] <= 1  # 시작 알림 최대 1
    assert (
        conn.execute("SELECT COUNT(*) FROM incidents WHERE attempt_id IS NOT NULL").fetchone()[0]
        == 0
    )
