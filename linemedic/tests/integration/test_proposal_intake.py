"""W09 통합 테스트: `POST /tools/proposals` 접수 → 백그라운드 브로커 B03~B06 → 초안·이관·거절.

- 202는 `{proposal_id, decision: RECEIVED}`만. 실행·허용을 뜻하지 않는다
- 정비 초안: execution DRAFT_WORK_ORDER SUCCEEDED, WORK_ORDER_DRAFTED/HANDED_OFF, HANDOFF_DRAFTED,
  `delivery_status: not_sent`, 복구 상태 아님(INV-10)
- escalate(증거 0개 허용): ESCALATED/BLOCKED(reason), WORK_BLOCKED blocker report
- create_pr: W10 전까지 REJECTED(PROTECTION_UNAVAILABLE) → 수정 1회 → 이관
- 접수 거부: 다른 work ID 403, 시작 알림 미확인·Issue scope 변경·deadline 409, 크기 413, 형식 422
- 제출 예산: 같은 키 재전송은 세지 않음, 서로 다른 제출 합산 2회, 세 번째 거부
- 백그라운드 루프: 시작 때 CHECKING 복구, 한 제안의 오류로 멈추지 않음
"""

import json
import threading
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.broker.intake import Broker
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.evidence import add_evidence
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.notifications import outbox
from linemedic.tests.helpers.api import ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.db_rows import (
    NOW,
    count,
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
    row,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = load_settings(REPO_ROOT / "config" / "linemedic.toml", {}).config
ATTEMPT = "ATT-00000000000A"
ISSUE = 9
SNAPSHOT = "0" * 64  # insert_issue의 snapshot
DEADLINE = "2026-09-27T00:20:00.000000Z"
DETAILS = {
    "vision-inspection": {"metric": {"anomaly": "brightness_drop", "equipment_id": "L3-CAM-2"}},
    "mes-api": {"signature": {"endpoint": "/lots/{lot_id}/summary", "error_type": "KeyError:x"}},
}
OBSERVED = {
    "vision-inspection": "L3-CAM-2 밝기가 기준보다 낮게 관찰됨",
    "mes-api": "/lots/{lot_id}/summary 요청에서 KeyError 오류 반복 관찰",
}
SECRET = "ghp_" + "A1b2C3d4" * 5  # 테스트 전용 가짜 token 형태


class World:
    """승인된 work가 RUNNING이고 시작 알림이 ACCEPTED인 조사 중 사건."""

    def __init__(self, store, conn, *, service="vision-inspection", templates=None, catalog=None):
        insert_run(conn, RUN)
        insert_issue(conn, ISSUE)
        self.store, self.conn = store, conn
        self.incident = insert_incident(
            conn,
            RUN,
            "INVESTIGATING",
            attempt_id=ATTEMPT,
            service=service,
            attempt_deadline=DEADLINE,
            details_json=json.dumps(DETAILS[service]),
        )
        self.service = service
        self.work = insert_work(
            conn,
            RUN,
            self.incident,
            ISSUE,
            "RUNNING",
            attempt_id=ATTEMPT,
            issue_snapshot_sha256=SNAPSHOT,
        )
        with store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (self.work,))
            self.notice = outbox.enqueue(
                tx, work, "WORK_STARTING", {"event_type": "WORK_STARTING"}, ROUTE_ID
            )
            kind = "equipment_metric" if service == "vision-inspection" else "log_error"
            self.evidence = [
                add_evidence(
                    tx,
                    run_id=RUN,
                    incident_id=self.incident,
                    kind=kind,
                    observed_at=NOW,
                    source_identity=f"test:{i}",
                    payload={"i": i},
                )
                for i in range(3)
            ]
        conn.execute(
            "UPDATE notifications SET status = 'ACCEPTED', receipt_id = 'receipt-1',"
            " accepted_at = ? WHERE id = ?",
            (NOW, self.notice),
        )
        conn.execute(
            "UPDATE work_items SET start_notification_id = ? WHERE id = ?", (self.notice, self.work)
        )
        knowledge = KnowledgeBase()
        self.catalog = catalog or Catalog.from_config(CONFIG)
        self.templates = load_manual_templates(knowledge) if templates is None else templates
        self.api = make_api(store, conn, catalog=Catalog.from_config(CONFIG), knowledge=knowledge)
        self.broker = Broker(store, self.catalog, self.templates, ROUTE_ID)
        self.principal = AgentPrincipal(RUN, self.incident, self.work, ATTEMPT)
        self.headers = self.api.agent(self.principal)

    def body(self, action_type="create_work_order_draft", **overrides):
        actions = {
            "create_work_order_draft": {
                "type": "create_work_order_draft",
                "equipment_id": "L3-CAM-2",
                "symptom": "다른 카메라 대비 밝기와 판정 신뢰도가 낮음",
                "probable_cause": "렌즈·조명·설정 중 원인은 미확정",
                "manual_ref_id": "MANUAL-L3-VISION-4.2",
                "open_questions": ["현장 담당자의 승인된 절차에 따른 점검이 필요함"],
            },
            "create_pr": {
                "type": "create_pr",
                "base_sha": "1" * 40,
                "root_cause_hypothesis": "필수라고 가정한 필드를 직접 조회합니다.",
                "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x\n+y\n",
                "new_test_path": "tests/repro/test_missing_inspector.py",
            },
            "escalate": {
                "type": "escalate",
                "reason": "INSUFFICIENT_EVIDENCE",
                "open_questions": ["현재 의존 서비스 상태를 확인하지 못함"],
                "missing_requirements": ["현재 의존 서비스의 상태를 확인할 자료"],
                "retry_condition": "담당자가 자료를 제공하고 새 generation을 승인한 뒤",
            },
        }
        category = {
            "create_work_order_draft": "equipment",
            "create_pr": "code_bug",
            "escalate": "unknown",
        }[action_type]
        data = {
            "schema_version": "linemedic.v4",
            "run_id": RUN,
            "incident_id": self.incident,
            "work_id": self.work,
            "attempt_id": ATTEMPT,
            "category": category,
            "summary": "카메라 2번에 한정된 밝기와 신뢰도 이상이 관찰됨",
            "evidence_ids": self.evidence[:2],
            "action": actions[action_type],
        }
        data.update(overrides)
        return data

    def submit(self, body, key="prop-1", headers=None):
        return self.api.client.post(
            "/tools/proposals",
            json=body,
            headers={**(headers or self.headers), "Idempotency-Key": key},
        )

    def submit_raw(self, raw: bytes, key="prop-raw"):
        headers = {**self.headers, "Idempotency-Key": key, "Content-Type": "application/json"}
        return self.api.client.post("/tools/proposals", content=raw, headers=headers)

    def get(self, proposal_id, headers=None):
        return self.api.client.get(
            f"/tools/proposals/{proposal_id}", headers=headers or self.headers
        )

    def incident_row(self):
        return row(self.conn, "incidents", self.incident)

    def work_row(self):
        return row(self.conn, "work_items", self.work)

    def notifications(self, event_type):
        return self.conn.execute(
            "SELECT * FROM notifications WHERE event_type = ?", (event_type,)
        ).fetchall()

    def audit_types(self):
        rows = self.conn.execute("SELECT event_type FROM audit_events ORDER BY seq").fetchall()
        return [r["event_type"] for r in rows]


