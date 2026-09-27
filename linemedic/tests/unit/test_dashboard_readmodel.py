"""W18 대시보드 읽기 모델: 상태 문구 매핑·미확인/N/A 구분·타임라인·운영 API (FR-13, D85).

실제 migration을 적용한 SQLite에 행을 넣고 `readmodel.build`와 `GET /ops/dashboard`로 읽는다.
"""

import json
from datetime import UTC, datetime

import pytest

from linemedic.common.ids import new_id
from linemedic.control_plane import audit
from linemedic.control_plane.auth import AgentPrincipal, OperatorPrincipal
from linemedic.dashboard import readmodel
from linemedic.tests.helpers.api import RUN, make_api
from linemedic.tests.helpers.db_rows import (
    REPOSITORY_ID,
    insert_incident,
    insert_issue,
    insert_work,
)

NOW = datetime(2026, 9, 27, 5, 0, 0, tzinfo=UTC)
T0, T1, T2, T3, T4 = (f"2026-09-27T0{h}:00:00.000000Z" for h in range(5))
MANIFEST = {
    "schema_version": "linemedic.v4",
    "run_id": RUN,
    "routing_scope": f"eval:{RUN}",
    "config": {
        "agent": {"mode": "sandbox", "runtime": "openclaw", "model_id": "nvidia/test-model"},
        "memory": {"mode": "cold_start"},
        "repository": {"full_name": "demo-team/l3-mes-api", "id": REPOSITORY_ID},
        "notifications": {
            "routes": {
                "github-issue-primary": {"adapter": "github_comment", "enabled": True},
                "ops-mail": {"adapter": "smtp", "enabled": False},
            }
        },
    },
    "runtime_env": {"demo_host_id": "demo-host-01"},
    "host_manifest": {"path": "evidence/host-manifest.json", "sha256": "ab" * 32},
}


def insert_run_manifest(conn, manifest=None, run_id=RUN, active=1, created_at=T0):
    conn.execute(
        "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES (?, ?, ?, ?)",
        (run_id, active, created_at, json.dumps(MANIFEST if manifest is None else manifest)),
    )


def insert_notification(
    conn, work, incident, event, status, *, route="github-issue-primary", **extra
):
    notification_id = new_id("NOT")
    conn.execute(
        "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
        " logical_key, payload_sha256, status, receipt_id, accepted_at, created_at, updated_at,"
        " payload_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')",
        (
            notification_id,
            RUN,
            incident,
            work,
            event,
            route,
            f"{event}:{notification_id}",
            "0" * 64,
            status,
            extra.get("receipt_id"),
            extra.get("accepted_at"),
            extra.get("at", T1),
            extra.get("at", T1),
            json.dumps(extra.get("payload") or {}),
        ),
    )
    return notification_id


def build(store, run_id=None):
    with store.read() as tx:
        return readmodel.build(tx, run_id, now=NOW)


@pytest.fixture
def seeded(store, conn):
    """RESOLVED 사건·SUCCEEDED work(시작 알림 접수) + 결과 알림 FAILED."""
    insert_run_manifest(conn)
    insert_issue(conn, 42)
    conn.execute(
        "UPDATE github_issues SET payload_json = ? WHERE issue_number = 42",
        (json.dumps({"title": "불량 집계 KeyError"}),),
    )
    incident = insert_incident(conn, RUN, "RESOLVED", attempt_id="ATT-0000000000D1")
    work = insert_work(conn, RUN, incident, 42, "SUCCEEDED", attempt_id="ATT-0000000000D1")
    start = insert_notification(
        conn, work, incident, "WORK_STARTING", "ACCEPTED", receipt_id="IC_1", accepted_at=T1
    )
    conn.execute("UPDATE work_items SET start_notification_id = ? WHERE id = ?", (start, work))
    insert_notification(conn, work, incident, "RECOVERY_VERIFIED", "FAILED", at=T4)
    return {"incident": incident, "work": work, "start": start}


# ── 상태 문구 매핑 (docs/11 §5) ────────────────────────────────


