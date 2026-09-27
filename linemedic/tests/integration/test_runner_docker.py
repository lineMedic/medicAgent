"""W10 docker 시험: 고정 runner image로 실제 R0/R1/R2와 격리를 확인한다(N06 runner 부분).

`make test-docker`에서만 실행된다. 확인하는 것:
- 올바른 수정: R0(base 회귀) PASS → R1(base + 새 테스트) KeyError로 실패 → R2(candidate) PASS
- base에서 통과하는 테스트·import 오류는 재현이 아니고, 보호 회귀를 깨는 수정은 R2에서 막힌다
- timeout·OOM은 재현으로 인정하지 않고 컨테이너를 남기지 않는다
- 컨테이너 안에서 외부 연결·DNS·repo·root filesystem 쓰기가 실패하고 /tmp만 쓸 수 있다
- docker inspect로 network none·read-only·capability 제거·no-new-privileges·자원 제한·비루트·
  mount를 확인한다
`LINEMEDIC_RECORD_EVIDENCE=1`이면 결과를 `evidence/N06-runner-isolation.md`에 남긴다.
image tag와 컨테이너는 이 테스트가 만든 정확한 이름만 다룬다(컨테이너는 runner가 지운다).
"""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.common.ids import new_id, new_run_id
from linemedic.control_plane.broker.patch_gate import GateRequest, PatchGate
from linemedic.control_plane.broker.patch_policy import load_policy
from linemedic.control_plane.broker.runner import MIB, Runner
from linemedic.integrations.docker import CliDocker
from linemedic.scripts.seed_demo_repo import build_seed_repo
from linemedic.tests.helpers.runner import profile

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[3]
PATCHES = REPO_ROOT / "linemedic" / "tests" / "fixtures" / "patches"
TEST_IMAGE = "linemedic-runner:test-w10"
EVIDENCE = REPO_ROOT / "evidence" / "N06-runner-isolation.md"
GIT_ENV = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
NEW_TESTS = {
    "fix_missing_inspector": "tests/repro/test_missing_inspector.py",
    "fix_breaks_regression": "tests/repro/test_missing_inspector_fields.py",
    "repro_passes_on_base": "tests/repro/test_summary_with_inspectors.py",
    "repro_import_error": "tests/repro/test_missing_module.py",
    "repro_timeout": "tests/repro/test_hangs.py",
    "repro_oom": "tests/repro/test_oom.py",
}
PROBE = """import os
import pathlib
import socket


def test_no_external_connection():
    sock = socket.socket()
    sock.settimeout(3)
    try:
        sock.connect(("1.1.1.1", 443))
        connected = True
    except OSError:
        connected = False
    finally:
        sock.close()
    assert not connected


def test_no_dns():
    try:
        socket.getaddrinfo("github.com", 443)
        resolved = True
    except OSError:
        resolved = False
    assert not resolved


def test_repo_and_root_filesystem_are_read_only():
    for path in ("/work/repo/probe.txt", "/probe.txt", "/usr/local/probe.txt"):
        try:
            pathlib.Path(path).write_text("x")
            written = True
        except OSError:
            written = False
        assert not written, path


def test_only_tmp_is_writable():
    pathlib.Path("/tmp/probe.txt").write_text("ok")


def test_runs_as_fixed_non_root_user():
    assert (os.getuid(), os.getgid()) == (10001, 10001)


def test_no_docker_socket():
    assert not os.path.exists("/var/run/docker.sock")
"""


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="module")
def runner_image() -> str:
    if not docker_ready():
        pytest.skip("Docker daemon을 쓸 수 없음")
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "linemedic/runner/runner.Dockerfile",
            "-t",
            TEST_IMAGE,
            "linemedic/runner",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", TEST_IMAGE],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return image_id


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("seed").resolve()
    built = build_seed_repo(root / "seed")
    mirror = root / "mirror" / "l3-mes-api.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(root / "seed"), str(mirror)],
        env=GIT_ENV,
        check=True,
    )
    return mirror, built["commit"]


def gate(seed, runner_image, runs_dir, **profile_overrides) -> PatchGate:
    mirror, _ = seed
    runner = Runner(CliDocker(), profile(image_id=runner_image, **profile_overrides), SystemClock())
    return PatchGate(
        policy=load_policy(), runner=runner, mirror=mirror, runs_dir=runs_dir.resolve()
    )


def request(seed, name) -> GateRequest:
    _, base = seed
    return GateRequest(
        run_id=new_run_id(SystemClock()),
        proposal_id=new_id("PROP"),
        base_sha=base,
        allowed_base=base,
        deploy_base=base,
        diff=(PATCHES / f"{name}.patch").read_text(encoding="utf-8"),
        new_test_path=NEW_TESTS[name],
        received_at=to_rfc3339(SystemClock().utc_now()),
    )


def leftover_containers(proposal_id: str) -> list[str]:
    out = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=linemedic.proposal={proposal_id}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def test_correct_fix_passes_r0_r1_r2_in_isolated_containers(seed, runner_image, tmp_path):
    req = request(seed, "fix_missing_inspector")
    outcome = gate(seed, runner_image, tmp_path).check(req)
    stages = {c["check"]: c for c in outcome.checks if c["check"] in ("R0", "R1", "R2")}
    assert outcome.passed, outcome.checks[-1]
    assert (stages["R0"]["exit_code"], stages["R0"]["junit"]["tests"]) == (0, 4)
    assert (stages["R1"]["exit_code"], stages["R1"]["junit"]["failures"]) == (1, 1)
    assert (stages["R2"]["exit_code"], stages["R2"]["junit"]["tests"]) == (0, 5)
    assert {stage["image_id"] for stage in stages.values()} == {runner_image}
    assert len({stage["container_id"] for stage in stages.values()}) == 3
    assert leftover_containers(req.proposal_id) == []