@pytest.fixture
def world(store, conn):
    return World(store, conn)


@pytest.fixture
def code_world(store, conn):
    return World(store, conn, service="mes-api")


def proposal_id_of(response) -> str:
    assert response.status_code == 202, response.text
    return response.json()["data"]["proposal_id"]


def error(response) -> dict:
    return response.json()["error"]


# ── 202 접수 ──────────────────────────────────────────────────


def test_valid_draft_is_only_received_with_202(world):
    response = world.submit(world.body())
    assert response.status_code == 202
    data = response.json()["data"]
    assert set(data) == {"proposal_id", "decision"} and data["decision"] == "RECEIVED"
    proposal = row(world.conn, "proposals", data["proposal_id"])
    assert proposal["decision"] == "RECEIVED"
    assert (proposal["work_id"], proposal["attempt_id"]) == (world.work, ATTEMPT)
    assert world.incident_row()["status"] == "VALIDATING"
    assert world.incident_row()["submissions"] == 1
    assert world.work_row()["status"] == "RUNNING"
    assert count(world.conn, "executions") == 0  # 202는 실행이 아니다
    assert world.notifications("HANDOFF_DRAFTED") == []
    assert "PROPOSAL_RECEIVED" in world.audit_types()


def test_draft_processing_creates_unsent_draft_and_handoff_intent(world):
    pid = proposal_id_of(world.submit(world.body()))
    assert world.broker.process_pending() == [pid]

    proposal = row(world.conn, "proposals", pid)
    record = json.loads(proposal["checks_json"])
    assert proposal["decision"] == "ALLOWED"
    assert [c["check"] for c in record["checks"]] == ["B01", "B02", "B03", "B04", "B05", "B06"]
    assert {c["result"] for c in record["checks"]} == {"PASS"}
    assert record["decision_reason"] == "WORK_ORDER_DRAFTED"

    execution = world.conn.execute("SELECT * FROM executions").fetchone()
    assert (execution["operation"], execution["status"]) == ("DRAFT_WORK_ORDER", "SUCCEEDED")
    assert execution["logical_key"] == f"draft:{world.work}"
    assert execution["proposal_id"] == pid
    draft = json.loads(execution["result_json"])
    template = world.templates["MANUAL-L3-VISION-4.2"]
    assert draft["delivery_status"] == "not_sent"
    assert draft["review_required"] is True
    assert draft["probable_cause"] == "렌즈·조명·설정 중 원인은 미확정"
    assert draft["probable_cause_is_hypothesis"] is True
    assert draft["evidence_ids"] == world.evidence[:2]
    assert draft["guidance"]["text"] == template.guidance  # 안내 문구는 승인 템플릿에서만

    incident, work = world.incident_row(), world.work_row()
    assert incident["status"] == "WORK_ORDER_DRAFTED"  # 복구(RESOLVED)가 아니다
    assert work["status"] == "HANDED_OFF"
    (handoff,) = world.notifications("HANDOFF_DRAFTED")
    payload = json.loads(handoff["payload_json"])
    assert handoff["status"] == "PENDING"
    assert payload["work_order"] == draft
    assert payload["execution_id"] == execution["id"]
    assert world.audit_types()[-1] == "WORK_TRANSITION"
    assert "PROPOSAL_ALLOWED" in world.audit_types()


