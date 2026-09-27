"""N12·S6(W26) live: 선택 route(GitHub Issue 댓글)의 실제 접수·강제 timeout·미전송 (G2·G10 필요).

사람이 고른 전용 repo의 open Issue에 댓글을 3개 남긴다(시작 알림 1, S6 차단 알림 1, 강제 timeout 1).
그래서 G10 `write_enabled=true`, 이번 실행의 허락 표시 `LINEMEDIC_CONFIRM_GITHUB_WRITE=1`,
bound Issue 번호 `LINEMEDIC_LIVE_NOTIFY_ISSUE`가 모두 있어야 한다. 남은 댓글은 지우지 않는다.

- 시작 알림: 운영자 연결(W24) → 승인(W25) → 댓글 receipt → READY → attempt.
  receipt 저장 시각 < attempt 시작 시각(같은 시계)과 provider 접수 시각을 함께 남긴다.
- S6 차단 알림: 모델 없이 host 기록만으로 만든 blocker report(`MODEL_UNAVAILABLE`)를 댓글로 보낸다.
- 강제 timeout: 요청은 GitHub에 보내고 응답만 버리는 transport로 UNKNOWN을 만든 뒤, 재발송 없이
  조회(reconcile)로 FOUND를 확인한다. 그 알림의 댓글은 1개다.
- 미전송: bound Issue가 없는 차단 알림은 보내지 않고 `FAILED(no_bound_issue)`로 남는다.

결과(comment ID·시각)는 `evidence/N12-notification-route.md`에 남긴다. 이것이 N12 스파이크 결과다.
"""

import json
import os
from datetime import UTC, timedelta
from pathlib import Path

import httpx
import pytest

from linemedic.common.clock import SystemClock, from_rfc3339, to_rfc3339
from linemedic.common.config import DEFAULT_CONFIG_PATH, load_settings, process_env
from linemedic.control_plane import supervisor
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.issue_router import IssueRouter
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.notifications.github_comment import GitHubCommentAdapter
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.control_plane.store import Store
from linemedic.integrations.github import GitHubNotConfigured, github_from_settings
from linemedic.tests.helpers.db_rows import insert_incident, insert_run

EVIDENCE = Path("evidence") / "N12-notification-route.md"
RUN = "r-20260927-000000-12ab"
OPERATOR = "operator:host-operator"


