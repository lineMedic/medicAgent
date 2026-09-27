"""W05 1부 단위 테스트: 업무 계약·판정 엔진·core observer·prober HTTP·S1b harness.

시간은 FakeClock으로만 흐른다(D51). 실제 S1b 컨테이너 시험은
`linemedic/tests/integration/test_verifier_docker.py`(docker 마커)에서 한다.
"""

import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from linemedic import cli
from linemedic.common.clock import FakeClock
from linemedic.common.config import ConfigError, validate_model
from linemedic.control_plane import verifier
from linemedic.control_plane.observer import (
    ContainerObserver,
    ObserverError,
    RecurrenceSignature,
)
from linemedic.control_plane.store import Store
from linemedic.control_plane.verifier import (
    Contract,
    FixtureGuard,
    HttpResponse,
    ProberHttp,
    RequestFailed,
    RequestTimeout,
    VerificationRun,
    evaluate_case,
    load_contract,
    resolve_cases,
    verify,
)
from linemedic.factory_sim import scenarios
from linemedic.factory_sim.negative import harness, wrong_200_defects
from linemedic.integrations.docker import CliDocker, CommandResult, FakeDocker, _CliLogStream
from linemedic.tests.helpers.db_rows import count, insert_run, row
from linemedic.tests.helpers.demo_states import prepare_verifying_incident

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "linemedic" / "contracts" / "defect-summary-v1.toml"
EVAL_DIR = REPO_ROOT / "linemedic" / "eval"
HOLDOUT = EVAL_DIR / "holdout-defects-v1.json"
LOTS = REPO_ROOT / "l3-mes-api-seed" / "data" / "lots"
NEGATIVE_DIR = REPO_ROOT / "linemedic" / "factory_sim" / "negative"

BUG_LOT = "L3-0927-118"
NORMAL_LOT = "L3-0927-101"
HOLDOUT_LOT = "L3-HOLDOUT-201"
RUN_ID = "r-20260927-050000-abcd"
TARGET = "linemedic-mes-candidate"
MES_ID = "sha256:" + "a" * 64
PROBER_ID = "sha256:" + "p" * 64
SINCE = "2026-09-27T00:00:00.000000Z"

# spec 08 §4 YAML을 손으로 옮긴 기대값. 계약 TOML과 필드별로 비교한다.
SPEC_08_CONTRACT = {
    "contract_id": "defect-summary-v1",
    "entry_service": "mes-api",
    "cases": [
        {
            "id": "missing-inspector",
            "request": {
                "method": "GET",
                "path": "/defects/summary",
                "query": {"lot_id": "L3-0927-118"},
            },
            "expect": {
                "status": 200,
                "body": {
                    "lot_id": "L3-0927-118",
                    "total_defects": 7,
                    "by_inspector": {"I-01": 3, "I-02": 2, "미지정": 2},
                },
            },
        },
        {
            "id": "normal-regression",
            "request": {
                "method": "GET",
                "path": "/defects/summary",
                "query": {"lot_id": "L3-0927-101"},
            },
            "expect": {
                "status": 200,
                "body": {
                    "lot_id": "L3-0927-101",
                    "total_defects": 3,
                    "by_inspector": {"I-01": 2, "I-02": 1},
                },
            },
        },
        {"id": "variant-held-out", "fixture_ref": "holdout-defects-v1"},
    ],
    "assertions": [
        "strict_response_schema",
        "exact_lot_id",
        "exact_total_defects",
        "exact_by_inspector_mapping",
        "sum_groups_equals_total",
    ],
    "observation": {
        "samples": 4,
        "interval_seconds": 10,
        "recurrence_window_seconds": 60,
        "require_all_samples": True,
        "require_log_observer_healthy": True,
        "require_runtime_identity_unchanged": True,
    },
}
# spec 08 §8 결과 예시의 필드
SPEC_08_RESULT_FIELDS = {
    "verification_id",
    "origin",
    "contract_id",
    "verdict",
    "reason",
    "samples_completed",
    "samples_required",
    "observation_complete",
    "failed_assertions",
    "resolved_written",
}
SPEC_08_FAILED_EXAMPLE = {
    "case_id": "missing-inspector",
    "field": "total_defects",
    "expected": 7,
    "actual": 0,
}

CONTRACT, CONTRACT_SHA = load_contract(CONTRACT_PATH)
CASES = resolve_cases(CONTRACT, EVAL_DIR)
EXPECTED = {case.query["lot_id"]: case.expect_body for case in CASES}
CASE_BY_LOT = {case.query["lot_id"]: case.case_id for case in CASES}
S1_ERROR = {
    "level": "ERROR",
    "service": "mes-api",
    "event": "request_failed",
    "lot_id": BUG_LOT,
    "path": "/defects/summary",
    "status": 500,
    "error_type": "KeyError",
    "error_field": "inspector_id",
    "top_frame": "app.defects:summarize",
    "top_frame_line": 7,
}


def as_json(body) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def load_lot(lot_id: str) -> list[dict]:
    if lot_id == HOLDOUT_LOT:
        return json.loads(HOLDOUT.read_text(encoding="utf-8"))["input"]["records"]
    return json.loads((LOTS / f"{lot_id}.json").read_text(encoding="utf-8"))["records"]


def reference_summary(lot_id: str, records: list[dict]) -> dict:
    """테스트 전용 참조 집계(업무 규칙). 제품 코드·시드에는 두지 않는다."""
    counts = Counter(r.get("inspector_id", "미지정") for r in records)
    return {"lot_id": lot_id, "total_defects": len(records), "by_inspector": dict(counts)}


Responder = Callable[[str, dict[str, str], float], HttpResponse]


def correct_response(path: str, query: dict[str, str], elapsed: float) -> HttpResponse:
    return HttpResponse(200, as_json(EXPECTED[query["lot_id"]]))


class FakeHttp:
    """계약 case별 응답을 돌려주는 가짜 HTTP. 호출 시각(t0 기준 경과 초)을 기록한다."""

    def __init__(self, clock: FakeClock, responder: Responder = correct_response) -> None:
        self.clock = clock
        self.t0 = clock.monotonic()
        self.responder = responder
        self.calls: list[tuple[float, str, dict[str, str]]] = []

    def get(self, path, query):
        elapsed = self.clock.monotonic() - self.t0
        self.calls.append((elapsed, path, dict(query)))
        return self.responder(path, dict(query), elapsed)


