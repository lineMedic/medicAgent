"""W25 통합 테스트: 승인·scope 재확인·시작 게이트·재시도·취소 (실제 SQLite + 운영 API).

- snapshot hash 불일치 승인 → 409 `ISSUE_SCOPE_CHANGED`, 오래된 version → 409 `STATE_CONFLICT`
- T-V4-02: retry 승인 중복 → 새 generation 1개, 같은 키·다른 body → 409
- T-ISS-06(일부): closed Issue·요구 변경 → 자동 진행 없음(시작 게이트에서 차단)
- 시작 게이트: 시작 알림 ACCEPTED·scope·취소 flag·`one_running_work`.
  늦게 온 receipt로 BLOCKED를 되살리지 않음
- terminal work에 새 로그 → evidence만 추가, 새 generation 자동 생성 없음
- attempt는 `start_attempt`만 만든다
"""

import json
import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from linemedic.common.clock import from_rfc3339
from linemedic.common.config import load_settings
from linemedic.control_plane import supervisor
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import Detector, settings_for_run
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.log_store import MemoryLogStore
from linemedic.control_plane.state import Actor, coupled_transition, transition_work
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import OPERATOR_TOKEN, ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.db_rows import count, insert_incident, insert_issue, insert_run

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "linemedic.toml"
ENV = {
    "GITHUB_REPOSITORY": "demo-team/l3-mes-api",
    "GITHUB_REPOSITORY_ID": "100001",
    "ISSUE_TRUSTED_AUTHOR_IDS": "200001",
    "ISSUE_INTAKE_ENABLED": "true",
}
CONFIG = load_settings(CONFIG_PATH, ENV).config
SNAPSHOT = "0" * 64  # insert_issue의 snapshot


class World:
    def __init__(self, store, conn, clock):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        self.sup = supervisor.Supervisor(store, config=CONFIG, clock=clock, route_id=ROUTE_ID)
        self.api = make_api(store, conn)
        self.issues = 8

    def new_work(self, authorization=None) -> str:
        self.issues += 1
        insert_issue(self.conn, self.issues)
        incident_id = insert_incident(self.conn, RUN, "NEW")
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            issue = tx.one("SELECT * FROM github_issues WHERE issue_number = ?", (self.issues,))
            work, _ = supervisor.ensure_work(
                tx, incident, issue, authorization=authorization or {"basis": "test"}
            )
        return work["id"]

    def work(self, work_id):
        return self.conn.execute("SELECT * FROM work_items WHERE id = ?", (work_id,)).fetchone()

    def incident_of(self, work_id):
        work = self.work(work_id)
        return self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (work["incident_id"],)
        ).fetchone()

    def approve(self, work_id):
        with self.store.tx() as tx:
            supervisor.approve(
                tx,
                work_id,
                self.work(work_id)["version"],
                SNAPSHOT,
                principal="operator:host-operator",
                note="승인",
                route_id=ROUTE_ID,
            )

    def accept(self, work_id):
        with self.store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
            tx.execute(
                "UPDATE notifications SET status = 'ACCEPTED', receipt_id = 'r1', accepted_at = ?"
                " WHERE id = ?",
                (tx.now, work["start_notification_id"]),
            )
            transition_work(tx, work_id, work["version"], "READY", Actor.NOTIFIER)

    def ready(self) -> str:
        work_id = self.new_work()
        self.approve(work_id)
        self.accept(work_id)
        return work_id

    def block(self, work_id, reason="VALIDATION_FAILED"):
        work, incident = self.work(work_id), self.incident_of(work_id)
        with self.store.tx() as tx:
            coupled_transition(
                tx,
                incident_id=incident["id"],
                expected_incident_version=incident["version"],
                incident_to="ESCALATED",
                work_id=work_id,
                expected_work_version=work["version"],
                work_to="BLOCKED",
                # 전이 표: WAITING_APPROVAL → BLOCKED는 router, 알림 대기 이후는 supervisor
                actor=Actor.ROUTER if work["status"] == "WAITING_APPROVAL" else Actor.SUPERVISOR,
                reason=reason,
            )

    def post(self, work_id, action, body, key="k1"):
        return self.api.client.post(
            f"/ops/work-items/{work_id}/{action}",
            json=body,
            headers={**self.api.operator, "Idempotency-Key": key},
        )

    def notifications(self, event_type):
        return self.conn.execute(
            "SELECT * FROM notifications WHERE event_type = ?", (event_type,)
        ).fetchall()


