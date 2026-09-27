"""W10 R0/R1/R2 판정: 실제 pytest 실행에서 얻은 junit XML fixture와 종료 코드를 함께 본다.

- T-REPRO-01: base에서도 통과하는 새 테스트 → REPRO_NOT_FAILING(passed_on_base)
- T-REPRO-02: import 오류(exit 2), 미수집(exit 5), timeout, OOM, skip·xfail만 → 재현으로 불인정
- T-REPRO-03: R1 통과 뒤 candidate에서 새 테스트 실패·보호 회귀 실패 → REGRESSION_FAILED
- R0: 보호 회귀가 모두 passed가 아니면 환경 이상(PROTECTION_UNAVAILABLE)
- Docker 125/126/127·runner 오류는 테스트 결과가 아니다(PROTECTION_UNAVAILABLE)
- junit: DOCTYPE·ENTITY 거부, 크기 상한, testsuite 속성 불일치 거부, symlink·FIFO 거부
"""

import os

import pytest

from linemedic.control_plane.broker.runner import (
    JunitError,
    StageRun,
    judge_r0,
    judge_r1,
    judge_r2,
    module_of,
    parse_junit,
    read_result_file,
)
from linemedic.tests.helpers.runner import JUNIT_FIXTURES

LIMIT = 1024 * 1024
NEW_TEST = "tests/repro/test_missing_inspector.py"
NEW = module_of(NEW_TEST)
REGRESSION = module_of("tests/regression")


def report(name):
    return parse_junit((JUNIT_FIXTURES / f"{name}.xml").read_bytes(), LIMIT)


def suite(*cases, **declared):
    """testcase 목록으로 xunit2 XML을 만든다. cases: (classname, name, 자식 태그 또는 None)."""
    body = "".join(
        f'<testcase classname="{c}" name="{n}">' + (f"<{tag} />" if tag else "") + "</testcase>"
        for c, n, tag in cases
    )
    counts = {
        "tests": len(cases),
        "failures": sum(1 for *_, tag in cases if tag == "failure"),
        "errors": sum(1 for *_, tag in cases if tag == "error"),
        "skipped": sum(1 for *_, tag in cases if tag == "skipped"),
    }
    counts.update(declared)
    attrs = " ".join(f'{k}="{v}"' for k, v in counts.items())
    return f'<testsuites><testsuite name="pytest" {attrs}>{body}</testsuite></testsuites>'.encode()


def stage(name="R1", exit_code=1, junit=None, **kwargs):
    return StageRun(name, (NEW_TEST,), exit_code=exit_code, junit=junit, **kwargs)


R0_CASES = [(c.classname, c.name) for c in report("r0_regression_passed").cases]


# ── junit 파싱 ────────────────────────────────────────────────


def test_real_pytest_junit_fixtures_parse():
    keyerror = report("r1_keyerror")
    assert (keyerror.tests, keyerror.failures, keyerror.errors) == (1, 1, 0)
    assert keyerror.cases[0].classname == NEW
    passed = report("r2_all_passed")
    assert (passed.tests, passed.count("passed")) == (5, 5)
    assert len(passed.in_module(NEW)) == 1 and len(passed.in_module(REGRESSION)) == 4
    collection = report("r1_collection_error")
    assert (collection.tests, collection.errors) == (1, 1)
    assert report("r0_regression_passed").summary()["tests"] == 4


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><testsuite tests="0"/>',
            "junit_dtd_forbidden",
        ),
        (b'<testsuite tests="0"><!ENTITY x "y"></testsuite>', "junit_dtd_forbidden"),
        (b'<testsuite tests="0">' + b" " * LIMIT + b"</testsuite>", "junit_too_large"),
        (b"<testsuite", "junit_invalid"),
        (b"<report/>", "junit_invalid"),
        (b'<testsuite tests="many"/>', "junit_invalid"),
        (suite((NEW, "a", "failure"), tests=2), "junit_inconsistent"),
        (suite((NEW, "a", "failure"), failures=0), "junit_inconsistent"),
    ],
)
def test_unsafe_or_inconsistent_junit_is_rejected(data, reason):
    with pytest.raises(JunitError) as info:
        parse_junit(data, LIMIT)
    assert str(info.value) == reason


def test_error_outranks_failure_in_one_testcase():
    data = (
        b'<testsuite tests="1" failures="0" errors="1" skipped="0">'
        b'<testcase classname="c" name="n"><failure/><error/></testcase></testsuite>'
    )
    assert parse_junit(data, LIMIT).cases[0].outcome == "error"


