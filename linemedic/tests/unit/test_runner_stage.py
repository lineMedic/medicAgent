"""W10 runner 단계 실행(FakeDocker): 고정 argv·실행 프로필·timeout·결과 파일 처리.

실제 컨테이너 격리(network none·read-only·자원 제한)는
`integration/test_runner_docker.py`가 확인한다.
"""

import os

import pytest

from linemedic.common.clock import FakeClock
from linemedic.control_plane.broker.runner import (
    JUNIT_NAME,
    PYTEST_INI,
    Runner,
    profile_problems,
)
from linemedic.integrations.docker import DockerError
from linemedic.tests.helpers.runner import IMAGE_ID, Scripted, profile, scripted_docker

NAME = "lm-runner-prop-000000000001-1-r1"
TESTS = ["tests/repro/test_missing_inspector.py"]
LABELS = {"linemedic.role": "runner", "linemedic.stage": "R1"}


def run_stage(tmp_path, outcome, **profile_overrides):
    docker = scripted_docker({"R1": outcome})
    runner = Runner(docker, profile(**profile_overrides), FakeClock())
    tree = tmp_path / "trees" / "repro"
    tree.mkdir(parents=True, exist_ok=True)
    result = runner.run_stage(
        stage="R1",
        name=NAME,
        tree=tree,
        results=tmp_path / "results" / "R1",
        logs=tmp_path / "logs",
        tests=TESTS,
        labels=LABELS,
    )
    return result, docker


def test_container_uses_only_server_fixed_image_argv_and_limits(tmp_path):
    result, docker = run_stage(tmp_path, Scripted(exit_code=1, junit="r1_keyerror"))
    ((_, options, image, command),) = [c for c in docker.calls if c[0] == "run"]
    assert image == IMAGE_ID
    joined = " ".join(options)
    for expected in (
        "--network none",
        "--read-only",
        "--tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m",
        "--cpus 1",
        "--memory 512m",
        "--memory-swap 512m",
        "--pids-limit 64",
        "--ulimit fsize=67108864",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 10001:10001",
        "target=/work/repo,readonly",
        "--env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
    ):
        assert expected in joined, expected
    for flag in ("--privileged", "--pid", "--ipc", "--cap-add", "--rm", "--volume", "-v"):
        assert flag not in options, flag
    assert "docker.sock" not in joined and "--network host" not in joined
    assert command[:5] == ["python", "-m", "pytest", "-c", PYTEST_INI]
    assert "--noconftest" in command and ["-p", "no:cacheprovider"] == command[7:9]
    assert command[-2:] == ["--", TESTS[0]]
    assert result.exit_code == 1 and result.junit is not None and result.junit.failures == 1
    assert result.runner_error is None and result.image_id == IMAGE_ID


def test_stale_container_is_removed_by_exact_name_before_and_after(tmp_path):
    _, docker = run_stage(tmp_path, Scripted(exit_code=0, junit="r0_regression_passed"))
    events = [c[0] if c[0] != "stop" else ("stop", c[1]) for c in docker.calls]
    run_at = events.index("run")
    assert ("stop", NAME) in events[:run_at] and ("stop", NAME) in events[run_at:]
    assert NAME not in docker.containers


def test_timeout_kills_the_whole_container_and_skips_results(tmp_path):
    result, docker = run_stage(tmp_path, Scripted(exit_code=None, junit="r1_keyerror"))
    assert result.timed_out and result.exit_code is None and result.junit is None
    assert ("wait", NAME, 60) in docker.calls
    assert docker.calls[-1] == ("stop", NAME)


def test_docker_run_failure_is_a_runner_error_with_exit_code(tmp_path):
    docker = scripted_docker({})

    def fail(options, image, command=None):
        raise DockerError("docker run 실패", 125)

    docker.run = fail
    runner = Runner(docker, profile(), FakeClock())
    result = runner.run_stage(
        stage="R1",
        name=NAME,
        tree=tmp_path,
        results=tmp_path / "results",
        logs=tmp_path / "logs",
        tests=TESTS,
        labels=LABELS,
    )
    assert (result.runner_error, result.exit_code) == ("docker_run_failed", 125)


