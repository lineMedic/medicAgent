"""격리 runner와 R0/R1/R2 판정 (W10, spec 06 §4·§5, docs/07 §2, D47).

- `Runner.run_stage`: 서버가 정한 image(고정 ID)·mount·argv·제한으로만 컨테이너를 실행한다.
  범용 컨테이너 API는 없다.
  `docker run --detach`(이름·label 고정) → `docker wait`(단계 제한 시간) → inspect(종료 코드·OOM·
  image·실제 적용된 제한) → 로그(상한까지) → `docker rm --force`.
  timeout이면 컨테이너 전체를 강제 종료·제거한다. `--rm`을 쓰지 않는 것은 종료 뒤 inspect·로그를
  읽기 위해서다(제거는 finally에서 항상 한다).
- 실제 적용된 제한(network none·read-only·capability·자원·mount)을 inspect로 다시 확인한다.
  다르면 결과를 쓰지 않고 runner 오류로 본다.
- pytest는 image 안의 보호 설정(`/opt/linemedic/pytest-protected.ini`)으로만 돈다.
  repo 설정·conftest·plugin 자동 로드를 쓰지 않는다. `--junitxml`은 결과 전용 mount에 쓴다.
- 결과 mount는 컨테이너가 쓸 수 있으므로 host는 그 안에 쓰지 않는다. `junit.xml`은 symlink를
  따라가지 않고 일반 파일일 때만 크기 상한까지 읽는다. DOCTYPE·ENTITY 선언이 있으면 파싱하지 않는다.
- 판정(`judge_r0`·`judge_r1`·`judge_r2`)은 종료 코드와 junit을 함께 본다.
  Docker 125/126/127과 실행 실패는 테스트 결과가 아니라 runner 오류다.
- 같은 Python 프로세스의 비신뢰 코드는 결과 파일을 조작할 수 있다. 테스트 PASS는 악성 코드가
  없다는 뜻이 아니다. 배포 뒤 업무 검증은 별도 프로세스(verifier)가 한다.
"""

import hashlib
import os
import re
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from linemedic.common.clock import Clock
from linemedic.common.config import Settings
from linemedic.integrations.docker import DockerError, DockerPort

PYTEST_INI = "/opt/linemedic/pytest-protected.ini"
REPO_MOUNT = "/work/repo"
RESULTS_MOUNT = "/work/results"
JUNIT_NAME = "junit.xml"
DOCKER_ERROR_EXITS = frozenset({125, 126, 127})
MIB = 1024 * 1024
MAX_RECORDED_CASES = 50
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}")
_MOUNT_SOURCE = re.compile(r"/[A-Za-z0-9_./-]+")  # bind 옵션을 깨뜨리는 `,`·`=`·공백 없음

PROTECTION_UNAVAILABLE = "PROTECTION_UNAVAILABLE"
REPRO_NOT_FAILING = "REPRO_NOT_FAILING"
REGRESSION_FAILED = "REGRESSION_FAILED"


@dataclass(frozen=True)
class RunnerProfile:
    image_id: str
    uid: int
    gid: int
    cpus: int
    memory_mib: int
    pids: int
    tmpfs_mib: int
    stage_timeout_seconds: int
    max_log_bytes: int

    def __post_init__(self) -> None:
        if not _IMAGE_ID.fullmatch(self.image_id):
            raise ValueError("runner image는 sha256:<64 hex> image ID로 고정한다")
        if self.uid == 0 or self.gid == 0:
            raise ValueError("runner는 root로 실행하지 않는다")

    @classmethod
    def from_settings(cls, settings: Settings) -> "RunnerProfile | None":
        """`RUNNER_IMAGE_ID`가 없으면 None(runner 없음)."""
        image_id = settings.runtime.runner_image_id
        if not image_id:
            return None
        runner = settings.config.runner
        return cls(
            image_id=image_id,
            uid=runner.uid,
            gid=runner.gid,
            cpus=runner.cpus,
            memory_mib=runner.memory_mib,
            pids=runner.pids,
            tmpfs_mib=runner.tmpfs_mib,
            stage_timeout_seconds=runner.stage_timeout_seconds,
            max_log_bytes=runner.max_log_bytes,
        )


# ── junit ─────────────────────────────────────────────────────


class JunitError(ValueError):
    """junit 결과를 쓸 수 없음(없음·크기·형식·DTD·일반 파일 아님)."""


