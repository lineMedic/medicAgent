"""S5-new live: 승인된 작성자가 만든 새 Issue 1개를 polling으로 감지 (W23, G2 필요).

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