def test_escalate_without_evidence_blocks_with_blocker_report(world):
    body = world.body("escalate", evidence_ids=[], summary="증거가 부족하여 조치를 고를 수 없음")
    pid = proposal_id_of(world.submit(body))
    world.broker.process_pending()

    assert row(world.conn, "proposals", pid)["decision"] == "ALLOWED"
    incident, work = world.incident_row(), world.work_row()
    assert incident["status"] == "ESCALATED"
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "INSUFFICIENT_EVIDENCE")
    assert count(world.conn, "executions") == 0
    (blocked,) = world.notifications("WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert report["blocker_code"] == "INSUFFICIENT_EVIDENCE"
    assert report["stage"] == "agent"
    assert report["evidence_ids"] == []
    assert report["symptom_impact"] == OBSERVED["vision-inspection"]  # host가 관찰한 현상
    assert report["agent_summary"] == "증거가 부족하여 조치를 고를 수 없음"  # 모델 문장은 따로
    assert report["reason_detail"]
    assert report["missing_requirements"] == ["현재 의존 서비스의 상태를 확인할 자료"]
    assert report["operator_next_step"] == ["현재 의존 서비스 상태를 확인하지 못함"]
    assert report["retry_condition"] == "담당자가 자료를 제공하고 새 generation을 승인한 뒤"
    assert report["side_effect_state"] == {"state": "NONE", "identities": []}
    assert report["owner_route_id"] == ROUTE_ID
    assert report["attempted_actions"] == [
        f"submit_proposal {pid} → escalate(INSUFFICIENT_EVIDENCE)"
    ]


def test_create_pr_is_rejected_until_patch_gate_then_revision_then_escalation(code_world):
    w = code_world
    first = proposal_id_of(w.submit(w.body("create_pr"), key="pr-1"))
    w.broker.process_pending()
    proposal = row(w.conn, "proposals", first)
    assert proposal["decision"] == "REJECTED"
    assert json.loads(proposal["checks_json"])["decision_reason"] == "PROTECTION_UNAVAILABLE"
    incident = w.incident_row()
    assert (incident["status"], incident["attempt_id"]) == ("INVESTIGATING", ATTEMPT)
    assert incident["attempt_deadline"] == DEADLINE  # 수정 제출에 deadline을 새로 주지 않는다
    assert count(w.conn, "executions") == 0  # 가짜 통과·임시 PR 경로가 없다

    status = w.get(first).json()["data"]
    assert status["decision"] == "REJECTED"
    assert status["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert status["revision_allowed"] is True
    assert (status["submissions_used"], status["max_submissions"]) == (1, 2)

    second = proposal_id_of(w.submit(w.body("create_pr"), key="pr-2"))
    assert second != first
    w.broker.process_pending()
    assert row(w.conn, "proposals", second)["decision"] == "REJECTED"
    incident, work = w.incident_row(), w.work_row()
    assert incident["status"] == "ESCALATED"
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "VALIDATION_FAILED")
    (blocked,) = w.notifications("WORK_BLOCKED")
    assert json.loads(blocked["payload_json"])["stage"] == "validation"
    assert w.get(second).status_code == 404  # attempt가 끝나 조회 범위 밖


# ── 접수 거부 (B01·형식) ──────────────────────────────────────


def test_schema_version_v2_is_422_and_counts_one_submission(world):  # T-V4-01
    response = world.submit(world.body(schema_version="linemedic.v2"))
    assert response.status_code == 422
    assert error(response)["code"] == "INVALID_PROPOSAL"
    details = error(response)["details"]
    assert (details["submissions_used"], details["max_submissions"]) == (1, 2)
    assert "escalated" not in details
    assert world.incident_row()["status"] == "INVESTIGATING"
    assert world.incident_row()["submissions"] == 1
    assert count(world.conn, "proposals") == 0
    assert "PROPOSAL_INVALID" in world.audit_types()


@pytest.mark.parametrize("field", ["incident_id", "work_id", "attempt_id"])
def test_ids_of_another_work_are_403_and_not_counted(world, field):  # T-V4-01
    other = {
        "incident_id": "INC-0000000000FF",
        "work_id": "WORK-0000000000FF",
        "attempt_id": "ATT-0000000000FF",
    }
    response = world.submit(world.body(**{field: other[field]}))
    assert response.status_code == 403
    assert error(response)["code"] == "FORBIDDEN_SCOPE"
    assert world.incident_row()["submissions"] == 0


def test_token_of_an_old_attempt_is_403(world):
    stale = AgentPrincipal(RUN, world.incident, world.work, "ATT-0000000000FF")
    response = world.submit(world.body(), headers=world.api.agent(stale))
    assert response.status_code == 403
    assert world.incident_row()["submissions"] == 0


@pytest.mark.parametrize("field", ["confidence", "actions", "actor", "model"])
def test_unused_v4_fields_are_422(world, field):
    body = world.body()
    body[field] = [body["action"]] if field == "actions" else "x"
    response = world.submit(body)
    assert response.status_code == 422
    assert error(response)["details"]["reason"] == "schema"


def test_duplicate_json_keys_are_422(world):
    raw = json.dumps(world.body(), ensure_ascii=False)
    raw = raw.replace('"summary":', '"summary": "첫 값", "summary":', 1)
    response = world.submit_raw(raw.encode("utf-8"))
    assert response.status_code == 422
    assert error(response)["details"]["reason"] == "invalid_json"


def test_body_over_128_kib_is_413_and_not_counted(world):
    body = world.body("create_pr", category="code_bug")
    body["action"]["diff"] = "+" + "x" * 131072
    response = world.submit(body)
    assert response.status_code == 413
    assert error(response)["code"] == "PAYLOAD_TOO_LARGE"
    assert world.incident_row()["submissions"] == 0


@pytest.mark.parametrize("status", ["PENDING", "SENDING", "FAILED", "UNKNOWN"])
def test_start_notice_not_accepted_is_409_and_not_counted(world, status):
    world.conn.execute("UPDATE notifications SET status = ? WHERE id = ?", (status, world.notice))
    response = world.submit(world.body())
    assert response.status_code == 409
    assert error(response)["code"] == "START_NOTICE_UNCONFIRMED"
    assert world.incident_row()["submissions"] == 0
    assert count(world.conn, "proposals") == 0


def test_missing_start_notice_is_409(world):
    world.conn.execute("UPDATE work_items SET start_notification_id = NULL")
    assert error(world.submit(world.body()))["code"] == "START_NOTICE_UNCONFIRMED"


@pytest.mark.parametrize(
    "change",
    ["UPDATE github_issues SET snapshot_sha256 = '2'", "UPDATE github_issues SET state = 'closed'"],
    ids=["body_changed", "closed"],
)
def test_changed_issue_scope_is_409(world, change):
    world.conn.execute(change)
    response = world.submit(world.body())
    assert response.status_code == 409
    assert error(response)["code"] == "ISSUE_SCOPE_CHANGED"
    assert world.incident_row()["submissions"] == 0


def test_deadline_passed_is_409(world, fake_clock):
    fake_clock.advance(20 * 60 + 1)
    response = world.submit(world.body())
    assert response.status_code == 409
    assert error(response)["details"] == {"reason": "attempt_deadline_passed"}


def test_second_submission_while_validating_is_409(world):
    proposal_id_of(world.submit(world.body(), key="a"))
    response = world.submit(world.body("escalate", evidence_ids=[]), key="b")
    assert response.status_code == 409
    assert error(response)["details"]["current_status"] == "VALIDATING"
    assert world.incident_row()["submissions"] == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"equipment_id": "L3-CAM-9"}, "unregistered_equipment"),
        ({"manual_ref_id": "MANUAL-OTHER-1"}, "manual_ref_not_allowed"),
    ],
)
def test_unregistered_equipment_or_manual_is_422(world, change, reason):
    body = world.body()
    body["action"].update(change)
    response = world.submit(body)
    assert response.status_code == 422
    assert error(response)["details"]["reason"] == reason
    assert world.incident_row()["submissions"] == 1