class DropCommentResponseOnce(httpx.BaseTransport):
    """첫 댓글 POST는 GitHub에 그대로 보내고 응답만 버린다(응답 대기 중 timeout)."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.dropped = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        if not self.dropped and request.method == "POST" and request.url.path.endswith("/comments"):
            response.read()
            response.close()
            self.dropped += 1
            raise httpx.ReadTimeout("N12 강제 timeout: 응답을 버렸다", request=request)
        return response

    def close(self) -> None:
        self.inner.close()


def _blocked(incident, work, route_id, **extra):
    return blocker_report(
        blocker_code="MODEL_UNAVAILABLE",
        stage="agent",
        incident=incident,
        work=work,
        symptom_impact="N12 live 시험: 모델 없이 host 기록만으로 만든 차단 보고",
        evidence_ids=[],
        owner_route_id=route_id,
        observed_at=to_rfc3339(SystemClock().utc_now()),
        missing_requirements=["없음(알림 경로 시험)"],
        operator_next_step=["이 댓글은 알림 경로 시험 기록이다"],
        retry_condition="시험용이라 다시 시작하지 않는다",
        **extra,
    )


@pytest.mark.live_github
def test_n12_start_receipt_blocked_notice_forced_timeout_and_unsent(tmp_path):
    if os.environ.get("LINEMEDIC_CONFIRM_GITHUB_WRITE") != "1":
        pytest.skip("이번 실행의 쓰기 허락 표시(LINEMEDIC_CONFIRM_GITHUB_WRITE=1)가 없다")
    issue_env = os.environ.get("LINEMEDIC_LIVE_NOTIFY_ISSUE", "")
    if not issue_env.isdigit():
        pytest.skip("bound Issue 번호(LINEMEDIC_LIVE_NOTIFY_ISSUE)가 없다: 사람이 Issue를 고른다")
    settings = load_settings(DEFAULT_CONFIG_PATH, dict(process_env()))
    try:
        port = github_from_settings(settings)
        cut_port = github_from_settings(settings, transport=DropCommentResponseOnce())
    except GitHubNotConfigured as exc:
        pytest.skip(f"NOT_CONFIGURED (G2): {exc}")
    if not port.write_enabled:
        pytest.skip("G10 전: config github.write_enabled=false (shadow 모드)")
    config, clock = settings.config, SystemClock()
    route_id = config.notifications.required_start_route_id
    store = Store(tmp_path / "control.db", clock)
    store.migrate()
    conn = store.connect()
    insert_run(conn, RUN)
    catalog = Catalog.from_config(config)
    sync = IssueSync(
        store,
        port,
        run_id=RUN,
        config=config,
        catalog=catalog,
        clock=clock,
        routing_scope=f"eval:{RUN}",
    )
    router = IssueRouter(store, port, sync, catalog=catalog, clock=clock)
    workers = {
        name: OutboxWorker(
            store,
            adapters={"github_comment": GitHubCommentAdapter(p)},
            config=config,
            repo=port.full_name,
            clock=clock,
        )
        for name, p in (("normal", port), ("cut", cut_port))
    }
    try:
        # 1) 시작 알림: 연결 → 승인 → receipt → READY → attempt
        incident_id = insert_incident(conn, RUN, "NEW", repository_id=port.repository_id)
        version = conn.execute(
            "SELECT version FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()[0]
        bound = router.operator_bind(incident_id, int(issue_env), version, "N12 live", OPERATOR)
        work_id = bound["work_id"]
        assert work_id is not None, bound
        with store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
            supervisor.approve(
                tx,
                work_id,
                work["version"],
                work["issue_snapshot_sha256"],
                principal=OPERATOR,
                note="N12 live 승인",
                route_id=route_id,
            )
        (start,) = workers["normal"].process_pending()
        assert (start["status"], start["start_gate"]) == ("ACCEPTED", "ready"), start
        started = supervisor.Supervisor(store, config=config, clock=clock).start_attempt(work_id)
        assert started.status == "started", started
        notice = conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (start["notification_id"],)
        ).fetchone()
        attempt = conn.execute(
            "SELECT created_at, payload_json FROM audit_events WHERE event_type = 'ATTEMPT_STARTED'"
        ).fetchone()
        recorded_at = json.loads(attempt["payload_json"])["start_notice_recorded_at"]
        assert from_rfc3339(recorded_at) < from_rfc3339(attempt["created_at"])

        # 2) S6 차단 알림 1회
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
            payload = _blocked(incident, work, route_id)
            blocked_id = outbox.enqueue(tx, work, "WORK_BLOCKED", payload, route_id, 1)
        (blocked,) = workers["normal"].process_pending()
        assert blocked["status"] == "ACCEPTED", blocked
        blocked_row = conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (blocked_id,)
        ).fetchone()

        # 3) 강제 timeout → UNKNOWN → 재발송 없이 조회 FOUND
        with store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,))
            payload = _blocked(
                incident, work, route_id, reason_detail="N12 강제 timeout 시험(응답만 버림)"
            )
            drill_id = outbox.enqueue(tx, work, "WORK_BLOCKED", payload, route_id, 2)
        (drill,) = workers["cut"].process_pending()
        assert drill["status"] == "UNKNOWN", drill
        assert workers["normal"].process_pending() == []  # UNKNOWN은 다시 보내지 않는다
        found = workers["normal"].reconcile(drill_id)
        assert found["outcome"] == "FOUND", found
        drill_row = conn.execute("SELECT * FROM notifications WHERE id = ?", (drill_id,)).fetchone()
        since = from_rfc3339(drill_row["created_at"]) - timedelta(minutes=5)
        listed = port.list_issue_comments(
            int(issue_env), since=since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        marker = f"id={drill_id} "
        assert sum(marker in (c.get("body") or "") for c in listed.data or []) == 1

        # 4) 미전송: bound Issue가 없는 차단 알림
        with store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            unsent_id = outbox.enqueue(
                tx,
                None,
                "WORK_BLOCKED",
                _blocked(incident, None, route_id),
                route_id,
                run_id=RUN,
                incident_id=incident_id,
            )
        assert workers["normal"].process_pending() == []  # 보낼 곳이 없어 가져가지 않는다
        unsent = conn.execute("SELECT * FROM notifications WHERE id = ?", (unsent_id,)).fetchone()
        assert (unsent["status"], unsent["attempt_count"], unsent["receipt_id"]) == (
            "FAILED",
            0,
            None,
        )
        assert json.loads(unsent["result_json"])["last"]["error"] == "no_bound_issue"
    finally:
        port.close()
        cut_port.close()

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with EVIDENCE.open("a", encoding="utf-8") as out:
        out.write(
            f"# N12 live ({to_rfc3339(clock.utc_now())})\n\n"
            f"- route: {route_id} (GitHub Issue 댓글), repo {port.full_name}"
            f" (ID {port.repository_id}), Issue #{issue_env}\n"
            f"- 시작 알림 {notice['id']}: comment {notice['receipt_id']},"
            f" provider 접수 {notice['accepted_at']}, receipt 저장 {recorded_at}"
            f" < attempt 시작 {attempt['created_at']}\n"
            f"- S6 차단 알림 {blocked_id}: comment {blocked_row['receipt_id']},"
            f" provider 접수 {blocked_row['accepted_at']}\n"
            f"- 강제 timeout {drill_id}: UNKNOWN → 재발송 0 → 조회 FOUND,"
            f" comment {drill_row['receipt_id']} (marker 댓글 1개)\n"
            f"- 미전송 {unsent_id}: bound Issue 없음 → FAILED(no_bound_issue), 발송 시도 0\n"
            "- 접수는 댓글 등록이다. 사람이 읽었다는 뜻이 아니다.\n\n"
        )