@dataclass
class Rig:
    clock: FakeClock
    docker: FakeDocker
    observer: ContainerObserver
    http: FakeHttp
    kwargs: dict
    run: VerificationRun | None = None

    def start(self) -> VerificationRun:
        self.run = VerificationRun(**self.kwargs)
        return self.run


def make_rig(
    responder: Responder = correct_response,
    fixture_guard: FixtureGuard | None = None,
    origin: str = harness.ORIGIN,
) -> Rig:
    clock = FakeClock()
    docker = FakeDocker()
    docker.images["mes:candidate"] = MES_ID
    docker.run(["--name", TARGET], "mes:candidate")
    observer = ContainerObserver(docker, TARGET, harness.S1_SIGNATURE)
    identity = observer.start(since=SINCE)
    http = FakeHttp(clock, responder)
    kwargs = {
        "contract": CONTRACT,
        "contract_sha256": CONTRACT_SHA,
        "cases": CASES,
        "http": http,
        "observer": observer,
        "clock": clock,
        "origin": origin,
        "target": {"container": TARGET, **identity},
        "fixture_guard": fixture_guard,
    }
    rig = Rig(clock, docker, observer, http, kwargs)
    rig.start()
    return rig


def drive(rig: Rig, events: dict[int, Callable[[Rig], None]] | None = None, until: int = 120):
    """1초씩 시계를 움직이며 step()을 부른다. events[t]는 그 초의 step 직전에 실행한다."""
    events = events or {}
    for t in range(until + 1):
        if t:
            rig.clock.advance(1)
        if t in events:
            events[t](rig)
        result = rig.run.step()
        if result is not None:
            return t, result
    raise AssertionError("판정이 나오지 않음")


def respond_for(lot_id: str, make_body: Callable[[dict], bytes], status: int = 200) -> Responder:
    def responder(path, query, elapsed):
        if query["lot_id"] == lot_id:
            return HttpResponse(status, make_body(EXPECTED[lot_id]))
        return correct_response(path, query, elapsed)

    return responder


# ── 업무 계약 ─────────────────────────────────────────────────


def test_contract_toml_matches_spec_08_yaml_field_by_field():
    raw = tomllib.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert raw.keys() == SPEC_08_CONTRACT.keys()
    for key, value in SPEC_08_CONTRACT.items():
        assert raw[key] == value, key
    dumped = CONTRACT.model_dump(exclude_none=True)
    for key, value in SPEC_08_CONTRACT.items():
        assert dumped[key] == value, key


def test_contract_sha256_is_file_bytes_digest():
    assert CONTRACT_SHA == hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()


def test_contract_does_not_carry_holdout_expectations():
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert HOLDOUT_LOT not in text
    assert "I-03" not in text


def test_resolve_cases_reads_holdout_expectation_from_eval():
    assert [case.case_id for case in CASES] == [
        "missing-inspector",
        "normal-regression",
        "variant-held-out",
    ]
    assert {case.path for case in CASES} == {"/defects/summary"}
    assert {case.expect_status for case in CASES} == {200}
    holdout = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    held_out = CASES[2]
    assert held_out.query == {"lot_id": HOLDOUT_LOT}
    assert held_out.expect_body == holdout["expected"]


def test_resolve_cases_rejects_holdout_with_mismatched_lot(tmp_path):
    holdout = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    holdout["expected"]["lot_id"] = "L3-OTHER-000"
    (tmp_path / "holdout-defects-v1.json").write_text(json.dumps(holdout), encoding="utf-8")
    with pytest.raises(ValueError, match="lot_id"):
        resolve_cases(CONTRACT, tmp_path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            'entry_service = "mes-api"', 'entry_service = "mes-api"\nowner = "x"', id="extra_key"
        ),
        pytest.param(
            "require_log_observer_healthy = true",
            "require_log_observer_healthy = false",
            id="relax_observer",
        ),
        pytest.param(
            'id = "missing-inspector"',
            'id = "missing-inspector"\nfixture_ref = "holdout-defects-v1"',
            id="two_sources",
        ),
        pytest.param("samples = 4", 'samples = "4"', id="string_number"),
        pytest.param("samples = 4", "samples = 7", id="sample_after_window"),
        pytest.param('"sum_groups_equals_total",', '"trust_app_status",', id="unknown_assertion"),
        pytest.param('method = "GET"', 'method = "POST"', id="non_get"),
    ],
)
def test_invalid_contract_is_rejected(tmp_path, old, new):
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "contract.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_contract(path)


# ── 판정 엔진: T-VERIFY-01 ────────────────────────────────────


def test_t_verify_01_pass_only_after_60_seconds():
    rig = make_rig()
    for t in range(60):
        if t:
            rig.clock.advance(1)
        assert rig.run.step() is None, f"t={t}에 판정이 나오면 안 된다"
    assert rig.run.elapsed() == 59
    rig.clock.advance(1)
    result = rig.run.step()
    assert result is not None
    assert (result.verdict, result.reason) == ("PASS", "all_checks_passed")
    assert result.samples_completed == result.samples_required == 4
    assert result.observation_complete is True
    assert result.failed_assertions == []
    assert result.resolved_written is False
    assert result.elapsed_seconds == 60.0
    # t=0·10·20·30초에 모든 case를 원래 경로로 호출한다.
    assert sorted({t for t, _, _ in rig.http.calls}) == [0, 10, 20, 30]
    assert len(rig.http.calls) == 4 * len(CASES)
    assert {path for _, path, _ in rig.http.calls} == {"/defects/summary"}
    assert {q["lot_id"] for _, _, q in rig.http.calls} == {BUG_LOT, NORMAL_LOT, HOLDOUT_LOT}


def test_verify_loop_passes_at_60_seconds_with_fake_clock():
    rig = make_rig()
    kwargs = {**rig.kwargs, "http": FakeHttp(rig.clock)}
    result = verify(**kwargs)
    assert (result.verdict, result.reason) == ("PASS", "all_checks_passed")
    assert result.elapsed_seconds == 60.0
    assert rig.clock.monotonic() == 60.0


