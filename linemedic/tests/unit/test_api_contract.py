"""W06 계약 테스트: 응답·오류 외피, 엄격한 body 처리, 크기 제한, 오래된 version, ops endpoint.

body 처리 순서: 크기 제한 → 엄격한 JSON(중복 key·NaN 거부) → pydantic strict·extra=forbid.
"""

import json
import re
import sqlite3

import pytest

from linemedic.control_plane.store import Store
from linemedic.tests.helpers.api import (
    OPERATOR_TOKEN,
    RUN,
    escalate,
    escalate_body,
    make_api,
    seed_pr_opened,
)
from linemedic.tests.helpers.db_rows import count, insert_incident, row

REQUEST_ID = re.compile(r"^REQ-[0-9A-F]{12}$")


@pytest.fixture
def api(store, conn):
    return make_api(store, conn)


def post_raw(api, incident, content, headers=None):
    base = {**api.operator, "Idempotency-Key": "raw-1", "Content-Type": "application/json"}
    return api.client.post(
        f"/ops/incidents/{incident}/escalate", content=content, headers={**base, **(headers or {})}
    )


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"schema_version", "request_id", "error"}
    assert body["schema_version"] == "linemedic.v4"
    assert REQUEST_ID.fullmatch(body["request_id"])
    assert set(body["error"]) == {"code", "message", "retryable", "details"}
    return body["error"]


# ── 성공 외피와 ops endpoint ──────────────────────────────────


def test_escalate_success_envelope_and_effects(api):
    seeded = seed_pr_opened(api)
    response = escalate(api, seeded["incident"], escalate_body())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "request_id", "data", "evidence_ids"}
    assert REQUEST_ID.fullmatch(body["request_id"]) and body["evidence_ids"] == []
    data = body["data"]
    assert (data["incident_id"], data["status"], data["version"]) == (
        seeded["incident"],
        "ESCALATED",
        1,
    )
    assert data["work"] == {"work_id": seeded["work"], "status": "BLOCKED", "version": 1}
    assert data["notification"]["event_type"] == "WORK_BLOCKED"
    assert data["notification"]["status"] == "PENDING"

    work = row(api.conn, "work_items", seeded["work"])
    assert (work["status"], work["reason_code"]) == ("BLOCKED", "PERMISSION_REQUIRED")
    note = api.conn.execute("SELECT * FROM notifications").fetchone()
    assert note["logical_key"] == (f"notify:{seeded['work']}:1:WORK_BLOCKED:1:github-issue-primary")
    payload = json.loads(note["payload_json"])
    assert payload["blocker_code"] == "PERMISSION_REQUIRED"
    assert payload["incident_status_before"] == "PR_OPENED"
    assert payload["requested_by"] == "operator:host-operator"
    events = [r["event_type"] for r in api.conn.execute("SELECT event_type FROM audit_events")]
    assert events == ["INCIDENT_TRANSITION", "WORK_TRANSITION"]


def test_escalate_incident_without_work_has_no_notification(api):
    seed_pr_opened(api)
    lonely = insert_incident(api.conn, RUN, "PR_OPENED")
    response = escalate(api, lonely, escalate_body())
    assert response.status_code == 200
    assert response.json()["data"]["work"] is None
    assert response.json()["data"]["notification"] is None
    assert count(api.conn, "notifications") == 0


def test_get_incident_links_work_and_audit(api):
    seeded = seed_pr_opened(api)
    escalate(api, seeded["incident"], escalate_body())
    response = api.client.get(f"/ops/incidents/{seeded['incident']}", headers=api.operator)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["incident"]["status"] == "ESCALATED"
    assert data["incident"]["details"] == {}
    assert data["work"]["status"] == "BLOCKED"
    assert data["work"]["authorization"] == {"basis": "test"}
    assert [event["event_type"] for event in data["audit_events"]] == [
        "INCIDENT_TRANSITION",
        "WORK_TRANSITION",
    ]
    assert data["audit_events"][0]["payload"]["details"]["note"] == escalate_body()["note"]
    assert data["proposals"] == data["executions"] == data["verifications"] == []


def test_operator_cannot_escalate_outside_table_and_nothing_changes(api):
    seed_pr_opened(api)
    investigating = insert_incident(api.conn, RUN, "INVESTIGATING")
    response = escalate(api, investigating, escalate_body())
    assert response.status_code == 409
    assert error_of(response)["details"] == {"current_status": "INVESTIGATING"}
    assert row(api.conn, "incidents", investigating)["status"] == "INVESTIGATING"
    assert count(api.conn, "api_requests") == 0


def test_stale_expected_version_is_state_conflict(api):
    seeded = seed_pr_opened(api)
    response = escalate(api, seeded["incident"], escalate_body(expected_incident_version=4))
    assert response.status_code == 409
    error = error_of(response)
    assert error["code"] == "STATE_CONFLICT" and error["retryable"] is False
    assert error["details"] == {"current_status": "PR_OPENED", "current_version": 0}


@pytest.mark.parametrize("target", ["INC-FFFFFFFFFFFF", "not-an-incident-id", "INC-00000000000"])
def test_unknown_incident_is_404(api, target):
    seed_pr_opened(api)
    for response in (
        api.client.get(f"/ops/incidents/{target}", headers=api.operator),
        escalate(api, target, escalate_body()),
    ):
        assert response.status_code == 404
        assert error_of(response)["code"] == "RESOURCE_NOT_FOUND"


def test_run_id_must_match_incident_run(api):
    seeded = seed_pr_opened(api)
    response = escalate(api, seeded["incident"], escalate_body(run_id="r-20260101-000000-0000"))
    assert response.status_code == 404
    assert row(api.conn, "incidents", seeded["incident"])["status"] == "PR_OPENED"


