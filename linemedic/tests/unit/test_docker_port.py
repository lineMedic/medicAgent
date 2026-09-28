"""W12 Docker port: 존재 조회가 "없음"과 "조회 실패"를 섞지 않고, FakeDocker가 배포 흉내에 필요한
라벨·network·mount·장애 주입을 준다.

CliDocker는 가짜 `docker` 실행 파일(이 테스트가 만든 Python 스크립트)로 확인한다.
실제 daemon은 쓰지 않는다.
"""

import sys
from pathlib import Path

import pytest

from linemedic.integrations.docker import CliDocker, DockerError, FakeDocker

FAKE_BINARY = """#!{python}
import json, os, sys
args = sys.argv[1:]
mode = os.environ.get("FAKE_DOCKER_MODE", "")
if args[:2] == ["container", "inspect"]:
    if mode == "present":
        print("f" * 64)
        sys.exit(0)
    if mode == "absent":
        print("Error response from daemon: No such container: " + args[-1], file=sys.stderr)
        sys.exit(1)
    print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["image", "inspect"]:
    if mode == "present":
        print(json.dumps([{{"Id": args[-1], "RepoDigests": []}}]))
        sys.exit(0)
    print("Error: No such object: " + args[-1], file=sys.stderr)
    sys.exit(1)
sys.exit(2)
"""


@pytest.fixture
def cli(tmp_path):
    binary = tmp_path / "docker"
    binary.write_text(FAKE_BINARY.format(python=sys.executable))
    binary.chmod(0o755)
    return CliDocker(binary=str(binary), timeout=10)


@pytest.mark.parametrize(
    ("mode", "expected"), [("present", True), ("absent", False), ("down", None)]
)
def test_container_exists_separates_absent_from_lookup_failure(cli, monkeypatch, mode, expected):
    monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
    assert cli.container_exists("linemedic-mes-r-1") is expected


def test_image_inspect_returns_the_record_or_none(cli, monkeypatch):
    image = "sha256:" + "a" * 64
    monkeypatch.setenv("FAKE_DOCKER_MODE", "present")
    assert cli.image_inspect(image) == {"Id": image, "RepoDigests": []}
    monkeypatch.setenv("FAKE_DOCKER_MODE", "absent")
    assert cli.image_inspect(image) is None


def test_fake_run_records_labels_network_and_mounts_and_new_stream():
    docker = FakeDocker()
    docker.images["img:tag"] = "sha256:" + "b" * 64
    options = ["--name", "mes", "--label", "a=1", "--network", "net", "--volume", "/d:/data:ro"]
    docker.logs_follow("mes").push("old")
    docker.run(options, "img:tag")
    info = docker.inspect("mes")
    assert info["Image"] == "sha256:" + "b" * 64 and info["Config"]["Labels"] == {"a": "1"}
    assert info["HostConfig"]["NetworkMode"] == "net"
    assert info["Mounts"] == [{"Type": "bind", "Source": "/d", "Destination": "/data", "RW": False}]
    assert docker.logs_follow("mes").read_lines() == []  # 새 컨테이너는 새 로그 스트림
    with pytest.raises(DockerError) as conflict:  # 같은 이름이 있으면 docker처럼 거절
        docker.run(options, "img:tag")
    assert conflict.value.returncode == 125


def test_fake_failure_injection():
    docker = FakeDocker()
    docker.run_errors["mes"] = 124
    with pytest.raises(DockerError) as timeout:
        docker.run(["--name", "mes"], "img")
    assert timeout.value.returncode == 124 and docker.container_exists("mes") is False
    docker.run(["--name", "mes"], "img")  # 한 번만 실패한다
    docker.stop_errors["mes"] = 1
    assert docker.stop("mes").returncode == 1 and docker.container_exists("mes") is True
    docker.daemon_down = True
    assert docker.container_exists("mes") is None
    docker.build_error = "pip 실패"
    with pytest.raises(DockerError):
        docker.build(Path("ctx"), Path("mes.Dockerfile"), "t")
    assert docker.image_inspect("t") is None
    assert docker.calls[-1] == ("build", "mes.Dockerfile", "t", None, "ctx")
    image = docker.build(Path("ctx"), Path("mes.Dockerfile"), "t")  # 다음 빌드는 된다
    assert docker.image_inspect(image) == {"Id": image, "RepoTags": [], "RepoDigests": []}
