"""W14 서버 측 도구 호출 예산: attempt마다 센다, 넘으면 429, 결정 확인은 제외,
범위 밖은 세지 않는다 (D87)."""

import json

from linemedic.control_plane.auth import AgentPrincipal
from linemedic.tests.helpers.api import make_api, seed_running


def calls(api, event="TOOL_CALL"):
    return [
        json.loads(row[0])
        for row in api.conn.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = ? ORDER BY seq", (event,)
        )
    ]


def test_budget_counts_calls_and_refuses_over_budget_with_429(store, conn):
    api = make_api(store, conn, tool_call_budget=3)
    principal = seed_running(api)
    agent = api.agent(principal)
    base = f"/tools/incidents/{principal.incident_id}"
    for path in (base, f"{base}/deploys", f"{base}/cases/search"):
        assert api.client.get(path, headers=agent).status_code != 429
    refused = api.client.get(base, headers=agent)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "RATE_LIMITED"
    assert [c["tool"] for c in calls(api)] == ["get_incident", "get_deploys", "search_cases"]
    assert [c["call"] for c in calls(api)] == [1, 2, 3]
    (over,) = calls(api, "TOOL_CALL_REFUSED")
    assert (over["tool"], over["used"], over["budget"]) == ("get_incident", 3, 3)
    assert over["attempt_id"] == principal.attempt_id


def test_decision_checks_are_exempt_and_still_recorded(store, conn):
    api = make_api(store, conn, tool_call_budget=1)
    principal = seed_running(api)
    agent = api.agent(principal)
    assert (
        api.client.get(f"/tools/incidents/{principal.incident_id}", headers=agent).status_code
        == 200
    )
    for _ in range(3):  # 예산을 다 써도 결정 확인은 된다(없는 제안이라 404)
        status = api.client.get("/tools/proposals/PROP-0000000000A1", headers=agent).status_code
        assert status == 404
    recorded = calls(api)
    assert [c["budget_exempt"] for c in recorded] == [False, True, True, True]
    assert calls(api, "TOOL_CALL_REFUSED") == []


def test_calls_outside_the_current_attempt_are_not_counted(store, conn):
    api = make_api(store, conn, tool_call_budget=1)
    principal = seed_running(api)
    stale = AgentPrincipal(principal.run_id, principal.incident_id, principal.work_id,
                           "ATT-00000000FFFF")  # fmt: skip
    headers = api.agent(stale)
    response = api.client.get(f"/tools/incidents/{principal.incident_id}", headers=headers)
    assert response.status_code == 404
    submitted = api.client.post(
        "/tools/proposals", json={}, headers={**headers, "Idempotency-Key": "k-1"}
    )
    assert submitted.status_code in (403, 404)  # intake의 원래 거절(W09)
    assert calls(api) == [] and calls(api, "TOOL_CALL_REFUSED") == []
    fresh = api.agent(principal)  # 지금 attempt의 예산은 그대로 남아 있다
    assert (
        api.client.get(f"/tools/incidents/{principal.incident_id}", headers=fresh).status_code
        == 200
    )


def test_rejected_query_does_not_consume_budget(store, conn):
    api = make_api(store, conn, tool_call_budget=1)
    principal = seed_running(api)
    agent = api.agent(principal)
    base = f"/tools/incidents/{principal.incident_id}/logs"
    assert api.client.get(base, headers=agent, params={"regex": "x"}).status_code == 422
    assert api.client.get(base, headers=agent, params={"limit": 99}).status_code == 422
    assert calls(api) == []


def test_an_earlier_attempts_calls_do_not_count_for_the_current_one(store, conn):
    api = make_api(store, conn, tool_call_budget=1)
    principal = seed_running(api)
    for _ in range(3):  # 같은 사건의 이전 attempt가 쓴 호출
        conn.execute(
            "INSERT INTO audit_events(run_id, incident_id, actor, event_type, created_at,"
            " payload_json) VALUES (?, ?, 'agent', 'TOOL_CALL', ?, ?)",
            (
                principal.run_id,
                principal.incident_id,
                "2026-09-27T00:00:00.000000Z",
                json.dumps({"attempt_id": "ATT-00000000EEEE", "tool": "get_incident",
                            "budget_exempt": False}),
            ),
        )  # fmt: skip
    agent = api.agent(principal)
    response = api.client.get(f"/tools/incidents/{principal.incident_id}", headers=agent)
    assert response.status_code == 200  # 지금 attempt의 첫 호출