# ── 엄격한 body ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b'{"schema_version": "linemedic.v4", "schema_version": "linemedic.v4"}', "invalid_json"),
        (b'{"expected_incident_version": NaN}', "invalid_json"),
        (b"not json", "invalid_json"),
        (b"\xff\xfe", "invalid_json"),
        (b"[1, 2]", "body_must_be_object"),
    ],
)
def test_malformed_json_is_422(api, content, reason):
    seeded = seed_pr_opened(api)
    response = post_raw(api, seeded["incident"], content)
    assert response.status_code == 422
    assert error_of(response)["details"] == {"reason": reason}


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "linemedic.v2"},
        {"expected_incident_version": "0"},
        {"expected_incident_version": True},
        {"expected_incident_version": -1},
        {"reason_code": "SOMETHING_ELSE"},
        {"note": ""},
        {"note": "x" * 2001},
        {"run_id": "run-001"},
        {"confidence": 0.9},
    ],
)
def test_schema_violations_are_422(api, overrides):
    seeded = seed_pr_opened(api)
    response = escalate(api, seeded["incident"], escalate_body(**overrides))
    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_REQUEST"
    assert row(api.conn, "incidents", seeded["incident"])["version"] == 0


def test_missing_field_is_422(api):
    seeded = seed_pr_opened(api)
    body = escalate_body()
    del body["expected_incident_version"]
    response = escalate(api, seeded["incident"], body)
    assert response.status_code == 422
    assert {"loc": ["expected_incident_version"], "type": "missing"} in error_of(response)[
        "details"
    ]["errors"]


def test_validation_errors_do_not_echo_input_values(api):
    seeded = seed_pr_opened(api)
    secret = "ghp_" + "S" * 36
    response = escalate(api, seeded["incident"], escalate_body(reason_code=secret))
    assert response.status_code == 422
    assert secret not in response.text


def test_payload_too_large_by_content_length(api):
    seeded = seed_pr_opened(api)
    huge = json.dumps(escalate_body(note="x" * 200_000)).encode()
    response = post_raw(api, seeded["incident"], huge)
    assert response.status_code == 413
    assert error_of(response)["code"] == "PAYLOAD_TOO_LARGE"


def test_payload_too_large_when_streamed_without_length(api):
    seeded = seed_pr_opened(api)
    response = post_raw(api, seeded["incident"], iter([b"{" + b" " * 70_000, b" " * 70_000 + b"}"]))
    assert response.status_code == 413


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_non_json_content_type_is_422(api, content_type):
    seeded = seed_pr_opened(api)
    response = post_raw(
        api, seeded["incident"], json.dumps(escalate_body()), {"Content-Type": content_type}
    )
    assert response.status_code == 422
    assert error_of(response)["details"] == {"reason": "content_type_must_be_json"}


@pytest.mark.parametrize("key", [None, "has space", "x" * 200])
def test_post_requires_valid_idempotency_key(api, key):
    seeded = seed_pr_opened(api)
    headers = {**api.operator}
    if key is not None:
        headers["Idempotency-Key"] = key
    response = api.client.post(
        f"/ops/incidents/{seeded['incident']}/escalate", json=escalate_body(), headers=headers
    )
    assert response.status_code == 422
    assert error_of(response)["details"] == {"reason": "idempotency_key_required"}


# ── 경로·누설 ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/ops/unknown", "/docs", "/openapi.json", "/healthz"])
def test_unknown_paths_are_404_after_auth(api, path):
    assert api.client.get(path).status_code == 401
    response = api.client.get(path, headers=api.operator)
    assert response.status_code == 404
    assert error_of(response)["code"] == "RESOURCE_NOT_FOUND"


def test_wrong_method_is_404(api):
    seeded = seed_pr_opened(api)
    response = api.client.get(f"/ops/incidents/{seeded['incident']}/escalate", headers=api.operator)
    assert response.status_code == 404


def test_error_responses_do_not_contain_token(api):
    seeded = seed_pr_opened(api)
    responses = [
        escalate(api, seeded["incident"], escalate_body(expected_incident_version=9)),
        escalate(api, "INC-FFFFFFFFFFFF", escalate_body()),
        post_raw(api, seeded["incident"], b"{bad"),
    ]
    for response in responses:
        assert OPERATOR_TOKEN not in response.text


def test_secret_in_note_is_redacted_in_audit_and_outbox(api):
    seeded = seed_pr_opened(api)
    secret = "nvapi-" + "K" * 40
    response = escalate(api, seeded["incident"], escalate_body(note=f"키가 로그에 있었음 {secret}"))
    assert response.status_code == 200
    stored = [r[0] for r in api.conn.execute("SELECT payload_json FROM audit_events")]
    stored += [r[0] for r in api.conn.execute("SELECT payload_json FROM notifications")]
    assert stored and all(secret not in text for text in stored)
    assert any("[REDACTED:nvidia_key]" in text for text in stored)


def test_store_busy_is_503(store, conn, fake_clock):
    busy_store = Store(
        store.path, fake_clock, busy_timeout_ms=20, busy_retries=1, sleep=lambda s: None
    )
    api = make_api(busy_store, conn)
    seeded = seed_pr_opened(api)
    blocker = sqlite3.connect(str(store.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        response = escalate(api, seeded["incident"], escalate_body())
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert response.status_code == 503
    assert error_of(response)["code"] == "DEPENDENCY_UNAVAILABLE"
    assert row(api.conn, "incidents", seeded["incident"])["status"] == "PR_OPENED"
