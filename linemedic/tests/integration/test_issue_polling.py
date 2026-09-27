"""W23 통합 테스트: FakeGitHub + 실제 SQLite로 Issue polling·mirror·checkpoint·새 Issue work.

- T-ISS-05: 초기 backlog → 자동 work 0개, 미승인 작성자 새 Issue → WAITING_APPROVAL(자동 수정 0건),
  다른 repo 데이터 없음
- T-ISS-06(일부): closed Issue·사람 assignee·다른 사람 PR → 자동 작업 없음, 충돌 보고
- PR 항목은 mirror에 없음, 10페이지 cap에서 잔여 페이지가 있으면 complete=false
- 두 번째 페이지 실패 → checkpoint 그대로, 재시작 뒤 이어 읽어도 work 중복 없음
- bot 댓글로 updated_at만 바뀌면 snapshot hash가 같고 새 work 없음
"""

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane import checkpoints
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.issue_sync import (
    INTEGRATION_ID,
    STATE_KEY,
    IssueSync,
    _parse,
    snapshot_sha256,
)
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import RUN, make_api
from linemedic.tests.helpers.db_rows import count, insert_run

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "linemedic.toml"
REPO, REPO_ID = "demo-team/l3-mes-api", 100001
TRUSTED, STRANGER, BOT = 200001, 300001, 900001
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "ISSUE_TRUSTED_AUTHOR_IDS": str(TRUSTED),
    "ISSUE_INTAKE_ENABLED": "true",
}


class World:
    def __init__(self, store, conn, clock, *, per_page=10, max_pages=3, env=ENV, full_every=10):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        config = load_settings(CONFIG_PATH, env).config
        intake = config.issue_intake.model_copy(
            update={"per_page": per_page, "max_pages": max_pages}
        )
        self.config = config.model_copy(update={"issue_intake": intake})
        self.catalog = Catalog.from_config(self.config)
        self.github = FakeGitHub(REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=True)
        self.approved: list[str] = []
        self.scope_changed: list[str] = []
        self.full_every = full_every
        self.sync = self.new_sync()

    def new_sync(self) -> IssueSync:
        """프로세스 재시작처럼 새 인스턴스를 만든다(상태는 DB에서 이어진다)."""
        return IssueSync(
            self.store,
            self.github,
            run_id=RUN,
            config=self.config,
            catalog=self.catalog,
            clock=self.clock,
            auto_approve=self.approved.append,
            on_scope_changed=self.scope_changed.append,
            full_every=self.full_every,
        )

    def later(self, seconds=61):
        self.clock.advance(seconds)

    def state(self) -> dict:
        with self.store.read() as tx:
            return checkpoints.get(tx, INTEGRATION_ID, self.sync.state_key) or {}

    def works(self):
        return self.conn.execute("SELECT * FROM work_items ORDER BY issue_number").fetchall()

    def work_for(self, number):
        return self.conn.execute(
            "SELECT * FROM work_items WHERE issue_number = ?", (number,)
        ).fetchone()

    def mirror_numbers(self) -> list[int]:
        rows = self.conn.execute("SELECT issue_number FROM github_issues ORDER BY issue_number")
        return [r["issue_number"] for r in rows]

    def notifications(self, event_type):
        return self.conn.execute(
            "SELECT * FROM notifications WHERE event_type = ?", (event_type,)
        ).fetchall()

    def audit_types(self):
        rows = self.conn.execute("SELECT event_type FROM audit_events ORDER BY seq").fetchall()
        return [r["event_type"] for r in rows]

    def activate(self):
        result = self.sync.poll_once()
        assert result.mode == "initial_import" and result.error is None
        self.later()
        return result


@pytest.fixture
def world(store, conn, fake_clock):
    return World(store, conn, fake_clock)


# ── 최초 관찰 ─────────────────────────────────────────────────