@pytest.mark.parametrize(
    ("table", "status", "text"),
    [
        (readmodel.WORK_LABELS, "WAITING_APPROVAL", "처리 범위 승인 대기"),
        (readmodel.WORK_LABELS, "WAITING_NOTIFICATION", "시작 알림 확인 대기, 수정 미시작"),
        (readmodel.WORK_LABELS, "RUNNING", "원인 조사·수정안 작성 중"),
        (readmodel.WORK_LABELS, "WAITING_REVIEW", "PR 준비, 사람 리뷰·배포 승인 필요"),
        (readmodel.INCIDENT_LABELS, "PR_OPENED", "PR 준비, 사람 리뷰·배포 승인 필요"),
        (readmodel.WORK_LABELS, "HANDED_OFF", "정비 요청 초안·담당자 확인 필요"),
        (readmodel.INCIDENT_LABELS, "WORK_ORDER_DRAFTED", "정비 요청 초안·담당자 확인 필요"),
        (readmodel.WORK_LABELS, "BLOCKED", "진행 불가, 사유와 필요한 조치"),
        (readmodel.INCIDENT_LABELS, "ESCALATED", "진행 불가, 사유와 필요한 조치"),
        (readmodel.INCIDENT_LABELS, "EXECUTION_UNKNOWN", "외부 조치 여부 미확인"),
        (readmodel.WORK_LABELS, "EXECUTION_UNKNOWN", "외부 조치 여부 미확인"),
        (readmodel.INCIDENT_LABELS, "RESOLVED", "지정한 업무 계약·관찰 범위 통과"),
        (readmodel.WORK_LABELS, "SUCCEEDED", "지정한 업무 계약·관찰 범위 통과"),
        (readmodel.CASE_LABELS, "UNVERIFIED", "아직 업무 검증 없는 시도"),
    ],
)
def test_labels_follow_the_docs_11_table(table, status, text):
    assert readmodel.label(table, status) == text


def test_notification_accepted_is_registered_or_received_never_read():
    assert readmodel.notification_label("ACCEPTED", "github_comment") == "댓글 등록"
    assert readmodel.notification_label("ACCEPTED", "smtp") == "메일 서버 접수"
    assert readmodel.notification_label("ACCEPTED", None) == "댓글 등록 / 메일 서버 접수"
    for adapter in ("github_comment", "smtp", None):
        assert "읽음" not in readmodel.notification_label("ACCEPTED", adapter)


def test_no_label_uses_a_banned_phrase_and_every_state_has_one():
    from linemedic.control_plane.state import INCIDENT_STATUSES, WORK_STATUSES

    assert set(readmodel.WORK_LABELS) == set(WORK_STATUSES)
    assert set(readmodel.INCIDENT_LABELS) == set(INCIDENT_STATUSES)
    texts = [
        *readmodel.WORK_LABELS.values(),
        *readmodel.INCIDENT_LABELS.values(),
        *readmodel.NOTIFICATION_LABELS.values(),
        *readmodel.ACCEPTED_LABELS.values(),
        readmodel.ACCEPTED_DEFAULT,
        *readmodel.CASE_LABELS.values(),
        *readmodel.VERIFICATION_LABELS.values(),
        *readmodel.EXECUTION_LABELS.values(),
        *readmodel.BINDING_LABELS.values(),
    ]
    for text in texts:
        for phrase in readmodel.BANNED_PHRASES:
            assert phrase not in text, (text, phrase)
    assert readmodel.WORK_LABELS["WAITING_REVIEW"] != "자동 복구 완료"


def test_unknown_state_is_not_given_an_invented_meaning():
    assert readmodel.label(readmodel.WORK_LABELS, "SOMETHING_NEW") == "미확인(SOMETHING_NEW)"


def test_kst_display_and_unknown_time():
    assert readmodel.kst("2026-09-27T05:10:00.000000Z") == "2026-09-27 14:10:00 KST"
    assert readmodel.kst("2026-09-27T05:10:00Z") == "2026-09-27 14:10:00 KST"
    assert readmodel.kst(None) == readmodel.kst("") == readmodel.kst("어제") == "미확인"
    assert readmodel.kst("2026-09-27T05:10:00") == "미확인"  # 시간대 없는 값은 추정하지 않는다


