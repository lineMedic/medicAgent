"""W06 통합 테스트: 변경 요청 멱등성 (T-IDEM-01·02, docs/03 §8).

실제 SQLite와 app을 쓴다. 같은 키·같은 본문은 저장된 응답을 그대로 돌려주고 다시 실행하지 않는다.
같은 키·다른 본문은 409 `IDEMPOTENCY_CONFLICT`,
처리 중·결과 불명(RECEIVED/UNKNOWN)은 409 `STATE_CONFLICT`다.
"""

import json

import pytest

from linemedic.control_plane import idempotency
from linemedic.control_plane.idempotency import Outcome
from linemedic.control_plane.ops_api import EscalateRequest
from linemedic.control_plane.store import StoreError
from linemedic.tests.helpers.api import RUN, escalate, escalate_body, make_api, seed_pr_opened
from linemedic.tests.helpers.db_rows import count, insert_run, row

SCOPE = {
    "principal_scope": "operator:host-operator",
    "method": "POST",
    "path": "/ops/incidents/INC-000000000001/escalate",
    "run_id": RUN,
    "key": "key-1",
}


@pytest.fixture
def api(store, conn):
    return make_api(store, conn)


def effects(api) -> tuple:
    return (
        count(api.conn, "audit_events"),
        count(api.conn, "notifications"),
        count(api.conn, "api_requests"),
    )


def test_t_idem_01_same_key_same_body_replays_without_rerun(api):
    seeded = seed_pr_opened(api)
    first = escalate(api, seeded["incident"], escalate_body())
    assert first.status_code == 200
    after_first = effects(api)
    second = escalate(api, seeded["incident"], escalate_body())
    assert second.status_code == 200
    assert second.json() == first.json()  # 같은 request_id를 포함한 저장 응답
    assert effects(api) == after_first == (2, 1, 1)
    assert row(api.conn, "incidents", seeded["incident"])["version"] == 1
    stored = api.conn.execute("SELECT status, response_json FROM api_requests").fetchone()
    assert stored["status"] == "COMPLETED"
    assert json.loads(stored["response_json"])["body"] == first.json()


def test_same_body_with_other_key_order_and_spacing_is_same_request(api):
    seeded = seed_pr_opened(api)
    first = escalate(api, seeded["incident"], escalate_body())
    reordered = json.dumps(dict(reversed(list(escalate_body().items()))), indent=4)
    second = api.client.post(
        f"/ops/incidents/{seeded['incident']}/escalate",
        content=reordered.encode(),
        headers={
            **api.operator,
            "Idempotency-Key": "escalate-1",
            "Content-Type": "application/json",
        },
    )
    assert second.status_code == 200 and second.json() == first.json()
    assert count(api.conn, "notifications") == 1


def test_t_idem_02_same_key_different_body_is_409(api):
    seeded = seed_pr_opened(api)
    assert escalate(api, seeded["incident"], escalate_body()).status_code == 200
    before = effects(api)
    response = escalate(api, seeded["incident"], escalate_body(note="다른 사유로 바꾼 요청"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert effects(api) == before
    assert row(api.conn, "incidents", seeded["incident"])["version"] == 1


def test_new_key_after_completion_meets_current_state(api):
    seeded = seed_pr_opened(api)
    assert escalate(api, seeded["incident"], escalate_body()).status_code == 200
    response = escalate(api, seeded["incident"], escalate_body(), key="escalate-2")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "STATE_CONFLICT"
    assert error["details"] == {"current_status": "ESCALATED", "current_version": 1}
    assert count(api.conn, "notifications") == 1


@pytest.mark.parametrize("status", ["RECEIVED", "UNKNOWN"])
def test_in_flight_or_unknown_request_is_not_rerun(api, status):
    seeded = seed_pr_opened(api)
    body = EscalateRequest.model_validate(escalate_body())
    api.conn.execute(
        "INSERT INTO api_requests(principal_scope, method, path, run_id, idempotency_key,"
        " body_sha256, status, response_json, created_at)"
        " VALUES (?, 'POST', ?, ?, ?, ?, ?, NULL, ?)",
        (
            "operator:host-operator",
            f"/ops/incidents/{seeded['incident']}/escalate",
            RUN,
            "escalate-1",
            idempotency.body_sha256(body.model_dump(mode="json")),
            status,
            "2026-09-27T00:00:00.000000Z",
        ),
    )
    response = escalate(api, seeded["incident"], escalate_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
    assert row(api.conn, "incidents", seeded["incident"])["status"] == "PR_OPENED"
    assert count(api.conn, "notifications") == 0


def test_failed_request_leaves_no_request_record(api):
    seeded = seed_pr_opened(api)
    stale = escalate(api, seeded["incident"], escalate_body(expected_incident_version=5))
    assert stale.status_code == 409
    assert effects(api) == (0, 0, 0)
    fixed = escalate(api, seeded["incident"], escalate_body())
    assert fixed.status_code == 200


# ── 모듈 단위 ────────────────────────────────────────────────


@pytest.fixture
def run_row(conn):
    insert_run(conn, RUN)


def test_begin_complete_replay_cycle(store, run_row):
    with store.tx() as tx:
        assert idempotency.begin(tx, **SCOPE, body_sha256="a" * 64).outcome is Outcome.NEW
        idempotency.complete(tx, **SCOPE, status_code=200, body={"data": 1})
    with store.tx() as tx:
        replay = idempotency.begin(tx, **SCOPE, body_sha256="a" * 64)
        assert replay.outcome is Outcome.REPLAY
        assert replay.response == {"status_code": 200, "body": {"data": 1}}
        assert idempotency.begin(tx, **SCOPE, body_sha256="b" * 64).outcome is Outcome.CONFLICT
        other_scope = {**SCOPE, "principal_scope": "operator:other"}
        assert idempotency.begin(tx, **other_scope, body_sha256="b" * 64).outcome is Outcome.NEW


def test_received_request_is_in_flight_and_restart_marks_unknown(store, conn, run_row):
    with store.tx() as tx:
        idempotency.begin(tx, **SCOPE, body_sha256="a" * 64)
    with store.tx() as tx:
        assert idempotency.begin(tx, **SCOPE, body_sha256="a" * 64).outcome is Outcome.IN_FLIGHT
        assert idempotency.mark_unknown(tx) == 1
    with store.tx() as tx:
        assert idempotency.begin(tx, **SCOPE, body_sha256="a" * 64).outcome is Outcome.IN_FLIGHT
        with pytest.raises(StoreError):
            idempotency.complete(tx, **SCOPE, status_code=200, body={})
    assert conn.execute("SELECT status FROM api_requests").fetchone()[0] == "UNKNOWN"


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "-leading", "키"])
def test_invalid_idempotency_key(store, run_row, key):
    assert not idempotency.valid_key(key)
    with pytest.raises(ValueError), store.tx() as tx:
        idempotency.begin(tx, **{**SCOPE, "key": key}, body_sha256="a" * 64)


def test_body_hash_is_canonical():
    assert idempotency.body_sha256({"b": 1, "a": "x"}) == idempotency.body_sha256(
        {"a": "x", "b": 1}
    )
    assert idempotency.body_sha256({"a": 1}) != idempotency.body_sha256({"a": 2})
