"""W18 실제 대시보드 프로세스: `python -m linemedic.dashboard`가 127.0.0.1에서 답하고
DB를 바꾸지 않는다.
"""

import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from linemedic.common.clock import SystemClock
from linemedic.control_plane import runs
from linemedic.control_plane.store import Store
from linemedic.tests.helpers.api import RUN

REPO_ROOT = Path(__file__).resolve().parents[3]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_dashboard_process_serves_localhost_and_leaves_the_db_unchanged(tmp_path):
    db = tmp_path / "linemedic.db"
    store = Store(db, SystemClock())
    store.migrate()
    with store.tx() as tx:
        runs.create_run(tx, RUN, {"run_id": RUN, "routing_scope": f"eval:{RUN}"})
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RUNS_DIR", "CONTROL_"))}
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "linemedic.dashboard", "--db", str(db), "--port", str(port),
            "--env-file", str(tmp_path / "none.env"),
        ],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/")
                break
            except httpx.TransportError:
                assert proc.poll() is None, proc.stderr.read() if proc.stderr else ""
                assert time.monotonic() < deadline, "대시보드가 뜨지 않았다"
                time.sleep(0.2)
        assert response.status_code == 200
        assert RUN in response.text and "읽기 전용" in response.text
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert httpx.post(f"http://127.0.0.1:{port}/").status_code == 405
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # 읽기만 했다
