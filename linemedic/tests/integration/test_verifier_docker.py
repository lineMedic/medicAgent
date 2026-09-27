"""W05 docker 시험: 실제 S1b 거짓 정상 컨테이너에 대한 verifier와 core observer.

`make test-docker`에서만 실행된다. 확인하는 것:
- 신뢰 레시피 MES 이미지 위에 만든 S1b 컨테이너를 내부 network의 신뢰 prober로 원래 경로로 호출하면
  FAIL/content_mismatch로 첫 표본에서 조기 종료하고 observation_complete·resolved_written은 false다
- 버그 base 컨테이너의 실제 KeyError 로그를 observer가 S1 재발 signature로 센다
정리는 이 테스트가 만든 run ID의 정확한 컨테이너·network만 지운다.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.common.ids import new_run_id
from linemedic.control_plane.observer import ContainerObserver
from linemedic.control_plane.verifier import ProberHttp
from linemedic.factory_sim.negative import harness
from linemedic.factory_sim.scenarios import mes_container_options
from linemedic.integrations.docker import CliDocker

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_IMAGE = "linemedic-mes:test-w05"


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def leftovers(run_id: str) -> tuple[list[str], list[str]]:
    label = f"label=linemedic.run_id={run_id}"
    containers = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", label],
        capture_output=True,
        text=True,
        check=True,
    )
    networks = subprocess.run(
        ["docker", "network", "ls", "--quiet", "--filter", label],
        capture_output=True,
        text=True,
        check=True,
    )
    return containers.stdout.split(), networks.stdout.split()


@pytest.fixture(scope="module")
def mes_image() -> str:
    if not docker_ready():
        pytest.skip("Docker daemon을 쓸 수 없음")
    prober = subprocess.run(
        ["docker", "image", "inspect", harness.PROBER_IMAGE], capture_output=True, check=False
    )
    if prober.returncode != 0:
        pytest.skip(f"prober image가 없음: {harness.PROBER_IMAGE} (make mes-image가 받아 둔다)")
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


def test_verify_negative_rejects_real_s1b_container(mes_image, tmp_path):
    run_id = new_run_id(SystemClock())
    outcome = harness.run_verify_negative(run_id, runs_dir=tmp_path, mes_image=mes_image)
    result = outcome["result"]

    assert (result["verdict"], result["reason"]) == ("FAIL", "content_mismatch"), result
    assert result["origin"] == "human_injected_negative"
    assert (result["samples_completed"], result["samples_required"]) == (1, 4)
    assert result["observation_complete"] is False
    assert result["resolved_written"] is False
    failed = result["failed_assertions"]
    assert {
        "case_id": "missing-inspector",
        "assertion": "exact_total_defects",
        "field": "total_defects",
        "expected": 7,
        "actual": 0,
    } in failed
    assert {f["case_id"] for f in failed} == {"missing-inspector", "variant-held-out"}
    assert result["target"]["image_id"] == outcome["s1b_image_id"]
    assert result["observer"]["stream_gap"] is False
    assert result["observer"]["identity_changed"] is False
    assert harness.expected_outcome(result)

    saved = Path(outcome["result_path"])
    assert saved.parent == tmp_path / run_id / "verifications"
    assert json.loads(saved.read_text(encoding="utf-8")) == result
    assert leftovers(run_id) == ([], [])


def test_observer_counts_real_s1_error_signature(mes_image, tmp_path):
    run_id = new_run_id(SystemClock())
    names = harness.resource_names(run_id)
    data_dir, _ = harness.prepare_data(tmp_path, run_id)
    docker = CliDocker()
    observer = None
    try:
        created = docker.network_create(names["network"], {"linemedic.run_id": run_id})
        assert created.returncode == 0, created.stderr
        docker.run(
            mes_container_options(names["mes"], run_id, names["network"], data_dir), mes_image
        )
        docker.run(
            harness.prober_options(names["prober"], run_id, names["network"]),
            harness.PROBER_IMAGE,
            ["sleep", "infinity"],
        )
        http = ProberHttp(docker, names["prober"], f"http://{names['mes']}:8000")
        harness.wait_healthy(http, SystemClock(), time.sleep)

        observer = ContainerObserver(docker, names["mes"], harness.S1_SIGNATURE)
        observer.start(since=to_rfc3339(SystemClock().utc_now()))
        assert http.get("/defects/summary", {"lot_id": "L3-0927-118"}).status == 500
        assert http.get("/defects/summary", {"lot_id": "L3-0927-101"}).status == 200

        deadline = time.monotonic() + 15
        status = observer.poll()
        while (status.recurrences < 1 or status.lines_seen < 2) and time.monotonic() < deadline:
            time.sleep(0.2)
            status = observer.poll()
        # health check 줄이 섞여도 signature가 다르므로 재발은 118 요청 한 번뿐이다.
        assert status.recurrences == 1, status
        assert status.lines_seen >= 2, status
        assert status.non_json_lines == 0
        assert status.stream_gap is False
        assert status.identity_changed is False
    finally:
        if observer is not None:
            observer.stop()
        docker.stop(names["mes"])
        docker.stop(names["prober"])
        docker.network_remove(names["network"])
    assert leftovers(run_id) == ([], [])