def test_initial_import_mirrors_backlog_but_creates_no_work(store, conn, fake_clock):
    w = World(store, conn, fake_clock, per_page=10, max_pages=10)
    for i in range(50):
        w.github.add_issue(title=f"backlog {i}", author_id=TRUSTED if i % 2 else STRANGER)
    result = w.sync.poll_once()
    assert (result.mode, result.seen, result.complete) == ("initial_import", 50, True)
    assert len(w.mirror_numbers()) == 50
    assert count(conn, "incidents") == 0 and count(conn, "work_items") == 0  # T-ISS-05
    state = w.state()
    assert state["activated_at"] == state["checkpoint"] and state["mirror_complete"] is True
    w.later()
    w.github.update_issue(3, body="backlog 본문 수정")  # 활성화 전 Issue의 변경
    assert w.sync.poll_once().new_works == []
    assert count(conn, "work_items") == 0


def test_backlog_beyond_import_cap_is_still_not_started_when_seen_later(world):
    for i in range(35):  # per_page 10 × max_pages 3: 5개는 최초 관찰에서 빠진다
        world.github.add_issue(title=f"backlog {i}", author_id=TRUSTED)
        world.later(10)
    assert world.sync.poll_once().complete is False
    world.later()
    later = world.sync.poll_once()  # overlap으로 빠졌던 backlog를 처음 본다
    assert later.seen >= 5 and len(world.mirror_numbers()) == 35
    assert count(world.conn, "work_items") == 0 and world.approved == []


def test_mirror_has_only_registered_repo_issues_not_pull_requests(world):
    world.github.add_issue(title="Issue")
    world.github.add_issue(title="PR 항목", is_pull=True)
    world.github.add_issue(title="다른 repo")["repository_url"] = (
        "https://api.github.com/repos/other/repo"
    )
    result = world.sync.poll_once()
    assert (result.skipped_pull_requests, result.skipped_other_repository) == (1, 1)
    assert world.mirror_numbers() == [1]
    payload = json.loads(
        world.conn.execute("SELECT payload_json FROM github_issues").fetchone()["payload_json"]
    )
    assert payload["title"] == "Issue"  # mirror에는 전체 payload


def test_page_cap_with_remaining_pages_is_incomplete(world):
    for i in range(35):  # per_page 10, max_pages 3 → 30개만 읽힘
        world.github.add_issue(title=f"#{i}")
    result = world.sync.poll_once()
    assert (result.pages, result.complete) == (3, False)
    assert len(world.mirror_numbers()) == 30
    assert world.state()["mirror_complete"] is False


# ── 새 Issue ──────────────────────────────────────────────────


def test_new_issue_from_stranger_waits_for_approval_without_auto_start(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=STRANGER)
    result = world.sync.poll_once()
    (work,) = world.works()
    assert result.new_works == [work["id"]] and world.approved == []
    assert (work["status"], work["generation"], work["issue_number"]) == (
        "WAITING_APPROVAL",
        1,
        issue["number"],
    )
    auth = json.loads(work["authorization_json"])
    assert auth == {
        "policy": "trusted_authors",
        "basis": "operator_approval_required",
        "author_id": STRANGER,
        "auto_start_eligible": False,
    }
    incident = world.conn.execute("SELECT * FROM incidents").fetchone()
    assert (incident["source_kind"], incident["status"], incident["count"]) == (
        "GITHUB_ISSUE",
        "NEW",
        0,
    )
    assert incident["fingerprint"] == f"issue:{REPO_ID}:{issue['number']}"
    assert count(world.conn, "executions") == 0  # 자동 수정 0건
    snapshot = world.conn.execute("SELECT snapshot_sha256 FROM github_issues").fetchone()[0]
    assert work["issue_snapshot_sha256"] == snapshot


def test_new_issue_from_trusted_author_is_offered_to_auto_approval(world):
    world.activate()
    world.github.add_issue(title="요청", author_id=TRUSTED)
    result = world.sync.poll_once()
    (work,) = world.works()
    assert world.approved == [work["id"]] == result.new_works
    assert json.loads(work["authorization_json"])["auto_start_eligible"] is True
    assert work["status"] == "WAITING_APPROVAL"  # 승인 전이는 W25 정책이 한다


