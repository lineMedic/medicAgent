"""W04 docker 시험: 신뢰 레시피로 만든 버그 base MES 컨테이너.

`make test-docker`에서만 실행된다. 확인하는 것:
- 로트 118 요청이 500으로 실패하고 KeyError JSON 로그 3줄이 남는다
- 로트 101 요청은 200이다
- stdout에는 JSON 로그만 있다
- 컨테이너는 비루트·read-only root·capability 제거·host port 없음, network는 internal이다
- 컨테이너 안에서 외부로 연결할 수 없다
정리는 이 테스트가 만든 run ID의 정확한 컨테이너·network만 지운다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock
from linemedic.common.ids import new_run_id
from linemedic.factory_sim import scenarios

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_IMAGE = "linemedic-mes:test-w04"


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def inspect(kind: list[str], name: str) -> dict:
    out = subprocess.run(
        ["docker", *kind, "inspect", name], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)[0]


@pytest.fixture(scope="module")
def mes_image() -> str:
    if not docker_ready():
        pytest.skip("Docker daemon을 쓸 수 없음")
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "linemedic/runner/mes.Dockerfile",
            "-t",
            TEST_IMAGE,
            "l3-mes-api-seed",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    return TEST_IMAGE


def test_s1_container_fails_on_bug_lot_with_json_logs(mes_image, tmp_path):
    run_id = new_run_id(SystemClock())
    try:
        result = scenarios.inject_s1(run_id, runs_dir=tmp_path, image=mes_image)
        assert [r["status"] for r in result["requests"]] == [500, 500, 500, 200]

        logs = scenarios.read_mes_logs(run_id)
        assert logs and all("_raw" not in line for line in logs), logs
        errors = [line for line in logs if line.get("event") == "request_failed"]
        assert len(errors) == 3
        assert {e["error_type"] for e in errors} == {"KeyError"}
        assert {e["error_field"] for e in errors} == {"inspector_id"}
        assert {e["top_frame"] for e in errors} == {"app.defects:summarize"}
        assert {e["lot_id"] for e in errors} == {"L3-0927-118"}
        normal = [
            line
            for line in logs
            if line.get("event") == "request_completed" and line.get("lot_id") == "L3-0927-101"
        ]
        assert [line["status"] for line in normal] == [200]

        container = inspect([], result["container"])
        assert container["HostConfig"]["ReadonlyRootfs"] is True
        assert container["Config"]["User"] == "10001:10001"
        assert container["HostConfig"]["CapDrop"] == ["ALL"]
        assert not container["HostConfig"]["PortBindings"]
        assert container["Config"]["Labels"]["linemedic.run_id"] == run_id
        assert inspect(["network"], result["network"])["Internal"] is True

        egress = subprocess.run(
            [
                "docker",
                "exec",
                result["container"],
                "python",
                "-c",
                "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert egress.returncode != 0, "내부 network 컨테이너가 외부로 연결됐다"
    finally:
        scenarios.stop_s1(run_id)