def test_result_is_final_and_stops_sampling():
    rig = make_rig(respond_for(BUG_LOT, lambda b: as_json({**b, "total_defects": 0})))
    _, result = drive(rig)
    calls = len(rig.http.calls)
    rig.clock.advance(30)
    assert rig.run.step() is result
    assert len(rig.http.calls) == calls


# ── 판정 엔진: T-VERIFY-02 거짓 정상 거절 ─────────────────────


DUPLICATE_KEY_BODY = (
    b'{"lot_id": "L3-0927-118", "lot_id": "L3-0927-118", "total_defects": 7, "by_inspector": {}}'
)


@pytest.mark.parametrize(
    ("lot_id", "make_body", "assertion"),
    [
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "total_defects": 0}),
            "exact_total_defects",
            id="total_zero",
        ),
        pytest.param(
            BUG_LOT, lambda b: as_json({**b, "lot_id": NORMAL_LOT}), "exact_lot_id", id="other_lot"
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "status": "resolved"}),
            "strict_response_schema",
            id="extra_key",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "total_defects": "7"}),
            "exact_total_defects",
            id="string_number",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "total_defects": True}),
            "exact_total_defects",
            id="bool_total",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "total_defects": -1}),
            "exact_total_defects",
            id="negative_total",
        ),
        # 7.0 == 7, True == 1이 Python에서 참이어도 JSON 정수가 아니면 거절한다.
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "total_defects": 7.0}),
            "exact_total_defects",
            id="float_total",
        ),
        pytest.param(
            NORMAL_LOT,
            lambda b: as_json({**b, "by_inspector": {"I-01": 2, "I-02": True}}),
            "exact_by_inspector_mapping",
            id="bool_group_count",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({"lot_id": b["lot_id"], "total_defects": b["total_defects"]}),
            "strict_response_schema",
            id="missing_key",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "by_inspector": {"I-01": 3, "I-02": 2}}),
            "exact_by_inspector_mapping",
            id="unassigned_dropped",
        ),
        pytest.param(
            BUG_LOT,
            lambda b: as_json({**b, "by_inspector": {"I-01": 3, "I-02": 2, "미지정": 3}}),
            "sum_groups_equals_total",
            id="groups_exceed_total",
        ),
        pytest.param(
            BUG_LOT, lambda b: DUPLICATE_KEY_BODY, "strict_response_schema", id="duplicate_key"
        ),
        pytest.param(BUG_LOT, lambda b: b"OK", "strict_response_schema", id="not_json"),
        pytest.param(BUG_LOT, lambda b: as_json([b]), "strict_response_schema", id="json_array"),
        pytest.param(
            HOLDOUT_LOT,
            lambda b: as_json({**b, "total_defects": 0}),
            "exact_total_defects",
            id="holdout_total_zero",
        ),
    ],
)
def test_t_verify_02_wrong_200_is_content_mismatch(lot_id, make_body, assertion):
    rig = make_rig(respond_for(lot_id, make_body))
    t, result = drive(rig)
    assert t == 0
    assert (result.verdict, result.reason) == ("FAIL", "content_mismatch")
    assert result.samples_completed == 1
    assert result.observation_complete is False
    assert result.resolved_written is False
    failed = {(f["case_id"], f["assertion"]) for f in result.failed_assertions}
    assert (CASE_BY_LOT[lot_id], assertion) in failed
    assert {case_id for case_id, _ in failed} == {CASE_BY_LOT[lot_id]}


def test_correct_responses_have_no_failed_assertions():
    for case in CASES:
        assert evaluate_case(case, correct_response(case.path, case.query, 0)) == []


def test_fail_result_has_spec_08_fields():
    rig = make_rig(respond_for(BUG_LOT, lambda b: as_json({**b, "total_defects": 0})))
    _, result = drive(rig)
    data = result.to_dict()
    assert SPEC_08_RESULT_FIELDS <= data.keys()
    assert re.fullmatch(r"VER-[0-9A-F]{12}", data["verification_id"])
    assert data["origin"] == "human_injected_negative"
    assert data["contract_id"] == "defect-summary-v1"
    assert data["contract_sha256"] == CONTRACT_SHA
    assert (data["samples_completed"], data["samples_required"]) == (1, 4)
    assert any(SPEC_08_FAILED_EXAMPLE.items() <= f.items() for f in data["failed_assertions"])
    assert data["target"] == {"container": TARGET, "container_id": f"{1:064x}", "image_id": MES_ID}
    assert json.loads(json.dumps(data)) == data


def test_http_error_status_is_business_error():
    rig = make_rig(
        respond_for(BUG_LOT, lambda b: b'{"error": "internal_error"}', status=500),
    )
    t, result = drive(rig)
    assert t == 0
    assert (result.verdict, result.reason) == ("FAIL", "business_error")
    assert {
        "case_id": "missing-inspector",
        "assertion": "status",
        "field": "status",
        "expected": 200,
        "actual": 500,
    } in result.failed_assertions


def test_mismatch_in_later_sample_fails_early():
    def responder(path, query, elapsed):
        if elapsed >= 20 and query["lot_id"] == NORMAL_LOT:
            return HttpResponse(200, as_json({**EXPECTED[NORMAL_LOT], "total_defects": 4}))
        return correct_response(path, query, elapsed)

    t, result = drive(make_rig(responder))
    assert t == 20
    assert (result.verdict, result.reason) == ("FAIL", "content_mismatch")
    assert result.samples_completed == 3
    assert result.observation_complete is False


# ── 판정 엔진: T-VERIFY-04 재발 ───────────────────────────────


def test_t_verify_04_same_error_after_all_samples_fails():
    rig = make_rig()
    t, result = drive(rig, {45: lambda r: r.docker.streams[TARGET].push(json.dumps(S1_ERROR))})
    assert t == 45
    assert (result.verdict, result.reason) == ("FAIL", "error_recurred")
    assert result.samples_completed == 4
    assert result.observation_complete is False
    assert result.observer["recurrences"] == 1
    assert result.resolved_written is False


def test_recurrence_ignores_line_number_and_lot_id():
    line = json.dumps({**S1_ERROR, "top_frame_line": 99, "lot_id": "L3-0927-777"})
    rig = make_rig()
    t, result = drive(rig, {12: lambda r: r.docker.streams[TARGET].push(line)})
    assert (t, result.verdict, result.reason) == (12, "FAIL", "error_recurred")


