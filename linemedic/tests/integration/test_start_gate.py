"""W26 시작 게이트: 시작 알림이 실제로 접수(receipt 저장)된 뒤에만 attempt가 생긴다 (T-NOT-01).

- receipt 전 `start_attempt` 거부, receipt 뒤 READY → 시작. receipt 시각 ≤ attempt 시작이 기록된다
- 60초 안에 접수되지 않거나 명확히 실패하면 BLOCKED(`START_NOTICE_UNCONFIRMED`)·incident ESCALATED
- 늦게 온 receipt(조정 FOUND)는 끝난 work를 되살리지 않는다
- shadow 모드(쓰기 꺼짐)에서는 게이트가 열리지 않는다
- 차단된 work의 시작 알림은 나중에 쓰기를 켜도 보내지 않는다(expire가 닫고 worker도 막는다)
"""

import json
from pathlib import Path

import pytest

from linemedic.common.clock import from_rfc3339
from linemedic.common.config import load_settings
from linemedic.control_plane import supervisor
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


class Gate:
    def __init__(self, store, conn, clock, *, write_enabled=True):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        self.github = FakeGitHub(
            REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=write_enabled
        )
        issue = self.github.add_issue(title="요청", author_id=200001)
        insert_issue(conn, issue["number"])
        incident_id = insert_incident(conn, RUN, "NEW")
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            mirror = tx.one("SELECT * FROM github_issues")
            work, _ = supervisor.ensure_work(tx, incident, mirror, authorization={"basis": "test"})
            supervisor.approve(
                tx,
                work["id"],
                work["version"],
                mirror["snapshot_sha256"],
                principal="operator:host-operator",
                note="승인",
                route_id=ROUTE_ID,
            )
        self.work_id, self.incident_id, self.issue_number = work["id"], incident_id, issue["number"]
        self.sup = supervisor.Supervisor(store, config=CONFIG, clock=clock)
        self.worker = OutboxWorker(
            store,
            adapters={"github_comment": GitHubCommentAdapter(self.github)},
            config=CONFIG,
            repo=REPO,
            clock=clock,
        )

    def work(self):
        return self.conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (self.work_id,)
        ).fetchone()

    def incident(self):
        return self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (self.incident_id,)
        ).fetchone()

    def notice(self):
        return self.conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (self.work()["start_notification_id"],)
        ).fetchone()

    def comments(self):
        return self.github.comments.get(self.issue_number, [])


@pytest.fixture
def gate(store, conn, fake_clock):
    return Gate(store, conn, fake_clock)


def test_attempt_is_refused_before_receipt_and_starts_after(gate):
    before = gate.sup.start_attempt(gate.work_id)
    assert (before.status, gate.incident()["attempt_id"]) == ("not_ready", None)  # attempt 0
    gate.clock.advance(5)
    (sent,) = gate.worker.process_pending()
    assert (sent["status"], sent["start_gate"]) == ("ACCEPTED", "ready")
    notice = gate.notice()
    assert notice["receipt_id"] == str(gate.comments()[0]["id"])
    assert gate.work()["status"] == "READY"
    gate.clock.advance(3)
    started = gate.sup.start_attempt(gate.work_id)
    assert started.status == "started"
    audit = gate.conn.execute(
        "SELECT created_at, payload_json FROM audit_events WHERE event_type = 'ATTEMPT_STARTED'"
    ).fetchone()
    payload = json.loads(audit["payload_json"])
    assert payload["start_notice_accepted_at"] == notice["accepted_at"]  # provider 시각
    recorded_at = payload["start_notice_recorded_at"]
    assert recorded_at == json.loads(notice["result_json"])["recorded_at"]
    assert from_rfc3339(recorded_at) < from_rfc3339(audit["created_at"])  # receipt < attempt 시작


def test_start_notice_not_accepted_in_time_blocks_without_attempt(gate):
    gate.github.fail_next("timeout", after_side_effect=False)
    (sent,) = gate.worker.process_pending()
    assert sent["status"] == "UNKNOWN"
    gate.clock.advance(30)
    assert gate.sup.expire_start_notices() == []  # 아직 60초 전
    gate.clock.advance(31)
    assert gate.sup.expire_start_notices() == [gate.work_id]
    work = gate.work()
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "START_NOTICE_UNCONFIRMED")
    assert gate.incident()["status"] == "ESCALATED"
    blocked = gate.conn.execute(
        "SELECT payload_json FROM notifications WHERE event_type = 'WORK_BLOCKED'"
    ).fetchone()
    assert json.loads(blocked["payload_json"])["blocker_code"] == "START_NOTICE_UNCONFIRMED"
    assert gate.sup.start_attempt(gate.work_id).status == "not_ready"
    assert gate.incident()["attempt_id"] is None


def test_failed_start_notice_blocks_immediately(gate):
    gate.github.fail_next("forbidden")
    (sent,) = gate.worker.process_pending()
    assert sent["status"] == "FAILED"
    assert gate.sup.expire_start_notices() == [gate.work_id]  # 기다리지 않는다


def test_late_receipt_after_timeout_does_not_revive_work(gate):
    gate.github.fail_next("timeout", after_side_effect=True)  # 댓글은 생겼지만 응답이 끊겼다
    gate.worker.process_pending()
    gate.clock.advance(61)
    gate.sup.expire_start_notices()
    found = gate.worker.reconcile(gate.notice()["id"])
    assert (found["outcome"], found["start_gate"]) == ("FOUND", "late")
    assert gate.notice()["status"] == "ACCEPTED"
    assert gate.work()["status"] == "BLOCKED"  # 되살리지 않는다
    events = [r[0] for r in gate.conn.execute("SELECT event_type FROM audit_events")]
    assert "LATE_START_RECEIPT" in events
    assert gate.sup.start_attempt(gate.work_id).status == "not_ready"


