"""W06: fresh DB의 DDL 제약 재현 (docs/09 §4, PACKAGE-VALIDATION §3).

20건 중 19건을 여기서 확인한다. 19번(FTS5 필터 질의)은 `case_search`를 만드는 W27에서 추가한다.
0001_init.sql이 spec 04 §5 DDL 원문과 글자 단위로 같은지도 확인한다.
"""

import sqlite3
from pathlib import Path

import pytest

from linemedic.common.ids import new_id
from linemedic.control_plane.store import MIGRATIONS_DIR, applied_versions, migration_files
from linemedic.tests.helpers.db_rows import (
    NOW,
    count,
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_04 = REPO_ROOT / "spec" / "docs" / "04-data-state.md"
RUN_A = "r-20260927-000000-aaaa"
RUN_B = "r-20260927-000001-bbbb"
SPEC_TABLES = {
    "demo_runs",
    "incidents",
    "github_issues",
    "issue_bindings",
    "work_items",
    "integration_state",
    "api_requests",
    "evidence",
    "proposals",
    "executions",
    "verifications",
    "audit_events",
    "notifications",
    "case_notes",
    "case_retrievals",
}


def spec_ddl() -> str:
    section = SPEC_04.read_text(encoding="utf-8").split("## 5. Fresh schema DDL\n", 1)[1]
    return section.split("```sql\n", 1)[1].split("\n```", 1)[0] + "\n"


def insert_notification(conn, run_id, incident_id, logical_key, status="PENDING"):
    conn.execute(
        "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
        " logical_key, payload_sha256, status, attempt_count, created_at, updated_at,"
        " payload_json, result_json) VALUES (?, ?, ?, NULL, 'WORK_BLOCKED', 'github-issue-primary',"
        " ?, ?, ?, 0, ?, ?, '{}', '{}')",
        (new_id("NOT"), run_id, incident_id, logical_key, "0" * 64, status, NOW, NOW),
    )


def insert_case(
    conn,
    note_id,
    run_id,
    incident_id,
    *,
    series="S-1",
    revision=1,
    event=None,
    outcome="UNVERIFIED",
    supersedes=None,
):
    conn.execute(
        "INSERT INTO case_notes(id, series_id, revision, supersedes_id, repository_id, service,"
        " problem_fingerprint, source_run_id, source_incident_id, work_id, source_event_key,"
        " outcome, phase, origin, publish_status, observed_at, created_at, content_sha256,"
        " payload_json)"
        " VALUES (?, ?, ?, ?, 100001, 'mes-api', 'fp', ?, ?, NULL, ?, ?, 'validation',"
        " 'agent_release', 'DRAFT', ?, ?, ?, '{}')",
        (
            note_id,
            series,
            revision,
            supersedes,
            run_id,
            incident_id,
            event or f"event:{note_id}",
            outcome,
            NOW,
            NOW,
            "0" * 64,
        ),
    )


def insert_api_request(conn, run_id, key="key-1"):
    conn.execute(
        "INSERT INTO api_requests(principal_scope, method, path, run_id, idempotency_key,"
        " body_sha256, status, response_json, created_at)"
        " VALUES ('operator:host-operator', 'POST', '/ops/x', ?, ?, ?, 'RECEIVED', NULL, ?)",
        (run_id, key, "0" * 64, NOW),
    )


@pytest.fixture
def base(conn):
    insert_run(conn, RUN_A)
    return conn


# ── migration ─────────────────────────────────────────────────


def test_migration_carries_spec_04_ddl_verbatim():
    text = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    start = text.index("-- ===== spec 04 §5 원문 시작 =====\n") + len(
        "-- ===== spec 04 §5 원문 시작 =====\n"
    )
    end = text.index("-- ===== spec 04 §5 원문 끝 =====\n")
    assert text[start:end] == spec_ddl()


def test_fresh_db_has_spec_tables_and_migration_record(store, conn):
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    }
    assert tables == SPEC_TABLES | {"schema_migrations"}
    assert applied_versions(conn) == {1}
    assert [version for version, _ in migration_files()] == [1]
    assert store.migrate() == []  # 두 번째 적용은 아무것도 하지 않는다
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# ── DDL 제약 (PACKAGE-VALIDATION §3의 1~18, 20) ───────────────


