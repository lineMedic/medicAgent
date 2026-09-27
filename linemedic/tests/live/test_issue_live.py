"""S5-new(W23)·S4(W24) live: Issue polling 감지와 로그 → Issue 신규/재사용/모호 (G2·G10 필요).

S4-new·existing은 실제 Issue를 1개 만들므로 G10 `write_enabled=true`와 이번 실행의 허락 표시
`LINEMEDIC_CONFIRM_GITHUB_WRITE=1`이 모두 있어야 하고, S4-ambiguous는 사람이 후보 Issue 2개를 먼저
만든 뒤 `LINEMEDIC_LIVE_S4_AMBIGUOUS=1`로 실행한다. 결과는 `evidence/S4-issue-live.md`에 남긴다.

S5-new live: 승인된 작성자가 만든 새 Issue 1개를 polling으로 감지 (W23, G2 필요).

`make test-live`에서만 실행되고, G2 env와 이번 실행의 표시 `LINEMEDIC_LIVE_S5=1`이 있어야 한다.
임시 제어 DB로 관찰(initial import)을 먼저 하고, 사람이 등록 repo에 새 Issue를 만들 때까지
`poll_interval_seconds` 간격으로 조회한다(`LINEMEDIC_LIVE_S5_TIMEOUT`, 기본 600초).
감지하면 Issue 생성 시각과 감지 시각을 `evidence/S5-new-issue-detect.md`에 남긴다.
"60초 안 탐지"를 SLA로 적지 않는다. GitHub에는 읽기만 한다(`write_enabled`와 무관).
"""

import os
import threading
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_CONFIG_PATH, load_settings, process_env
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.store import Store
from linemedic.integrations.github import GitHubNotConfigured, github_from_settings
from linemedic.tests.helpers.db_rows import insert_run

EVIDENCE = Path("evidence") / "S5-new-issue-detect.md"
RUN = "r-20260927-000000-5e5e"


@pytest.mark.live_github
def test_new_issue_from_trusted_author_is_detected_by_polling(tmp_path):
    if os.environ.get("LINEMEDIC_LIVE_S5") != "1":
        pytest.skip("이번 실행 표시(LINEMEDIC_LIVE_S5=1)가 없다: 사람이 새 Issue를 만들어야 한다")
    env = {**process_env(), "ISSUE_INTAKE_ENABLED": "true"}
    settings = load_settings(DEFAULT_CONFIG_PATH, env)
    try:
        port = github_from_settings(settings)
    except GitHubNotConfigured as exc:
        pytest.skip(f"NOT_CONFIGURED (G2): {exc}")
    clock = SystemClock()
    store = Store(tmp_path / "control.db", clock)
    store.migrate()
    conn = store.connect()
    insert_run(conn, RUN)
    sync = IssueSync(
        store,
        port,
        run_id=RUN,
        config=settings.config,
        catalog=Catalog.from_config(settings.config),
        clock=clock,
        routing_scope=f"eval:{RUN}",
    )
    assert sync.poll_once().mode == "initial_import"
    print("관찰 완료: 이제 승인된 작성자 계정으로 등록 repo에 새 Issue를 만든다")
    timeout = float(os.environ.get("LINEMEDIC_LIVE_S5_TIMEOUT", "600"))
    stop = threading.Event()
    deadline = clock.monotonic() + timeout
    work = None
    while clock.monotonic() < deadline and work is None:
        result = sync.poll_once()
        assert result.error is None or result.error == "RateLimited", result.error
        work = conn.execute(
            "SELECT w.id, w.issue_number, g.created_at FROM work_items w JOIN github_issues g"
            " ON g.repository_id = w.repository_id AND g.issue_number = w.issue_number"
        ).fetchone()
        if work is None:
            stop.wait(sync.next_wait(result))
    assert work is not None, f"{timeout}초 안에 새 Issue를 감지하지 못했다"
    detected_at = to_rfc3339(clock.utc_now())
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        "# S5-new: 새 Issue polling 감지\n\n"
        f"- repo: {port.full_name} (ID {port.repository_id})\n"
        f"- Issue: #{work['issue_number']}, 생성 시각(GitHub): {work['created_at']}\n"
        f"- 감지 시각(UTC): {detected_at}\n"
        f"- work: {work['id']} (WAITING_APPROVAL, 자동 승인은 W25)\n"
        f"- poll 간격: {settings.config.issue_intake.poll_interval_seconds}초 (탐지 SLA 아님)\n",
        encoding="utf-8",
    )


# ── S4 (W24): 로그 → Issue 신규/재사용/모호 ─────────────────────

S4_EVIDENCE = Path("evidence") / "S4-issue-live.md"
S4_RUN = "r-20260927-000000-54ae"