@pytest.fixture
def world(store, conn, fake_clock):
    return World(store, conn, fake_clock)


def approve_body(version, snapshot=SNAPSHOT, **overrides):
    body = {
        "schema_version": "linemedic.v4",
        "expected_work_version": version,
        "expected_issue_snapshot_sha256": snapshot,
        "approval_note": "Issue 내용과 지원 범위를 확인함",
    }
    body.update(overrides)
    return body


# ── 승인 ──────────────────────────────────────────────────────


def test_approve_records_waiting_notification_and_start_intent(world):
    work_id = world.new_work()
    response = world.post(work_id, "approve", approve_body(0))
    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["status"], data["version"]) == ("WAITING_NOTIFICATION", 1)
    work = world.work(work_id)
    assert work["start_notification_id"] == data["start_notification"]["id"]
    (start,) = world.notifications("WORK_STARTING")
    payload = json.loads(start["payload_json"])
    assert (start["status"], payload["issue_snapshot_sha256"]) == ("PENDING", SNAPSHOT)
    assert payload["scope"]["allowed_actions"] == [
        "create_pr",
        "create_work_order_draft",
        "escalate",
    ]
    assert json.loads(work["authorization_json"])["approved"]["by"] == "operator:host-operator"


def test_approve_with_changed_snapshot_is_issue_scope_changed(world):
    work_id = world.new_work()
    response = world.post(work_id, "approve", approve_body(0, snapshot="1" * 64))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ISSUE_SCOPE_CHANGED"
    assert error["details"]["current_snapshot_sha256"] == SNAPSHOT
    assert world.work(work_id)["status"] == "WAITING_APPROVAL"
    assert world.notifications("WORK_STARTING") == []


def test_approve_rejects_stale_version_closed_issue_and_wrong_state(world):
    work_id = world.new_work()
    stale = world.post(work_id, "approve", approve_body(5), key="a")
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current_version"] == 0
    world.conn.execute("UPDATE github_issues SET state = 'closed'")
    closed = world.post(work_id, "approve", approve_body(0), key="b")
    assert closed.json()["error"]["details"] == {"reason": "issue_not_open"}
    world.conn.execute("UPDATE github_issues SET state = 'open'")
    assert world.post(work_id, "approve", approve_body(0), key="c").status_code == 200
    again = world.post(work_id, "approve", approve_body(1), key="d")
    assert again.json()["error"]["details"]["current_status"] == "WAITING_NOTIFICATION"
    assert len(world.notifications("WORK_STARTING")) == 1


def test_approve_idempotency_replay_and_conflict(world):
    work_id = world.new_work()
    first = world.post(work_id, "approve", approve_body(0), key="same")
    replay = world.post(work_id, "approve", approve_body(0), key="same")
    assert replay.content == first.content
    other = world.post(work_id, "approve", approve_body(0, approval_note="다른 메모"), key="same")
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(world.notifications("WORK_STARTING")) == 1


def test_auto_approve_only_for_eligible_trusted_author_work(world):
    eligible = world.new_work({"basis": "trusted_author", "auto_start_eligible": True})
    other = world.new_work({"basis": "operator_approval_required", "auto_start_eligible": False})
    world.sup.auto_approve(eligible)
    world.sup.auto_approve(other)
    assert world.work(eligible)["status"] == "WAITING_NOTIFICATION"
    assert world.work(other)["status"] == "WAITING_APPROVAL"
    auth = json.loads(world.work(eligible)["authorization_json"])
    assert auth["approved"]["by"] == "policy:trusted_authors"
    actors = world.conn.execute(
        "SELECT actor FROM audit_events WHERE event_type = 'WORK_APPROVED'"
    ).fetchall()
    assert [a["actor"] for a in actors] == ["router"]


def test_trusted_new_issue_is_auto_approved_through_issue_sync(world):
    github = FakeGitHub(100001, "demo-team/l3-mes-api", clock=world.clock, bot_id=900001)
    sync = IssueSync(
        world.store,
        github,
        run_id=RUN,
        config=CONFIG,
        catalog=Catalog.from_config(CONFIG),
        clock=world.clock,
        routing_scope=f"eval:{RUN}",
        auto_approve=world.sup.auto_approve,
    )
    sync.poll_once()
    world.clock.advance(61)
    github.add_issue(title="요청", author_id=200001)
    result = sync.poll_once()
    (work_id,) = result.new_works
    assert world.work(work_id)["status"] == "WAITING_NOTIFICATION"
    assert len(world.notifications("WORK_STARTING")) == 1