def test_trusted_author_with_deny_label_is_not_auto_approved(world):
    world.activate()
    world.github.add_issue(title="요청", author_id=TRUSTED, labels=["needs-human"])
    world.sync.poll_once()
    (work,) = world.works()
    assert json.loads(work["authorization_json"])["basis"] == "deny_label"
    assert world.approved == []


def test_human_assignee_blocks_new_issue_with_conflict_report(world):
    world.activate()
    world.github.add_issue(title="요청", author_id=TRUSTED, assignees=[400001])
    result = world.sync.poll_once()
    (work,) = world.works()
    assert result.blocked_works == [work["id"]] and world.approved == []
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "HUMAN_WORK_IN_PROGRESS")
    incident = world.conn.execute("SELECT status FROM incidents").fetchone()
    assert incident["status"] == "ESCALATED"
    (blocked,) = world.notifications("WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert (report["blocker_code"], report["stage"]) == ("HUMAN_WORK_IN_PROGRESS", "intake")


def test_open_pull_request_by_someone_else_referencing_issue_is_human_work(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=TRUSTED)
    world.github.add_pull(title="수정", body=f"Fixes #{issue['number']}", author_id=400001)
    world.sync.poll_once()
    assert world.work_for(issue["number"])["reason_code"] == "HUMAN_WORK_IN_PROGRESS"


def test_our_own_pull_request_or_other_numbers_are_not_human_work(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=TRUSTED)
    number = issue["number"]
    world.github.add_pull(title="봇 PR", body=f"Related to #{number}", author_id=BOT)
    world.github.add_pull(title="다른 PR", body=f"see #{number}0 and a#{number}", author_id=400001)
    world.sync.poll_once()
    assert world.work_for(number)["status"] == "WAITING_APPROVAL"
    assert world.approved == [world.work_for(number)["id"]]


def test_issue_already_closed_when_first_seen_gets_no_work(world):
    world.activate()
    world.github.add_issue(title="바로 닫힘", author_id=TRUSTED, state="closed")
    world.sync.poll_once()
    assert count(world.conn, "work_items") == 0 and count(world.conn, "incidents") == 0


def test_router_created_issue_reuses_existing_work(world):
    world.activate()
    issue = world.github.create_issue("[linemedic] 오류", "router가 만든 Issue").data
    # W24 router: CREATE_ISSUE 응답으로 mirror를 먼저 upsert하고(work FK) incident·work를 만든다
    snapshot = snapshot_sha256(REPO_ID, issue, world.catalog.deny_labels)
    world.conn.execute(
        "INSERT INTO github_issues(repository_id, issue_number, node_id, state, author_id,"
        " created_at, updated_at, snapshot_sha256, payload_json, last_observed_at)"
        " VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, 't')",
        (
            REPO_ID,
            issue["number"],
            issue["node_id"],
            BOT,
            issue["created_at"],
            issue["updated_at"],
            snapshot,
            json.dumps(issue),
        ),
    )
    world.conn.execute(
        "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, status, service, line_id, first_seen, last_seen,"
        " details_json) VALUES ('INC-0000000000AA', ?, 'live', ?, 'fp-log', 'fp-v1', 'LOG',"
        " 'NEW', 'mes-api', 'L3', 't', 't', '{}')",
        (RUN, REPO_ID),
    )
    world.conn.execute(
        "INSERT INTO work_items(id, run_id, incident_id, routing_scope, repository_id,"
        " issue_number, generation, status, issue_snapshot_sha256, authorization_json,"
        " created_at, updated_at, details_json) VALUES ('WORK-0000000000AA', ?,"
        " 'INC-0000000000AA', 'live', ?, ?, 1, 'WAITING_APPROVAL', ?, '{}', 't', 't', '{}')",
        (RUN, REPO_ID, issue["number"], snapshot),
    )
    first = world.sync.poll_once()
    world.later()
    world.github.create_issue_comment(issue["number"], "LineMedic 작업 시작 알림")  # bot 댓글
    second = world.sync.poll_once()
    assert [w["id"] for w in world.works()] == ["WORK-0000000000AA"]  # 같은 work를 쓴다
    assert count(world.conn, "incidents") == 1
    assert first.new_works == second.new_works == [] and world.approved == []
    assert second.scope_changed == [] and "ISSUE_BOT_WITHOUT_WORK" not in world.audit_types()


def test_bot_authored_issue_without_work_is_mirror_only(world):
    world.activate()
    world.github.create_issue("[linemedic-smoke] N11", "smoke", ["linemedic-smoke"])
    world.sync.poll_once()
    assert count(world.conn, "work_items") == 0
    assert "ISSUE_BOT_WITHOUT_WORK" in world.audit_types()


# ── 이미 본 Issue ─────────────────────────────────────────────


def test_bot_comment_changes_updated_at_but_not_snapshot_or_work(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=STRANGER)
    world.sync.poll_once()
    before = world.conn.execute("SELECT updated_at, snapshot_sha256 FROM github_issues").fetchone()
    world.later()
    world.github.create_issue_comment(issue["number"], "LineMedic 작업 시작 알림")
    result = world.sync.poll_once()
    after = world.conn.execute("SELECT updated_at, snapshot_sha256 FROM github_issues").fetchone()
    assert after["updated_at"] > before["updated_at"]
    assert after["snapshot_sha256"] == before["snapshot_sha256"]
    assert count(world.conn, "work_items") == 1
    assert result.scope_changed == [] and world.scope_changed == []


def test_body_change_with_active_work_goes_to_scope_recheck(world):
    world.activate()
    issue = world.github.add_issue(title="요청", body="원래 요구", author_id=STRANGER)
    world.sync.poll_once()
    work = world.works()[0]
    world.later()
    world.github.update_issue(issue["number"], body="바뀐 요구")
    result = world.sync.poll_once()
    assert result.scope_changed == [work["id"]] == world.scope_changed
    assert "ISSUE_SCOPE_CHANGED" in world.audit_types()
    mirror = world.conn.execute("SELECT snapshot_sha256 FROM github_issues").fetchone()[0]
    assert world.work_for(issue["number"])["issue_snapshot_sha256"] != mirror  # 승인 snapshot 유지
    assert count(world.conn, "work_items") == 1


def test_closing_issue_blocks_unstarted_work_without_resolving(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=STRANGER)
    world.sync.poll_once()
    world.later()
    world.github.update_issue(issue["number"], state="closed")
    result = world.sync.poll_once()
    work = world.work_for(issue["number"])
    assert result.blocked_works == [work["id"]]
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "SOURCE_CHANGED")
    incident = world.conn.execute("SELECT status FROM incidents").fetchone()
    assert incident["status"] == "ESCALATED"  # RESOLVED가 아니다(INV-11)
    (blocked,) = world.notifications("WORK_BLOCKED")
    assert json.loads(blocked["payload_json"])["stage"] == "intake"


