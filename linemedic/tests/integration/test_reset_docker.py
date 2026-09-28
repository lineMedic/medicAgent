"""W19 docker 시험: 실제 Docker에서 run 정리가 이 run 라벨의 컨테이너·network만 지운다
(make test-docker).

`linemedic.run_id`·`linemedic.run` 라벨을 붙인 이 run의 컨테이너 둘과 다른 run의 컨테이너 하나,
이 run의 network 하나를 만들고 `runs.cleanup`(CliDocker)을 부른다. 이미지는 로컬에 이미 있는
것만 쓰고 받지 않는다(다른 작업이 태그를 바꿀 수 있어 후보 중 있는 것을 고른다).
뒷정리도 이 테스트가 만든 정확한 이름만 지운다.
"""

import shutil
import subprocess

import pytest

from linemedic.common.clock import SystemClock
from linemedic.common.ids import new_run_id
from linemedic.control_plane import runs
from linemedic.integrations.docker import CliDocker

pytestmark = pytest.mark.docker

IMAGES = ("python:3.12-slim", "linemedic-mes:base", "linemedic-runner:test-w10")


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def exists(name: str) -> bool:
    return docker("container", "inspect", name).returncode == 0


def test_cleanup_removes_only_this_runs_labeled_containers_and_network(tmp_path):
    if shutil.which("docker") is None or docker("info").returncode != 0:
        pytest.skip("Docker daemon을 쓸 수 없음")
    # containerd 이미지 저장소에서는 `image inspect <태그>`가 실패할 수 있어 목록으로 확인한다
    image = next((i for i in IMAGES if docker("images", "-q", i).stdout.strip()), None)
    if image is None:
        pytest.skip("쓸 수 있는 로컬 이미지가 없음(받지 않는다)")
    run_id, other = new_run_id(SystemClock()), new_run_id(SystemClock())
    names = {
        "mes": (f"linemedic-w19-mes-{run_id}", f"linemedic.run_id={run_id}"),
        "runner": (f"linemedic-w19-runner-{run_id}", f"linemedic.run={run_id}"),
        "other": (f"linemedic-w19-mes-{other}", f"linemedic.run_id={other}"),
    }
    network = f"linemedic-w19-net-{run_id}"
    runs_dir = tmp_path / "runs"
    (runs_dir / run_id / "workspaces" / "ATT-000000000001").mkdir(parents=True)
    try:
        for name, label in names.values():
            started = docker(
                "run", "-d", "--name", name, "--label", label, "--entrypoint", "sleep", image, "300"
            )
            assert started.returncode == 0, started.stderr
        created = docker(
            "network", "create", "--internal", "--label", f"linemedic.run_id={run_id}", network
        )
        assert created.returncode == 0, created.stderr
        result = runs.cleanup(CliDocker(), runs_dir, run_id)
        assert result["errors"] == []
        assert sorted(item["name"] for item in result["containers"]) == sorted(
            [names["mes"][0], names["runner"][0]]
        )
        assert all(len(item["id"]) == 64 for item in result["containers"])  # 전체 ID로 지웠다
        assert not exists(names["mes"][0]) and not exists(names["runner"][0])
        assert exists(names["other"][0])  # 다른 run은 그대로
        assert result["networks"] == [network]
        assert docker("network", "inspect", network).returncode != 0
        assert not (runs_dir / run_id / "workspaces").exists()
    finally:
        for name, _ in names.values():  # 이 테스트가 만든 정확한 이름만
            docker("rm", "--force", name)
        docker("network", "rm", network)