# ── scope 변경 ────────────────────────────────────────────────


def test_scope_change_blocks_waiting_notification_and_requests_cancel_when_running(world):
    waiting = world.new_work()
    world.sup.on_scope_changed(waiting)
    assert (
        world.work(waiting)["status"] == "WAITING_APPROVAL"
    )  # 재승인 대기(approve가 새 snapshot 요구)
    world.approve(waiting)
    world.sup.on_scope_changed(waiting)
    work = world.work(waiting)
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "ISSUE_SCOPE_CHANGED")
    assert world.incident_of(waiting)["status"] == "ESCALATED"
    (blocked,) = world.notifications("WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert (report["blocker_code"], report["stage"]) == ("SOURCE_CHANGED", "preflight")

    running = world.ready()
    assert world.sup.start_attempt(running).status == "started"
    world.sup.on_scope_changed(running)
    assert (world.work(running)["status"], world.work(running)["cancel_requested"]) == (
        "RUNNING",
        1,
    )


# ── 시작 게이트 ───────────────────────────────────────────────


def test_start_attempt_issues_attempt_in_one_transaction(world):
    work_id = world.ready()
    result = world.sup.start_attempt(work_id)
    assert result.status == "started" and result.tool_call_budget == 15
    work, incident = world.work(work_id), world.incident_of(work_id)
    assert (work["status"], work["attempt_id"]) == ("RUNNING", result.attempt_id)
    assert (incident["status"], incident["attempt_id"], incident["submissions"]) == (
        "INVESTIGATING",
        result.attempt_id,
        0,
    )
    deadline = from_rfc3339(incident["attempt_deadline"])
    assert deadline - world.clock.utc_now() == timedelta(seconds=240)
    assert re.fullmatch(r"ATT-[0-9A-F]{12}", result.attempt_id)


def test_start_attempt_requires_ready_and_accepted_start_notice(world):
    work_id = world.new_work()
    assert world.sup.start_attempt(work_id).reason == "WAITING_APPROVAL"
    world.approve(work_id)
    work = world.work(work_id)
    with world.store.tx() as tx:  # receipt 없이 READY만 된 비정상 상태
        transition_work(tx, work_id, work["version"], "READY", Actor.NOTIFIER)
    result = world.sup.start_attempt(work_id)
    assert (result.status, result.reason) == ("not_ready", "start_notice_unconfirmed")
    assert world.work(work_id)["attempt_id"] is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("UPDATE github_issues SET snapshot_sha256 = '2'", "snapshot_changed"),
        ("UPDATE github_issues SET state = 'closed'", "issue_closed"),
    ],
)
def test_start_attempt_blocks_when_issue_changed_or_closed(world, change, reason):
    work_id = world.ready()
    world.conn.execute(change)
    result = world.sup.start_attempt(work_id)
    assert (result.status, result.reason) == ("blocked", reason)
    work = world.work(work_id)
    assert (work["status"], work["reason_code"], work["attempt_id"]) == (
        "BLOCKED",
        "ISSUE_SCOPE_CHANGED",
        None,
    )


def test_start_attempt_honours_cancel_request(world):
    work_id = world.ready()
    cancel = world.post(work_id, "cancel", cancel_body(world.work(work_id)["version"]))
    assert cancel.json()["data"]["cancel_requested"] is True
    assert world.sup.start_attempt(work_id).status == "cancelled"
    assert world.work(work_id)["status"] == "CANCELLED"
    assert world.incident_of(work_id)["status"] == "ESCALATED"
    assert len(world.notifications("WORK_CANCELLED")) == 1


def test_start_attempt_waits_when_running_slot_is_taken(world):
    first, second = world.ready(), world.ready()
    assert world.sup.start_attempt(first).status == "started"
    waiting = world.sup.start_attempt(second)
    assert (waiting.status, waiting.reason) == ("waiting_slot", first)
    assert world.work(second)["status"] == "READY"  # 실패가 아니다


def test_late_receipt_does_not_revive_blocked_work(world):
    work_id = world.new_work()
    world.approve(work_id)
    world.block(work_id, "START_NOTICE_UNCONFIRMED")
    world.conn.execute("UPDATE notifications SET status = 'ACCEPTED'")  # 늦게 온 receipt
    assert world.sup.start_attempt(work_id).status == "not_ready"
    assert world.work(work_id)["status"] == "BLOCKED"