def test_other_log_lines_are_not_recurrence():
    lines = [
        json.dumps({"level": "INFO", "event": "request_completed", "path": "/defects/summary"}),
        json.dumps({**S1_ERROR, "path": "/healthz"}),
        json.dumps({**S1_ERROR, "error_type": "ValueError"}),
        json.dumps({**S1_ERROR, "top_frame": "app.data:load_lot"}),
        "Traceback (most recent call last):",
        "",
    ]

    def push_all(rig):
        for line in lines:
            rig.docker.streams[TARGET].push(line)

    t, result = drive(make_rig(), {5: push_all})
    assert (t, result.verdict) == (60, "PASS")
    assert result.observer["recurrences"] == 0
    assert result.observer["lines_seen"] == 5
    assert result.observer["non_json_lines"] == 1


# ── 판정 엔진: T-VERIFY-05 identity·fixture ──────────────────


def test_t_verify_05_image_change_is_inconclusive():
    def swap_image(rig):
        rig.docker.containers[TARGET]["Image"] = "sha256:" + "b" * 64

    t, result = drive(make_rig(), {25: swap_image})
    assert t == 25
    assert (result.verdict, result.reason) == ("INCONCLUSIVE", "identity_changed")
    assert result.detail == "image ID 변경"
    assert result.samples_completed == 3
    assert result.observation_complete is False


def test_container_replacement_is_inconclusive():
    def replace(rig):
        rig.docker.containers[TARGET]["Id"] = "f" * 64

    t, result = drive(make_rig(), {33: replace})
    assert (t, result.verdict, result.reason) == (33, "INCONCLUSIVE", "identity_changed")
    assert result.detail == "container ID 변경"


def test_container_disappearing_is_inconclusive():
    t, result = drive(make_rig(), {50: lambda rig: rig.docker.containers.pop(TARGET)})
    assert (t, result.verdict, result.reason) == (50, "INCONCLUSIVE", "identity_changed")
    assert result.detail == "검증 대상 컨테이너가 사라짐"


def test_identity_change_wins_over_wrong_response():
    """대상이 바뀐 뒤의 응답은 그 대상의 반증으로 쓰지 않는다."""

    def responder(path, query, elapsed):
        if elapsed >= 10:
            return HttpResponse(200, as_json({**EXPECTED[query["lot_id"]], "total_defects": 0}))
        return correct_response(path, query, elapsed)

    def swap_image(rig):
        rig.docker.containers[TARGET]["Image"] = "sha256:" + "b" * 64

    t, result = drive(make_rig(responder), {10: swap_image})
    assert (t, result.verdict, result.reason) == (10, "INCONCLUSIVE", "identity_changed")
    assert result.failed_assertions == []


@pytest.fixture
def guarded_files(tmp_path):
    contract = tmp_path / "defect-summary-v1.toml"
    contract.write_bytes(CONTRACT_PATH.read_bytes())
    lot = tmp_path / f"{BUG_LOT}.json"
    lot.write_bytes((LOTS / f"{BUG_LOT}.json").read_bytes())
    return contract, lot


def test_t_verify_05_fixture_change_is_inconclusive(guarded_files):
    contract, lot = guarded_files
    guard = FixtureGuard([contract, lot])

    def tamper(rig):
        lot.write_text(json.dumps({"lot_id": BUG_LOT, "records": []}), encoding="utf-8")

    t, result = drive(make_rig(fixture_guard=guard), {15: tamper})
    assert t == 15
    assert (result.verdict, result.reason) == ("INCONCLUSIVE", "fixture_changed")
    assert result.fixture_sha256 == guard.digest
    assert result.observation_complete is False


def test_fixture_removal_is_inconclusive(guarded_files):
    contract, lot = guarded_files
    guard = FixtureGuard([contract, lot])
    t, result = drive(make_rig(fixture_guard=guard), {41: lambda rig: contract.unlink()})
    assert (t, result.verdict, result.reason) == (41, "INCONCLUSIVE", "fixture_changed")


def test_unchanged_fixture_allows_pass(guarded_files):
    guard = FixtureGuard(list(guarded_files))
    t, result = drive(make_rig(fixture_guard=guard))
    assert (t, result.verdict) == (60, "PASS")
    assert result.fixture_sha256 == guard.digest


# ── core observer: 관찰 공백·무응답 ───────────────────────────


def test_core_observer_stream_end_is_inconclusive_not_pass():
    t, result = drive(make_rig(), {40: lambda rig: rig.docker.streams[TARGET].end()})
    assert t == 40
    assert (result.verdict, result.reason) == ("INCONCLUSIVE", "observer_gap")
    assert result.detail == "로그 스트림이 끊김"
    assert result.samples_completed == 4
    assert result.observer["recurrences"] == 0
    assert result.observation_complete is False


@pytest.mark.parametrize(
    "error",
    [RequestTimeout("요청 timeout"), RequestFailed("ConnectionRefusedError")],
    ids=["timeout", "connection_failed"],
)
def test_unanswered_sample_is_inconclusive(error):
    def responder(path, query, elapsed):
        if elapsed >= 10 and query["lot_id"] == NORMAL_LOT:
            raise error
        return correct_response(path, query, elapsed)

    t, result = drive(make_rig(responder))
    assert t == 10
    assert (result.verdict, result.reason) == ("INCONCLUSIVE", "sample_unanswered")
    assert result.samples_completed == 1
    assert result.detail.startswith("normal-regression: ")


def test_unexpected_error_during_verification_is_inconclusive_not_pass():
    def responder(path, query, elapsed):
        if elapsed >= 10:
            raise RuntimeError("예상하지 못한 오류")
        return correct_response(path, query, elapsed)

    rig = make_rig(responder)
    result = verify(**{**rig.kwargs, "http": FakeHttp(rig.clock, responder)})
    assert (result.verdict, result.reason, result.detail) == (
        "INCONCLUSIVE",
        "verifier_error",
        "RuntimeError",
    )
    assert result.samples_completed == 1
    assert result.observation_complete is False