def test_deny_label_added_later_revokes_waiting_work(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=STRANGER)
    world.sync.poll_once()
    world.later()
    world.github.update_issue(issue["number"], labels=["linemedic-ignore"])
    world.sync.poll_once()
    work = world.work_for(issue["number"])
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "PERMISSION_REQUIRED")


def test_closing_issue_of_started_work_only_requests_cancel(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=TRUSTED)
    world.sync.poll_once()
    work = world.work_for(issue["number"])
    world.conn.execute("UPDATE work_items SET status = 'READY' WHERE id = ?", (work["id"],))
    world.later()
    world.github.update_issue(issue["number"], state="closed")
    result = world.sync.poll_once()
    after = world.work_for(issue["number"])
    assert result.cancel_requested == [work["id"]]
    assert (after["status"], after["cancel_requested"]) == ("READY", 1)  # 안전 경계에서 멈춘다
    assert after["version"] == work["version"] + 1


def test_reopened_issue_does_not_start_again(world):
    world.activate()
    issue = world.github.add_issue(title="요청", author_id=TRUSTED)
    world.sync.poll_once()
    world.later()
    world.github.update_issue(issue["number"], state="closed")
    world.sync.poll_once()
    world.later()
    world.github.update_issue(issue["number"], state="open")
    result = world.sync.poll_once()
    assert result.new_works == [] and count(world.conn, "work_items") == 1
    assert world.work_for(issue["number"])["status"] == "BLOCKED"
    assert "ISSUE_REOPENED" in world.audit_types()