def test_only_start_attempt_creates_attempt_ids():
    root = REPO_ROOT / "linemedic"
    creators = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "tests" not in path.parts and 'new_id("ATT")' in path.read_text(encoding="utf-8")
    )
    assert creators == ["control_plane/supervisor.py"]
    source = (root / "control_plane" / "supervisor.py").read_text(encoding="utf-8")
    assert source.count('new_id("ATT")') == 1


# ── 재시도 ────────────────────────────────────────────────────


def retry_body(version, **overrides):
    body = {
        "schema_version": "linemedic.v4",
        "expected_work_version": version,
        "blocker_resolution_note": "부족한 자료를 받았다",
    }
    body.update(overrides)
    return body


def test_retry_creates_new_incident_and_generation_once(world):
    work_id = world.new_work()
    world.block(work_id)
    old = world.work(work_id)
    first = world.post(work_id, "retry", retry_body(old["version"]), key="r1")
    assert first.status_code == 200
    data = first.json()["data"]
    new_work = world.work(data["work_id"])
    assert (new_work["status"], new_work["generation"]) == ("WAITING_APPROVAL", 2)
    new_incident = world.conn.execute(
        "SELECT * FROM incidents WHERE id = ?", (data["incident_id"],)
    ).fetchone()
    assert (new_incident["status"], new_incident["reopened_from"]) == ("NEW", old["incident_id"])
    assert json.loads(new_work["authorization_json"])["basis"] == "operator_retry"
    replay = world.post(work_id, "retry", retry_body(old["version"]), key="r1")
    assert replay.content == first.content
    duplicate = world.post(work_id, "retry", retry_body(old["version"]), key="r2")  # 두 번째 승인
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"] == {
        "reason": "already_retried",
        "work_id": data["work_id"],
    }
    conflict = world.post(
        work_id, "retry", retry_body(old["version"], blocker_resolution_note="x"), key="r1"
    )
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert count(world.conn, "work_items") == 2  # 새 generation 1개


def test_retry_with_stale_version_is_state_conflict(world):
    work_id = world.new_work()
    world.block(work_id)
    stale = world.post(work_id, "retry", retry_body(0), key="stale")  # 차단 전에 본 version
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STATE_CONFLICT"
    assert count(world.conn, "work_items") == 1


def test_start_attempt_with_inconsistent_incident_is_not_ready(world):
    work_id = world.ready()
    world.conn.execute(
        "UPDATE incidents SET status = 'ESCALATED' WHERE id = ?",
        (world.work(work_id)["incident_id"],),
    )
    result = world.sup.start_attempt(work_id)  # 예외가 아니라 시작하지 않음
    assert (result.status, result.reason) == ("not_ready", "incident_not_new")
    assert world.work(work_id)["attempt_id"] is None


def test_retry_rejects_non_terminal_work_and_closed_issue(world):
    work_id = world.new_work()
    assert world.post(work_id, "retry", retry_body(0), key="a").status_code == 409
    world.block(work_id)
    world.conn.execute("UPDATE github_issues SET state = 'closed'")
    closed = world.post(work_id, "retry", retry_body(world.work(work_id)["version"]), key="b")
    assert closed.json()["error"]["details"] == {"reason": "issue_not_open"}
    assert count(world.conn, "work_items") == 1


# ── 취소 ──────────────────────────────────────────────────────


def cancel_body(version, **overrides):
    body = {
        "schema_version": "linemedic.v4",
        "expected_work_version": version,
        "cancel_note": "중단",
    }
    body.update(overrides)
    return body


def test_cancel_before_start_cancels_and_escalates_incident(world):
    work_id = world.new_work()
    response = world.post(work_id, "cancel", cancel_body(0))
    assert response.json()["data"]["status"] == "CANCELLED"
    assert world.incident_of(work_id)["status"] == "ESCALATED"
    (cancelled,) = world.notifications("WORK_CANCELLED")
    payload = json.loads(cancelled["payload_json"])
    assert (payload["work_status_before"], payload["side_effect_state"]) == (
        "WAITING_APPROVAL",
        "NONE",
    )
    notified = world.new_work()
    world.approve(notified)
    ok = world.post(notified, "cancel", cancel_body(world.work(notified)["version"]), key="k2")
    assert ok.json()["data"]["status"] == "CANCELLED"