def test_equipment_of_another_service_is_422(code_world):
    response = code_world.submit(code_world.body())  # mes-api 사건에 L3-CAM-2 초안
    assert error(response)["details"]["reason"] == "unregistered_equipment"


@pytest.mark.parametrize("field", ["procedure", "control_values", "url", "recipient"])
def test_draft_free_procedure_or_recipient_field_is_422(world, field):
    body = world.body()
    body["action"][field] = "x"
    assert world.submit(body).status_code == 422


def test_validation_error_details_do_not_echo_input(world):
    body = world.body(schema_version="linemedic.v2", summary=f"값 {SECRET}")
    body[SECRET] = "x"  # 비밀 형태의 key 이름도 오류 위치에 그대로 싣지 않는다
    response = world.submit(body)
    assert response.status_code == 422
    assert SECRET not in response.text
    audit = world.conn.execute("SELECT payload_json FROM audit_events").fetchall()
    assert all(SECRET not in r["payload_json"] for r in audit)
    stored = world.conn.execute("SELECT response_json FROM api_requests").fetchall()
    assert all(SECRET not in r["response_json"] for r in stored)


def test_huge_number_in_body_is_422_and_counts(world):
    raw = json.dumps(world.body()).encode()[:-1] + b', "n": ' + b"9" * 5000 + b"}"
    response = world.submit_raw(raw)
    assert response.status_code == 422  # 500이 아니다
    assert error(response)["details"]["reason"] == "invalid_json"
    assert world.incident_row()["submissions"] == 1


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({"Content-Type": "text/plain"}, {}),
        ({}, {"debug": "1"}),
    ],
    ids=["content_type", "unknown_query"],
)
def test_request_shape_errors_are_422_invalid_request(world, headers, params):
    response = world.api.client.post(
        "/tools/proposals",
        content=json.dumps(world.body()).encode(),
        params=params,
        headers={
            **world.headers,
            "Idempotency-Key": "k",
            "Content-Type": "application/json",
            **headers,
        },
    )
    assert response.status_code == 422
    assert error(response)["code"] == "INVALID_REQUEST"
    assert world.incident_row()["submissions"] == 0