# ── 머리·미확인/N/A ───────────────────────────────────────────


def test_header_shows_manifest_values_and_polling_in_kst(store, conn):
    insert_run_manifest(conn)
    conn.execute(
        "INSERT INTO integration_state(integration_id, state_key, version, updated_at,"
        " payload_json) VALUES ('github_issues', ?, 1, ?, ?)",
        (f"sync:eval:{RUN}", T2, json.dumps({"last_success_at": T2, "consecutive_failures": 0})),
    )
    head = build(store)["run"]
    assert (head["run_id"], head["active"], head["host_id"]) == (RUN, "활성", "demo-host-01")
    assert head["host_manifest"].startswith("evidence/host-manifest.json (sha256 abababababab")
    assert (head["runtime"], head["agent_mode"], head["model_id"]) == (
        "openclaw",
        "sandbox",
        "nvidia/test-model",
    )
    assert head["runtime_version"] == "미확인"
    assert head["sandbox_verified"] == "미확인(sandbox 검증 기록 없음)"
    assert (head["memory_mode"], head["memory_snapshot"]) == ("cold_start", "N/A")
    assert head["repository"] == f"demo-team/l3-mes-api (ID {REPOSITORY_ID})"
    assert head["polling"] == {
        "last_success": "2026-09-27 11:00:00 KST",
        "last_failure": "N/A",
        "failures": 0,
    }


def test_missing_values_are_unknown_and_inapplicable_values_are_na(store, conn):
    insert_run_manifest(conn, {"run_id": RUN, "routing_scope": f"eval:{RUN}"})
    head = build(store)["run"]
    for key in ("host_id", "host_manifest", "runtime", "agent_mode", "model_id", "memory_mode"):
        assert head[key] == "미확인", key
    assert head["memory_snapshot"] == "미확인"  # 모드를 모르면 해당 여부도 모른다
    assert head["polling"]["last_success"] == "N/A(등록 repo 없음)"
    local = {**MANIFEST, "config": {**MANIFEST["config"], "agent": {"mode": "local"}}}
    conn.execute("UPDATE demo_runs SET config_json = ?", (json.dumps(local),))
    head = build(store)["run"]
    assert head["sandbox_verified"] == "N/A(local 모드)"
    assert head["polling"]["last_success"] == "미확인"  # repo는 있는데 성공 기록이 없다


def test_snapshot_comes_from_retrievals_then_the_snapshot_audit(store, conn):
    memory = {**MANIFEST["config"], "memory": {"mode": "memory_assisted"}}
    insert_run_manifest(conn, {**MANIFEST, "config": memory})
    assert build(store)["run"]["memory_snapshot"] == "미확인"
    with store.tx() as tx:
        audit.append(
            tx,
            RUN,
            None,
            "operator",
            "MEMORY_SNAPSHOT_CREATED",
            {"snapshot_id": "MEM-AAAAAAAAAAAA"},
        )
    assert build(store)["run"]["memory_snapshot"] == "MEM-AAAAAAAAAAAA(만듦, 검색 기록 없음)"
    insert_issue(conn, 5)
    incident = insert_incident(conn, RUN, "INVESTIGATING")
    work = insert_work(conn, RUN, incident, 5, "RUNNING")
    conn.execute(
        "INSERT INTO case_retrievals(id, run_id, incident_id, work_id, mode, snapshot_id, engine,"
        " status, query_json, results_json, created_at) VALUES (?, ?, ?, ?, 'memory_assisted',"
        " 'MEM-BBBBBBBBBBBB', 'sqlite_fts5', 'OK', '{}', '{}', ?)",
        (new_id("RET"), RUN, incident, work, T3),
    )
    assert build(store)["run"]["memory_snapshot"] == "MEM-BBBBBBBBBBBB(검색에 사용)"


def test_no_run_is_reported_without_inventing_one(store):
    model = build(store)
    assert (model["run"], model["works"], model["run_notifications"]) == (None, [], [])


