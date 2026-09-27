"""W07 통합 테스트: 감지기 60초 창·사건 생성·병합·증거·로그 보관 (실제 SQLite + FakeClock).

- 60초 안 2회 → 사건 없음, 3회 → 사건 1개, 그 뒤는 같은 사건의 count 증가
- 활성 사건이 있으면 병합, 가장 최근 사건이 terminal이면 그 사건에 붙이고 새 사건을 만들지 않는다
- 로그·증거는 비밀과 평가 전용 식별자를 가려 저장한다
"""

import json
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane.detector import (
    Detector,
    DetectorSettings,
    problem_fingerprint,
    settings_for_run,
    signature,
)
from linemedic.control_plane.log_store import FileLogStore, LogRecord, MemoryLogStore
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.tests.helpers.db_rows import count, insert_run

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN = "r-20260927-070000-abcd"
OTHER_RUN = "r-20260927-070001-beef"
SOURCE = "container:linemedic-mes-test"
HOLDOUT_LOT = "L3-HOLDOUT-201"


def error_line(n: int = 0, **changes) -> str:
    event = {
        "ts": f"2026-09-27T01:00:{n % 60:02d}.000000Z",
        "level": "ERROR",
        "service": "mes-api",
        "event": "request_failed",
        "request_id": f"{n:032x}",
        "lot_id": "L3-0927-118",
        "path": "/defects/summary",
        "status": 500,
        "error_type": "KeyError",
        "error_field": "inspector_id",
        "top_frame": "app.defects:summarize",
        "top_frame_line": 7,
    }
    event.update(changes)
    return json.dumps(event)


INFO_LINE = json.dumps(
    {"level": "INFO", "event": "request_completed", "path": "/defects/summary", "status": 200}
)


def make_detector(store, fake_clock, run_id=RUN, logs=None, terms=None, **overrides):
    settings = DetectorSettings(
        run_id=run_id,
        routing_scope=f"eval:{run_id}",
        service="mes-api",
        line_id="L3",
        repository_id=100001,
        **overrides,
    )
    return Detector(
        store,
        settings,
        fake_clock,
        logs if logs is not None else MemoryLogStore(),
        terms if terms is not None else eval_identifiers(),
    )


@pytest.fixture
def detector(store, conn, fake_clock):
    insert_run(conn, RUN)
    return make_detector(store, fake_clock)


def feed(detector, clock, times: list[float], line_factory=error_line):
    """times[i]초(시작 기준)에 오류 줄을 하나씩 넣는다."""
    start = clock.monotonic()
    outcomes = []
    for i, at in enumerate(times):
        clock.advance(start + at - clock.monotonic())
        outcomes.append(detector.observe_line(line_factory(i), SOURCE))
    return outcomes


def incidents(conn):
    return conn.execute("SELECT * FROM incidents ORDER BY rowid").fetchall()


def test_two_errors_in_window_create_no_incident(detector, conn, fake_clock):
    outcomes = feed(detector, fake_clock, [0, 20])
    assert [o.error for o in outcomes] == [True, True]
    assert all(o.incident_id is None for o in outcomes)
    assert incidents(conn) == []


def test_third_error_creates_one_new_incident(detector, conn, fake_clock):
    outcomes = feed(detector, fake_clock, [0, 20, 40])
    assert outcomes[-1].action == "created"
    [incident] = incidents(conn)
    assert outcomes[-1].incident_id == incident["id"]
    assert (incident["status"], incident["source_kind"], incident["count"]) == ("NEW", "LOG", 3)
    assert (incident["service"], incident["line_id"], incident["repository_id"]) == (
        "mes-api",
        "L3",
        100001,
    )
    assert incident["routing_scope"] == f"eval:{RUN}"
    assert incident["fingerprint_version"] == "fp-v1"
    assert incident["fingerprint"] == problem_fingerprint(
        signature(json.loads(error_line()), "mes-api")
    )
    assert (incident["first_seen"], incident["last_seen"]) == (
        "2026-09-27T00:00:00.000000Z",
        "2026-09-27T00:00:40.000000Z",
    )
    assert json.loads(incident["details_json"])["signature"] == {
        "service": "mes-api",
        "error_type": "KeyError:inspector_id",
        "top_frame": "app.defects:summarize",
        "endpoint": "/defects/summary",
    }
    assert count(conn, "evidence") == 3
    audit = conn.execute("SELECT actor, event_type, payload_json FROM audit_events").fetchall()
    assert [(a["actor"], a["event_type"]) for a in audit] == [("detector", "INCIDENT_DETECTED")]
    assert json.loads(audit[0]["payload_json"])["count"] == 3


def test_later_errors_increase_count_of_same_incident(detector, conn, fake_clock):
    feed(detector, fake_clock, [i * 5 for i in range(10)])
    [incident] = incidents(conn)
    assert incident["count"] == 10
    assert incident["version"] == 0  # count 갱신은 상태 전이가 아니다
    assert count(conn, "evidence") == 10


def test_evidence_is_capped_but_count_keeps_growing(detector, conn, fake_clock):
    feed(detector, fake_clock, [i for i in range(25)])
    [incident] = incidents(conn)
    assert incident["count"] == 25
    assert count(conn, "evidence") == 20


def test_window_is_inclusive_at_60_seconds(detector, conn, fake_clock):
    feed(detector, fake_clock, [0, 30, 60])
    assert len(incidents(conn)) == 1


def test_errors_older_than_window_do_not_count(detector, conn, fake_clock):
    feed(detector, fake_clock, [0, 30, 61])
    assert incidents(conn) == []
    feed(detector, fake_clock, [1])  # 30·61·62초 → 60초 안 3회
    assert len(incidents(conn)) == 1


