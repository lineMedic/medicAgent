"""W10 runner 시험 도우미(테스트 전용). 운영 코드에서 쓰지 않는다.

- `inspect_from_options`: `docker run` 옵션으로 Docker inspect와 같은 모양의 값을 만든다
- `scripted_docker`: 단계별(R0·R1·R2)로 준비한 종료 코드·junit·OOM·로그를 돌려주는 FakeDocker
- `local_pytest_docker`: 컨테이너 대신 로컬 pytest를 같은 argv·보호 설정으로 실행하는 FakeDocker.
  격리는 흉내 내지 않는다(격리는 `make test-docker`의 실제 컨테이너 시험이 확인한다)
"""

import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.control_plane.broker.runner import (
    JUNIT_NAME,
    MIB,
    PYTEST_INI,
    REPO_MOUNT,
    RESULTS_MOUNT,
    RunnerProfile,
)
from linemedic.integrations.docker import FakeDocker

REPO_ROOT = Path(__file__).resolve().parents[3]
PROTECTED_INI = REPO_ROOT / "linemedic" / "runner" / "pytest-protected.ini"
JUNIT_FIXTURES = REPO_ROOT / "linemedic" / "tests" / "fixtures" / "junit"
IMAGE_ID = "sha256:" + "ab" * 32


def profile(**overrides: Any) -> RunnerProfile:
    values = {
        "image_id": IMAGE_ID,
        "uid": 10001,
        "gid": 10001,
        "cpus": 1,
        "memory_mib": 512,
        "pids": 64,
        "tmpfs_mib": 64,
        "stage_timeout_seconds": 60,
        "max_log_bytes": 1024 * 1024,
    }
    return RunnerProfile(**{**values, **overrides})


def _flag_values(options: list[str], flag: str) -> list[str]:
    return [options[i + 1] for i, item in enumerate(options[:-1]) if item == flag]


def _size(value: str) -> int:
    return int(value.removesuffix("m")) * MIB


def mounts_of(options: list[str]) -> dict[str, tuple[Path, bool]]:
    """`--mount type=bind,source=,target=[,readonly]` → {target: (source, read_only)}."""
    found = {}
    for spec in _flag_values(options, "--mount"):
        parts = spec.split(",")
        fields = dict(part.split("=", 1) for part in parts if "=" in part)
        found[fields["target"]] = (Path(fields["source"]), "readonly" in parts)
    return found


def inspect_from_options(name: str, options: list[str], image: str) -> dict[str, Any]:
    tmpfs = {}
    for spec in _flag_values(options, "--tmpfs"):
        target, _, opts = spec.partition(":")
        tmpfs[target] = opts
    ulimits = []
    for spec in _flag_values(options, "--ulimit"):
        name, _, value = spec.partition("=")
        soft, _, hard = value.partition(":")
        ulimits.append({"Name": name, "Soft": int(soft), "Hard": int(hard or soft)})
    return {
        "Name": f"/{name}",
        "Image": image,
        "Config": {"User": (_flag_values(options, "--user") or [""])[0]},
        "HostConfig": {
            "NetworkMode": (_flag_values(options, "--network") or ["bridge"])[0],
            "ReadonlyRootfs": "--read-only" in options,
            "Privileged": "--privileged" in options,
            "CapAdd": _flag_values(options, "--cap-add") or None,
            "CapDrop": _flag_values(options, "--cap-drop"),
            "SecurityOpt": _flag_values(options, "--security-opt"),
            "Memory": _size(_flag_values(options, "--memory")[0]),
            "MemorySwap": _size(_flag_values(options, "--memory-swap")[0]),
            "NanoCpus": int(float(_flag_values(options, "--cpus")[0]) * 1_000_000_000),
            "PidsLimit": int(_flag_values(options, "--pids-limit")[0]),
            "PidMode": "",
            "IpcMode": "private",
            "Tmpfs": tmpfs,
            "Ulimits": ulimits or None,
        },
        "Mounts": [
            {"Type": "bind", "Source": str(source), "Destination": target, "RW": not read_only}
            for target, (source, read_only) in mounts_of(options).items()
        ],
    }


def stage_of(name: str) -> str:
    return name.rsplit("-", 1)[-1].upper()


@dataclass
class Scripted:
    """한 단계의 흉내 결과. `junit`은 fixture 이름(확장자 없이) 또는 XML 바이트."""

    exit_code: int | None = 0
    junit: str | bytes | None = None
    oom: bool = False
    logs: list[str] = field(default_factory=list)
    inspect: Mapping[str, Any] = field(default_factory=dict)  # HostConfig 등 덮어쓰기
    before: Callable[[Path], None] | None = None  # 결과 mount에 컨테이너가 남길 것을 흉내


def scripted_docker(outcomes: Mapping[str, Scripted], image_id: str = IMAGE_ID) -> FakeDocker:
    docker = FakeDocker()
    docker.images[image_id] = image_id

    def handler(name: str, options: list[str], command: list[str] | None) -> None:
        outcome = outcomes[stage_of(name)]
        results = mounts_of(options)[RESULTS_MOUNT][0]
        if outcome.before is not None:
            outcome.before(results)
        if outcome.junit is not None:
            data = outcome.junit
            if isinstance(data, str):
                data = (JUNIT_FIXTURES / f"{data}.xml").read_bytes()
            (results / JUNIT_NAME).write_bytes(data)
        info = inspect_from_options(name, options, image_id)
        for key, value in outcome.inspect.items():
            info[key] = {**info.get(key, {}), **value} if isinstance(value, dict) else value
        info["State"] = {"ExitCode": outcome.exit_code, "OOMKilled": outcome.oom}
        docker.containers[name].update(info)
        docker.exit_codes[name] = outcome.exit_code
        docker.log_history[name] = list(outcome.logs)

    docker.run_handler = handler
    return docker


def local_pytest_docker(image_id: str = IMAGE_ID, timeout: float = 60) -> FakeDocker:
    """runner가 만든 argv를 host 경로로 바꿔 로컬 pytest로 실행한다(같은 보호 설정)."""
    docker = FakeDocker()
    docker.images[image_id] = image_id

    def handler(name: str, options: list[str], command: list[str] | None) -> None:
        assert command is not None
        mounts = mounts_of(options)
        tree, results = mounts[REPO_MOUNT][0], mounts[RESULTS_MOUNT][0]
        mapping = {"python": sys.executable, PYTEST_INI: str(PROTECTED_INI), REPO_MOUNT: str(tree)}
        argv = [mapping.get(arg, arg).replace(RESULTS_MOUNT, str(results)) for arg in command]
        proc = subprocess.run(
            argv,
            cwd=tree,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(tree),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PY_COLORS": "0",
                "HOME": str(results),
            },
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        info = inspect_from_options(name, options, image_id)
        info["State"] = {"ExitCode": proc.returncode, "OOMKilled": False}
        docker.containers[name].update(info)
        docker.exit_codes[name] = proc.returncode
        docker.log_history[name] = (proc.stdout + proc.stderr).splitlines()

    docker.run_handler = handler
    return docker
