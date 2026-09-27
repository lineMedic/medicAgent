"""W06 통합 테스트: 저장소 연결·migration·트랜잭션·SQLITE_BUSY 재시도·run 생성·outbox intent."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane import runs
from linemedic.control_plane import store as store_module
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.store import (
    MigrationError,
    Store,
    StoreBusy,
    migrate,
    migration_files,
    split_statements,
)
from linemedic.tests.helpers.db_rows import (
    count,
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN = "r-20260927-030000-abcd"


# ── 연결·트랜잭션 ─────────────────────────────────────────────


def test_tx_commits_and_rolls_back(store, conn):
    with store.tx() as tx:
        insert_run_sql = (
            "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES (?, 0, ?, '{}')"
        )
        tx.execute(insert_run_sql, (RUN, tx.now))
    assert count(conn, "demo_runs") == 1
    with pytest.raises(RuntimeError), store.tx() as tx:
        tx.execute(insert_run_sql, ("r-20260927-030001-abcd", tx.now))
        raise RuntimeError("중간 실패")
    assert count(conn, "demo_runs") == 1


def test_tx_now_uses_injected_clock(store):
    with store.tx() as tx:
        assert tx.now == "2026-09-27T00:00:00.000000Z"


def test_read_transaction_is_query_only(store):
    with pytest.raises(sqlite3.OperationalError), store.read() as tx:
        tx.execute(
            "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES ('x', 0, 'x', '{}')"
        )


def test_busy_begin_retries_then_stops(store, fake_clock):
    sleeps = []
    busy = Store(store.path, fake_clock, busy_timeout_ms=10, busy_retries=2, sleep=sleeps.append)
    blocker = sqlite3.connect(str(store.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreBusy), busy.tx():
            pass
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert len(sleeps) == 2  # 처음 1회 + 재시도 2회, 사이의 대기 2번
    with busy.tx() as tx:  # 잠금이 풀리면 정상
        assert tx.one("SELECT 1")[0] == 1


def test_tx_object_has_no_network_client(store):
    with store.tx() as tx:
        assert set(type(tx).__slots__) == {"_conn", "now"}


# ── migration ─────────────────────────────────────────────────


def test_split_statements_ignores_semicolons_in_comments_and_strings():
    sql = (
        "-- 설명; 주석\nCREATE TABLE t (a TEXT DEFAULT 'x;y');\n"
        "-- 끝\nINSERT INTO t VALUES ('1;2');\n"
    )
    assert split_statements(sql) == [
        "-- 설명; 주석\nCREATE TABLE t (a TEXT DEFAULT 'x;y');",
        "-- 끝\nINSERT INTO t VALUES ('1;2');",
    ]
    with pytest.raises(MigrationError):
        split_statements("CREATE TABLE t (a TEXT)")


def test_migration_files_reject_bad_names(tmp_path):
    (tmp_path / "0001_ok.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "bad.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        migration_files(tmp_path)


def test_failed_migration_is_rolled_back(tmp_path, fake_clock):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_init.sql").write_text(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);\n"
        "CREATE TABLE a (x INTEGER);\nINSERT INTO missing_table VALUES (1);\n",
        encoding="utf-8",
    )
    connection = Store(tmp_path / "db.sqlite", fake_clock).connect()
    with pytest.raises(sqlite3.OperationalError):
        migrate(connection, fake_clock, migrations)
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert tables == []
    connection.close()


def test_migration_applied_by_another_process_is_skipped(tmp_path, fake_clock, monkeypatch):
    """두 프로세스가 동시에 처음 migration할 때 늦은 쪽은 트랜잭션 안에서 다시 확인하고 건너뛴다."""
    db = tmp_path / "race.db"
    first, second = Store(db, fake_clock).connect(), Store(db, fake_clock).connect()
    real = store_module.applied_versions
    reads = {"count": 0}

    def stale_before_transaction(conn):
        reads["count"] += 1
        return set() if reads["count"] == 1 else real(conn)  # 첫 읽기는 먼저 적용되기 전 값

    assert migrate(first, fake_clock) == [1, 2]
    monkeypatch.setattr(store_module, "applied_versions", stale_before_transaction)
    assert migrate(second, fake_clock) == []
    assert second.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    first.close()
    second.close()


def test_db_with_unknown_migration_is_rejected(store, conn):
    conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (99, 'x')")
    with pytest.raises(MigrationError):
        store.migrate()


# ── run 생성 ─────────────────────────────────────────────────


def settings_without_secrets():
    return load_settings(
        REPO_ROOT / "config" / "linemedic.toml", {"CONTROL_OPERATOR_TOKEN": "t" * 40}
    )


def test_create_run_switches_active_run_and_records_manifest(store, conn, tmp_path, fake_clock):
    host = tmp_path / "host-manifest.json"
    host.write_text('{"os": "test"}\n', encoding="utf-8")
    first = runs.new_run(store, settings_without_secrets(), fake_clock, host)
    second = runs.new_run(store, settings_without_secrets(), fake_clock)
    assert second["previous_run_id"] == first["run_id"]
    active = conn.execute("SELECT id FROM demo_runs WHERE active = 1").fetchall()
    assert [r[0] for r in active] == [second["run_id"]]
    manifest = json.loads(
        conn.execute(
            "SELECT config_json FROM demo_runs WHERE id = ?", (first["run_id"],)
        ).fetchone()[0]
    )
    assert manifest["routing_scope"] == f"eval:{first['run_id']}"
    assert manifest["config_hash"] == settings_without_secrets().config_hash()
    assert manifest["host_manifest"] == {
        "path": str(host),
        "sha256": hashlib.sha256(host.read_bytes()).hexdigest(),
    }
    assert "t" * 40 not in json.dumps(manifest)  # 비밀 값은 manifest에 없다
    events = [r[0] for r in conn.execute("SELECT event_type FROM audit_events ORDER BY seq")]
    assert events == ["RUN_CREATED", "RUN_DEACTIVATED", "RUN_CREATED"]


def test_create_run_rejects_bad_run_id(store):
    with pytest.raises(ValueError), store.tx() as tx:
        runs.create_run(tx, "../bad", {})


def test_cli_run_new(tmp_path):
    db = tmp_path / "runs" / "linemedic.db"
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "linemedic.cli",
            "run-new",
            "--db",
            str(db),
            "--env-file",
            str(env_file),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT)},
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["db"] == str(db) and summary["host_manifest"] is None
    connection = sqlite3.connect(str(db))
    assert (
        connection.execute("SELECT id FROM demo_runs WHERE active = 1").fetchone()[0]
        == summary["run_id"]
    )
    connection.close()


def test_default_db_path():
    assert runs.default_db_path({}) == Path("runs/linemedic.db")
    assert runs.default_db_path({"RUNS_DIR": "/srv/runs"}) == Path("/srv/runs/linemedic.db")


# ── outbox intent ────────────────────────────────────────────


@pytest.fixture
def work(conn):
    insert_run(conn, RUN)
    insert_issue(conn, 3)
    incident = insert_incident(conn, RUN, "PR_OPENED")
    work_id = insert_work(conn, RUN, incident, 3, "WAITING_REVIEW")
    return dict(conn.execute("SELECT * FROM work_items WHERE id = ?", (work_id,)).fetchone())


def test_outbox_enqueue_uses_server_logical_key_and_dedupes(store, conn, work):
    with store.tx() as tx:
        first = outbox.enqueue(tx, work, "WORK_BLOCKED", {"reason": "x"}, "github-issue-primary")
        again = outbox.enqueue(tx, work, "WORK_BLOCKED", {"reason": "x"}, "github-issue-primary")
    assert first == again
    stored = conn.execute("SELECT * FROM notifications").fetchall()
    assert len(stored) == 1
    assert stored[0]["logical_key"] == f"notify:{work['id']}:1:WORK_BLOCKED:1:github-issue-primary"
    assert (stored[0]["status"], stored[0]["attempt_count"]) == ("PENDING", 0)


def test_outbox_same_key_different_payload_conflicts(store, work):
    with store.tx() as tx:
        outbox.enqueue(tx, work, "WORK_BLOCKED", {"reason": "x"}, "github-issue-primary")
        with pytest.raises(outbox.OutboxConflict):
            outbox.enqueue(tx, work, "WORK_BLOCKED", {"reason": "y"}, "github-issue-primary")


def test_outbox_rejects_work_starting_without_work_and_bad_values(store, work):
    with store.tx() as tx:
        with pytest.raises(ValueError):
            outbox.enqueue(
                tx, None, "WORK_STARTING", {}, "github-issue-primary", run_id=RUN, incident_id="x"
            )
        with pytest.raises(ValueError):
            outbox.enqueue(tx, work, "DELIVERED_TO_HUMAN", {}, "github-issue-primary")
        with pytest.raises(ValueError):
            outbox.enqueue(tx, work, "WORK_BLOCKED", {}, "https://evil.example/hook")
        intake = outbox.enqueue(
            tx,
            None,
            "WORK_BLOCKED",
            {},
            "github-issue-primary",
            run_id=RUN,
            incident_id=work["incident_id"],
        )
    assert intake.startswith("NOT-")