def test_slow_final_sample_is_observed_before_pass():
    """t=30 표본이 60초를 넘겨도 PASS 전에 관찰 상태를 다시 본다(검증에서 발견)."""
    rig = make_rig()

    def slow(path, query, elapsed):
        if elapsed >= 30 and query["lot_id"] == HOLDOUT_LOT:
            rig.docker.streams[TARGET].push(json.dumps(S1_ERROR))  # 느린 응답 동안 재발
            rig.clock.advance(33)
        return correct_response(path, query, elapsed)

    rig.http.responder = slow
    _, result = drive(rig)
    assert (result.verdict, result.reason) == ("FAIL", "error_recurred")
    assert result.observation_complete is False


def test_stream_end_during_slow_final_sample_is_inconclusive():
    rig = make_rig()

    def slow(path, query, elapsed):
        if elapsed >= 30 and query["lot_id"] == HOLDOUT_LOT:
            rig.docker.streams[TARGET].end()
            rig.clock.advance(33)
        return correct_response(path, query, elapsed)

    rig.http.responder = slow
    _, result = drive(rig)
    assert (result.verdict, result.reason) == ("INCONCLUSIVE", "observer_gap")


def test_observed_mismatch_wins_over_unanswered_case_in_same_sample():
    """한 표본에서 틀린 응답을 봤으면 다른 case가 무응답이어도 FAIL이다(검증에서 발견)."""

    def responder(path, query, elapsed):
        if query["lot_id"] == BUG_LOT:
            return HttpResponse(200, as_json({**EXPECTED[BUG_LOT], "total_defects": 0}))
        if query["lot_id"] == NORMAL_LOT:
            raise RequestTimeout("요청 timeout")
        return correct_response(path, query, elapsed)

    t, result = drive(make_rig(responder))
    assert t == 0
    assert (result.verdict, result.reason) == ("FAIL", "content_mismatch")
    assert {f["case_id"] for f in result.failed_assertions} == {"missing-inspector"}
    assert "normal-regression" in result.detail


@pytest.mark.parametrize(
    "change",
    [
        {"cases": []},
        {"observation": {**SPEC_08_CONTRACT["observation"], "samples": 3}},
        {"observation": {**SPEC_08_CONTRACT["observation"], "recurrence_window_seconds": 59}},
    ],
    ids=["no_cases", "fewer_samples", "shorter_window"],
)
def test_contract_cannot_weaken_core_observation(change):
    with pytest.raises(ConfigError):
        validate_model(Contract, {**SPEC_08_CONTRACT, **change}, "contract")


def test_cli_log_stream_survives_invalid_utf8():
    """비신뢰 MES가 잘못된 UTF-8을 써도 관찰 스트림이 계속 읽는다(검증에서 발견)."""
    payload = b'first\n\xff\xfe broken\n{"after": 1}\n'
    code = (
        f"import sys, time; sys.stdout.buffer.write({payload!r}); sys.stdout.flush(); time.sleep(5)"
    )
    stream = _CliLogStream([sys.executable, "-c", code])
    try:
        lines: list[str] = []
        deadline = time.monotonic() + 5
        while len(lines) < 3 and time.monotonic() < deadline:
            lines += stream.read_lines()
            time.sleep(0.05)
        assert lines[0] == "first" and lines[2] == '{"after": 1}'
        assert "\ufffd" in lines[1]
        assert stream.alive()
    finally:
        stream.close()
    assert not stream.alive()


def test_cli_log_stream_with_dead_reader_is_not_alive():
    stream = _CliLogStream([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        stream._reader = dead
        assert stream.alive() is False
    finally:
        stream.close()


def test_cli_docker_decodes_invalid_bytes_without_crashing():
    result = CliDocker(binary=sys.executable)._run(
        ["-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\n')"]
    )
    assert result.returncode == 0 and result.stdout.startswith("ok ")


def test_observer_requires_existing_target():
    observer = ContainerObserver(FakeDocker(), "missing")
    with pytest.raises(ObserverError):
        observer.start()
    with pytest.raises(ObserverError):
        observer.poll()


def test_observer_follows_logs_from_t0_and_stop_is_not_a_gap():
    docker = FakeDocker()
    docker.run(["--name", TARGET], "mes:candidate")
    observer = ContainerObserver(docker, TARGET, harness.S1_SIGNATURE)
    identity = observer.start(since=SINCE)
    assert identity == {"container_id": f"{1:064x}", "image_id": "mes:candidate"}
    assert ("logs_follow", TARGET, SINCE) in docker.calls
    assert observer.poll().stream_gap is False
    observer.stop()
    status = observer.poll()
    assert status.stream_gap is False
    assert docker.streams[TARGET].closed is True


def test_recurrence_signature_needs_all_fields():
    signature = RecurrenceSignature("KeyError", "app.defects:summarize", "/defects/summary")
    assert signature.matches(S1_ERROR)
    for key in ("error_type", "top_frame", "path"):
        event = dict(S1_ERROR)
        del event[key]
        assert not signature.matches(event)


# ── prober HTTP ──────────────────────────────────────────────


def probe_reply(payload: dict, returncode: int = 0):
    return lambda name, command: CommandResult(returncode, json.dumps(payload) + "\n", "")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_prober_http_runs_fixed_probe_and_decodes_body():
    body = as_json(EXPECTED[BUG_LOT])
    docker = FakeDocker(probe_reply({"status": 200, "body": b64(body)}))
    http = ProberHttp(docker, "prober-x", "http://mes-x:8000/")
    assert http.get("/defects/summary", {"lot_id": BUG_LOT}) == HttpResponse(200, body)
    _, name, command = docker.calls[-1]
    assert name == "prober-x"
    assert command == [
        "python",
        "-c",
        verifier.PROBE_CODE,
        "http://mes-x:8000/defects/summary?lot_id=L3-0927-118",
        "5.0",
    ]


def test_prober_http_encodes_query_values():
    docker = FakeDocker(probe_reply({"status": 400, "body": b64(b"{}")}))
    http = ProberHttp(docker, "prober-x", "http://mes-x:8000")
    assert http.get("/defects/summary", {"lot_id": "A B&x=1"}).status == 400
    assert docker.calls[-1][2][3] == "http://mes-x:8000/defects/summary?lot_id=A+B%26x%3D1"


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (probe_reply({"error": "timeout"}), RequestTimeout),
        (probe_reply({"error": "ConnectionRefusedError"}), RequestFailed),
        (lambda name, command: CommandResult(124, "", "timeout"), RequestTimeout),
        (lambda name, command: CommandResult(1, "not json\n", ""), RequestFailed),
        (lambda name, command: CommandResult(137, "", ""), RequestFailed),
    ],
    ids=["probe_timeout", "connection_refused", "exec_timeout", "garbage", "no_output"],
)
def test_prober_http_errors(reply, error):
    http = ProberHttp(FakeDocker(reply), "prober-x", "http://mes-x:8000")
    with pytest.raises(error):
        http.get("/defects/summary", {"lot_id": BUG_LOT})


