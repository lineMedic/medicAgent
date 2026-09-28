"""W13 `make start` 조립·기동 복구·종료.

- 외부 연결이 없으면 그 기능만 끄고 뜬다(G2 전 GitHub, RUNNER_IMAGE_ID 없음).
  활성 run이 아니면 기동하지 않는다
- 기동 복구: 끝나지 않은 API 요청·SENDING 알림·CREATE_ISSUE intent는 UNKNOWN,
  끊긴 attempt는 이관. 아무것도 다시 실행하지 않는다
- pid 파일: 같은 run의 프로세스가 살아 있으면 기동하지 않는다. `make stop`은 pid가 이 run의
  LineMedic일 때만 SIGTERM을 보내고, 남은 파일은 지우며, 다른 프로세스에는 보내지 않는다
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from linemedic import cli
from linemedic.common.clock import SystemClock
from linemedic.common.config import load_settings
from linemedic.control_plane import main as control_main
from linemedic.control_plane import runs
from linemedic.control_plane.auth import TokenRegistry
from linemedic.control_plane.main import ControlPlaneError, build_control_plane
from linemedic.control_plane.memory import snapshot as memory_snapshot
from linemedic.control_plane.store import Store
from linemedic.integrations.docker import FakeDocker
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import OPERATOR_TOKEN, RUN
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run, insert_work

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO, REPO_ID = "demo-team/l3-mes-api", 100001
SETTINGS = load_settings(
    REPO_ROOT / "config" / "linemedic.toml",
    {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)},
)


def build(store, clock, tmp_path, settings=SETTINGS, **kwargs):
    return build_control_plane(
        settings,
        RUN,
        store=store,
        clock=clock,
        tokens=TokenRegistry(),
        runs_dir=tmp_path / "runs",
        docker=FakeDocker(),
        **kwargs,
    )


def test_without_external_connections_only_those_features_are_off(
    store, conn, fake_clock, tmp_path
):
    insert_run(conn, RUN)
    plane = build(store, fake_clock, tmp_path)
    assert plane.features["github"].startswith("off") and plane.features["release"].startswith(
        "off"
    )
    assert plane.features["patch_gate"].startswith("off") and plane.features["agent"].startswith(
        "off"
    )
    ctx = plane.context
    assert (ctx.issue_sync, ctx.issue_router, ctx.outbox_worker, ctx.release_executor) == (
        None,
        None,
        None,
        None,
    )
    assert ctx.execution_reconciler is not None and ctx.catalog is not None
    assert ctx.case_search is not None and ctx.case_search.mode == "cold_start"
    assert plane.features["memory"].startswith("on: cold_start")
    step = plane.step()
    assert step["detect"] is None  # MES container가 아직 없다
    assert (step["poll"], step["route"], step["outbox"], step["broker"]) == (None, [], [], [])
    assert step["cases"] == []
    assert plane.container == f"linemedic-mes-{RUN}"


def settings_with(**env: str):
    base = {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)}
    return load_settings(REPO_ROOT / "config" / "linemedic.toml", {**base, **env})


def test_memory_assisted_reads_the_snapshot_or_reports_it_off(store, conn, fake_clock, tmp_path):
    insert_run(conn, RUN)
    plane = build(
        store, fake_clock, tmp_path, settings=settings_with(MEMORY_MODE="memory_assisted")
    )
    assert plane.features["memory"] == (
        "off: memory_assisted인데 snapshot을 쓸 수 없다(snapshot_missing)"
    )
    with store.tx() as tx:
        snapshot = memory_snapshot.build_snapshot(tx, run_id=RUN)
    path = memory_snapshot.write_snapshot(snapshot, tmp_path / "snapshots")
    settings = settings_with(MEMORY_MODE="memory_assisted", MEMORY_SNAPSHOT_PATH=str(path))
    plane = build(store, fake_clock, tmp_path, settings=settings)
    assert plane.features["memory"] == f"on: memory_assisted {snapshot.snapshot_id}(노트 0개)"
    assert plane.context.case_search.snapshot_id == snapshot.snapshot_id
    path.write_text("{}", encoding="utf-8")  # 바뀐 manifest는 쓰지 않는다
    plane = build(store, fake_clock, tmp_path, settings=settings)
    assert plane.features["memory"].endswith("(snapshot_invalid)")


def test_inactive_run_is_not_started(store, conn, fake_clock, tmp_path):
    insert_run(conn, RUN, active=0)
    with pytest.raises(ControlPlaneError, match="활성 run"):
        build(store, fake_clock, tmp_path)


def test_startup_recovery_marks_unknown_and_reruns_nothing(store, conn, fake_clock, tmp_path):
    insert_run(conn, RUN)
    insert_issue(conn, 5)
    incident = insert_incident(conn, RUN, "NEW")
    running = insert_incident(conn, RUN, "INVESTIGATING", attempt_id="ATT-0000000000AA")
    insert_work(conn, RUN, running, 5, "RUNNING", attempt_id="ATT-0000000000AA")
    conn.execute(
        "INSERT INTO executions(id, run_id, incident_id, operation, logical_key, idempotency_key,"
        " request_sha256, status, stage, intended_at, updated_at, request_json, result_json)"
        " VALUES ('EXE-0000000000AA', ?, ?, 'CREATE_ISSUE', 'issue:x', 'issue:x', ?, 'INTENDED',"
        " 'intended', '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z', '{}', '{}')",
        (RUN, incident, "a" * 64),
    )
    conn.execute(
        "INSERT INTO api_requests(principal_scope, method, path, run_id, idempotency_key,"
        " body_sha256, status, response_json, created_at) VALUES ('operator:x', 'POST', '/ops/x',"
        " ?, 'k1', ?, 'RECEIVED', NULL, '2026-01-01T00:00:00.000000Z')",
        (RUN, "b" * 64),
    )
    github = FakeGitHub(REPO_ID, REPO, clock=fake_clock, write_enabled=True)
    plane = build(store, fake_clock, tmp_path, github=github)
    recovered = plane.recover()
    assert recovered["api_requests_unknown"] == 1
    assert recovered["issue_creates_unknown"] == ["EXE-0000000000AA"]
    execution = conn.execute("SELECT * FROM executions WHERE id = 'EXE-0000000000AA'").fetchone()
    assert (execution["status"], json.loads(execution["result_json"])["observation"]) == (
        "UNKNOWN",
        "interrupted_before_result",
    )
    assert conn.execute("SELECT status FROM incidents WHERE id = ?", (incident,)).fetchone()[0] == (
        "EXECUTION_UNKNOWN"
    )
    (closed,) = recovered["attempts_closed"]
    assert conn.execute("SELECT status FROM work_items WHERE id = ?", (closed,)).fetchone()[0] == (
        "BLOCKED"
    )
    assert github.requests == []  # 조회·재생성 없음
    again = plane.recover()
    assert (again["api_requests_unknown"], again["issue_creates_unknown"]) == (0, [])


def test_git_remote_is_the_registered_github_repo():
    assert control_main.git_remote_url(SETTINGS) == f"https://github.com/{REPO}.git"
    bare = load_settings(REPO_ROOT / "config" / "linemedic.toml", {})
    with pytest.raises(ControlPlaneError):
        control_main.git_remote_url(bare)


# ── pid 파일·종료 ─────────────────────────────────────────────


def test_pid_file_blocks_a_second_start_for_the_same_run(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    path = control_main.claim_pid_file(runs_dir, RUN)
    assert path.read_text().strip() == str(os.getpid())
    monkeypatch.setattr(control_main, "_ours", lambda pid, run_id: True)
    with pytest.raises(ControlPlaneError, match="이미 실행 중"):
        control_main.claim_pid_file(runs_dir, RUN)
    monkeypatch.setattr(control_main, "_ours", lambda pid, run_id: False)  # 남은 파일
    assert control_main.claim_pid_file(runs_dir, RUN) == path
    with pytest.raises(ControlPlaneError):
        control_main.claim_pid_file(runs_dir, "../x")


def test_stop_signals_only_this_runs_linemedic_process(tmp_path):
    runs_dir = tmp_path / "runs"
    assert control_main.stop(runs_dir, RUN) == "not_running"
    path = control_main.pid_path(runs_dir, RUN)
    path.parent.mkdir(parents=True)
    path.write_text(f"{os.getpid()}\n")  # 이 테스트 프로세스: run ID가 명령줄에 없다
    with pytest.raises(ControlPlaneError, match="LineMedic 프로세스가 아니다"):
        control_main.stop(runs_dir, RUN, wait_seconds=1)
    assert path.exists()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "linemedic", RUN]
    )
    reap_in_background(child)  # 끝난 뒤 좀비로 남아 pid가 보이지 않게
    try:
        path.write_text(f"{child.pid}\n")
        assert control_main.stop(runs_dir, RUN, wait_seconds=10) in ("stopped", "signal_sent")
        assert child.wait(timeout=10) != 0  # SIGTERM으로 끝났다
    finally:
        if child.poll() is None:
            child.kill()
    path.write_text(f"{child.pid}\n")  # 이제 없는 프로세스
    assert control_main.stop(runs_dir, RUN) == "stale_pid_removed" and not path.exists()


def test_cli_start_without_db_and_stop_exit_codes(tmp_path, monkeypatch):
    args = argparse.Namespace(
        run_id=RUN,
        db=tmp_path / "missing.db",
        manual_proposal=control_main.DEFAULT_MANUAL_PROPOSAL,
        config=cli.DEFAULT_CONFIG_PATH,
        env_file=tmp_path / "none.env",
    )
    assert cli._start(args) == 2
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    stop_args = argparse.Namespace(run_id=RUN, env_file=tmp_path / "none.env")
    assert cli._stop(stop_args) == 1  # 실행 중이 아니다
    bad = argparse.Namespace(run_id="not-a-run", env_file=tmp_path / "none.env")
    assert cli._stop(bad) == 2


def reap_in_background(proc: subprocess.Popen) -> None:
    threading.Thread(target=proc.wait, daemon=True).start()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_process_serves_the_api_and_stops_on_signal(tmp_path):
    """실제 `linemedic.cli start` 프로세스: 외부 연결 없이 뜨고, API가 답하고, stop으로 끝난다."""
    port = free_port()
    config = tmp_path / "linemedic.toml"
    text = (REPO_ROOT / "config" / "linemedic.toml").read_text(encoding="utf-8")
    config.write_text(text.replace("port = 8080", f"port = {port}", 1), encoding="utf-8")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    db = runs_dir / "linemedic.db"
    store = Store(db, SystemClock())
    store.migrate()
    with store.tx() as tx:
        runs.create_run(tx, RUN, {"run_id": RUN, "routing_scope": f"eval:{RUN}"})
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GITHUB_", "CONTROL_", "RUNNER_", "BASELINE_"))
    }
    env.update(RUNS_DIR=str(runs_dir), CONTROL_OPERATOR_TOKEN=OPERATOR_TOKEN)
    none_env = tmp_path / "none.env"
    argv = [sys.executable, "-m", "linemedic.cli"]
    common = ["--run-id", RUN, "--env-file", str(none_env)]
    proc = subprocess.Popen(
        [*argv, "start", *common, "--db", str(db), "--config", str(config)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/ops/executions/EXE-000000000000"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                answered = httpx.get(url, headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"})
                break
            except httpx.TransportError:
                assert proc.poll() is None, proc.stderr.read() if proc.stderr else ""
                assert time.monotonic() < deadline, "Control API가 뜨지 않았다"
                time.sleep(0.2)
        assert answered.status_code == 404  # operator 인증은 됐고 그 execution은 없다
        assert httpx.get(url).status_code == 401
        pid_file = runs_dir / RUN / control_main.PID_FILE
        assert pid_file.read_text().strip() == str(proc.pid)
        reap_in_background(proc)
        stopped = subprocess.run(
            [*argv, "stop", *common], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            timeout=60,
        )  # fmt: skip
        assert stopped.returncode == 0 and stopped.stdout.strip() in ("stopped", "signal_sent")
        assert proc.wait(timeout=30) == 0
        assert not pid_file.exists()
        assert proc.stdout is not None
        started = json.loads(proc.stdout.read())
        assert started["features"]["github"].startswith("off")
        assert started["features"]["agent"].startswith("on: scripted")
        assert started["recovered"]["attempts_closed"] == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