def test_01_single_active_run(base):
    insert_run(base, RUN_B, active=0)
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("UPDATE demo_runs SET active = 1 WHERE id = ?", (RUN_B,))


def test_02_active_fingerprint_unique_but_terminal_repeats_allowed(base):
    insert_incident(base, RUN_A, "INVESTIGATING", fingerprint="fp-1")
    with pytest.raises(sqlite3.IntegrityError):
        insert_incident(base, RUN_A, "NEW", fingerprint="fp-1")
    insert_incident(base, RUN_A, "RESOLVED", fingerprint="fp-1")
    insert_incident(base, RUN_A, "ESCALATED", fingerprint="fp-1")
    insert_incident(base, RUN_A, "WORK_ORDER_DRAFTED", fingerprint="fp-1")


def test_03_incident_fk_run(base):
    with pytest.raises(sqlite3.IntegrityError):
        insert_incident(base, "r-20990101-000000-dead")


def test_04_incident_status_check(base):
    with pytest.raises(sqlite3.IntegrityError):
        insert_incident(base, RUN_A, "DONE")


def test_05_incident_nonnegative_count(base):
    with pytest.raises(sqlite3.IntegrityError):
        insert_incident(base, RUN_A, count=-1)


def test_06_single_active_work_per_issue(base):
    insert_issue(base, 7)
    first = insert_incident(base, RUN_A)
    insert_work(base, RUN_A, first, 7, "WAITING_REVIEW", generation=1)
    second = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, second, 7, "WAITING_APPROVAL", generation=2)
    base.execute("UPDATE work_items SET status = 'BLOCKED' WHERE incident_id = ?", (first,))
    insert_work(base, RUN_A, second, 7, "WAITING_APPROVAL", generation=2)


def test_07_work_generation_unique(base):
    insert_issue(base, 7)
    first = insert_incident(base, RUN_A)
    insert_work(base, RUN_A, first, 7, "BLOCKED", generation=1)
    second = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, second, 7, "BLOCKED", generation=1)


def test_08_work_incident_unique(base):
    insert_issue(base, 7)
    insert_issue(base, 8)
    incident = insert_incident(base, RUN_A)
    insert_work(base, RUN_A, incident, 7, "BLOCKED")
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, incident, 8, "WAITING_APPROVAL")


def test_09_work_issue_fk(base):
    incident = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, incident, 404)


def test_10_work_generation_positive(base):
    insert_issue(base, 7)
    incident = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, incident, 7, generation=0)


def test_11_single_global_running_work(base):
    insert_issue(base, 7)
    insert_issue(base, 8)
    insert_work(base, RUN_A, insert_incident(base, RUN_A), 7, "RUNNING")
    with pytest.raises(sqlite3.IntegrityError):
        insert_work(base, RUN_A, insert_incident(base, RUN_A), 8, "RUNNING")
    insert_work(base, RUN_A, insert_incident(base, RUN_A), 8, "READY")


