"""테스트 전용 행 생성 도우미 (W06). 운영 API로 노출하지 않는다.

제품 코드에는 상태를 임의로 정하는 경로가 없다(force-resolve·상태 PATCH 금지). 테스트는 이 도우미로
원하는 출발 상태의 행을 직접 넣고, 전이는 제품 함수(`state.py`)로만 한다.
"""

import json
import sqlite3
from typing import Any

from linemedic.common.ids import new_id

NOW = "2026-09-27T00:00:00.000000Z"
REPOSITORY_ID = 100001


def insert_run(conn: sqlite3.Connection, run_id: str, active: int = 1) -> str:
    conn.execute(
        "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES (?, ?, ?, '{}')",
        (run_id, active, NOW),
    )
    return run_id


def insert_incident(
    conn: sqlite3.Connection,
    run_id: str,
    status: str = "NEW",
    *,
    fingerprint: str | None = None,
    **overrides: Any,
) -> str:
    incident_id = overrides.pop("id", None) or new_id("INC")
    row = {
        "id": incident_id,
        "run_id": run_id,
        "routing_scope": f"eval:{run_id}",
        "repository_id": REPOSITORY_ID,
        "fingerprint": fingerprint or f"fp-{incident_id}",
        "fingerprint_version": "v1",
        "source_kind": "LOG",
        "status": status,
        "service": "mes-api",
        "line_id": "L3",
        "first_seen": NOW,
        "last_seen": NOW,
        "details_json": "{}",
        **overrides,
    }
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO incidents({columns}) VALUES ({marks})", list(row.values()))
    return incident_id


def insert_issue(
    conn: sqlite3.Connection, issue_number: int, repository_id: int = REPOSITORY_ID
) -> int:
    conn.execute(
        "INSERT INTO github_issues(repository_id, issue_number, node_id, state, author_id,"
        " created_at, updated_at, snapshot_sha256, payload_json, last_observed_at)"
        " VALUES (?, ?, ?, 'open', 1, ?, ?, ?, '{}', ?)",
        (repository_id, issue_number, f"I_node_{issue_number}", NOW, NOW, "0" * 64, NOW),
    )
    return issue_number


def insert_work(
    conn: sqlite3.Connection,
    run_id: str,
    incident_id: str,
    issue_number: int,
    status: str = "WAITING_APPROVAL",
    **overrides: Any,
) -> str:
    work_id = overrides.pop("id", None) or new_id("WORK")
    row = {
        "id": work_id,
        "run_id": run_id,
        "incident_id": incident_id,
        "routing_scope": f"eval:{run_id}",
        "repository_id": REPOSITORY_ID,
        "issue_number": issue_number,
        "generation": 1,
        "status": status,
        "issue_snapshot_sha256": "1" * 64,
        "authorization_json": json.dumps({"basis": "test"}),
        "created_at": NOW,
        "updated_at": NOW,
        "details_json": "{}",
        **overrides,
    }
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO work_items({columns}) VALUES ({marks})", list(row.values()))
    return work_id


def row(conn: sqlite3.Connection, table: str, row_id: str) -> sqlite3.Row:
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