@dataclass(frozen=True)
class CaseResult:
    classname: str
    name: str
    outcome: Literal["passed", "failed", "error", "skipped"]


@dataclass(frozen=True)
class JunitReport:
    cases: tuple[CaseResult, ...]

    def count(self, outcome: str) -> int:
        return sum(1 for case in self.cases if case.outcome == outcome)

    @property
    def tests(self) -> int:
        return len(self.cases)

    @property
    def failures(self) -> int:
        return self.count("failed")

    @property
    def errors(self) -> int:
        return self.count("error")

    @property
    def skipped(self) -> int:
        return self.count("skipped")

    def in_module(self, module: str) -> list[CaseResult]:
        return [
            c for c in self.cases if c.classname == module or c.classname.startswith(f"{module}.")
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "tests": self.tests,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
            "cases": [
                {"classname": c.classname[:200], "name": c.name[:200], "outcome": c.outcome}
                for c in self.cases[:MAX_RECORDED_CASES]
            ],
        }


def _count_attribute(suite: ElementTree.Element, name: str) -> int:
    try:
        return int(suite.get(name, "0"))
    except ValueError:
        raise JunitError("junit_invalid") from None


def parse_junit(data: bytes, max_bytes: int) -> JunitReport:
    """pytest junit XML(xunit2). testsuite 속성과 testcase 개수가 맞아야 한다."""
    if len(data) > max_bytes:
        raise JunitError("junit_too_large")
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise JunitError("junit_dtd_forbidden")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise JunitError("junit_invalid") from None
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = root.findall("testsuite")
    else:
        raise JunitError("junit_invalid")
    cases: list[CaseResult] = []
    for suite in suites:
        found = []
        for case in suite.findall("testcase"):
            tags = {child.tag for child in case}
            outcome: Literal["passed", "failed", "error", "skipped"] = (
                "error"
                if "error" in tags
                else "failed"
                if "failure" in tags
                else "skipped"
                if "skipped" in tags
                else "passed"
            )
            found.append(CaseResult(case.get("classname", ""), case.get("name", ""), outcome))
        report = JunitReport(tuple(found))
        declared = tuple(
            _count_attribute(suite, n) for n in ("tests", "failures", "errors", "skipped")
        )
        if declared != (report.tests, report.failures, report.errors, report.skipped):
            raise JunitError("junit_inconsistent")
        cases.extend(found)
    return JunitReport(tuple(cases))