def _s4_world(tmp_path, env):
    """임시 DB·eval scope의 sync·router·detector. G2가 없으면 skip."""
    from linemedic.control_plane.detector import Detector, settings_for_run
    from linemedic.control_plane.issue_router import IssueRouter
    from linemedic.control_plane.log_store import MemoryLogStore

    settings = load_settings(DEFAULT_CONFIG_PATH, env)
    try:
        port = github_from_settings(settings)
    except GitHubNotConfigured as exc:
        pytest.skip(f"NOT_CONFIGURED (G2): {exc}")
    clock = SystemClock()
    store = Store(tmp_path / "control.db", clock)
    store.migrate()
    conn = store.connect()
    insert_run(conn, S4_RUN)
    catalog = Catalog.from_config(settings.config)
    scope = f"eval:{S4_RUN}"
    sync = IssueSync(
        store,
        port,
        run_id=S4_RUN,
        config=settings.config,
        catalog=catalog,
        clock=clock,
        routing_scope=scope,
    )
    router = IssueRouter(store, port, sync, catalog=catalog, clock=clock)
    detector = Detector(
        store, settings_for_run(settings.config, S4_RUN, scope, "mes-api"), clock, MemoryLogStore()
    )
    return port, conn, sync, router, detector


def _s4_line(n: int, endpoint: str) -> str:
    import json

    return json.dumps(
        {
            "level": "ERROR",
            "service": "mes-api",
            "event": "request_failed",
            "path": endpoint,
            "status": 500,
            "error_type": "KeyError",
            "error_field": "inspector_id",
            "top_frame": "app.defects:summarize",
            "request_id": f"{n:032x}",
        }
    )


@pytest.mark.live_github
def test_s4_new_then_existing_keeps_issue_number(tmp_path):
    if os.environ.get("LINEMEDIC_CONFIRM_GITHUB_WRITE") != "1":
        pytest.skip("이번 실행의 쓰기 허락 표시(LINEMEDIC_CONFIRM_GITHUB_WRITE=1)가 없다")
    env = {**process_env(), "ISSUE_INTAKE_ENABLED": "true"}
    port, conn, sync, router, detector = _s4_world(tmp_path, env)
    if not port.write_enabled:
        pytest.skip("G10 전: config github.write_enabled=false (shadow 모드)")
    sync.poll_once()
    endpoint = f"/defects/s4-live-{S4_RUN[-4:]}"  # 이 시험 전용 signature(다른 Issue와 겹치지 않게)
    for n in range(3):
        outcome = detector.observe_line(_s4_line(n, endpoint), "live:s4")
    first = router.route(outcome.incident_id)
    assert first.action == "created", first
    conn.execute("UPDATE work_items SET status = 'BLOCKED'")  # 첫 generation 종료(W25 retry 흉내)
    conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (first.incident_id,))
    conn.execute(
        "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, status, service, line_id, count, first_seen,"
        " last_seen, details_json) SELECT 'INC-0000000054AE', run_id, routing_scope,"
        " repository_id, fingerprint, fingerprint_version, source_kind, 'NEW', service, line_id,"
        " count, first_seen, last_seen, details_json FROM incidents WHERE id = ?",
        (first.incident_id,),
    )
    second = router.route("INC-0000000054AE")
    assert (second.lookup, second.issue_number) == ("EXISTING_BINDING", first.issue_number)
    S4_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with S4_EVIDENCE.open("a", encoding="utf-8") as out:
        out.write(
            f"# S4 live ({to_rfc3339(SystemClock().utc_now())})\n\n"
            f"- S4-new: Issue #{first.issue_number} 생성, execution {first.execution_id}\n"
            f"- S4-existing: 같은 fingerprint의 새 incident → #{second.issue_number} 재사용\n"
        )


@pytest.mark.live_github
def test_s4_ambiguous_creates_nothing(tmp_path):
    """준비: 사람이 'mes-api KeyError /defects/s4-ambiguous' 제목의 open Issue 2개를 만든다."""
    if os.environ.get("LINEMEDIC_LIVE_S4_AMBIGUOUS") != "1":
        pytest.skip("후보 Issue 2개 준비 표시(LINEMEDIC_LIVE_S4_AMBIGUOUS=1)가 없다")
    port, conn, sync, router, detector = _s4_world(tmp_path, dict(process_env()))
    sync.poll_once()
    for n in range(3):
        outcome = detector.observe_line(_s4_line(n, "/defects/s4-ambiguous"), "live:s4")
    result = router.route(outcome.incident_id)
    assert result.lookup == "AMBIGUOUS" and len(result.detail["candidates"]) >= 2
    assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0  # 생성 없음
    S4_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with S4_EVIDENCE.open("a", encoding="utf-8") as out:
        out.write(f"- S4-ambiguous: 후보 {result.detail['candidates']} → 생성 0, 운영자 대기\n")