# ── checkpoint·실패·재시작 ────────────────────────────────────


def test_checkpoint_uses_server_time_and_next_since_overlaps(world):
    world.activate()
    first = world.state()["checkpoint"]
    world.github.add_issue(title="새 Issue", author_id=STRANGER)
    world.later(300)  # 서버 시각 경계가 읽은 항목의 최대 시각보다 늦다
    world.sync.poll_once()
    request = [r for r in world.github.requests if r.path.endswith("/issues")][-1]
    expected = _parse(first) - timedelta(seconds=world.config.issue_intake.overlap_seconds)
    assert request.params["since"] == expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (request.params["sort"], request.params["direction"]) == ("updated", "asc")
    assert _parse(world.state()["checkpoint"]) == world.clock.utc_now().replace(microsecond=0)


def test_failure_on_second_page_keeps_checkpoint_and_restart_does_not_duplicate(world):
    world.activate()
    before = world.state()["checkpoint"]
    for i in range(15):  # per_page 10 → 2페이지
        world.github.add_issue(title=f"새 요청 {i}", author_id=TRUSTED)
    world.github.fail_next("forbidden", when=lambda r: r.params.get("page") == 2)
    result = world.sync.poll_once()
    assert result.error == "Forbidden"
    assert world.state()["checkpoint"] == before  # 앞으로 가지 않는다
    assert len(world.works()) == 10  # 1페이지는 저장됐다

    restarted = world.new_sync()  # 재시작
    again = restarted.poll_once()
    assert again.error is None and again.complete is True
    numbers = [w["issue_number"] for w in world.works()]
    assert sorted(numbers) == list(range(1, 16)) and len(set(numbers)) == 15  # 중복 없음
    assert len(world.approved) == 15
    assert _parse(world.state()["checkpoint"]) > _parse(before)


def test_capped_delta_moves_checkpoint_only_to_what_was_read(world):
    world.activate()
    for i in range(35):  # per_page 10 × max_pages 3 = 30개만 한 번에 읽힌다
        world.github.add_issue(title=f"새 요청 {i}", author_id=STRANGER)
        world.later(10)
    world.later(600)
    first = world.sync.poll_once()
    assert (first.complete, len(first.new_works)) == (False, 30)
    assert world.state()["mirror_complete"] is False
    read_max = world.conn.execute("SELECT MAX(updated_at) FROM github_issues").fetchone()[0]
    assert world.state()["checkpoint"] == read_max  # 서버 경계까지 건너뛰지 않는다
    world.later()
    second = world.sync.poll_once()
    assert second.complete is True
    numbers = [w["issue_number"] for w in world.works()]
    assert sorted(numbers) == list(range(1, 36))  # 남은 5개도 읽었고 중복은 없다


def test_overlap_rereads_are_deduplicated(world):
    world.activate()
    world.github.add_issue(title="요청", author_id=STRANGER)
    first = world.sync.poll_once()
    audits = len(world.audit_types())
    world.later(30)  # overlap 120초 안: 같은 항목을 다시 읽는다
    second = world.sync.poll_once()
    assert (first.mirrored, second.seen, second.mirrored) == (1, 1, 0)
    assert len(world.audit_types()) == audits and count(world.conn, "work_items") == 1


def test_rate_limit_backs_off_without_calls_until_retry_after(world):
    world.activate()
    world.github.fail_next("rate_limited")
    limited = world.sync.poll_once()
    assert (limited.error, limited.retry_after) == ("RateLimited", 60)
    calls = len(world.github.requests)
    waiting = world.sync.poll_once()
    assert waiting.mode == "backoff" and len(world.github.requests) == calls  # 빠른 재시도 없음
    assert world.sync.next_wait(waiting) >= 60
    world.later(61)
    assert world.sync.poll_once().error is None