def read_result_file(path: Path, max_bytes: int) -> bytes | None:
    """컨테이너가 쓸 수 있는 결과 파일을 읽는다. symlink·FIFO·장치 파일은 거부, 없으면 None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise JunitError("junit_unreadable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise JunitError("junit_not_regular_file")
        if info.st_size > max_bytes:
            raise JunitError("junit_too_large")
        chunks, total = [], 0
        while total <= max_bytes:
            chunk = os.read(fd, max_bytes + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


# ── 단계 실행 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class StageRun:
    stage: str
    tests: tuple[str, ...]
    exit_code: int | None = None
    timed_out: bool = False
    oom_killed: bool = False
    runner_error: str | None = None
    container_id: str | None = None
    image_id: str | None = None
    junit: JunitReport | None = None
    junit_error: str | None = None
    log_path: str | None = None
    log_sha256: str | None = None
    log_bytes: int = 0
    log_truncated: bool = False
    profile: Mapping[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "tests": list(self.tests),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "oom_killed": self.oom_killed,
            "runner_error": self.runner_error,
            "container_id": self.container_id,
            "image_id": self.image_id,
            "junit": self.junit.summary() if self.junit is not None else None,
            "junit_error": self.junit_error,
            "log": {
                "path": self.log_path,
                "sha256": self.log_sha256,
                "bytes": self.log_bytes,
                "truncated": self.log_truncated,
            },
            "profile": dict(self.profile),
        }


def effective_profile(info: Mapping[str, Any]) -> dict[str, Any]:
    """inspect에서 실제 적용된 격리·자원 설정만 뽑는다(N06 기록에도 쓴다)."""
    host = info.get("HostConfig") or {}
    config = info.get("Config") or {}
    return {
        "image": info.get("Image"),
        "network_mode": host.get("NetworkMode"),
        "read_only_rootfs": host.get("ReadonlyRootfs"),
        "privileged": host.get("Privileged"),
        "cap_add": host.get("CapAdd"),
        "cap_drop": host.get("CapDrop"),
        "security_opt": host.get("SecurityOpt"),
        "memory": host.get("Memory"),
        "memory_swap": host.get("MemorySwap"),
        "nano_cpus": host.get("NanoCpus"),
        "pids_limit": host.get("PidsLimit"),
        "pid_mode": host.get("PidMode"),
        "ipc_mode": host.get("IpcMode"),
        "tmpfs": host.get("Tmpfs"),
        "ulimits": sorted(
            (
                {"name": u.get("Name"), "soft": u.get("Soft"), "hard": u.get("Hard")}
                for u in host.get("Ulimits") or []
            ),
            key=lambda u: str(u["name"]),
        ),
        "user": config.get("User"),
        "mounts": sorted(
            (
                {"destination": m.get("Destination"), "rw": m.get("RW"), "type": m.get("Type")}
                for m in info.get("Mounts") or []
            ),
            key=lambda m: str(m["destination"]),
        ),
    }


def profile_problems(effective: Mapping[str, Any], profile: RunnerProfile) -> list[str]:
    """요청한 실행 프로필과 실제 적용 값이 다른 항목."""
    problems = []
    mounts = {m["destination"]: m for m in effective.get("mounts") or []}
    fsize = profile.tmpfs_mib * MIB
    checks = {
        "image": effective.get("image") == profile.image_id,
        "network": effective.get("network_mode") == "none",
        "read_only": effective.get("read_only_rootfs") is True,
        "privileged": not effective.get("privileged"),
        "capabilities": "ALL" in (effective.get("cap_drop") or []) and not effective.get("cap_add"),
        "no_new_privileges": any(
            str(opt).startswith("no-new-privileges") for opt in effective.get("security_opt") or []
        ),
        "memory": effective.get("memory") == profile.memory_mib * MIB,
        "swap": effective.get("memory_swap") == profile.memory_mib * MIB,
        "cpus": effective.get("nano_cpus") == profile.cpus * 1_000_000_000,
        "pids": effective.get("pids_limit") == profile.pids,
        "file_size_limit": {"name": "fsize", "soft": fsize, "hard": fsize}
        in (effective.get("ulimits") or []),
        "namespaces": effective.get("pid_mode") in (None, "")
        and effective.get("ipc_mode") not in ("host",),
        "user": effective.get("user") == f"{profile.uid}:{profile.gid}",
        "repo_read_only": (mounts.get(REPO_MOUNT) or {}).get("rw") is False,
        "results_mount": (mounts.get(RESULTS_MOUNT) or {}).get("rw") is True,
        "no_extra_mounts": set(mounts) <= {REPO_MOUNT, RESULTS_MOUNT},
    }
    problems.extend(name for name, ok in checks.items() if not ok)
    return problems


class Runner:
    def __init__(self, docker: DockerPort, profile: RunnerProfile, clock: Clock) -> None:
        self.docker = docker
        self.profile = profile
        self.clock = clock

    def image_problem(self) -> str | None:
        """고정한 image ID가 이 host에 그대로 있는가."""
        found = self.docker.image_id(self.profile.image_id)
        if found is None:
            return "runner_image_missing"
        return None if found == self.profile.image_id else "runner_image_mismatch"

    def options(self, name: str, tree: Path, results: Path, labels: Mapping[str, str]) -> list[str]:
        p = self.profile
        options = [
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={p.tmpfs_mib}m",
            "--cpus",
            str(p.cpus),
            "--memory",
            f"{p.memory_mib}m",
            "--memory-swap",
            f"{p.memory_mib}m",
            "--pids-limit",
            str(p.pids),
            # 파일 하나의 크기 상한(/tmp와 같은 값). 결과 mount의 junit.xml도 이 크기를 넘지 못한다
            "--ulimit",
            f"fsize={p.tmpfs_mib * MIB}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{p.uid}:{p.gid}",
            "--mount",
            f"type=bind,source={tree},target={REPO_MOUNT},readonly",
            "--mount",
            f"type=bind,source={results},target={RESULTS_MOUNT}",
            "--workdir",
            REPO_MOUNT,
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            f"PYTHONPATH={REPO_MOUNT}",
            "--env",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--env",
            "HOME=/tmp",
            "--log-driver",
            "json-file",
            "--log-opt",
            f"max-size={2 * p.max_log_bytes // 1024}k",
            "--log-opt",
            "max-file=1",
        ]
        for key, value in sorted(labels.items()):
            options += ["--label", f"{key}={value}"]
        return options

    @staticmethod
    def command(tests: Sequence[str]) -> list[str]:
        return [
            "python",
            "-m",
            "pytest",
            "-c",
            PYTEST_INI,
            "--rootdir",
            REPO_MOUNT,
            "-p",
            "no:cacheprovider",
            "--noconftest",
            "-q",
            f"--junitxml={RESULTS_MOUNT}/{JUNIT_NAME}",
            "--",
            *tests,
        ]

    def run_stage(
        self,
        *,
        stage: str,
        name: str,
        tree: Path,
        results: Path,
        logs: Path,
        tests: Sequence[str],
        labels: Mapping[str, str],
    ) -> StageRun:
        """한 단계를 실행한다. `results`는 새 빈 경로, `logs`는 컨테이너에 보이지 않는 host 경로.

        결과 폴더는 쓰기 가능하게 mount되지만 폴더 자체는 읽기 전용(0555)이다. 컨테이너는
        미리 만든 `junit.xml` 하나에만 쓸 수 있고(`--ulimit fsize`로 크기 상한),
        새 파일·폴더·symlink를 만들 수 없다.
        `docker run` 뒤의 Docker·OS 오류는 예외로 올리지 않고 `runner_error`로 돌려준다.
        """
        base = StageRun(stage, tuple(tests))
        tree, results = tree.resolve(), results.resolve()
        if not _CONTAINER_NAME.fullmatch(name) or not all(
            _MOUNT_SOURCE.fullmatch(str(path)) for path in (tree, results)
        ):
            return replace(base, runner_error="unsafe_stage_parameters")
        results.mkdir(parents=True)
        junit_file = results / JUNIT_NAME
        junit_file.touch()
        os.chmod(junit_file, 0o666)  # 비루트 컨테이너 사용자가 이 파일 하나에만 쓴다
        os.chmod(results, 0o555)  # 새 항목을 만들 수 없다(host 디스크를 파일 수로 채우지 못한다)
        self.docker.stop(name)  # 같은 이름의 이전 컨테이너(정확한 이름)만 지운다
        step = "docker_run_failed"
        try:
            container_id = self.docker.run(
                self.options(name, tree, results, labels),
                self.profile.image_id,
                self.command(tests),
            )
            step = "docker_wait_failed"
            exit_code = self.docker.wait(name, self.profile.stage_timeout_seconds)
            step = "docker_inspect_failed"
            info = self.docker.inspect(name) or {}
            step = "docker_logs_failed"
            log, truncated = self.docker.logs_capped(name, self.profile.max_log_bytes)
        except (DockerError, OSError) as exc:
            code = exc.returncode if isinstance(exc, DockerError) else None
            return replace(base, runner_error=step, exit_code=code)
        finally:
            self.docker.stop(name)  # 제한 시간을 넘겼으면 여기서 컨테이너 전체를 강제 종료·제거한다
            os.chmod(results, 0o755)  # host가 run 정리(reset·archive) 때 지울 수 있게 되돌린다
        try:
            logs.mkdir(parents=True, exist_ok=True)
            log_file = logs / f"{stage}.log"
            log_file.write_bytes(log)
        except OSError:
            return replace(base, runner_error="log_write_failed", container_id=container_id)
        state = info.get("State") or {}
        effective = effective_profile(info)
        run = replace(
            base,
            exit_code=exit_code,
            timed_out=exit_code is None,
            oom_killed=exit_code is not None and bool(state.get("OOMKilled")),
            container_id=container_id,
            image_id=info.get("Image"),
            log_path=str(log_file),
            log_sha256=hashlib.sha256(log).hexdigest(),
            log_bytes=len(log),
            log_truncated=truncated,
            profile=effective,
        )
        problems = profile_problems(effective, self.profile)
        if problems:
            return replace(run, runner_error="profile_mismatch:" + ",".join(problems))
        if run.timed_out:
            return run
        unexpected = sorted(set(os.listdir(results)) - {JUNIT_NAME})
        if unexpected:  # 폴더 권한이 지켜지지 않은 host(권한을 무시하는 mount 등)
            return replace(run, runner_error="unexpected_result_entries")
        try:
            data = read_result_file(junit_file, self.profile.max_log_bytes)
            if not data:  # 미리 만든 빈 파일 그대로면 pytest가 쓰지 않은 것이다
                return replace(run, junit_error="junit_missing")
            return replace(run, junit=parse_junit(data, self.profile.max_log_bytes))
        except JunitError as exc:
            return replace(run, junit_error=str(exc))


# ── 판정 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: str | None = None
    reason: str | None = None


def module_of(path: str) -> str:
    """`tests/repro/test_x.py` → junit classname 접두어 `tests.repro.test_x`."""
    return path.removesuffix(".py").replace("/", ".")


def _runner_problem(run: StageRun) -> str | None:
    if run.runner_error is not None:
        return run.runner_error
    if run.exit_code in DOCKER_ERROR_EXITS:
        return f"docker_exit_{run.exit_code}"
    return None


def _execution_problem(run: StageRun) -> str | None:
    if run.timed_out:
        return "timeout"
    if run.oom_killed:
        return "oom"
    return None


def judge_r0(run: StageRun, regression_module: str) -> Verdict:
    """R0: base에서 보호 회귀가 모두 passed(exit 0). 아니면 환경 이상으로 멈춘다."""
    problem = _runner_problem(run) or _execution_problem(run)
    if problem is None and run.exit_code != 0:
        problem = f"pytest_exit_{run.exit_code}"
    if problem is None and run.junit is None:
        problem = run.junit_error or "junit_missing"
    if problem is None:
        assert run.junit is not None
        cases = run.junit.cases
        if not cases or any(c.outcome != "passed" for c in cases):
            problem = "not_all_passed"
        elif len(run.junit.in_module(regression_module)) != len(cases):
            problem = "unexpected_cases"
    if problem is not None:
        return Verdict(False, PROTECTION_UNAVAILABLE, f"r0_{problem}")
    return Verdict(True)


def judge_r1(run: StageRun, new_test_module: str) -> Verdict:
    """R1 재현: exit 1 + 수집 ≥ 1 + failures ≥ 1 + errors 0(새 테스트 파일의 case만)."""
    problem = _runner_problem(run)
    if problem is not None:
        return Verdict(False, PROTECTION_UNAVAILABLE, problem)
    problem = _execution_problem(run)
    if problem is None:
        if run.exit_code == 0:
            skipped_only = run.junit is not None and 0 < run.junit.skipped == run.junit.tests
            problem = "skipped_only" if skipped_only else "passed_on_base"
        elif run.exit_code != 1:
            problem = f"pytest_exit_{run.exit_code}"
        elif run.junit is None:
            problem = run.junit_error or "junit_missing"
        elif run.junit.tests < 1:
            problem = "no_tests"
        elif run.junit.errors > 0:
            problem = "errors"
        elif run.junit.failures < 1:
            problem = "no_failures"
        elif len(run.junit.in_module(new_test_module)) != run.junit.tests:
            problem = "unexpected_cases"
    if problem is not None:
        return Verdict(False, REPRO_NOT_FAILING, problem)
    return Verdict(True)


def judge_r2(
    run: StageRun, new_test_module: str, regression_cases: Iterable[tuple[str, str]]
) -> Verdict:
    """R2: exit 0 + 새 테스트 case 모두 passed + R0에서 본 보호 회귀 case 모두 passed."""
    problem = _runner_problem(run)
    if problem is not None:
        return Verdict(False, PROTECTION_UNAVAILABLE, problem)
    problem = _execution_problem(run)
    if problem is None and run.junit is None:
        problem = run.junit_error or "junit_missing"
    if problem is None:
        assert run.junit is not None
        report = run.junit
        new_cases = report.in_module(new_test_module)
        passed = {(c.classname, c.name) for c in report.cases if c.outcome == "passed"}
        required = list(regression_cases)
        if not new_cases or any(c.outcome != "passed" for c in new_cases):
            problem = "new_test_not_passed"
        elif not required or any(case not in passed for case in required):
            problem = "regression_not_passed"
        elif report.failures or report.errors or report.skipped:
            problem = "not_all_passed"
        elif run.exit_code != 0:
            problem = f"pytest_exit_{run.exit_code}"
    if problem is not None:
        return Verdict(False, REGRESSION_FAILED, problem)
    return Verdict(True)