def test_receipt_after_the_wait_does_not_open_the_gate_before_expiry_runs(gate):
    """만료 검사가 아직 돌지 않았어도(루프 지연) 60초 뒤 receipt로 READY가 되지 않는다.

    PR #53 리뷰 재현.
    """
    gate.github.fail_next("timeout", after_side_effect=True)
    gate.worker.process_pending()
    gate.clock.advance(61)  # expire_start_notices()를 부르지 않는다
    found = gate.worker.reconcile(gate.notice()["id"])
    assert (found["outcome"], found["start_gate"]) == ("FOUND", "late")
    assert gate.notice()["status"] == "ACCEPTED"
    assert gate.work()["status"] == "WAITING_NOTIFICATION"  # READY로 올리지 않는다
    (late,) = [
        json.loads(r[0])
        for r in gate.conn.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = 'LATE_START_RECEIPT'"
        )
    ]
    assert late["reason"] == "start_wait_exceeded"
    assert gate.sup.start_attempt(gate.work_id).status == "not_ready"
    assert gate.sup.expire_start_notices() == [gate.work_id]  # 늦게 접수된 것도 멈춘다
    work = gate.work()
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "START_NOTICE_UNCONFIRMED")
    assert gate.incident()["attempt_id"] is None


def test_start_notice_past_the_wait_is_not_sent_even_before_expiry_runs(gate):
    """발송이 늦어진 시작 알림은 60초가 지나면 worker가 보내지 않는다(작업 시작 예정 댓글 없음)."""
    gate.clock.advance(61)  # expire_start_notices()를 부르지 않는다
    sent = gate.worker.process_pending()
    assert "WORK_STARTING" not in [s["event_type"] for s in sent]
    assert gate.comments() == []
    notice = gate.notice()
    assert notice["status"] == "FAILED"
    assert json.loads(notice["result_json"])["last"] == {"error": "expired_before_send"}
    assert gate.work()["status"] == "WAITING_NOTIFICATION"
    assert gate.sup.expire_start_notices() == [gate.work_id]  # FAILED → 바로 멈춘다


def test_start_notice_sent_within_the_wait_still_opens_the_gate(gate):
    gate.clock.advance(59)
    (sent,) = gate.worker.process_pending()
    assert (sent["status"], sent["start_gate"]) == ("ACCEPTED", "ready")
    assert gate.work()["status"] == "READY"
    gate.clock.advance(30)  # 접수 뒤에는 만료 대상이 아니다
    assert gate.sup.expire_start_notices() == []
    assert gate.sup.start_attempt(gate.work_id).status == "started"


def test_start_attempt_requires_the_required_route(gate):
    gate.worker.process_pending()
    gate.conn.execute(
        "UPDATE notifications SET route_id = 'ops-mail' WHERE id = ?", (gate.notice()["id"],)
    )
    result = gate.sup.start_attempt(gate.work_id)
    assert (result.status, result.reason) == ("not_ready", "start_notice_unconfirmed")


def test_shadow_mode_never_opens_the_gate(store, conn, fake_clock):
    g = Gate(store, conn, fake_clock, write_enabled=False)
    assert g.worker.process_pending() == []  # 보내지 않고 PENDING으로 둔다
    assert g.notice()["status"] == "PENDING" and g.github.write_calls == 0
    fake_clock.advance(61)
    assert g.sup.expire_start_notices() == [g.work_id]
    assert g.sup.start_attempt(g.work_id).status == "not_ready"


def test_expired_start_notice_is_not_sent_after_writes_are_enabled(store, conn, fake_clock):
    """shadow로 막힌 work에 G10 뒤 "작업 시작 예정" 댓글이 달리지 않는다(PR #48 리뷰 재현)."""
    g = Gate(store, conn, fake_clock, write_enabled=False)
    fake_clock.advance(61)
    assert g.sup.expire_start_notices() == [g.work_id]
    notice = g.notice()
    assert notice["status"] == "FAILED"
    assert json.loads(notice["result_json"])["last"] == {"error": "expired_before_send"}
    g.github.write_enabled = True  # G10에서 쓰기를 켠다
    sent = g.worker.process_pending()
    assert [s["event_type"] for s in sent] == ["WORK_BLOCKED"]
    bodies = [c["body"] for c in g.comments()]
    assert len(bodies) == 1
    assert "진행 중단" in bodies[0] and "작업 시작 예정" not in bodies[0]
    assert g.notice()["status"] == "FAILED"
    failed = [
        json.loads(r[0])
        for r in g.conn.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = 'NOTIFICATION_FAILED'"
        )
    ]
    assert {"notification_id": notice["id"], "event_type": "WORK_STARTING"}.items() <= (
        failed[0].items()
    )


def test_start_notice_of_work_no_longer_waiting_is_not_claimed(store, conn, fake_clock):
    """expire가 아닌 경로로 멈춘 work의 PENDING 시작 알림도 worker가 보내지 않는다."""
    g = Gate(store, conn, fake_clock, write_enabled=False)
    g.sup.on_scope_changed(g.work_id)  # 시작 전 Issue 변경 → BLOCKED(시작 알림은 PENDING 그대로)
    assert g.work()["status"] == "BLOCKED" and g.notice()["status"] == "PENDING"
    g.github.write_enabled = True
    sent = g.worker.process_pending()
    assert "WORK_STARTING" not in [s["event_type"] for s in sent]
    assert not any("작업 시작 예정" in c["body"] for c in g.comments())
    notice = g.notice()
    assert notice["status"] == "FAILED"
    assert json.loads(notice["result_json"])["last"] == {
        "error": "work_not_waiting",
        "work_status": "BLOCKED",
    }