def test_12_api_request_scope_key_unique(base):
    insert_api_request(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_api_request(base, RUN_A)
    insert_api_request(base, RUN_A, key="key-2")


def test_13_notification_logical_key_unique(base):
    incident = insert_incident(base, RUN_A)
    insert_notification(base, RUN_A, incident, "notify:intake:x:1")
    with pytest.raises(sqlite3.IntegrityError):
        base.execute(
            "INSERT INTO notifications(id, run_id, incident_id, event_type, route_id, logical_key,"
            " payload_sha256, status, created_at, updated_at, payload_json, result_json)"
            " VALUES ('NOT-00000000000B', ?, ?, 'WORK_BLOCKED', 'r', 'notify:intake:x:1', ?,"
            " 'PENDING', ?, ?, '{}', '{}')",
            (RUN_A, incident, "1" * 64, NOW, NOW),
        )


def test_14_notification_state_check(base):
    incident = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_notification(base, RUN_A, incident, "notify:intake:x:2", status="DELIVERED_TO_HUMAN")


def test_15_case_source_event_unique(base):
    incident = insert_incident(base, RUN_A)
    insert_case(base, "CASE-000000000001", RUN_A, incident, event="verification:VER-1:final")
    with pytest.raises(sqlite3.IntegrityError):
        insert_case(
            base,
            "CASE-000000000002",
            RUN_A,
            incident,
            series="S-2",
            event="verification:VER-1:final",
        )


def test_16_case_revision_unique(base):
    incident = insert_incident(base, RUN_A)
    insert_case(base, "CASE-000000000001", RUN_A, incident, series="S-1", revision=1)
    with pytest.raises(sqlite3.IntegrityError):
        insert_case(base, "CASE-000000000002", RUN_A, incident, series="S-1", revision=1)


def test_17_case_outcome_check(base):
    incident = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_case(base, "CASE-000000000001", RUN_A, incident, outcome="RESOLVED")


def test_18_case_supersedes_fk(base):
    incident = insert_incident(base, RUN_A)
    with pytest.raises(sqlite3.IntegrityError):
        insert_case(base, "CASE-000000000002", RUN_A, incident, supersedes="CASE-FFFFFFFFFFFF")


def test_20_foreign_key_check_empty_after_fixture_inserts(base):
    insert_issue(base, 7)
    incident = insert_incident(base, RUN_A)
    work = insert_work(base, RUN_A, incident, 7, "RUNNING")
    base.execute(
        "INSERT INTO issue_bindings(routing_scope, repository_id, fingerprint_version,"
        " problem_fingerprint, issue_number, basis, decision_json, created_at)"
        " VALUES (?, 100001, 'v1', 'fp', 7, 'CREATED', '{}', ?)",
        (f"eval:{RUN_A}", NOW),
    )
    base.execute(
        "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
        " body_sha256, decision, received_at, payload_json, checks_json)"
        " VALUES ('PROP-000000000001', ?, ?, ?, 'ATT-000000000001', 'k', ?, 'RECEIVED', ?,"
        " '{}', '[]')",
        (RUN_A, incident, work, "0" * 64, NOW),
    )
    base.execute(
        "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
        " logical_key, idempotency_key, request_sha256, status, stage, intended_at, updated_at,"
        " request_json, result_json) VALUES ('EXE-000000000001', ?, ?, ?, 'PROP-000000000001',"
        " 'CREATE_PR', 'pr:x', 'k', ?, 'INTENDED', 'intent', ?, ?, '{}', '{}')",
        (RUN_A, incident, work, "0" * 64, NOW, NOW),
    )
    base.execute(
        "INSERT INTO verifications(id, run_id, incident_id, execution_id, origin, verdict,"
        " contract_id, contract_sha256, started_at, ended_at, result_json)"
        " VALUES ('VER-000000000001', ?, ?, 'EXE-000000000001', 'human_injected_negative', 'FAIL',"
        " 'defect-summary-v1', ?, ?, ?, '{}')",
        (RUN_A, incident, "0" * 64, NOW, NOW),
    )
    insert_notification(base, RUN_A, incident, "notify:intake:x:3")
    insert_case(base, "CASE-000000000001", RUN_A, incident)
    insert_case(
        base, "CASE-000000000002", RUN_A, incident, revision=2, supersedes="CASE-000000000001"
    )
    assert count(base, "case_notes") == 2
    assert base.execute("PRAGMA foreign_key_check").fetchall() == []