def test_missing_idempotency_key_is_422(world):
    response = world.api.client.post("/tools/proposals", json=world.body(), headers=world.headers)
    assert error(response)["details"] == {"reason": "idempotency_key_required"}


# ── 제출 예산·멱등 ────────────────────────────────────────────


def test_replay_does_not_consume_budget_and_third_distinct_submission_is_refused(world):
    invalid = world.body(schema_version="linemedic.v2")
    first = world.submit(invalid, key="k1")
    replay = world.submit(invalid, key="k1")
    assert first.status_code == replay.status_code == 422
    assert replay.content == first.content
    assert world.incident_row()["submissions"] == 1

    second = world.submit(world.body(summary=""), key="k2")
    assert second.status_code == 422
    details = error(second)["details"]
    assert (details["submissions_used"], details["escalated"]) == (2, True)
    incident, work = world.incident_row(), world.work_row()
    assert incident["status"] == "ESCALATED"
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "VALIDATION_FAILED")
    (blocked,) = world.notifications("WORK_BLOCKED")
    assert json.loads(blocked["payload_json"])["stage"] == "validation"
    assert world.audit_types().count("PROPOSAL_INVALID") == 2

    third = world.submit(world.body(), key="k3")
    assert third.status_code == 409
    assert world.incident_row()["submissions"] == 2