def test_full_reconciliation_runs_periodically_and_uses_etag(store, conn, fake_clock):
    w = World(store, conn, fake_clock, full_every=2)
    w.github.add_issue(title="요청", author_id=STRANGER)
    w.activate()
    w.sync.poll_once()  # polls=1: delta
    w.later()
    full = w.sync.poll_once()  # polls=2: 전체 조회
    assert full.mode == "full" and full.complete is True
    last = [r for r in w.github.requests if r.path.endswith("/issues")][-1]
    assert "since" not in last.params
    w.later()
    w.sync.poll_once()  # delta
    w.later()
    again = w.sync.poll_once()  # 전체 조회: 바뀐 것이 없으면 304
    assert again.mode == "full" and again.complete is True
    assert (again.seen, again.mirrored) == (0, 0)  # 304 페이지는 다시 처리하지 않는다
    last = [r for r in w.github.requests if r.path.endswith("/issues")][-1]
    assert last.headers.get("If-None-Match", "").startswith('W/"')


def test_issue_missing_from_complete_full_pass_blocks_waiting_work(store, conn, fake_clock):
    w = World(store, conn, fake_clock, full_every=2)
    w.activate()
    issue = w.github.add_issue(title="요청", author_id=STRANGER)
    w.sync.poll_once()
    w.github.remove_issue(issue["number"])  # 삭제·이전
    w.later()
    result = w.sync.poll_once()
    assert result.mode == "full"
    assert w.work_for(issue["number"])["reason_code"] == "SOURCE_CHANGED"
    assert "ISSUE_MISSING" in w.audit_types()


def test_intake_disabled_only_plans_work(store, conn, fake_clock):
    w = World(store, conn, fake_clock, env={**ENV, "ISSUE_INTAKE_ENABLED": "false"})
    w.activate()
    w.github.add_issue(title="요청", author_id=TRUSTED)
    result = w.sync.poll_once()
    assert result.planned == [{"issue_number": 1, "basis": "trusted_author"}]
    assert count(conn, "work_items") == 0 and w.mirror_numbers() == [1]


def test_auto_approve_failure_keeps_polling(world):
    world.activate()

    def broken(work_id):
        raise RuntimeError("policy down")

    world.sync.auto_approve = broken
    world.github.add_issue(title="요청", author_id=TRUSTED)
    result = world.sync.poll_once()
    assert result.error is None and len(result.new_works) == 1
    assert "WORK_AUTO_APPROVE_FAILED" in world.audit_types()
    assert world.works()[0]["status"] == "WAITING_APPROVAL"


def test_concurrent_poll_is_refused_as_busy(world):
    assert world.sync._lock.acquire()
    try:
        assert world.sync.poll_once().mode == "busy"
    finally:
        world.sync._lock.release()


def test_poll_loop_runs_until_stopped(world):
    stop = threading.Event()
    stop.set()
    world.sync.run(stop)  # 한 번 조회하고 끝난다
    assert world.state()["activated_at"]


def test_eval_scope_must_match_run(store, conn, fake_clock):
    with pytest.raises(ValueError):
        World(store, conn, fake_clock, env={**ENV, "ROUTING_SCOPE": "eval:r-20260101-000000-beef"})


def test_each_routing_scope_activates_separately(world):
    world.github.add_issue(title="이전 run의 Issue", author_id=TRUSTED)
    world.activate()  # live scope 활성화
    eval_sync = IssueSync(
        world.store,
        world.github,
        run_id=RUN,
        config=world.config,
        catalog=world.catalog,
        clock=world.clock,
        routing_scope=f"eval:{RUN}",
    )
    first = eval_sync.poll_once()
    assert first.mode == "initial_import"  # 새 scope는 관찰부터 다시 시작한다
    assert eval_sync.state_key == f"{STATE_KEY}:eval:{RUN}" != world.sync.state_key
    assert count(world.conn, "work_items") == 0


# ── snapshot hash ─────────────────────────────────────────────


