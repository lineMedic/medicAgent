"""W18 T-UI-01: 화면 escape·읽기 전용 연결·127.0.0.1 bind·GET만 (D49·D85).

Issue 제목·로그 근거·case 본문·운영자 메모에 `<script>`·`<img onerror>`·`javascript:` 링크를 넣고
실제 읽기 모델로 렌더링한다. 문자열로만 보이고 실행 가능한 태그·링크가 생기지 않아야 한다.
"""

import json
import re
import sqlite3

import pytest
from starlette.testclient import TestClient

from linemedic.common.ids import new_id
from linemedic.dashboard import __main__ as dashboard
from linemedic.tests.helpers.api import RUN
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_work

SCRIPT = "<script>alert(1)</script>"
IMG = "<img src=x onerror=alert(1)>"
LINK = '<a href="javascript:alert(1)">클릭</a>'
BARE = "javascript:alert(1)"
ATTACK = f"{SCRIPT} {IMG} {LINK} {BARE}"
ACTIVE_ATTRIBUTE = re.compile(r"<[a-z][^>]*\s(?:href|src|on[a-z]+)\s*=")


@pytest.fixture
def db(store, conn):
    conn.execute(
        "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES (?, 1, ?, ?)",
        (RUN, "2026-09-27T00:00:00.000000Z", json.dumps({"runtime_env": {"demo_host_id": IMG}})),
    )
    insert_issue(conn, 42)
    conn.execute(
        "UPDATE github_issues SET payload_json = ? WHERE issue_number = 42",
        (json.dumps({"title": ATTACK}),),
    )
    incident = insert_incident(conn, RUN, "ESCALATED", details_json="{}")
    work = insert_work(conn, RUN, incident, 42, "BLOCKED", reason_code="PERMISSION_REQUIRED")
    evidence = [
        ("log_error", {"event": {"error_type": SCRIPT, "path": LINK, "message": IMG}}),
        ("history_projection", {"note_id": SCRIPT, "outcome": "BLOCKED", "summary": ATTACK}),
    ]
    for kind, payload in evidence:
        conn.execute(
            "INSERT INTO evidence(id, run_id, incident_id, kind, observed_at, source_identity,"
            " payload_json, content_sha256) VALUES (?, ?, ?, ?, ?, 'test', ?, ?)",
            (
                new_id("EV"),
                RUN,
                incident,
                kind,
                "2026-09-27T01:00:00.000000Z",
                json.dumps(payload),
                "0" * 64,
            ),
        )
    conn.execute(
        "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
        " logical_key, payload_sha256, status, created_at, updated_at, payload_json, result_json)"
        " VALUES (?, ?, ?, ?, 'WORK_BLOCKED', ?, 'k-1', ?, 'FAILED', ?, ?, ?, '{}')",
        (
            new_id("NOT"),
            RUN,
            incident,
            work,
            "route-" + SCRIPT,
            "0" * 64,
            "2026-09-27T02:00:00.000000Z",
            "2026-09-27T02:00:00.000000Z",
            json.dumps({"blocker_code": "PERMISSION_REQUIRED", "operator_note": ATTACK}),
        ),
    )
    return store.path


def test_untrusted_text_is_rendered_as_text_not_markup(db):
    html = dashboard.render(dashboard.load_model(db))
    lowered = html.lower()
    assert "<script" not in lowered  # 페이지에 script가 전혀 없다
    assert "<img" not in lowered  # onerror 속성을 가질 실제 태그가 없다
    assert "<a " not in lowered  # 데이터로 링크를 만들지 않는다
    assert ACTIVE_ATTRIBUTE.search(lowered) is None  # 실제 태그의 href·src·on* 속성이 없다
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;a href=&#34;javascript:alert(1)&#34;&gt;" in html
    assert html.count("&lt;script&gt;") >= 4  # 제목·근거·사례·운영자 메모·route 모두 문자열로


def test_page_has_no_script_and_a_script_blocking_csp(db):
    client = TestClient(dashboard.create_app(db))
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "<script" not in response.text.lower()


def test_only_get_is_served(db):
    client = TestClient(dashboard.create_app(db))
    assert client.head("/").status_code == 200
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/").status_code == 405
    assert client.get("/ops/dashboard").status_code == 404
    assert client.get("/tools/incidents").status_code == 404


def test_connection_is_read_only(db):
    conn = dashboard.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO demo_runs(id, active, created_at, config_json)"
                " VALUES ('x', 0, 'x', '{}')"
            )
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        conn.execute("PRAGMA query_only = OFF")  # 이것을 풀어도 파일 자체가 읽기 전용(mode=ro)이다
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM demo_runs")
    finally:
        conn.close()


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        dashboard.connect_readonly(missing)
    assert dashboard.main(["--db", str(missing), "--env-file", str(tmp_path / "none.env")]) == 2
    assert not missing.exists()


def test_server_binds_only_to_localhost(db, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dashboard.uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    env = ["--env-file", str(tmp_path / "none.env")]
    assert dashboard.main(["--db", str(db), "--port", "8123", *env]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123, "log_level": "warning"}]
    with pytest.raises(SystemExit):  # bind 주소를 바꾸는 옵션이 없다
        dashboard.main(["--db", str(db), "--host", "0.0.0.0", *env])
    assert dashboard.main(["--db", str(db), "--port", "0", *env]) == 2
    assert dashboard.main(["--db", str(db), "--run-id", "../x", *env]) == 2
    assert len(calls) == 1


def test_empty_database_shows_no_run_message(store):
    html = dashboard.render(dashboard.load_model(store.path))
    assert "기록된 run이 없다" in html
