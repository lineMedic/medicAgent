"""W07 단위 테스트: 로그 줄 파싱·오류 signature·problem_fingerprint (spec 15 §3.1, D72).

request_id·lot_id·timestamp·줄 번호만 다른 로그는 같은 fingerprint,
error_field가 다르면 다른 fingerprint다.
"""

import hashlib
import json

import pytest

from linemedic.common.canonical_json import canonical_dumps
from linemedic.control_plane.detector import (
    FINGERPRINT_VERSION,
    Signature,
    normalize_endpoint,
    normalize_top_frame,
    parse_line,
    problem_fingerprint,
    signature,
)

SERVICE = "mes-api"
# W04 MES가 로트 118에서 남기는 오류 로그와 같은 모양(D57·D66)
S1_EVENT = {
    "ts": "2026-09-27T01:00:00.000000Z",
    "level": "ERROR",
    "service": "mes-api",
    "event": "request_failed",
    "request_id": "4f0c8a1d2b3e4f5a6b7c8d9e0f1a2b3c",
    "lot_id": "L3-0927-118",
    "path": "/defects/summary",
    "status": 500,
    "error_type": "KeyError",
    "error_field": "inspector_id",
    "top_frame": "app.defects:summarize",
    "top_frame_line": 7,
    "stack": ["app.main:defects_summary:60", "app.defects:summarize:7"],
}


def fp(event: dict, service: str = SERVICE) -> str:
    sig = signature(event, service)
    assert sig is not None
    return problem_fingerprint(sig)


def test_s1_log_signature_fields():
    assert signature(S1_EVENT, SERVICE) == Signature(
        service="mes-api",
        error_type="KeyError:inspector_id",
        top_frame="app.defects:summarize",
        endpoint="/defects/summary",
    )


def test_fingerprint_is_sha256_of_canonical_parts():
    expected = hashlib.sha256(
        canonical_dumps(
            [
                FINGERPRINT_VERSION,
                SERVICE,
                "KeyError:inspector_id",
                "app.defects:summarize",
                "/defects/summary",
            ]
        ).encode("utf-8")
    ).hexdigest()
    assert fp(S1_EVENT) == expected
    assert FINGERPRINT_VERSION == "fp-v1"


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "ffffffffffffffffffffffffffffffff"},
        {"lot_id": "L3-0927-777"},
        {"ts": "2026-09-28T09:09:09.000000Z"},
        {"top_frame_line": 99, "stack": ["app.defects:summarize:99"]},
        {"path": "/defects/summary?lot_id=L3-0927-118"},
        {"path": "/defects/summary/"},
        {"top_frame": "app.defects:summarize:7"},
        {"service": "other-service"},  # 로그 줄의 service는 믿지 않는다
        {"status": 503, "level": "WARNING"},
    ],
)
def test_unstable_fields_do_not_change_fingerprint(changes):
    assert fp({**S1_EVENT, **changes}) == fp(S1_EVENT)


@pytest.mark.parametrize(
    "changes",
    [
        {"error_field": "defect_id"},
        {"error_field": None},
        {"error_type": "ValueError"},
        {"top_frame": "app.data:load_lot"},
        {"path": "/defects/export"},
    ],
)
def test_distinguishing_fields_change_fingerprint(changes):
    assert fp({**S1_EVENT, **changes}) != fp(S1_EVENT)


def test_service_and_version_change_fingerprint_but_run_does_not_exist_in_key():
    sig = signature(S1_EVENT, SERVICE)
    assert problem_fingerprint(sig) != problem_fingerprint(sig, version="fp-v2")
    assert fp(S1_EVENT, "vision-inspection") != fp(S1_EVENT)
    # signature에는 run·요청 식별자가 없다. 격리는 routing_scope로 한다.
    assert set(Signature.__dataclass_fields__) == {"service", "error_type", "top_frame", "endpoint"}


@pytest.mark.parametrize(
    "event",
    [
        {"level": "INFO", "event": "request_completed", "path": "/defects/summary", "status": 200},
        {**S1_EVENT, "error_type": ""},
        {**S1_EVENT, "top_frame": None},
        {**S1_EVENT, "path": 123},
        {k: v for k, v in S1_EVENT.items() if k != "path"},
    ],
)
def test_non_error_or_incomplete_events_have_no_signature(event):
    assert signature(event, SERVICE) is None


@pytest.mark.parametrize(
    "line",
    [
        "Traceback (most recent call last):",
        "[1, 2]",
        '{"a": 1, "a": 2}',
        '{"a": NaN}',
        '"just a string"',
        json.dumps({"x": "y" * 70_000}),
    ],
)
def test_parse_line_rejects_non_objects_and_unsafe_json(line):
    assert parse_line(line) is None


def test_parse_line_accepts_json_object():
    assert parse_line(json.dumps(S1_EVENT)) == S1_EVENT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("app.defects:summarize", "app.defects:summarize"),
        ("app.defects:summarize:123", "app.defects:summarize"),
        ("module", "module"),
    ],
)
def test_normalize_top_frame(raw, expected):
    assert normalize_top_frame(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/defects/summary?lot_id=x", "/defects/summary"),
        ("/defects/summary#frag", "/defects/summary"),
        ("/defects/summary/", "/defects/summary"),
        ("/", "/"),
    ],
)
def test_normalize_endpoint(raw, expected):
    assert normalize_endpoint(raw) == expected


def test_long_values_are_capped_in_signature():
    sig = signature({**S1_EVENT, "error_type": "E" * 5000}, SERVICE)
    assert sig is not None and len(sig.error_type) <= 200 + len(":inspector_id")