def test_default_is_the_active_run_and_a_run_can_be_chosen(store, conn):
    later = "r-20260928-000000-aaaa"  # 더 나중에 만들었지만 비활성인 run
    insert_run_manifest(conn, run_id=later, active=0, created_at=T4)
    insert_run_manifest(conn)
    assert build(store)["run"]["run_id"] == RUN
    assert build(store, later)["run"]["active"] == "비활성"
    conn.execute("UPDATE demo_runs SET active = 0")
    assert build(store)["run"]["run_id"] == later  # 활성 run이 없으면 가장 최근 run


# ── work 카드·타임라인 ─────────────────────────────────────────


def test_resolved_and_failed_notification_are_both_shown_as_stored(store, seeded):
    (card,) = build(store)["works"]
    assert card["incident_text"] == "지정한 업무 계약·관찰 범위 통과"
    assert card["work_text"] == "지정한 업무 계약·관찰 범위 통과"
    statuses = {n["event_type"]: n["status_text"] for n in card["notifications"]}
    assert statuses == {"WORK_STARTING": "댓글 등록", "RECOVERY_VERIFIED": "발송 실패"}
    assert card["start_notice"] == "댓글 등록 #IC_1"
    assert card["title"] == f"Issue #42 · {seeded['work']}/g1 · {seeded['incident']} · mes-api"
    assert card["issue_title"] == "불량 집계 KeyError"
    assert card["mail"] == "사용하지 않음"  # smtp route가 꺼져 있다


def test_start_notice_is_na_before_approval_and_unknown_when_it_should_exist(store, conn):
    insert_run_manifest(conn)
    insert_issue(conn, 7)
    insert_issue(conn, 8)
    waiting = insert_incident(conn, RUN, "NEW")
    insert_work(conn, RUN, waiting, 7, "WAITING_APPROVAL")
    running = insert_incident(conn, RUN, "INVESTIGATING", attempt_id="ATT-0000000000D2")
    insert_work(conn, RUN, running, 8, "RUNNING", attempt_id="ATT-0000000000D2")
    cards = {card["work_status"]: card for card in build(store)["works"]}
    assert cards["WAITING_APPROVAL"]["start_notice"] == "N/A"
    assert cards["RUNNING"]["start_notice"] == "미확인"
    assert cards["WAITING_APPROVAL"]["verification_text"] == "아직 미검증"


def test_mail_route_status_is_shown_only_when_mail_is_used(store, conn, seeded):
    routes = MANIFEST["config"]["notifications"]["routes"]
    enabled = {**routes, "ops-mail": {"adapter": "smtp", "enabled": True}}
    config = {**MANIFEST["config"], "notifications": {"routes": enabled}}
    conn.execute(
        "UPDATE demo_runs SET config_json = ?", (json.dumps({**MANIFEST, "config": config}),)
    )
    assert build(store)["works"][0]["mail"] == "미확인"  # 켜져 있는데 기록이 없다
    insert_notification(
        conn, seeded["work"], seeded["incident"], "RECOVERY_VERIFIED", "ACCEPTED", route="ops-mail"
    )
    assert build(store)["works"][0]["mail"] == "메일 서버 접수"


def test_blocked_work_shows_reason_and_required_action(store, conn):
    insert_run_manifest(conn)
    insert_issue(conn, 9)
    incident = insert_incident(conn, RUN, "ESCALATED")
    work = insert_work(conn, RUN, incident, 9, "BLOCKED", reason_code="PERMISSION_REQUIRED")
    insert_notification(
        conn,
        work,
        incident,
        "WORK_BLOCKED",
        "PENDING",
        payload={
            "blocker_code": "PERMISSION_REQUIRED",
            "reason_detail": "봇 PR 생성 권한이 없다",
            "operator_next_step": ["GitHub App 권한 확인"],
        },
    )
    (card,) = build(store)["works"]
    assert card["work_text"] == "진행 불가, 사유와 필요한 조치"
    assert card["blocker"] == {
        "code": "PERMISSION_REQUIRED",
        "detail": "봇 PR 생성 권한이 없다",
        "next_steps": ["GitHub App 권한 확인"],
    }


