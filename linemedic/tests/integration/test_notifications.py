"""W26 알림: 템플릿·outbox worker·GitHub 댓글 adapter·조정·운영 API (FakeGitHub + 실제 SQLite).

- T-NOT-02: ACCEPTED 표현은 "댓글 등록"이며 "읽음/배달"이 없다
- T-NOT-03: 댓글 생성 뒤 timeout → UNKNOWN·재발송 0·조정 FOUND,
  재시작 SENDING → UNKNOWN, 중복 enqueue 1건
- T-NOT-04: 모델 없이(MODEL_UNAVAILABLE) blocker report가 완성되고 발송 또는 미전송이 기록된다
- T-NOT-05: verifier PASS 뒤 알림 FAILED → incident RESOLVED 유지
- T-NOT-06: payload의 수신자·URL·`@team` → catalog 밖 전송 0, 링크·멘션 무력화, 비밀 마스킹
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane.broker.proposals import WorkOrderDraftAction
from linemedic.control_plane.broker.work_order import build_draft
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.notifications import outbox, templates
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.notifications.github_comment import GitHubCommentAdapter
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import OPERATOR_TOKEN, ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run, insert_work

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO, REPO_ID, BOT, STRANGER = "demo-team/l3-mes-api", 100001, 900001, 300001
CONFIG = load_settings(
    REPO_ROOT / "config" / "linemedic.toml",
    {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)},
).config
SECRET = "ghp_" + "Q7w8E9r0" * 5  # 테스트 전용 가짜 token 형태


class Outbox:
    def __init__(
        self, store, conn, clock, *, incident_status="NEW", work_status="WAITING_APPROVAL"
    ):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        self.github = FakeGitHub(REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=True)
        issue = self.github.add_issue(title="요청", author_id=200001)
        insert_issue(conn, issue["number"])
        self.issue_number = issue["number"]
        self.incident_id = insert_incident(conn, RUN, incident_status)
        self.work_id = insert_work(
            conn,
            RUN,
            self.incident_id,
            issue["number"],
            work_status,
            issue_snapshot_sha256="0" * 64,
        )
        self.worker = OutboxWorker(
            store,
            adapters={"github_comment": GitHubCommentAdapter(self.github)},
            config=CONFIG,
            repo=REPO,
            clock=clock,
        )

    def enqueue(self, event_type, payload, *, work=True) -> str:
        with self.store.tx() as tx:
            row = tx.one("SELECT * FROM work_items WHERE id = ?", (self.work_id,)) if work else None
            return outbox.enqueue(
                tx, row, event_type, payload, ROUTE_ID, run_id=RUN, incident_id=self.incident_id
            )

    def notification(self, notification_id):
        return self.conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        ).fetchone()

    def comments(self):
        return self.github.comments.get(self.issue_number, [])


@pytest.fixture
def box(store, conn, fake_clock):
    return Outbox(store, conn, fake_clock)


def report(**overrides):
    kwargs = {
        "blocker_code": "MODEL_UNAVAILABLE",
        "stage": "agent",
        "incident": {"run_id": RUN, "id": "INC-0000000000AA", "repository_id": REPO_ID},
        "work": None,
        "symptom_impact": "/defects/summary 요청에서 KeyError 오류 반복 관찰",
        "evidence_ids": ["EV-000000000001"],
        "owner_route_id": ROUTE_ID,
        "observed_at": "2026-09-27T00:00:00.000000Z",
        "missing_requirements": ["모델 endpoint 복구"],
        "operator_next_step": ["모델 상태 확인 뒤 새 generation 승인 여부 결정"],
        "retry_condition": "모델 API가 복구된 뒤",
    }
    kwargs.update(overrides)
    return blocker_report(**kwargs)


def draft_payload():
    template = load_manual_templates(KnowledgeBase())["MANUAL-L3-VISION-4.2"]
    action = WorkOrderDraftAction.model_validate(
        {
            "type": "create_work_order_draft",
            "equipment_id": "L3-CAM-2",
            "symptom": "밝기가 낮음",
            "probable_cause": "원인 미확정",
            "manual_ref_id": "MANUAL-L3-VISION-4.2",
            "open_questions": ["담당자 확인 필요"],
        }
    )
    return {"work_order": build_draft(action, ["EV-000000000001"], template)}


PAYLOADS = {
    "WORK_STARTING": (
        {
            "work_id": "WORK-0000000000AA",
            "generation": 1,
            "approved_by": "operator:x",
            "scope": {"service": "mes-api"},
        },
        "작업 완료·복구·배포 승인을 뜻하지 않습니다",
    ),
    "WORK_BLOCKED": (report(), "원인이 확정됐다는 뜻이 아닙니다"),
    "HANDOFF_DRAFTED": (draft_payload(), "현장 작업 지시·설비 제어·복구 완료가 아닙니다"),
    "PR_READY": ({"pr_number": 51}, "아직 업무 복구가 확인된 것은 아닙니다"),
    "RECOVERY_VERIFIED": (
        {
            "verdict": "PASS",
            "contract_id": "defect-summary-v1",
            "samples_completed": 4,
            "samples_required": 4,
        },
        "관찰 범위에 한정되며",
    ),
    "RECOVERY_NOT_VERIFIED": (
        {"verdict": "FAIL", "reason": "content_mismatch", "contract_id": "defect-summary-v1"},
        "추가 수정은 자동으로 시작하지 않습니다",
    ),
    "WORK_CANCELLED": (
        {
            "work_id": "WORK-0000000000AA",
            "work_status_before": "WAITING_APPROVAL",
            "side_effect_state": "NONE",
            "note": "중단",
        },
        "문제가 해결됐다는 뜻이 아닙니다",
    ),
}


# ── 템플릿 ────────────────────────────────────────────────────


@pytest.mark.parametrize("event_type", sorted(PAYLOADS))
def test_every_event_renders_what_it_does_not_mean_and_marker(event_type):
    payload, not_meaning = PAYLOADS[event_type]
    rendered = templates.render(
        event_type,
        payload,
        repo=REPO,
        issue_number=7,
        notification_id="NOT-0000000000AA",
        payload_sha256="ab" * 32,
    )
    assert not_meaning in rendered.body
    assert rendered.body.rstrip().endswith(
        "<!-- linemedic:notify id=NOT-0000000000AA h=abababababababab -->"
    )
    assert f"https://github.com/{REPO}/issues/7" in rendered.body
    assert "읽음" not in rendered.body and "배달" not in rendered.body


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("unchanged", "이전 MES 컨테이너가 그대로 실행 중임을 확인했습니다."),
        ("previous_removed", "운영자가 execution 기록의 복원 절차를 직접 실행해야 합니다."),
        ("<b>x</b>", "확인하지 못함(&lt;b&gt;x&lt;/b&gt;)"),
    ],
)
def test_not_deployed_recovery_notice_says_no_check_ran(environment, expected):  # W12
    payload = {
        "verdict": "NOT_DEPLOYED",
        "stage": "start",
        "reason": "start_failed",
        "environment": environment,
        "contract_id": "defect-summary-v1",
        "restore": {"text": ["docker run ... /Users/someone/runs"]},  # 알림에 넣지 않는다
    }
    rendered = templates.render(
        "RECOVERY_NOT_VERIFIED",
        payload,
        repo=REPO,
        issue_number=7,
        notification_id="NOT-0000000000AA",
        payload_sha256="ab" * 32,
    )
    assert rendered.title == "업무 복구 미확인 — Issue #7 / 배포 안 됨"
    assert "검사를 하지 않았습니다(단계 start, 사유 start_failed)" in rendered.body
    assert expected in rendered.body and "복구 완료가 아닙니다" in rendered.body
    assert "docker run" not in rendered.body and "NOT_DEPLOYED" not in rendered.body


def test_blocker_report_without_model_renders_ten_fields():  # T-NOT-04
    rendered = templates.render(
        "WORK_BLOCKED",
        report(),
        repo=REPO,
        issue_number=None,
        notification_id="NOT-0000000000AB",
        payload_sha256="cd" * 32,
    )
    for label in (
        "현상·영향",
        "중단 사유",
        "실제로 한 일",
        "확인한 근거",
        "부작용 상태",
        "필요한 것",
        "담당자 다음 단계",
        "담당 route",
        "다시 시작 조건",
    ):
        assert f"- {label}:" in rendered.body
    assert "MODEL_UNAVAILABLE" in rendered.title and "(단계: agent)" in rendered.body
    assert "에이전트 판단" not in rendered.body  # 모델 요약이 없어도 완성된다
    assert "Issue 미연결" in rendered.body


def test_untrusted_strings_are_neutralized():  # T-NOT-06
    hostile = (
        f"@admin-team 확인 https://evil.example/x {SECRET} <script>x</script>"
        f" https://github.com/{REPO}/pull/9"
    )
    rendered = templates.render(
        "WORK_BLOCKED",
        report(symptom_impact=hostile, agent_summary="recipient: ops@evil.example"),
        repo=REPO,
        issue_number=7,
        notification_id="NOT-0000000000AC",
        payload_sha256="ef" * 32,
    )
    body = rendered.body
    assert "@admin-team" not in body and "@\u200badmin-team" in body
    assert "https://evil.example" not in body and "https[:]//evil.example" in body
    assert SECRET not in body and "[REDACTED:" in body
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert f"https://github.com/{REPO}/pull/9" in body  # 등록 repo 링크만 남는다


# ── worker ────────────────────────────────────────────────────


def test_worker_sends_comment_and_records_receipt_as_comment_registered(box):  # T-NOT-02
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    (sent,) = box.worker.process_pending()
    assert sent["status"] == "ACCEPTED"
    row = box.notification(notification_id)
    (comment,) = box.comments()
    assert (row["receipt_id"], row["attempt_count"]) == (str(comment["id"]), 1)
    assert json.loads(row["result_json"])["canonical_ref"] == comment["html_url"]
    assert comment["user"]["id"] == BOT and "LineMedic 자동 알림" in comment["body"]
    api = make_api(box.store, box.conn, catalog=_catalog())
    listed = api.client.get("/ops/notifications", headers=api.operator).json()["data"]
    (item,) = listed["notifications"]
    assert item["status_label"] == "댓글 등록"
    source = (REPO_ROOT / "linemedic/control_plane/notifications/templates.py").read_text()
    assert "읽음" not in source.split('"""', 2)[2] and "배달" not in source.split('"""', 2)[2]


def test_payload_issue_number_or_recipient_does_not_redirect(box):  # T-NOT-06
    other = box.github.add_issue(title="다른 Issue", author_id=200001)
    payload = {
        **report(work=None),
        "issue_number": other["number"],
        "recipient": "ops@evil.example",
    }
    box.enqueue("WORK_BLOCKED", payload)
    box.worker.process_pending()
    assert len(box.comments()) == 1 and box.github.comments.get(other["number"]) is None


def test_timeout_after_comment_is_unknown_without_resend_then_reconcile_found(box):  # T-NOT-03
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("timeout", after_side_effect=True)
    (sent,) = box.worker.process_pending()
    assert sent["status"] == "UNKNOWN" and len(box.comments()) == 1
    assert box.worker.process_pending() == []  # UNKNOWN은 다시 보내지 않는다
    assert len(box.comments()) == 1
    found = box.worker.reconcile(notification_id)
    assert found["outcome"] == "FOUND"
    row = box.notification(notification_id)
    assert (row["status"], row["receipt_id"]) == ("ACCEPTED", str(box.comments()[0]["id"]))


def test_restart_turns_sending_into_unknown(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.conn.execute("UPDATE notifications SET status = 'SENDING'")
    assert box.worker.recover_sending() == 1
    assert box.notification(notification_id)["status"] == "UNKNOWN"
    assert box.worker.process_pending() == [] and box.comments() == []


def test_duplicate_enqueue_is_one_row_and_different_payload_conflicts(box):
    first = box.enqueue("WORK_BLOCKED", report(work=None))
    assert box.enqueue("WORK_BLOCKED", report(work=None)) == first
    with pytest.raises(outbox.OutboxConflict):
        box.enqueue("WORK_BLOCKED", report(work=None, symptom_impact="다른 내용"))
    assert box.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1


def test_rate_limited_send_retries_with_backoff_then_fails_after_three(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    for _ in range(3):
        box.github.fail_next("rate_limited")  # retry_after 60
    (first,) = box.worker.process_pending()
    assert first["status"] == "PENDING"
    assert box.worker.process_pending() == []  # 아직 다음 시도 시각 전
    box.clock.advance(61)
    (second,) = box.worker.process_pending()
    assert second["status"] == "PENDING"
    box.clock.advance(61)
    (third,) = box.worker.process_pending()
    assert third["status"] == "FAILED"  # 최대 3회
    row = box.notification(notification_id)
    assert (row["attempt_count"], box.comments()) == (3, [])


def test_connect_failure_is_known_unsent_and_retried(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("connect_failed")  # 요청을 보내기 전 실패: 보내지 않았음이 확실하다
    (first,) = box.worker.process_pending()
    assert first["status"] == "PENDING"  # UNKNOWN이 아니다
    box.clock.advance(31)
    (second,) = box.worker.process_pending()
    assert second["status"] == "ACCEPTED" and len(box.comments()) == 1
    assert box.notification(notification_id)["attempt_count"] == 2


def test_rejected_send_that_is_not_retryable_fails_once(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("forbidden")
    (sent,) = box.worker.process_pending()
    assert (sent["status"], sent["error"]) == ("FAILED", "Forbidden")
    events = [r[0] for r in box.conn.execute("SELECT event_type FROM audit_events")]
    assert "NOTIFICATION_FAILED" in events
    assert box.notification(notification_id)["attempt_count"] == 1


def test_verifier_pass_then_notification_failure_keeps_resolved(
    store, conn, fake_clock
):  # T-NOT-05
    box = Outbox(store, conn, fake_clock, incident_status="RESOLVED", work_status="SUCCEEDED")
    box.enqueue("RECOVERY_VERIFIED", PAYLOADS["RECOVERY_VERIFIED"][0])
    box.github.fail_next("forbidden")
    (sent,) = box.worker.process_pending()
    assert sent["status"] == "FAILED"
    incident = conn.execute("SELECT status FROM incidents").fetchone()
    assert incident["status"] == "RESOLVED"  # 알림 실패가 복구 판정을 바꾸지 않는다


def test_notification_without_bound_issue_is_marked_unsent(box):  # T-NOT-04 미전송
    notification_id = box.enqueue("WORK_BLOCKED", report(), work=False)
    (sent,) = box.worker.process_pending() or [None]
    row = box.notification(notification_id)
    assert (
        row["status"] == "FAILED"
        and json.loads(row["result_json"])["last"]["error"] == "no_bound_issue"
    )
    assert box.comments() == [] and sent is None


def test_reconcile_ignores_other_authors_and_changed_bodies(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("timeout", after_side_effect=False)
    box.worker.process_pending()
    row = box.notification(notification_id)
    marker = templates.notify_marker(notification_id, row["payload_sha256"])
    copied = templates.render(  # 우리가 보냈을 본문을 다른 사용자가 그대로 복사했다
        "WORK_BLOCKED",
        json.loads(row["payload_json"]),
        repo=REPO,
        issue_number=box.issue_number,
        notification_id=notification_id,
        payload_sha256=row["payload_sha256"],
    ).body
    at = "2026-09-27T00:00:00Z"
    box.github.comments[box.issue_number] = [
        {"id": 1, "body": copied, "user": {"id": STRANGER}, "updated_at": at},
        {"id": 2, "body": f"다른 본문 {marker}", "user": {"id": BOT}, "updated_at": at},
    ]
    result = box.worker.reconcile(notification_id)
    assert result["outcome"] == "CONFIRMED_ABSENT"
    assert box.notification(notification_id)["status"] == "UNKNOWN"
    box.github.fail_next("server_error")
    assert box.worker.reconcile(notification_id)["outcome"] == "INCONCLUSIVE"


def test_adapter_reconcile_requires_marker_even_when_hash_matches(box):
    """adapter 계약: 봇 작성 + marker + 본문 hash가 모두 맞아야 한다(hash는 호출자가 준 값이다)."""
    adapter = GitHubCommentAdapter(box.github)
    marker = templates.notify_marker("NOT-0000000000AD", "a" * 64)
    plain = "마커 없는 봇 댓글"
    marked = f"{plain}\n{marker}"
    at = "2026-09-27T00:00:00Z"
    box.github.comments[box.issue_number] = [
        {"id": 1, "body": plain, "user": {"id": BOT}, "updated_at": at},
    ]
    digest = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    missing = adapter.reconcile(box.issue_number, marker=marker, body_sha256=digest, since=None)
    assert missing.outcome == "CONFIRMED_ABSENT"
    box.github.comments[box.issue_number].append(
        {"id": 2, "body": marked, "user": {"id": BOT}, "updated_at": at}
    )
    digest = hashlib.sha256(marked.encode("utf-8")).hexdigest()
    found = adapter.reconcile(box.issue_number, marker=marker, body_sha256=digest, since=None)
    assert found.outcome == "FOUND" and found.receipt.receipt_id == "2"


def test_shadow_mode_leaves_notifications_pending(store, conn, fake_clock):
    box = Outbox(store, conn, fake_clock)
    box.github.write_enabled = False
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    assert box.worker.process_pending() == []
    assert box.notification(notification_id)["status"] == "PENDING" and box.comments() == []


def test_operator_escalate_intent_from_w06_is_delivered(box):
    """이미 다른 카드가 넣는 intent(W06 운영자 중단)도 이 worker로 발송된다."""
    box.enqueue(
        "WORK_BLOCKED",
        {
            "event_type": "WORK_BLOCKED",
            "blocker_code": "PERMISSION_REQUIRED",
            "operator_note": "@lead 배포하지 않음",
            "incident_status_before": "PR_OPENED",
            "work_status_before": "WAITING_REVIEW",
        },
    )
    (sent,) = box.worker.process_pending()
    assert sent["status"] == "ACCEPTED"
    body = box.comments()[0]["body"]
    assert "운영자가 이 작업을 중단했습니다" in body and "@lead" not in body


# ── 운영 API·CLI ──────────────────────────────────────────────


def _catalog():
    from linemedic.control_plane.catalog import Catalog

    return Catalog.from_config(CONFIG)


def test_ops_notifications_list_and_reconcile(box):
    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("timeout", after_side_effect=True)
    box.worker.process_pending()
    api = make_api(box.store, box.conn, catalog=_catalog(), outbox_worker=box.worker)
    unknown = api.client.get("/ops/notifications?status=UNKNOWN", headers=api.operator)
    (item,) = unknown.json()["data"]["notifications"]
    assert (item["id"], item["status_label"]) == (notification_id, "전송 여부 미확인")
    assert "payload_json" not in item and "recipient" not in json.dumps(item)
    assert api.client.get("/ops/notifications?status=READ", headers=api.operator).status_code == 422
    body = {"schema_version": "linemedic.v4"}
    headers = {**api.operator, "Idempotency-Key": "rc-1"}
    response = api.client.post(
        f"/ops/notifications/{notification_id}/reconcile", json=body, headers=headers
    )
    assert response.json()["data"]["outcome"] == "FOUND"
    replay = api.client.post(
        f"/ops/notifications/{notification_id}/reconcile", json=body, headers=headers
    )
    assert replay.content == response.content
    missing = api.client.post(
        "/ops/notifications/NOT-0000000000FF/reconcile",
        json=body,
        headers={**api.operator, "Idempotency-Key": "rc-2"},
    )
    assert missing.status_code == 404
    without = make_api(box.store, box.conn)
    assert (
        without.client.post(
            f"/ops/notifications/{notification_id}/reconcile",
            json=body,
            headers={**without.operator, "Idempotency-Key": "rc-3"},
        ).status_code
        == 503
    )


def test_cli_notification_reconcile_calls_control_api(box, tmp_path, monkeypatch):
    from linemedic import cli

    notification_id = box.enqueue("WORK_BLOCKED", report(work=None))
    box.github.fail_next("timeout", after_side_effect=True)
    box.worker.process_pending()
    api = make_api(box.store, box.conn, catalog=_catalog(), outbox_worker=box.worker)

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            k: v for k, v in request.headers.items() if k in ("authorization", "idempotency-key")
        }
        headers["content-type"] = "application/json"
        response = api.client.post(request.url.path, content=request.content, headers=headers)
        return httpx.Response(response.status_code, content=response.content)

    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args = cli.build_parser().parse_args(
        [
            "notification-reconcile",
            "--notification-id",
            notification_id,
            "--env-file",
            str(tmp_path / "x"),
        ]
    )
    assert cli._notification_reconcile(args, transport=httpx.MockTransport(handler)) == 0
    assert box.notification(notification_id)["status"] == "ACCEPTED"