def test_cancel_running_only_requests_and_refuses_unknown_or_review(world):
    running = world.ready()
    world.sup.start_attempt(running)
    requested = world.post(running, "cancel", cancel_body(world.work(running)["version"]))
    assert requested.json()["data"] == {
        "work_id": running,
        "status": "RUNNING",
        "version": world.work(running)["version"],
        "cancel_requested": True,
    }
    for status, reason in (
        ("EXECUTION_UNKNOWN", "external_result_unknown"),
        ("WAITING_REVIEW", "use_incident_escalate"),
    ):
        work_id = world.new_work()
        world.conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, work_id))
        response = world.post(work_id, "cancel", cancel_body(0), key=f"k-{status}")
        assert response.json()["error"]["details"] == {"reason": reason}
    terminal = world.new_work()
    world.block(terminal)
    assert world.post(terminal, "cancel", cancel_body(1), key="k-t").status_code == 409


def test_work_item_view_and_not_found(world):
    work_id = world.new_work()
    world.approve(work_id)
    view = world.api.client.get(f"/ops/work-items/{work_id}", headers=world.api.operator)
    data = view.json()["data"]
    assert data["issue"]["snapshot_sha256"] == SNAPSHOT
    assert data["start_notification"]["status"] == "PENDING"
    for bad in ("WORK-0000000000FF", "not-an-id"):
        assert (
            world.api.client.get(f"/ops/work-items/{bad}", headers=world.api.operator).status_code
            == 404
        )


# ── terminal work와 새 로그 ───────────────────────────────────


def test_new_logs_after_terminal_work_only_add_evidence(world):
    scope = f"eval:{RUN}"
    detector = Detector(
        world.store, settings_for_run(CONFIG, RUN, scope, "mes-api"), world.clock, MemoryLogStore()
    )
    line = json.dumps(
        {
            "level": "ERROR",
            "service": "mes-api",
            "path": "/defects/summary",
            "error_type": "KeyError",
            "top_frame": "app.defects:summarize",
        }
    )
    for _ in range(3):
        outcome = detector.observe_line(line, "c")
    incident_id = outcome.incident_id
    insert_issue(world.conn, 99)
    with world.store.tx() as tx:
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        issue = tx.one("SELECT * FROM github_issues WHERE issue_number = 99")
        work, _ = supervisor.ensure_work(tx, incident, issue, authorization={"basis": "test"})
    world.block(work["id"])
    evidence_before = count(world.conn, "evidence")
    later = detector.observe_line(line, "c")
    assert (later.action, later.incident_id) == ("updated", incident_id)  # terminal 사건에 흡수
    assert count(world.conn, "evidence") == evidence_before + 1
    assert count(world.conn, "work_items") == 1 and count(world.conn, "incidents") == 1


# ── CLI ───────────────────────────────────────────────────────


def bridge(api, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("idempotency-key")))
        headers = {
            k: v for k, v in request.headers.items() if k in ("authorization", "idempotency-key")
        }
        if request.method == "GET":
            response = api.client.get(request.url.path, headers=headers)
        else:
            headers["content-type"] = "application/json"
            response = api.client.post(request.url.path, content=request.content, headers=headers)
        return httpx.Response(response.status_code, content=response.content)

    return httpx.MockTransport(handler)


def test_cli_approve_retry_cancel_call_control_api(world, tmp_path, monkeypatch):
    from linemedic import cli

    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    env = ["--env-file", str(tmp_path / "none")]
    seen: list = []
    work_id = world.new_work()
    args = cli.build_parser().parse_args(
        ["approve-work", "--work-id", work_id, "--expected-version", "0", *env]
    )
    assert cli._work_command(args, transport=bridge(world.api, seen)) == 0
    assert world.work(work_id)["status"] == "WAITING_NOTIFICATION"
    assert seen[-1] == ("POST", f"/ops/work-items/{work_id}/approve", f"approve-work:{work_id}:0")

    world.block(work_id)
    args = cli.build_parser().parse_args(
        ["retry-work", "--work-id", work_id, "--reason", "자료 받음", *env]
    )
    assert cli._work_command(args, transport=bridge(world.api, seen)) == 0
    assert count(world.conn, "work_items") == 2

    fresh = world.new_work()
    args = cli.build_parser().parse_args(["cancel-work", "--work-id", fresh, *env])
    assert cli._work_command(args, transport=bridge(world.api, seen)) == 0
    assert world.work(fresh)["status"] == "CANCELLED"

    stale = cli.build_parser().parse_args(
        ["approve-work", "--work-id", fresh, "--expected-version", "9", *env]
    )
    assert cli._work_command(stale, transport=bridge(world.api, seen)) == 1
    monkeypatch.delenv("CONTROL_OPERATOR_TOKEN")
    assert cli._work_command(stale, transport=bridge(world.api, seen)) == 2