def test_replay_of_202_returns_the_same_receipt_after_state_changed(world):
    first = world.submit(world.body(), key="same")
    replay = world.submit(world.body(), key="same")
    assert replay.status_code == 202
    assert replay.content == first.content
    assert count(world.conn, "proposals") == 1
    assert world.incident_row()["submissions"] == 1


def test_same_key_with_different_body_is_409(world):
    proposal_id_of(world.submit(world.body(), key="same"))
    response = world.submit(world.body(summary="다른 요약"), key="same")
    assert response.status_code == 409
    assert error(response)["code"] == "IDEMPOTENCY_CONFLICT"


# ── 백그라운드 검사 B03~B06 ───────────────────────────────────


def test_evidence_of_another_incident_is_rejected_then_revision_is_allowed(world):
    other = insert_incident(world.conn, RUN, "NEW")
    with world.store.tx() as tx:
        foreign = add_evidence(
            tx,
            run_id=RUN,
            incident_id=other,
            kind="log_error",
            observed_at=NOW,
            source_identity="test:other",
            payload={},
        )
    pid = proposal_id_of(world.submit(world.body(evidence_ids=[world.evidence[0], foreign])))
    world.broker.process_pending()
    record = json.loads(row(world.conn, "proposals", pid)["checks_json"])
    assert record["checks"][-1] == {
        "check": "B03",
        "result": "EVIDENCE_SCOPE_MISMATCH",
        "evidence_id": foreign,
    }
    assert world.incident_row()["status"] == "INVESTIGATING"
    assert count(world.conn, "executions") == 0