def test_result_file_is_read_without_following_links(tmp_path):
    assert read_result_file(tmp_path / "missing.xml", LIMIT) is None
    target = tmp_path / "host-secret.txt"
    target.write_text("secret")
    (tmp_path / "link.xml").symlink_to(target)
    with pytest.raises(JunitError, match="junit_unreadable"):
        read_result_file(tmp_path / "link.xml", LIMIT)
    os.mkfifo(tmp_path / "fifo.xml")  # 읽는 쪽을 영원히 막을 수 있는 파일
    with pytest.raises(JunitError, match="junit_not_regular_file"):
        read_result_file(tmp_path / "fifo.xml", LIMIT)
    (tmp_path / "big.xml").write_bytes(b"x" * (LIMIT + 1))
    with pytest.raises(JunitError, match="junit_too_large"):
        read_result_file(tmp_path / "big.xml", LIMIT)
    (tmp_path / "ok.xml").write_bytes(b"<x/>")
    assert read_result_file(tmp_path / "ok.xml", LIMIT) == b"<x/>"


def test_module_of_maps_paths_to_junit_classnames():
    assert module_of("tests/repro/test_x.py") == "tests.repro.test_x"
    assert module_of("tests/regression") == "tests.regression"


# ── R1 재현 ───────────────────────────────────────────────────


def test_r1_accepts_only_exit_1_with_a_real_failure():
    verdict = judge_r1(stage(exit_code=1, junit=report("r1_keyerror")), NEW)
    assert (verdict.ok, verdict.code) == (True, None)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"exit_code": 0, "junit": parse_junit(suite((NEW, "a", None)), LIMIT)}, "passed_on_base"),
        (
            {"exit_code": 0, "junit": parse_junit(suite((NEW, "a", "skipped")), LIMIT)},
            "skipped_only",
        ),
        ({"exit_code": 2, "junit": None}, "pytest_exit_2"),
        ({"exit_code": 5, "junit": parse_junit(suite(), LIMIT)}, "pytest_exit_5"),
        ({"exit_code": 3}, "pytest_exit_3"),
        ({"exit_code": 4}, "pytest_exit_4"),
        ({"exit_code": 137, "oom_killed": True}, "oom"),
        ({"exit_code": None, "timed_out": True}, "timeout"),
        ({"exit_code": 1, "junit": None}, "junit_missing"),
        ({"exit_code": 1, "junit_error": "junit_dtd_forbidden"}, "junit_dtd_forbidden"),
        ({"exit_code": 1, "junit": parse_junit(suite(), LIMIT)}, "no_tests"),
        (
            {
                "exit_code": 1,
                "junit": parse_junit(suite((NEW, "a", "failure"), (NEW, "b", "error")), LIMIT),
            },
            "errors",
        ),
        ({"exit_code": 1, "junit": parse_junit(suite((NEW, "a", None)), LIMIT)}, "no_failures"),
        (
            {"exit_code": 1, "junit": parse_junit(suite(("tests.other", "a", "failure")), LIMIT)},
            "unexpected_cases",
        ),
    ],
)
def test_r1_does_not_count_other_outcomes_as_reproduction(kwargs, reason):  # T-REPRO-01·02
    verdict = judge_r1(stage(**kwargs), NEW)
    assert (verdict.ok, verdict.code, verdict.reason) == (False, "REPRO_NOT_FAILING", reason)


def test_r1_collection_error_fixture_is_not_reproduction():  # T-REPRO-02 import 오류
    verdict = judge_r1(stage(exit_code=2, junit=report("r1_collection_error")), NEW)
    assert (verdict.code, verdict.reason) == ("REPRO_NOT_FAILING", "pytest_exit_2")


@pytest.mark.parametrize("exit_code", [125, 126, 127])
def test_docker_exit_codes_are_runner_errors_not_test_results(exit_code):
    for judge in (
        lambda run: judge_r1(run, NEW),
        lambda run: judge_r2(run, NEW, R0_CASES),
    ):
        verdict = judge(stage(exit_code=exit_code, junit=report("r1_keyerror")))
        assert (verdict.code, verdict.reason) == (
            "PROTECTION_UNAVAILABLE",
            f"docker_exit_{exit_code}",
        )


