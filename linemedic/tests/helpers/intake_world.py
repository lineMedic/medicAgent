"""브로커 접수 시험 세계(W09·W17): 승인된 work가 RUNNING이고 시작 알림이 ACCEPTED인 조사 중 사건."""

import json
from pathlib import Path

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

    def __init__(
        self, store, conn, *, service="vision-inspection", templates=None, catalog=None,
        patch_gate=None,
    ):  # fmt: skip
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
        self.broker = Broker(store, self.catalog, self.templates, ROUTE_ID, patch_gate=patch_gate)
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


def proposal_id_of(response) -> str:
    assert response.status_code == 202, response.text
    return response.json()["data"]["proposal_id"]


def error(response) -> dict:
    return response.json()["error"]