class _ProbeTarget(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(2)
        status, body = (200, "검사자 미지정".encode()) if self.path == "/ok" else (404, b"{}")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # timeout 시험에서 끊긴 연결에 늦게 쓰는 오류는 무시한다


@pytest.fixture
def probe_target():
    server = _QuietServer(("127.0.0.1", 0), _ProbeTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def run_probe(url: str, timeout: str = "2") -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", verifier.PROBE_CODE, url, timeout],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_probe_code_reports_raw_status_and_body(probe_target):
    ok = run_probe(probe_target + "/ok")
    assert ok["status"] == 200
    assert base64.b64decode(ok["body"]).decode() == "검사자 미지정"
    assert run_probe(probe_target + "/missing")["status"] == 404
    assert run_probe(probe_target + "/slow", timeout="0.5") == {"error": "timeout"}


def test_probe_code_reports_connection_failure():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert run_probe(f"http://127.0.0.1:{port}/healthz") == {"error": "ConnectionRefusedError"}


# ── S1b 거짓 정상 구현 ────────────────────────────────────────


def test_s1b_summary_hides_missing_inspector_as_zero_with_200():
    assert wrong_200_defects.summarize(BUG_LOT, load_lot(BUG_LOT)) == {
        "lot_id": BUG_LOT,
        "total_defects": 0,
        "by_inspector": {"I-01": 3, "I-02": 2},
    }
    assert wrong_200_defects.summarize(NORMAL_LOT, load_lot(NORMAL_LOT)) == EXPECTED[NORMAL_LOT]


def test_verifier_rejects_s1b_responses():
    def responder(path, query, elapsed):
        lot_id = query["lot_id"]
        return HttpResponse(200, as_json(wrong_200_defects.summarize(lot_id, load_lot(lot_id))))

    t, result = drive(make_rig(responder))
    assert t == 0
    assert (result.verdict, result.reason) == ("FAIL", "content_mismatch")
    assert {f["case_id"] for f in result.failed_assertions} == {
        "missing-inspector",
        "variant-held-out",
    }
    assert any(SPEC_08_FAILED_EXAMPLE.items() <= f.items() for f in result.failed_assertions)


def test_s1b_image_recipe_overrides_only_summary_module():
    text = (NEGATIVE_DIR / "s1b.Dockerfile").read_text(encoding="utf-8")
    instructions = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert instructions == [
        "ARG MES_IMAGE=linemedic-mes:base",
        "FROM ${MES_IMAGE}",
        "COPY wrong_200_defects.py /srv/app/defects.py",
    ]


def test_s1b_path_is_reachable_only_from_trusted_harness():
    """제품 broker·/tools·MES 시드에서 S1b를 배포할 경로가 없어야 한다(spec 08 §7)."""
    users = []
    for path in sorted((REPO_ROOT / "linemedic").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[:3] == ("linemedic", "factory_sim", "negative") or rel.parts[1] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if "factory_sim.negative" in text or "wrong_200" in text:
            users.append(rel.as_posix())
    assert users == ["linemedic/cli.py"]
    for path in (REPO_ROOT / "l3-mes-api-seed").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".md", ".toml"}:
            text = path.read_text(encoding="utf-8").lower()
            assert "wrong_200" not in text and "s1b" not in text, path


# ── S1b trusted harness (FakeDocker + 실제 제어 DB) ──────────


def mes_exec_handler(data_dir: Path, summarize, prober: str):
    """prober exec를 흉내 낸다. URL을 해석해 run 데이터 디렉터리의 로트로 집계한다."""

    def handler(name, command):
        assert name == prober, "HTTP 표본은 신뢰 prober에서만 보낸다"
        url = urlparse(command[3])
        if url.path == "/healthz":
            return probe_reply({"status": 200, "body": b64(b'{"status":"ok"}')})(name, command)
        lot_id = parse_qs(url.query)["lot_id"][0]
        document = json.loads((data_dir / "lots" / f"{lot_id}.json").read_text(encoding="utf-8"))
        body = as_json(summarize(lot_id, document["records"]))
        return probe_reply({"status": 200, "body": b64(body)})(name, command)

    return handler


def harness_docker(runs_dir: Path, summarize) -> FakeDocker:
    names = harness.resource_names(RUN_ID)
    data_dir = runs_dir / RUN_ID / "verify-negative" / "mes-data"
    docker = FakeDocker(mes_exec_handler(data_dir, summarize, names["prober"]))
    docker.images.update({scenarios.DEFAULT_MES_IMAGE: MES_ID, harness.PROBER_IMAGE: PROBER_ID})
    return docker


@pytest.fixture
def run_store(store, conn):
    """활성 run이 있는 제어 DB(`make run-new`에 해당)."""
    insert_run(conn, RUN_ID)
    return store


def run_harness(runs_dir, store, clock, docker, **kwargs):
    return harness.run_verify_negative(
        RUN_ID, runs_dir, store=store, docker=docker, clock=clock, sleep=clock.sleep, **kwargs
    )


def incident_audit(conn, incident_id):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT actor, event_type FROM audit_events WHERE incident_id = ? ORDER BY seq",
            (incident_id,),
        )
    ]