def test_runner_error_is_protection_unavailable():
    verdict = judge_r1(stage(runner_error="profile_mismatch:network"), NEW)
    assert (verdict.code, verdict.reason) == ("PROTECTION_UNAVAILABLE", "profile_mismatch:network")


# ── R2 candidate ──────────────────────────────────────────────


def test_r2_passes_only_when_new_test_and_every_r0_case_passed():
    verdict = judge_r2(stage("R2", exit_code=0, junit=report("r2_all_passed")), NEW, R0_CASES)
    assert verdict.ok


@pytest.mark.parametrize(
    ("kwargs", "cases", "reason"),
    [
        ({"exit_code": 1, "junit": report("r1_keyerror")}, R0_CASES, "new_test_not_passed"),
        (
            {"exit_code": 0, "junit": report("r2_all_passed")},
            [*R0_CASES, (f"{REGRESSION}.test_summary_regression", "test_removed_case")],
            "regression_not_passed",
        ),
        ({"exit_code": 0, "junit": report("r2_all_passed")}, [], "regression_not_passed"),
        (
            {
                "exit_code": 0,
                "junit": parse_junit(
                    suite((NEW, "a", "skipped"), *((c, n, None) for c, n in R0_CASES)), LIMIT
                ),
            },
            R0_CASES,
            "new_test_not_passed",
        ),
        (
            {
                "exit_code": 0,
                "junit": parse_junit(
                    suite(
                        (NEW, "a", None),
                        ("tests.other", "x", "skipped"),
                        *((c, n, None) for c, n in R0_CASES),
                    ),
                    LIMIT,
                ),
            },
            R0_CASES,
            "not_all_passed",
        ),
        ({"exit_code": 1, "junit": report("r2_all_passed")}, R0_CASES, "pytest_exit_1"),
        ({"exit_code": None, "timed_out": True}, R0_CASES, "timeout"),
        ({"exit_code": 137, "oom_killed": True}, R0_CASES, "oom"),
        ({"exit_code": 0, "junit": None}, R0_CASES, "junit_missing"),
    ],
)
def test_r2_failures_block_the_pr(kwargs, cases, reason):  # T-REPRO-03
    verdict = judge_r2(stage("R2", **kwargs), NEW, cases)
    assert (verdict.ok, verdict.code, verdict.reason) == (False, "REGRESSION_FAILED", reason)


def test_r2_real_regression_failure_blocks_the_pr():  # T-REPRO-03 보호 회귀 실패
    fields_module = module_of("tests/repro/test_missing_inspector_fields.py")
    run = stage("R2", exit_code=1, junit=report("r2_regression_failed"))
    verdict = judge_r2(run, fields_module, R0_CASES)
    assert (verdict.code, verdict.reason) == ("REGRESSION_FAILED", "regression_not_passed")


# ── R0 기준 환경 ──────────────────────────────────────────────


def test_r0_requires_all_protected_regression_cases_passed():
    assert judge_r0(stage("R0", exit_code=0, junit=report("r0_regression_passed")), REGRESSION).ok


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"exit_code": 1, "junit": report("r0_regression_passed")}, "r0_pytest_exit_1"),
        ({"exit_code": None, "timed_out": True}, "r0_timeout"),
        ({"exit_code": 137, "oom_killed": True}, "r0_oom"),
        ({"exit_code": 0, "junit": None}, "r0_junit_missing"),
        ({"exit_code": 0, "junit_error": "junit_invalid"}, "r0_junit_invalid"),
        ({"exit_code": 0, "junit": parse_junit(suite(), LIMIT)}, "r0_not_all_passed"),
        (
            {
                "exit_code": 0,
                "junit": parse_junit(suite((f"{REGRESSION}.t", "a", "skipped")), LIMIT),
            },
            "r0_not_all_passed",
        ),
        (
            {"exit_code": 0, "junit": parse_junit(suite(("tests.other", "a", None)), LIMIT)},
            "r0_unexpected_cases",
        ),
        ({"exit_code": 125}, "r0_docker_exit_125"),
        ({"runner_error": "docker_run_failed"}, "r0_docker_run_failed"),
    ],
)
def test_r0_problems_mean_the_environment_is_unusable(kwargs, reason):
    verdict = judge_r0(stage("R0", **kwargs), REGRESSION)
    assert (verdict.ok, verdict.code, verdict.reason) == (False, "PROTECTION_UNAVAILABLE", reason)