def test_different_fingerprints_do_not_combine(detector, conn, fake_clock):
    detector.observe_line(error_line(1), SOURCE)
    detector.observe_line(error_line(2), SOURCE)
    detector.observe_line(error_line(3, error_field="defect_id"), SOURCE)
    assert incidents(conn) == []


def test_varying_request_and_lot_ids_join_one_incident(detector, conn, fake_clock):
    for i, lot in enumerate(["L3-0927-118", "L3-0927-120", "L3-0927-130", "L3-0927-140"]):
        detector.observe_line(error_line(i, lot_id=lot), SOURCE)
    [incident] = incidents(conn)
    assert incident["count"] == 4


def test_terminal_incident_absorbs_new_logs_without_new_incident(detector, conn, fake_clock):
    feed(detector, fake_clock, [0, 1, 2])
    [incident] = incidents(conn)
    conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (incident["id"],))
    fake_clock.advance(3600)
    outcomes = feed(detector, fake_clock, [0, 1, 2, 3])
    assert {o.incident_id for o in outcomes} == {incident["id"]}
    assert {o.action for o in outcomes} == {"updated"}
    [same] = incidents(conn)
    assert (same["status"], same["count"]) == ("ESCALATED", 7)
    assert count(conn, "work_items") == 0


def test_same_fingerprint_in_other_run_is_separate(store, conn, fake_clock):
    insert_run(conn, RUN, active=0)
    insert_run(conn, OTHER_RUN)
    for run_id in (RUN, OTHER_RUN):
        watcher = make_detector(store, fake_clock, run_id=run_id)
        for i in range(3):
            watcher.observe_line(error_line(i), SOURCE)
    rows = incidents(conn)
    assert len(rows) == 2
    assert rows[0]["fingerprint"] == rows[1]["fingerprint"]
    assert {r["run_id"] for r in rows} == {RUN, OTHER_RUN}


def test_log_line_service_claim_is_ignored(detector, conn, fake_clock):
    for i in range(3):
        detector.observe_line(error_line(i, service="vision-inspection"), SOURCE)
    [incident] = incidents(conn)
    assert incident["service"] == "mes-api"


def test_non_error_lines_are_archived_but_not_counted(store, conn, fake_clock):
    insert_run(conn, RUN)
    logs = MemoryLogStore()
    watcher = make_detector(store, fake_clock, logs=logs)
    summary = watcher.observe_lines(
        [INFO_LINE, "Traceback (most recent call last):", "", error_line(1)], SOURCE
    )
    assert (summary.lines, summary.errors, summary.created) == (3, 1, [])
    archived = logs.read(RUN, "mes-api", "", "9999")
    assert [r.line for r in archived] == [
        INFO_LINE,
        "Traceback (most recent call last):",
        error_line(1),
    ]
    assert all(r.source == SOURCE for r in archived)


def test_secrets_and_eval_identifiers_are_redacted_in_logs_and_evidence(store, conn, fake_clock):
    insert_run(conn, RUN)
    logs = MemoryLogStore()
    watcher = make_detector(store, fake_clock, logs=logs)
    token = "ghp_" + "Z" * 36
    for i in range(3):
        watcher.observe_line(error_line(i, lot_id=HOLDOUT_LOT, note=f"token {token}"), SOURCE)
    archived = " ".join(r.line for r in logs.read(RUN, "mes-api", "", "9999"))
    stored = " ".join(r[0] for r in conn.execute("SELECT payload_json FROM evidence"))
    for text in (archived, stored):
        assert token not in text and HOLDOUT_LOT not in text
        assert "[REDACTED:github_token]" in text and "[REDACTED:eval]" in text


def test_observe_lines_summary(detector, fake_clock):
    summary = detector.observe_lines([error_line(i) for i in range(5)], SOURCE)
    assert (summary.lines, summary.errors, len(summary.created), len(summary.updated)) == (
        5,
        5,
        1,
        1,
    )
    assert summary.created == summary.updated


def test_settings_for_run_uses_service_catalog():
    config = load_settings(REPO_ROOT / "config" / "linemedic.toml", {}).config
    settings = settings_for_run(config, RUN, f"eval:{RUN}", "mes-api")
    assert (settings.line_id, settings.repository_id) == ("L3", 0)
    assert (settings.window_seconds, settings.min_occurrences) == (60, 3)
    with_repo = load_settings(
        REPO_ROOT / "config" / "linemedic.toml", {"GITHUB_REPOSITORY_ID": "4242"}
    )
    assert settings_for_run(with_repo.config, RUN, f"eval:{RUN}", "mes-api").repository_id == 4242
    with pytest.raises(ValueError):
        settings_for_run(config, RUN, f"eval:{RUN}", "unknown-service")


def test_file_log_store_roundtrip_and_cap(tmp_path):
    logs = FileLogStore(tmp_path, max_bytes=300)
    first = LogRecord("2026-09-27T00:00:00.000000Z", SOURCE, "a")
    second = LogRecord("2026-09-27T00:10:00.000000Z", SOURCE, "b")
    assert logs.append(RUN, "mes-api", first) and logs.append(RUN, "mes-api", second)
    assert logs.read(
        RUN, "mes-api", "2026-09-27T00:05:00.000000Z", "2026-09-27T01:00:00.000000Z"
    ) == [second]
    assert logs.path(RUN, "mes-api") == tmp_path / RUN / "logs" / "mes-api.jsonl"
    big = LogRecord("2026-09-27T00:20:00.000000Z", SOURCE, "x" * 400)
    assert logs.append(RUN, "mes-api", big) is False and logs.dropped == 1
    with pytest.raises(ValueError):
        logs.path("../etc", "mes-api")
    with pytest.raises(ValueError):
        logs.path(RUN, "../passwd")