def test_final_rejection_report_lists_only_evidence_found_in_this_incident(world):
    other = insert_incident(world.conn, RUN, "NEW")
    with world.store.tx() as tx:
        foreign = add_evidence(
            tx,
            run_id=RUN,
            incident_id=other,
            kind="log_error",
            observed_at=NOW,
            source_identity="test:other",
            payload={},
        )
    proposal_id_of(world.submit(world.body(evidence_ids=[foreign]), key="e1"))
    world.broker.process_pending()
    assert world.incident_row()["status"] == "INVESTIGATING"
    cited = [world.evidence[0], foreign, "EV-0000000000FF"]
    proposal_id_of(world.submit(world.body(evidence_ids=cited), key="e2"))
    world.broker.process_pending()
    assert world.incident_row()["status"] == "ESCALATED"
    (blocked,) = world.notifications("WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert report["evidence_ids"] == [world.evidence[0]]  # 확인하지 않은 ID를 근거로 싣지 않는다
    assert report["symptom_impact"] == OBSERVED["vision-inspection"]
    assert "EVIDENCE_SCOPE_MISMATCH" in report["reason_detail"]
    assert report["agent_summary"] is None


def test_unknown_evidence_id_is_rejected(world):
    pid = proposal_id_of(world.submit(world.body(evidence_ids=["EV-0000000000FF"])))
    world.broker.process_pending()
    assert world.get(pid).json()["data"]["decision_reason"] == "EVIDENCE_SCOPE_MISMATCH"


@pytest.mark.parametrize(
    ("action_type", "field", "text"),
    [
        ("create_work_order_draft", "symptom", f"밝기 낮음 {SECRET}"),
        ("create_work_order_draft", "open_questions", ["https://example.invalid/procedure 참고"]),
        ("create_work_order_draft", "probable_cause", "operator@example.invalid 에게 확인"),
        ("escalate", "retry_condition", "www.example.invalid 확인 뒤"),
        ("create_pr", "diff", f"+TOKEN = '{SECRET}'\n"),
        ("create_work_order_draft", "open_questions", ["점검 절차는https://evil.example/p"]),
        ("create_work_order_draft", "symptom", "절차는www.evil.example 참고"),
        ("create_work_order_draft", "probable_cause", "ftp://evil.example/manual 참고"),
        ("escalate", "retry_condition", "자료는file:///etc/passwd 참고"),
    ],
    ids=[
        "secret",
        "url",
        "email",
        "escalate_url",
        "pr_secret",
        "korean_glued_https",
        "korean_glued_www",
        "ftp",
        "file_scheme",
    ],
)
def test_sensitive_values_or_channels_are_rejected(store, conn, action_type, field, text):
    w = World(store, conn, service="mes-api" if action_type == "create_pr" else "vision-inspection")
    body = w.body(action_type)
    body["action"][field] = text
    pid = proposal_id_of(w.submit(body))
    w.broker.process_pending()
    record = json.loads(row(w.conn, "proposals", pid)["checks_json"])
    assert record["checks"][-1]["check"] == "B05"
    assert record["decision_reason"] == "SENSITIVE_CONTENT"
    assert count(w.conn, "executions") == 0
    assert w.notifications("HANDOFF_DRAFTED") == []


def test_url_in_create_pr_diff_is_not_a_channel_violation(code_world):
    body = code_world.body("create_pr")
    body["action"]["diff"] = "+DOCS = 'https://example.invalid/api'\n"
    pid = proposal_id_of(code_world.submit(body))
    code_world.broker.process_pending()
    record = json.loads(row(code_world.conn, "proposals", pid)["checks_json"])
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"  # B05는 통과


def test_existing_execution_rejects_new_action_b04(world):
    pid = proposal_id_of(world.submit(world.body()))
    world.conn.execute(
        "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
        " logical_key, idempotency_key, request_sha256, status, stage, intended_at, updated_at,"
        " request_json, result_json) VALUES ('EXE-0000000000FF', ?, ?, ?, NULL, 'CREATE_PR',"
        " 'pr:test', 'k', ?, 'UNKNOWN', 'test', ?, ?, '{}', '{}')",
        (RUN, world.incident, world.work, "0" * 64, NOW, NOW),
    )
    world.broker.process_pending()
    record = json.loads(row(world.conn, "proposals", pid)["checks_json"])
    assert record["checks"][-1] == {
        "check": "B04",
        "result": "STATE_CONFLICT",
        "reason": "existing_execution",
    }
    assert count(world.conn, "executions") == 1


def test_state_changed_after_intake_is_rejected_without_transition_b06(world):
    pid = proposal_id_of(world.submit(world.body()))
    world.conn.execute("UPDATE incidents SET version = version + 1")  # 다른 주체의 변경 흉내
    world.broker.process_pending()
    record = json.loads(row(world.conn, "proposals", pid)["checks_json"])
    assert record["checks"][-1] == {
        "check": "B06",
        "result": "STATE_CONFLICT",
        "reason": "state_changed",
    }
    assert world.incident_row()["status"] == "VALIDATING"  # 바뀐 상태 위에 전이하지 않는다
    assert count(world.conn, "executions") == 0


def test_catalog_changed_after_intake_is_rejected_b06(store, conn):
    data = CONFIG.model_dump()
    data["equipment"]["L3-CAM-2"]["manual_ref_ids"] = []
    changed = Catalog.from_config(type(CONFIG).model_validate(data))
    w = World(store, conn, catalog=changed)
    pid = proposal_id_of(w.submit(w.body()))
    w.broker.process_pending()
    record = json.loads(row(w.conn, "proposals", pid)["checks_json"])
    assert record["checks"][-1]["reason"] == "catalog_changed"
    assert w.incident_row()["status"] == "INVESTIGATING"


def test_missing_manual_template_is_protection_unavailable(store, conn):
    w = World(store, conn, templates={})
    pid = proposal_id_of(w.submit(w.body()))
    w.broker.process_pending()
    record = json.loads(row(w.conn, "proposals", pid)["checks_json"])
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert count(w.conn, "executions") == 0


def test_recover_checking_rechecks_only_proposals_without_execution(world):
    pid = proposal_id_of(world.submit(world.body()))
    world.conn.execute("UPDATE proposals SET decision = 'CHECKING'")  # claim 뒤 중단 흉내
    assert world.broker.process_pending() == []
    assert world.broker.recover_checking() == 1
    assert world.broker.process_pending() == [pid]
    assert row(world.conn, "proposals", pid)["decision"] == "ALLOWED"

    world.conn.execute("UPDATE proposals SET decision = 'CHECKING'")  # 실행 intent가 있는 제안
    assert world.broker.recover_checking() == 0
    assert row(world.conn, "proposals", pid)["decision"] == "CHECKING"


# ── get_proposal ──────────────────────────────────────────────


def test_get_proposal_shows_progress_to_the_current_attempt_only(world):
    pid = proposal_id_of(world.submit(world.body()))
    data = world.get(pid).json()["data"]
    assert data["decision"] == "RECEIVED"
    assert data["checks"] == [
        {"check": "B01", "result": "PASS", "reason": None, "evidence_id": None},
        {"check": "B02", "result": "PASS", "reason": None, "evidence_id": None},
    ]
    assert (data["category"], data["action_type"]) == ("equipment", "create_work_order_draft")
    assert data["revision_allowed"] is False

    stale = AgentPrincipal(RUN, world.incident, world.work, "ATT-0000000000FF")
    assert world.get(pid, headers=world.api.agent(stale)).status_code == 404
    assert world.get("PROP-0000000000FF").status_code == 404
    assert world.get("not-an-id").status_code == 404
    assert (
        world.api.client.get(f"/tools/proposals/{pid}", headers=world.api.operator).status_code
        == 403
    )


def test_get_proposal_hides_proposals_of_other_incidents(world):
    other = insert_incident(world.conn, RUN, "ESCALATED", attempt_id="ATT-0000000000FF")
    insert_issue(world.conn, 10)
    other_work = insert_work(world.conn, RUN, other, 10, "BLOCKED", attempt_id="ATT-0000000000FF")
    world.conn.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
        " body_sha256, decision, received_at, payload_json, checks_json) VALUES"
        " ('PROP-0000000000EE', ?, ?, ?, 'ATT-0000000000FF', 'k', ?, 'REJECTED', ?, '{}', '{}')",
        (RUN, other, other_work, "0" * 64, NOW),
    )
    assert world.get("PROP-0000000000EE").status_code == 404


