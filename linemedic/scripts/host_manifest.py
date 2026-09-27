"""호스트 manifest (FR-16).

OS·kernel·arch·CPU·메모리·디스크·Python·SQLite(FTS5 여부)·git·Docker·OpenShell·runtime
버전을 모은다.
찾지 못한 도구는 `null`이다. 환경 변수 전체를 덤프하지 않고 `DEMO_HOST_ID`만 기록한다.
외부 명령은 고정 argv 리스트로만 실행한다(`shell=False`).
"""

import os
import platform
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, to_rfc3339

RunFn = Callable[[list[str]], str | None]
WhichFn = Callable[[str], str | None]

RUNTIME_COMMANDS: dict[str, list[str]] = {
    "openclaw": ["openclaw", "--version"],
    "nemoclaw": ["nemoclaw", "--version"],
    "nat": ["nat", "--version"],
}


def run_version_command(argv: list[str]) -> str | None:
    """고정 argv로 명령을 실행해 표준 출력의 첫 줄을 돌려준다. 실패하면 None."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else None


def fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(body)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def _tool_version(name: str, argv: list[str], run: RunFn, which: WhichFn) -> str | None:
    if which(name) is None:
        return None
    return run(argv) or "installed (version unknown)"


def _memory_bytes(run: RunFn) -> int | None:
    system = platform.system()
    if system == "Darwin":
        value = run(["sysctl", "-n", "hw.memsize"])
        return int(value) if value and value.isdigit() else None
    if system == "Linux":
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
    return None


def _docker(run: RunFn, which: WhichFn) -> dict[str, str | None] | None:
    if which("docker") is None:
        return None
    return {
        "client": run(["docker", "--version"]),
        "server": run(["docker", "version", "--format", "{{.Server.Version}}"]),
    }


def collect(
    run: RunFn = run_version_command,
    which: WhichFn = shutil.which,
    env: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    disk_path: Path = Path("."),
) -> dict[str, Any]:
    env = os.environ if env is None else env
    clock = clock or SystemClock()
    try:
        usage = shutil.disk_usage(disk_path)
        disk: dict[str, Any] | None = {
            "path": str(disk_path.resolve()),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
        }
    except OSError:
        disk = None
    return {
        "schema_version": "linemedic.v4",
        "collected_at": to_rfc3339(clock.utc_now()),
        "demo_host_id": env.get("DEMO_HOST_ID") or None,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": _memory_bytes(run),
        "disk": disk,
        "python": platform.python_version(),
        "sqlite": {"version": sqlite3.sqlite_version, "fts5": fts5_available()},
        "git": _tool_version("git", ["git", "--version"], run, which),
        "docker": _docker(run, which),
        "openshell": _tool_version("openshell", ["openshell", "--version"], run, which),
        "runtime": {
            name: _tool_version(name, argv, run, which) for name, argv in RUNTIME_COMMANDS.items()
        },
    }