def test_effective_profile_mismatch_discards_results(tmp_path):
    outcome = Scripted(
        exit_code=0,
        junit="r0_regression_passed",
        inspect={"HostConfig": {"NetworkMode": "bridge", "CapDrop": []}},
    )
    result, _ = run_stage(tmp_path, outcome)
    assert result.runner_error == "profile_mismatch:network,capabilities"
    assert result.junit is None


def test_profile_problems_cover_every_isolation_item(tmp_path):
    result, _ = run_stage(tmp_path, Scripted(exit_code=0, junit="r0_regression_passed"))
    good = dict(result.profile)
    assert profile_problems(good, profile()) == []
    bad = {
        **good,
        "image": "sha256:" + "0" * 64,
        "read_only_rootfs": False,
        "privileged": True,
        "cap_add": ["NET_ADMIN"],
        "security_opt": [],
        "memory": 0,
        "memory_swap": -1,
        "nano_cpus": 0,
        "pids_limit": None,
        "ulimits": [],
        "pid_mode": "host",
        "user": "0:0",
        "mounts": [
            {"destination": "/work/repo", "rw": True, "type": "bind"},
            {"destination": "/var/run/docker.sock", "rw": True, "type": "bind"},
        ],
    }
    assert profile_problems(bad, profile()) == [
        "image",
        "read_only",
        "privileged",
        "capabilities",
        "no_new_privileges",
        "memory",
        "swap",
        "cpus",
        "pids",
        "file_size_limit",
        "namespaces",
        "user",
        "repo_read_only",
        "results_mount",
        "no_extra_mounts",
    ]


def test_logs_are_capped_and_kept_outside_the_container_writable_mount(tmp_path):
    lines = ["x" * 1000] * 50
    result, _ = run_stage(
        tmp_path, Scripted(exit_code=1, junit="r1_keyerror", logs=lines), max_log_bytes=4096
    )
    assert result.log_truncated and result.log_bytes == 4096
    assert result.log_path == str(tmp_path / "logs" / "R1.log")
    assert not (tmp_path / "results" / "R1" / "R1.log").exists()
    # 단계가 끝나면 host가 정리할 수 있게 폴더 권한을 되돌린다
    assert oct(os.stat(tmp_path / "results" / "R1").st_mode & 0o777) == "0o755"


@pytest.mark.skipif(os.geteuid() == 0, reason="root는 폴더 권한을 무시한다")
def test_container_can_write_only_the_precreated_junit_file(tmp_path):
    """결과 폴더는 0555라 새 파일·폴더를 만들 수 없고 junit.xml 하나에만 쓴다(PR #50 리뷰)."""
    seen = {}

    def during_run(results):
        seen["dir_mode"] = oct(os.stat(results).st_mode & 0o777)
        seen["entries"] = sorted(os.listdir(results))
        for attempt in (
            lambda: (results / "extra.bin").write_bytes(b"x"),
            lambda: (results / "sub").mkdir(),
        ):
            try:
                attempt()
                seen.setdefault("created", True)
            except PermissionError:
                pass

    result, _ = run_stage(tmp_path, Scripted(exit_code=1, junit="r1_keyerror", before=during_run))
    assert seen == {"dir_mode": "0o555", "entries": [JUNIT_NAME]}
    assert result.junit is not None and result.junit_error is None


def test_unexpected_entries_in_the_results_folder_are_a_runner_error(tmp_path):
    """폴더 권한이 지켜지지 않은 host에서 컨테이너가 다른 항목을 만들었으면 결과를 쓰지 않는다."""

    def ignore_permissions(results):
        os.chmod(results, 0o755)
        (results / "fill.bin").write_bytes(b"x" * 10)

    result, _ = run_stage(
        tmp_path, Scripted(exit_code=0, junit="r0_regression_passed", before=ignore_permissions)
    )
    assert result.runner_error == "unexpected_result_entries" and result.junit is None


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("wait", "docker_wait_failed"),
        ("inspect", "docker_inspect_failed"),
        ("logs_capped", "docker_logs_failed"),
    ],
)
def test_docker_errors_after_run_are_runner_errors_and_the_container_is_removed(
    tmp_path, monkeypatch, method, error
):
    """`docker run` 뒤의 Docker 오류도 예외로 올리지 않는다(PR #50 리뷰: work가 RUNNING에 굳음)."""
    docker = scripted_docker({"R1": Scripted(exit_code=1, junit="r1_keyerror")})

    def fail(*args, **kwargs):
        raise DockerError(f"docker {method} 실패: No such container: {NAME}", 1)

    monkeypatch.setattr(docker, method, fail)
    runner = Runner(docker, profile(), FakeClock())
    (tmp_path / "tree").mkdir()
    result = runner.run_stage(
        stage="R1",
        name=NAME,
        tree=tmp_path / "tree",
        results=tmp_path / "results",
        logs=tmp_path / "logs",
        tests=TESTS,
        labels=LABELS,
    )
    assert (result.runner_error, result.exit_code) == (error, 1)
    assert [c for c in docker.calls if c[0] == "stop"][-1] == ("stop", NAME)
    assert NAME not in docker.containers
    assert oct(os.stat(tmp_path / "results").st_mode & 0o777) == "0o755"


