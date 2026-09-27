"""W12 docker 시험: fixture commit을 실제로 배포·검증한다.

fixture commit은 시드 + fix_missing_inspector 패치의 squash merge다.

`make test-docker`에서만 실행된다. 확인하는 것:

- 신뢰 mirror로 merge commit 하나만 가져와 고정 runner image로 실제 R0·R1·R2를 다시 돌린다
- 신뢰 레시피(`linemedic/runner/mes.Dockerfile`)로 final tree만 빌드하고 image **ID**로 기동한다
- `docker inspect`로 본 container의 image ID·container ID가 execution 기록과 같고 라벨이 붙어 있다
- 신뢰 prober로 원래 경로를 60초 관찰해 PASS하고 사건이 RESOLVED가 된다
- 기록한 복원 절차(사람이 실행)를 그대로 실행하면 이전 image의 MES가 다시 뜬다

GitHub는 FakeGitHub(사람 리뷰·머지 흉내)다. run ID는 테스트 도우미의 고정 값이라, 같은 이름의
container가 이미 있으면 지우지 않고 건너뛴다. 정리는 이 테스트가 만든 정확한 이름만 대상으로 한다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock
from linemedic.control_plane.broker.runner import Runner
from linemedic.factory_sim.scenarios import mes_container_options, prepare_s1_data
from linemedic.integrations.docker import CliDocker
from linemedic.tests.helpers.api import RUN
from linemedic.tests.helpers.pr_world import build_seed_mirror
from linemedic.tests.helpers.release_world import ReleaseWorld
from linemedic.tests.helpers.runner import profile

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_TEST_IMAGE = "linemedic-runner:test-w12"
MES_TEST_IMAGE = "linemedic-mes:test-w12-base"
PROBER_IMAGE = "python:3.12-slim"


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=900, check=check
    )


def build(dockerfile: str, tag: str, context: str) -> str:
    built = subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", tag, context],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-3000:]
    return docker("image", "inspect", "--format", "{{.Id}}", tag).stdout.strip()


@pytest.fixture(scope="module")
def images() -> dict[str, str]:
    if not docker_ready():
        pytest.skip("Docker daemon을 쓸 수 없음")
    if docker("image", "inspect", PROBER_IMAGE, check=False).returncode != 0:
        pytest.skip(f"prober image가 없음: {PROBER_IMAGE} (make mes-image가 받아 둔다)")
    return {
        "runner": build(
            "linemedic/runner/runner.Dockerfile", RUNNER_TEST_IMAGE, "linemedic/runner"
        ),
        "mes": build("linemedic/runner/mes.Dockerfile", MES_TEST_IMAGE, "l3-mes-api-seed"),
    }


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


def labelled(kind: str) -> list[str]:
    """이 run 라벨이 붙은 container(`ps`) 또는 network ID."""
    args = ["ps", "--all"] if kind == "ps" else ["network", "ls"]
    label = f"label=linemedic.run_id={RUN}"
    return docker(*args, "--quiet", "--filter", label, check=False).stdout.split()


def test_fixture_commit_is_built_deployed_by_image_id_and_verified(
    images, seed, store, conn, fake_clock, tmp_path
):
    world = ReleaseWorld(store, conn, fake_clock, seed, tmp_path)
    if labelled("ps") or labelled("network"):
        pytest.skip(
            f"run {RUN} 라벨의 container·network가 이미 있다(다른 실행이 쓰는 중일 수 있음)"
        )
    cli = CliDocker()
    world.executor.docker = cli
    world.executor.runner = Runner(cli, profile(image_id=images["runner"]), SystemClock())
    world.executor.clock = SystemClock()
    runs_dir = world.runs_dir.resolve()
    world.executor.runs_dir = runs_dir
    data_dir = prepare_s1_data(runs_dir, RUN)
    built_tag = None
    try:
        created = cli.network_create(world.network, {"linemedic.run_id": RUN})
        assert created.returncode == 0, created.stderr
        previous_id = cli.run(
            mes_container_options(world.mes, RUN, world.network, data_dir), images["mes"]
        )
        merge_sha = world.merge()
        response = world.approve(expected_current_image_id=images["mes"])
        assert response.status_code == 202, response.text
        execution_id = response.json()["data"]["execution_id"]
        (summary,) = world.drain()
        execution = world.deploy()
        request = json.loads(execution["request_json"])
        result = json.loads(execution["result_json"])
        built_tag = result.get("target", {}).get("image_tag")
        assert (summary["verdict"], summary["incident_status"]) == ("PASS", "RESOLVED"), (
            summary,
            result,
        )
        assert request["runtime"]["previous"]["container_id"] == previous_id

        # 실제 R0·R1·R2
        assert [(c["check"], c["result"]) for c in result["checks"]] == [
            ("R0", "PASS"),
            ("R1", "PASS"),
            ("R2", "PASS"),
        ]
        assert all(c["image_id"] == images["runner"] for c in result["checks"])

        # host inspect로 본 실행 대상 = execution 기록
        info = cli.inspect(world.mes)
        assert info is not None
        assert info["Image"] == result["target"]["image_id"] != images["mes"]
        assert info["Id"] == result["container_id"]
        assert info["Config"]["Labels"]["linemedic.execution_id"] == execution_id
        assert info["Config"]["Labels"]["linemedic.incident_id"] == world.incident
        assert info["HostConfig"]["NetworkMode"] == world.network
        assert info["HostConfig"]["ReadonlyRootfs"] is True
        assert (
            result["target"]["image_identity"] == "local_image_id"
        )  # registry digest로 적지 않는다
        chain = world.api.client.get(
            f"/ops/executions/{execution_id}", headers=world.api.operator
        ).json()["data"]["identity_chain"]
        assert (chain["image_id"], chain["container_id"]) == (info["Image"], info["Id"])
        assert result["trees"]["approved_merge_sha"] == merge_sha

        verification = world.conn.execute("SELECT * FROM verifications").fetchone()
        stored = json.loads(verification["result_json"])
        assert (verification["verdict"], verification["execution_id"]) == ("PASS", execution_id)
        assert stored["target"]["image_id"] == info["Image"]
        assert stored["target"]["container_id"] == info["Id"]
        assert stored["observation_complete"] is True and stored["elapsed_seconds"] >= 60
        assert world.lock() is None

        # 사람이 실행하는 복원 절차: 이전 image ID로 같은 이름의 MES가 다시 뜬다
        for command in result["restore"]["commands"]:
            subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
        restored = cli.inspect(world.mes)
        assert restored is not None and restored["Image"] == images["mes"]
    finally:
        cli.stop(world.mes)  # prober는 executor가 지운다(아래 라벨 검사로 확인)
        cli.network_remove(world.network)
        if built_tag:
            docker("image", "rm", built_tag, check=False)
    assert labelled("ps") == [] and labelled("network") == []