def test_harness_runs_s1b_and_records_fail(tmp_runs_dir, fake_clock, run_store, conn):
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    outcome = run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    result = outcome["result"]
    names = harness.resource_names(RUN_ID)
    data_dir = tmp_runs_dir / RUN_ID / "verify-negative" / "mes-data"

    assert (result["verdict"], result["reason"]) == ("FAIL", "content_mismatch")
    assert result["origin"] == "human_injected_negative"
    assert (result["samples_completed"], result["samples_required"]) == (1, 4)
    assert result["observation_complete"] is False
    assert result["resolved_written"] is False
    assert any(SPEC_08_FAILED_EXAMPLE.items() <= f.items() for f in result["failed_assertions"])
    assert harness.expected_outcome(outcome)

    result_path = Path(outcome["result_path"])
    assert result_path == (
        tmp_runs_dir / RUN_ID / "verifications" / f"{result['verification_id']}.json"
    )
    assert json.loads(result_path.read_text(encoding="utf-8")) == result
    assert result["target"]["image_id"] == outcome["s1b_image_id"]
    assert (outcome["mes_image_id"], outcome["prober_image_id"]) == (MES_ID, PROBER_ID)

    # 제어 DB: 시험 사건은 verifier 주체로 ESCALATED, verification은 FAIL·human_injected_negative
    assert outcome["incident_status"] == "ESCALATED"
    incident = row(conn, "incidents", outcome["incident_id"])
    assert (incident["status"], incident["reason_code"], incident["version"]) == (
        "ESCALATED",
        "VERIFICATION_FAILED",
        1,
    )
    stored = row(conn, "verifications", result["verification_id"])
    assert (stored["origin"], stored["verdict"], stored["incident_id"]) == (
        "human_injected_negative",
        "FAIL",
        outcome["incident_id"],
    )
    assert json.loads(stored["result_json"]) == result
    assert outcome["notification_id"] is None and count(conn, "notifications") == 0
    assert incident_audit(conn, outcome["incident_id"]) == [
        ("trusted_harness", "DEMO_STATE_PREPARED"),
        ("verifier", "INCIDENT_TRANSITION"),
        ("verifier", "VERIFICATION_RECORDED"),
    ]

    # S1b는 신뢰 레시피 MES 이미지 위에 한 파일만 덮어 만든다.
    builds = [call for call in docker.calls if call[0] == "build"]
    assert builds == [
        (
            "build",
            str(NEGATIVE_DIR / "s1b.Dockerfile"),
            harness.S1B_IMAGE,
            {"MES_IMAGE": scenarios.DEFAULT_MES_IMAGE},
        )
    ]
    # MES·prober 모두 이 run의 internal network에만 붙고 host port를 열지 않는다.
    runs = [call for call in docker.calls if call[0] == "run"]
    assert runs[0][1] == scenarios.mes_container_options(
        names["mes"], RUN_ID, names["network"], data_dir
    )
    assert runs[0][2] == outcome["s1b_image_id"]
    assert runs[1][1] == harness.prober_options(names["prober"], RUN_ID, names["network"])
    assert (runs[1][2], runs[1][3]) == (harness.PROBER_IMAGE, ["sleep", "infinity"])
    for call in runs:
        assert not {"-p", "--publish", "--privileged", "--network=host"} & set(call[1])
    assert ("network_create", names["network"], {"linemedic.run_id": RUN_ID}) in docker.calls
    # t0: 관찰 시작 시각부터 로그를 읽는다.
    assert ("logs_follow", names["mes"], result["started_at"]) in docker.calls
    # 정리는 이 run의 정확한 이름만
    assert [call[1] for call in docker.calls if call[0] == "stop"] == [
        names["mes"],
        names["prober"],
    ]
    assert [call[1] for call in docker.calls if call[0] == "network_remove"] == [names["network"]]
    assert docker.containers == {} and docker.networks == set()
    # MES 데이터에는 입력만 있고 기대값은 없다.
    documents = sorted(data_dir.rglob("*.json"))
    assert [p.name for p in documents] == sorted(
        f"{lot}.json" for lot in (BUG_LOT, NORMAL_LOT, HOLDOUT_LOT)
    )
    for path in documents:
        assert set(json.loads(path.read_text(encoding="utf-8"))) == {"lot_id", "records"}


def test_harness_passes_correct_service_so_expected_outcome_is_false(
    tmp_runs_dir, fake_clock, run_store, conn
):
    """S1b가 우연히 정상이면 harness가 기대 결과가 아니라고 알려야 한다."""
    docker = harness_docker(tmp_runs_dir, reference_summary)
    outcome = run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    assert (outcome["result"]["verdict"], outcome["result"]["reason"]) == (
        "PASS",
        "all_checks_passed",
    )
    assert outcome["result"]["resolved_written"] is True
    assert row(conn, "incidents", outcome["incident_id"])["status"] == "RESOLVED"
    assert not harness.expected_outcome(outcome)
    assert docker.containers == {} and docker.networks == set()


def test_harness_records_inconclusive_when_verifier_errors(
    tmp_runs_dir, fake_clock, run_store, conn
):
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    healthy = docker.exec_handler

    def broken(name, command):
        if "/defects/summary" in command[3]:
            raise RuntimeError("prober 내부 오류")
        return healthy(name, command)

    docker.exec_handler = broken
    outcome = run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    result = outcome["result"]
    assert (result["verdict"], result["reason"], result["detail"]) == (
        "INCONCLUSIVE",
        "verifier_error",
        "RuntimeError",
    )
    incident = row(conn, "incidents", outcome["incident_id"])
    assert (incident["status"], incident["reason_code"]) == (
        "ESCALATED",
        "OBSERVATION_INCONCLUSIVE",
    )
    assert not harness.expected_outcome(outcome)
    assert docker.containers == {} and docker.networks == set()


def test_harness_reports_unfinished_test_incident(tmp_runs_dir, fake_clock, run_store, conn):
    """중단된 이전 실행이 남긴 VERIFYING 시험 사건이 있으면 추적 가능한 오류로 알린다."""
    with run_store.tx() as tx:
        stale = prepare_verifying_incident(
            tx, RUN_ID, purpose=harness.DEMO_PURPOSE, fingerprint=harness.DEMO_FINGERPRINT
        )
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    with pytest.raises(harness.HarnessError, match=stale):
        run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    assert not [call for call in docker.calls if call[0] in {"build", "run"}]