def test_log_write_failure_is_a_runner_error(tmp_path):
    (tmp_path / "logs").write_text("not a directory")  # logs 경로에 폴더를 만들 수 없다
    result, _ = run_stage(tmp_path, Scripted(exit_code=1, junit="r1_keyerror"))
    assert result.runner_error == "log_write_failed"


def test_symlinked_junit_planted_by_the_container_is_not_followed(tmp_path):
    secret = tmp_path / "host-secret.xml"
    secret.write_text("<testsuite tests='0'/>")

    def plant(results):  # 폴더 권한을 무시하는 host를 흉내 낸 뒤 junit.xml을 symlink로 바꾼다
        os.chmod(results, 0o755)
        (results / JUNIT_NAME).unlink()
        (results / JUNIT_NAME).symlink_to(secret)

    result, _ = run_stage(tmp_path, Scripted(exit_code=1, before=plant))
    assert result.junit is None and result.junit_error == "junit_unreadable"


def test_missing_or_unsafe_junit_is_recorded(tmp_path):
    result, _ = run_stage(tmp_path, Scripted(exit_code=1))
    assert result.junit_error == "junit_missing"
    result, _ = run_stage(tmp_path / "b", Scripted(exit_code=1, junit=b"<!DOCTYPE x><testsuite/>"))
    assert result.junit_error == "junit_dtd_forbidden"


def test_unsafe_mount_paths_or_names_never_reach_docker(tmp_path):
    docker = scripted_docker({})
    runner = Runner(docker, profile(), FakeClock())
    bad_tree = tmp_path / "a,readonly=false"
    bad_tree.mkdir()
    for name, tree in ((NAME, bad_tree), ("Bad Name", tmp_path)):
        result = runner.run_stage(
            stage="R1",
            name=name,
            tree=tree,
            results=tmp_path / "results",
            logs=tmp_path / "logs",
            tests=TESTS,
            labels=LABELS,
        )
        assert result.runner_error == "unsafe_stage_parameters"
    assert docker.calls == []


def test_image_problem_requires_the_pinned_id(tmp_path):
    docker = scripted_docker({})
    runner = Runner(docker, profile(), FakeClock())
    assert runner.image_problem() is None
    docker.images[IMAGE_ID] = "sha256:" + "cd" * 32
    assert runner.image_problem() == "runner_image_mismatch"
    del docker.images[IMAGE_ID]
    assert runner.image_problem() == "runner_image_missing"


def doctor_check(env, *, docker=True, found=None):
    from linemedic.scripts import doctor

    ctx = doctor.DoctorContext(
        env=env,
        which=lambda name: f"/usr/bin/{name}" if docker else None,
        run=lambda argv: found,
    )
    return doctor.check_runner_image(ctx)


def test_doctor_runner_image_pins_the_exact_image_id():
    assert doctor_check({})[0] == "NOT_CONFIGURED"
    assert doctor_check({"RUNNER_IMAGE_ID": "linemedic-runner:v1"})[0] == "FAIL"  # 태그 금지
    pinned = {"RUNNER_IMAGE_ID": IMAGE_ID}
    assert doctor_check(pinned, docker=False)[0] == "MISSING"
    assert doctor_check(pinned, found=None)[0] == "MISSING"
    status, detail = doctor_check(pinned, found="sha256:" + "cd" * 32)
    assert status == "FAIL" and "불일치" in detail
    assert doctor_check(pinned, found=IMAGE_ID)[0] == "OK"


def test_profile_rejects_tags_and_root(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        profile(image_id="linemedic-runner:latest")
    with pytest.raises(ValueError):
        profile(uid=0)