@pytest.mark.parametrize(
    ("name", "code", "reason"),
    [
        ("repro_passes_on_base", "REPRO_NOT_FAILING", "passed_on_base"),  # T-REPRO-01
        ("repro_import_error", "REPRO_NOT_FAILING", "pytest_exit_2"),  # T-REPRO-02
        ("fix_breaks_regression", "REGRESSION_FAILED", "regression_not_passed"),  # T-REPRO-03
    ],
)
def test_non_reproductions_and_regressions_are_rejected(
    seed, runner_image, tmp_path, name, code, reason
):
    outcome = gate(seed, runner_image, tmp_path).check(request(seed, name))
    assert (outcome.passed, outcome.code, outcome.reason) == (False, code, reason)


def test_timeout_kills_the_container_and_is_not_reproduction(seed, runner_image, tmp_path):
    req = request(seed, "repro_timeout")
    outcome = gate(seed, runner_image, tmp_path, stage_timeout_seconds=8).check(req)
    assert (outcome.code, outcome.reason) == ("REPRO_NOT_FAILING", "timeout")  # T-REPRO-02
    assert outcome.checks[-1]["timed_out"] is True
    assert leftover_containers(req.proposal_id) == []


def test_out_of_memory_is_not_reproduction(seed, runner_image, tmp_path):
    outcome = gate(seed, runner_image, tmp_path).check(request(seed, "repro_oom"))
    assert (outcome.code, outcome.reason) == ("REPRO_NOT_FAILING", "oom")  # T-REPRO-02
    assert outcome.checks[-1]["oom_killed"] is True


def test_n06_runner_isolation_probe_and_inspect(runner_image, tmp_path):
    tree = (tmp_path / "tree").resolve()
    (tree / "tests" / "repro").mkdir(parents=True)
    (tree / "tests" / "repro" / "test_probe.py").write_text(PROBE, encoding="utf-8")
    runner = Runner(CliDocker(), profile(image_id=runner_image), SystemClock())
    proposal_id = new_id("PROP")
    run = runner.run_stage(
        stage="R1",
        name=f"lm-runner-{proposal_id.lower()}-n06-r1",
        tree=tree,
        results=tmp_path / "results",
        logs=tmp_path / "logs",
        tests=["tests/repro/test_probe.py"],
        labels={"linemedic.role": "runner", "linemedic.proposal": proposal_id},
    )
    assert run.runner_error is None, run.runner_error
    assert run.exit_code == 0, Path(run.log_path).read_text()[-2000:]
    assert run.junit is not None and run.junit.tests == 6 and run.junit.count("passed") == 6
    effective = run.profile
    assert effective["network_mode"] == "none"
    assert effective["read_only_rootfs"] is True and not effective["privileged"]
    assert effective["cap_drop"] == ["ALL"] and not effective["cap_add"]
    assert any(opt.startswith("no-new-privileges") for opt in effective["security_opt"])
    assert (effective["memory"], effective["memory_swap"]) == (512 * MIB, 512 * MIB)
    assert (effective["nano_cpus"], effective["pids_limit"]) == (1_000_000_000, 64)
    assert effective["user"] == "10001:10001"
    assert effective["tmpfs"] == {"/tmp": "rw,noexec,nosuid,nodev,size=64m"}
    assert [(m["destination"], m["rw"]) for m in effective["mounts"]] == [
        ("/work/repo", False),
        ("/work/results", True),
    ]
    assert leftover_containers(proposal_id) == []
    if os.environ.get("LINEMEDIC_RECORD_EVIDENCE") == "1":
        _record_evidence(runner_image, run)


def _record_evidence(image_id, run) -> None:
    server = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True
    ).stdout.strip()
    probes = [f"  - `{case.name}`: {case.outcome.upper()}" for case in run.junit.cases]
    EVIDENCE.write_text(
        "# N06 runner 부분: 격리 runner 네트워크·마운트·자원 제한\n\n"
        f"- 실행 시각(UTC): {to_rfc3339(SystemClock().utc_now())}\n"
        f"- 실행 환경: 로컬 개발 Mac({platform.platform()}), Docker server {server}."
        " 데모 호스트(G1) 아님\n"
        "- 명령: `LINEMEDIC_RECORD_EVIDENCE=1 make test-docker`"
        " (`linemedic/tests/integration/test_runner_docker.py`)\n"
        f"- runner image: `{image_id}` (`linemedic/runner/runner.Dockerfile`)\n"
        f"- 결과: PASS (컨테이너 안 probe {run.junit.tests}개 모두 통과, exit {run.exit_code})\n"
        + "\n".join(probes)
        + "\n- docker inspect(실제 적용 값):\n\n```json\n"
        + json.dumps(dict(run.profile), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n\n"
        "- 한계: 같은 Python 프로세스의 비신뢰 코드는 결과 파일을 조작할 수 있다."
        " 테스트 PASS는 악성 코드가 없다는 뜻이 아니다."
        " 배포 MES의 네트워크 부분(N06 나머지)은 W12에서 확인한다\n",
        encoding="utf-8",
    )