def test_timeline_follows_the_path_in_order(store, conn, seeded):
    run, incident, work = RUN, seeded["incident"], seeded["work"]
    with store.tx() as tx:
        audit.append(
            tx, run, incident, "supervisor", "ATTEMPT_STARTED",
            {"attempt_id": "ATT-0000000000D1", "adapter": "scripted",
             "origin": "manual_integration"},
        )  # fmt: skip
        audit.append(
            tx, run, incident, "broker", "WORK_TRANSITION",
            {"work_id": work, "from": "RUNNING", "to": "WAITING_REVIEW"},
        )  # fmt: skip
    proposal = new_id("PROP")
    checks = {
        "checks": [{"check": "PATCH_POLICY", "result": "PASS"}, {"check": "R2", "result": "PASS"}],
        "decision_reason": "PR_OPENED",
    }
    conn.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
        " body_sha256, decision, received_at, payload_json, checks_json)"
        " VALUES (?, ?, ?, ?, 'ATT-0000000000D1', 'k', ?, 'ALLOWED', ?, '{}', ?)",
        (proposal, run, incident, work, "0" * 64, T2, json.dumps(checks)),
    )
    for operation, result in (("CREATE_PR", {"pr_number": 51}), ("DEPLOY", {})):
        conn.execute(
            "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
            " logical_key, idempotency_key, request_sha256, status, stage, intended_at,"
            " updated_at, request_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'k', ?,"
            " 'SUCCEEDED', 'done', ?, ?, '{}', ?)",
            (
                new_id("EXE"), run, incident, work, proposal, operation, f"{operation}:1",
                "0" * 64, T3, T3, json.dumps(result),
            ),
        )  # fmt: skip
    conn.execute(
        "INSERT INTO verifications(id, run_id, incident_id, execution_id, origin, verdict,"
        " contract_id, contract_sha256, started_at, ended_at, result_json)"
        " VALUES (?, ?, ?, NULL, 'manual_integration', 'PASS', 'defect-summary-v1', ?, ?, ?, '{}')",
        (new_id("VER"), run, incident, "c" * 64, T4, T4),
    )
    (card,) = build(store)["works"]
    steps = [(s["name"], s["state"]) for s in card["timeline"]]
    assert steps == [
        ("Issue 연결", "done"),
        ("시작 알림 접수", "done"),
        ("agent 시작", "done"),
        ("사례/근거 조회", "waiting"),
        ("테스트·패치 검사", "done"),
        ("PR", "done"),
        ("사람 승인 대기", "done"),
        ("배포", "done"),
        ("검증", "done"),
    ]
    by_name = {s["name"]: s for s in card["timeline"]}
    assert by_name["PR"]["detail"] == "PR #51 / 완료"
    assert by_name["테스트·패치 검사"]["detail"] == "ALLOWED PR_OPENED — PATCH_POLICY PASS, R2 PASS"
    assert by_name["agent 시작"]["detail"] == "scripted / origin manual_integration"
    assert by_name["검증"]["detail"] == "지정한 업무 계약·관찰 범위 통과"
    assert card["trace"]["status"] == "N/A(사람이 미리 작성한 제안, 모델 도구 선택 없음)"


def test_evidence_and_retrieved_cases_carry_interpretation_boundaries(store, conn, seeded):
    incident = seeded["incident"]
    rows = [
        ("log_error", {"event": {"error_type": "KeyError", "path": "/defects/summary"}}),
        ("equipment_metric", {"equipment_id": "L3-CAM-2", "samples": [{}, {}]}),
        (
            "history_projection",
            {
                "note_id": "CASE-AAAAAAAAAAAA-R1",
                "outcome": "BLOCKED",
                "summary": "당시 권한 부족",
                "failure_conditions": ["PERMISSION_REQUIRED: 권한 없음"],
            },
        ),
    ]
    for kind, payload in rows:
        conn.execute(
            "INSERT INTO evidence(id, run_id, incident_id, kind, observed_at, source_identity,"
            " payload_json, content_sha256) VALUES (?, ?, ?, ?, ?, 'test', ?, ?)",
            (new_id("EV"), RUN, incident, kind, T1, json.dumps(payload), "0" * 64),
        )
    (card,) = build(store)["works"]
    assert [item["summary"] for item in card["evidence"]] == [
        "KeyError · /defects/summary",
        "L3-CAM-2 · 표본 2개",
        "CASE-AAAAAAAAAAAA-R1 · 진행 불가 기록 — 현재도 틀린 패치라는 뜻 아님",
    ]
    (case,) = card["retrieved_cases"]
    assert case["meaning"] == "진행 불가 기록 — 현재도 틀린 패치라는 뜻 아님"
    assert case["condition"] == "PERMISSION_REQUIRED: 권한 없음"