def test_harness_records_inconclusive_when_interrupted(tmp_runs_dir, fake_clock, run_store, conn):
    """Ctrl-C로 중단돼도 시험 사건을 VERIFYING에 남기지 않고 INCONCLUSIVE로 기록한다."""
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    healthy = docker.exec_handler

    def interrupted(name, command):
        if "/defects/summary" in command[3]:
            raise KeyboardInterrupt
        return healthy(name, command)

    docker.exec_handler = interrupted
    with pytest.raises(KeyboardInterrupt):
        run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    [incident] = conn.execute("SELECT status, reason_code FROM incidents").fetchall()
    assert tuple(incident) == ("ESCALATED", "OBSERVATION_INCONCLUSIVE")
    [stored] = conn.execute("SELECT verdict, result_json FROM verifications").fetchall()
    assert stored["verdict"] == "INCONCLUSIVE"
    assert json.loads(stored["result_json"])["detail"] == "interrupted:KeyboardInterrupt"
    assert docker.containers == {} and docker.networks == set()


def test_harness_requires_active_run_in_db(tmp_runs_dir, fake_clock, store, conn):
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    with pytest.raises(harness.HarnessError, match="make run-new"):
        run_harness(tmp_runs_dir, store, fake_clock, docker)
    insert_run(conn, RUN_ID, active=0)
    with pytest.raises(harness.HarnessError, match="활성 run"):
        run_harness(tmp_runs_dir, store, fake_clock, docker)
    assert not [call for call in docker.calls if call[0] in {"build", "run"}]


def test_harness_requires_mes_image(tmp_runs_dir, fake_clock, run_store):
    docker = FakeDocker()
    with pytest.raises(harness.HarnessError, match="make mes-image"):
        run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    assert not [call for call in docker.calls if call[0] in {"build", "run", "network_create"}]


def test_harness_checks_recorded_mes_image_id(tmp_runs_dir, fake_clock, run_store):
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    with pytest.raises(harness.HarnessError, match="MES_BASE_IMAGE_ID"):
        run_harness(
            tmp_runs_dir, run_store, fake_clock, docker, expected_mes_image_id="sha256:" + "c" * 64
        )
    assert not [call for call in docker.calls if call[0] == "build"]


def test_harness_rejects_invalid_run_id(tmp_runs_dir, fake_clock, run_store):
    with pytest.raises(harness.HarnessError, match="run_id"):
        harness.run_verify_negative(
            "../x", tmp_runs_dir, store=run_store, docker=FakeDocker(), clock=fake_clock
        )


def test_harness_cleans_up_when_service_never_ready(tmp_runs_dir, fake_clock, run_store, conn):
    docker = harness_docker(tmp_runs_dir, wrong_200_defects.summarize)
    docker.exec_handler = probe_reply({"error": "ConnectionRefusedError"})
    with pytest.raises(harness.HarnessError, match="준비되지 않았다"):
        run_harness(tmp_runs_dir, run_store, fake_clock, docker)
    assert docker.containers == {} and docker.networks == set()
    assert not (tmp_runs_dir / RUN_ID / "verifications").exists()
    assert count(conn, "incidents") == 0  # 사건은 MES가 준비된 뒤에만 만든다


# ── CLI·Makefile ─────────────────────────────────────────────


@pytest.fixture
def negative_env(tmp_path, monkeypatch, fake_clock):
    for name in ("RUNS_DIR", "MES_BASE_IMAGE_ID"):
        monkeypatch.delenv(name, raising=False)
    runs_dir = tmp_path / "cli-runs"
    runs_dir.mkdir()
    Store(runs_dir / "linemedic.db", fake_clock).migrate()
    env_file = tmp_path / ".env"
    env_file.write_text(f"RUNS_DIR={runs_dir}\nMES_BASE_IMAGE_ID={MES_ID}\n", encoding="utf-8")
    return env_file


def cli_args(env_file: Path) -> list[str]:
    return [
        "verify-negative",
        "--run-id",
        RUN_ID,
        "--env-file",
        str(env_file),
        "--config",
        str(REPO_ROOT / "config" / "linemedic.toml"),
    ]


@pytest.mark.parametrize(
    ("verdict", "reason", "incident_status", "exit_code"),
    [
        ("FAIL", "content_mismatch", "ESCALATED", 0),
        ("PASS", "all_checks_passed", "RESOLVED", 1),
        ("FAIL", "error_recurred", "ESCALATED", 1),
        ("FAIL", "content_mismatch", "VERIFYING", 1),
    ],
)
def test_cli_verify_negative_exit_code(
    monkeypatch, negative_env, verdict, reason, incident_status, exit_code
):
    seen = {}

    def fake_run(run_id, runs_dir, *, store, mes_image, expected_mes_image_id, route_id):
        seen.update(
            run_id=run_id,
            runs_dir=runs_dir,
            db=store.path,
            mes_image=mes_image,
            expected=expected_mes_image_id,
            route_id=route_id,
        )
        return {
            "result": {
                "verdict": verdict,
                "reason": reason,
                "resolved_written": verdict == "PASS",
                "observation_complete": verdict == "PASS",
            },
            "incident_status": incident_status,
        }

    monkeypatch.setattr(harness, "run_verify_negative", fake_run)
    assert cli.main(cli_args(negative_env)) == exit_code
    runs_dir = negative_env.parent / "cli-runs"
    assert seen == {
        "run_id": RUN_ID,
        "runs_dir": runs_dir,
        "db": runs_dir / "linemedic.db",
        "mes_image": scenarios.DEFAULT_MES_IMAGE,
        "expected": MES_ID,
        "route_id": "github-issue-primary",
    }


def test_cli_verify_negative_reports_harness_error(monkeypatch, negative_env, capsys):
    def fake_run(*args, **kwargs):
        raise harness.HarnessError("MES 이미지가 없다")

    monkeypatch.setattr(harness, "run_verify_negative", fake_run)
    assert cli.main(cli_args(negative_env)) == 2
    assert "MES 이미지가 없다" in capsys.readouterr().err


def test_cli_verify_negative_requires_control_db(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("RUNS_DIR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"RUNS_DIR={tmp_path / 'empty'}\n", encoding="utf-8")
    assert cli.main(cli_args(env_file)) == 2
    assert "make run-new" in capsys.readouterr().err


def test_make_verify_negative_requires_run_id():
    env = {key: value for key, value in os.environ.items() if key != "RUN_ID"}
    proc = subprocess.run(
        ["make", "--no-print-directory", "verify-negative"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert "make verify-negative RUN_ID=" in proc.stdout