# ── 백그라운드 루프 ───────────────────────────────────────────


def test_worker_loop_recovers_checking_then_processes(world):
    pid = proposal_id_of(world.submit(world.body()))
    world.conn.execute("UPDATE proposals SET decision = 'CHECKING'")  # 이전 프로세스가 중단됨
    stop = threading.Event()
    stop.set()  # 한 주기만 돌고 끝난다
    world.broker.run(stop, interval_seconds=0)
    assert row(world.conn, "proposals", pid)["decision"] == "ALLOWED"
    assert world.incident_row()["status"] == "WORK_ORDER_DRAFTED"


def test_worker_loop_keeps_going_when_one_proposal_fails(world, monkeypatch):
    pid = proposal_id_of(world.submit(world.body()))
    # 다른 사건의 두 번째 제안(그 work는 RUNNING이 아니라 B06에서 거절된다)
    other = insert_incident(world.conn, RUN, "ESCALATED", attempt_id="ATT-0000000000FF")
    insert_issue(world.conn, 10)
    other_work = insert_work(world.conn, RUN, other, 10, "BLOCKED", attempt_id="ATT-0000000000FF")
    other_body = dict(world.body("escalate", evidence_ids=[]), incident_id=other)
    other_body.update(work_id=other_work, attempt_id="ATT-0000000000FF")
    world.conn.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
        " body_sha256, decision, received_at, payload_json, checks_json) VALUES"
        " ('PROP-0000000000EE', ?, ?, ?, 'ATT-0000000000FF', 'k', ?, 'RECEIVED', ?, ?, ?)",
        (
            RUN,
            other,
            other_work,
            "0" * 64,
            "2026-09-27T00:00:01.000000Z",
            json.dumps(other_body),
            json.dumps({"intake_incident_version": 0, "checks": []}),
        ),
    )
    original = Broker._process

    def flaky(self, row):
        if row["id"] == pid:
            raise RuntimeError(f"처리 실패 {SECRET}")
        return original(self, row)

    monkeypatch.setattr(Broker, "_process", flaky)
    with pytest.raises(RuntimeError):
        world.broker.process_pending()  # 기본값은 오류를 숨기지 않는다
    world.conn.execute("UPDATE proposals SET decision = 'RECEIVED' WHERE id = ?", (pid,))

    stop = threading.Event()
    stop.set()
    world.broker.run(stop, interval_seconds=0)
    assert row(world.conn, "proposals", pid)["decision"] == "CHECKING"
    assert world.incident_row()["status"] == "VALIDATING"
    assert row(world.conn, "proposals", "PROP-0000000000EE")["decision"] == "REJECTED"  # 계속 처리
    (audit,) = world.conn.execute(
        "SELECT payload_json FROM audit_events WHERE event_type = 'PROPOSAL_CHECK_ERROR'"
    ).fetchall()
    assert json.loads(audit["payload_json"]) == {"proposal_id": pid, "error": "RuntimeError"}