def test_notifications_never_carry_payload_or_recipient(store, conn, seeded):
    insert_notification(
        conn,
        seeded["work"],
        seeded["incident"],
        "WORK_BLOCKED",
        "UNKNOWN",
        route="ops-mail",
        payload={"recipient": "RECIPIENT-MARKER-7F3A"},  # 주소 모양 값은 쓰지 않는다
    )
    model = build(store)
    text = json.dumps(model, ensure_ascii=False)
    assert "RECIPIENT-MARKER-7F3A" not in text and "recipient" not in text
    statuses = [n["status_text"] for n in model["works"][0]["notifications"]]
    assert "발송 결과 미확인" in statuses


def test_unbound_incidents_are_listed_separately(store, conn, seeded):
    assert build(store)["unbound_incidents"] == []  # work에 연결된 사건은 카드에만
    incident = insert_incident(conn, RUN, "NEW")
    model = build(store)
    assert len(model["works"]) == 1
    assert [(i["incident_id"], i["status_text"]) for i in model["unbound_incidents"]] == [
        (incident, "감지됨, 처리 시작 전")
    ]


def test_rejected_proposal_shows_a_failed_check_step(store, conn, seeded):
    checks = {
        "checks": [{"check": "PATCH_POLICY", "result": "PATCH_PATH_DENIED"}],
        "decision_reason": "PATCH_PATH_DENIED",
    }
    conn.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
        " body_sha256, decision, received_at, payload_json, checks_json)"
        " VALUES (?, ?, ?, ?, 'ATT-0000000000D1', 'k', ?, 'REJECTED', ?, '{}', ?)",
        (new_id("PROP"), RUN, seeded["incident"], seeded["work"], "0" * 64, T2, json.dumps(checks)),
    )
    (card,) = build(store)["works"]
    step = {s["name"]: s for s in card["timeline"]}["테스트·패치 검사"]
    assert step["state"] == "failed"
    assert step["detail"] == "REJECTED PATCH_PATH_DENIED — PATCH_POLICY PATCH_PATH_DENIED"


# ── GET /ops/dashboard ─────────────────────────────────────────


def test_ops_dashboard_returns_the_same_read_model(store, conn, seeded):
    api = make_api(store, conn)
    response = api.client.get("/ops/dashboard", headers=api.operator)
    assert response.status_code == 200
    data = response.json()["data"]
    expected = build(store)
    assert data["run"] == expected["run"] and data["works"] == expected["works"]
    unknown = api.client.get("/ops/dashboard", headers=api.operator, params={"x": 1})
    assert unknown.status_code == 422  # 정의하지 않은 query는 거부(INVALID_REQUEST)
    for run_id in ("r-20990101-000000-dead", "../etc"):
        missing = api.client.get("/ops/dashboard", headers=api.operator, params={"run_id": run_id})
        assert missing.status_code == 404
    agent = api.agent(AgentPrincipal(RUN, seeded["incident"], seeded["work"], "ATT-0000000000D1"))
    assert api.client.get("/ops/dashboard", headers=agent).status_code == 403
    no_read = "test-maint-token-" + "e" * 32
    api.tokens.register_operator(no_read, OperatorPrincipal("maint", frozenset({"maintenance"})))
    denied = api.client.get("/ops/dashboard", headers={"Authorization": f"Bearer {no_read}"})
    assert denied.status_code == 403  # read 역할이 있어야 한다