def test_snapshot_hash_uses_only_approval_fields():
    base = {
        "node_id": "I_1",
        "user": {"id": TRUSTED},
        "title": "t",
        "body": "b",
        "state": "open",
        "assignees": [{"id": 2}, {"id": 1}],
        "labels": [{"name": "bug"}, {"name": "needs-human"}],
        "updated_at": "2026-09-27T00:00:00Z",
        "comments": 0,
    }
    deny = {"linemedic-ignore", "needs-human"}
    same = dict(base, updated_at="2026-09-28T00:00:00Z", comments=5, labels=base["labels"][::-1])
    same["assignees"] = base["assignees"][::-1]
    assert snapshot_sha256(REPO_ID, base, deny) == snapshot_sha256(REPO_ID, same, deny)
    other_label = dict(base, labels=[{"name": "needs-human"}])  # 권한과 무관한 label 제거
    assert snapshot_sha256(REPO_ID, base, deny) == snapshot_sha256(REPO_ID, other_label, deny)
    for change in (
        {"body": "b2"},
        {"title": "t2"},
        {"state": "closed"},
        {"assignees": [{"id": 3}]},
        {"labels": []},
        {"user": {"id": STRANGER}},
    ):
        assert snapshot_sha256(REPO_ID, dict(base, **change), deny) != snapshot_sha256(
            REPO_ID, base, deny
        )


# ── 운영 API·CLI ──────────────────────────────────────────────


def sync_body(**overrides):
    return {"schema_version": "linemedic.v4", "run_id": RUN, **overrides}


def post_sync(api, body, key="sync-1"):
    return api.client.post(
        "/ops/integrations/github/sync",
        json=body,
        headers={**api.operator, "Idempotency-Key": key},
    )


def test_ops_sync_runs_one_poll_and_replays_same_key(world):
    api = make_api(world.store, world.conn, issue_sync=world.sync)
    response = post_sync(api, sync_body())
    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "initial_import"
    calls = len(world.github.requests)
    replay = post_sync(api, sync_body())
    assert replay.content == response.content and len(world.github.requests) == calls
    world.later()
    world.github.add_issue(title="요청", author_id=STRANGER)
    second = post_sync(api, sync_body(), key="sync-2")
    assert second.json()["data"]["new_works"] == [world.works()[0]["id"]]


@pytest.mark.parametrize(
    "body",
    [
        sync_body(repository="other/repo"),
        sync_body(url="https://evil.example"),
        sync_body(schema_version="linemedic.v2"),
    ],
)
def test_ops_sync_cannot_choose_repository_or_url(world, body):
    api = make_api(world.store, world.conn, issue_sync=world.sync)
    assert post_sync(api, body).status_code == 422
    assert world.github.requests == []


def test_ops_sync_errors(world):
    api = make_api(world.store, world.conn, issue_sync=world.sync)
    assert post_sync(api, sync_body(run_id="r-20260101-000000-beef")).status_code == 409
    world.github.fail_next("forbidden")
    failed = post_sync(api, sync_body(), key="k-forbidden")
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "LOOKUP_INCOMPLETE"
    world.github.fail_next("rate_limited")
    limited = post_sync(api, sync_body(), key="k-limited")
    assert limited.status_code == 429 and limited.json()["error"]["details"]["retry_after"] == 60
    assert world.sync._lock.acquire()
    try:
        busy = post_sync(api, sync_body(), key="k-busy")
    finally:
        world.sync._lock.release()
    assert busy.status_code == 409
    assert busy.json()["error"]["details"] == {"reason": "sync_in_progress"}
    world.later(61)
    assert post_sync(api, sync_body(), key="k-busy").status_code == 200  # busy 키는 다시 쓸 수 있다
    without = make_api(world.store, world.conn)
    assert post_sync(without, sync_body(), key="k-none").status_code == 503


def test_cli_issue_sync_without_github_settings_is_not_configured(tmp_path, capsys, monkeypatch):
    from linemedic import cli

    for name in ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_BROKER_CREDENTIAL"):
        monkeypatch.delenv(name, raising=False)  # 개발 PC의 실제 값으로 GitHub를 부르지 않게

    code = cli.main(["issue-sync", "--run-id", RUN, "--env-file", str(tmp_path / "none.env")])
    assert code == 2
    assert "NOT_CONFIGURED (G2)" in capsys.readouterr().err
