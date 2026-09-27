"""W06 단위 테스트: token → principal, prefix 권한 가드 (T-AUTH-01·02·03).

T-AUTH-01은 현재 있는 `/ops/incidents/*`로 시험하고 W12에서 `/ops/releases`로 다시 확인한다.
T-AUTH-02는 `/tools` endpoint(W07)가 쓸 사건 범위 가드 `load_visible_incident`를 시험한다.
"""

import pytest

from linemedic.control_plane.auth import (
    OPERATOR_ROLES,
    AgentPrincipal,
    AuthError,
    OperatorPrincipal,
    TokenRegistry,
    bearer_token,
    can_access_incident,
    load_visible_incident,
    token_hash,
)
from linemedic.control_plane.errors import ApiError
from linemedic.tests.helpers.api import (
    OPERATOR_TOKEN,
    OTHER_RUN,
    RUN,
    escalate,
    escalate_body,
    make_api,
    seed_pr_opened,
    seed_running,
)
from linemedic.tests.helpers.db_rows import count, insert_incident, insert_run, row


@pytest.fixture
def api(store, conn):
    return make_api(store, conn)


def principal_for(api, incident: str) -> AgentPrincipal:
    return AgentPrincipal(RUN, incident, api.seeded["work"], "ATT-00000000000A")


def assert_db_untouched(api, incident: str) -> None:
    current = row(api.conn, "incidents", incident)
    assert (current["status"], current["version"]) == ("PR_OPENED", 0)
    assert count(api.conn, "api_requests") == 0
    assert count(api.conn, "audit_events") == 0
    assert count(api.conn, "notifications") == 0


# ── token 등록부 ──────────────────────────────────────────────


def test_registry_stores_only_token_hashes():
    registry = TokenRegistry()
    principal = OperatorPrincipal("host-operator", frozenset({"read"}))
    registry.register_operator(OPERATOR_TOKEN, principal)
    assert registry.resolve(OPERATOR_TOKEN) == principal
    assert list(registry._by_hash) == [token_hash(OPERATOR_TOKEN)]
    assert OPERATOR_TOKEN not in repr(registry)
    assert registry.resolve(OPERATOR_TOKEN + "x") is None
    assert registry.resolve("") is None and registry.resolve(None) is None


def test_registry_rejects_short_operator_token_and_unknown_role():
    registry = TokenRegistry()
    with pytest.raises(AuthError):
        registry.register_operator("short", OperatorPrincipal("op", frozenset({"read"})))
    with pytest.raises(AuthError):
        registry.register_operator(OPERATOR_TOKEN, OperatorPrincipal("op", frozenset({"root"})))


def test_agent_token_is_scoped_and_revoked_with_attempt():
    registry = TokenRegistry()
    first = AgentPrincipal(RUN, "INC-000000000001", "WORK-000000000001", "ATT-000000000001")
    second = AgentPrincipal(RUN, "INC-000000000002", "WORK-000000000002", "ATT-000000000002")
    token_a, token_b = registry.issue_agent_token(first), registry.issue_agent_token(second)
    assert token_a != token_b and len(token_a) >= 32
    assert registry.resolve(token_a) == first
    assert registry.revoke_attempt("ATT-000000000001") == 1
    assert registry.resolve(token_a) is None
    assert registry.resolve(token_b) == second


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc.def", "abc.def"),
        ("bearer abc", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer a b", None),
        (None, None),
    ],
)
def test_bearer_token_parsing(header, expected):
    assert bearer_token(header) == expected


# ── prefix 가드 ───────────────────────────────────────────────


def test_missing_or_unknown_token_is_401(api):
    incident = seed_pr_opened(api)["incident"]
    for headers in ({}, {"Authorization": "Bearer not-a-registered-token"}):
        response = api.client.get(f"/ops/incidents/{incident}", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
        assert response.headers["www-authenticate"] == "Bearer"


def test_t_auth_01_agent_token_on_ops_is_403_and_changes_nothing(api):
    incident = seed_pr_opened(api)["incident"]
    agent = api.agent(principal_for(api, incident))
    response = escalate(api, incident, escalate_body(), headers=agent)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_SCOPE"
    read = api.client.get(f"/ops/incidents/{incident}", headers=agent)
    assert read.status_code == 403
    assert_db_untouched(api, incident)


def test_prefix_guard_rejects_agent_before_routing(api):
    """라우트가 없는 /ops 경로도 agent token이면 404가 아니라 403이다(경로 존재를 드러내지 않음)."""
    incident = seed_pr_opened(api)["incident"]
    agent = api.agent(principal_for(api, incident))
    for path in ("/ops/releases", "/ops/does-not-exist", "/ops"):
        response = api.client.post(path, json={}, headers=agent)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "FORBIDDEN_SCOPE"


def test_operator_token_on_tools_is_403(api):
    response = api.client.get("/tools/incidents/INC-000000000001", headers=api.operator)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_SCOPE"


def test_operator_without_role_is_403(store, conn):
    api = make_api(store, conn)
    reader = "reader-token-" + "f" * 32
    api.tokens.register_operator(reader, OperatorPrincipal("reader", frozenset({"read"})))
    incident = seed_pr_opened(api)["incident"]
    headers = {"Authorization": f"Bearer {reader}"}
    assert api.client.get(f"/ops/incidents/{incident}", headers=headers).status_code == 200
    response = escalate(api, incident, escalate_body(), headers=headers)
    assert response.status_code == 403
    assert_db_untouched(api, incident)


def test_t_auth_02_agent_scope_hides_other_run_and_incident(api, store):
    principal = seed_running(api)
    own = principal.incident_id
    same_run_other = insert_incident(api.conn, RUN, "NEW")
    insert_run(api.conn, OTHER_RUN, active=0)
    other_run = insert_incident(api.conn, OTHER_RUN, "NEW")
    with store.read() as tx:
        assert load_visible_incident(tx, principal, own)["id"] == own
        errors = []
        for target in (same_run_other, other_run, "INC-FFFFFFFFFFFF", "not-an-id"):
            with pytest.raises(ApiError) as caught:
                load_visible_incident(tx, principal, target)
            errors.append((caught.value.code, caught.value.details))
    # 범위 밖·없음·형식 오류가 같은 응답이라 존재 여부를 알 수 없다.
    assert errors == [("RESOURCE_NOT_FOUND", {})] * 4


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE work_items SET status = 'WAITING_REVIEW' WHERE id = :work",
        "UPDATE work_items SET attempt_id = 'ATT-00000000000B' WHERE id = :work",
        "UPDATE incidents SET attempt_id = 'ATT-00000000000B' WHERE id = :incident",
    ],
    ids=["work_not_running", "work_other_attempt", "incident_other_attempt"],
)
def test_agent_scope_requires_current_running_attempt(api, store, change):
    """폐기되지 않은 token이라도 attempt가 끝났거나 바뀌었으면 조회할 수 없다."""
    principal = seed_running(api)
    api.conn.execute(change, {"work": principal.work_id, "incident": principal.incident_id})
    with store.read() as tx, pytest.raises(ApiError) as caught:
        load_visible_incident(tx, principal, principal.incident_id)
    assert (caught.value.code, caught.value.details) == ("RESOURCE_NOT_FOUND", {})


def test_can_access_incident_rules():
    incident = {"run_id": RUN, "id": "INC-000000000001"}
    assert can_access_incident(OperatorPrincipal("op", OPERATOR_ROLES), incident)
    assert not can_access_incident(OperatorPrincipal("op", frozenset({"operate"})), incident)
    agent = AgentPrincipal(RUN, "INC-000000000001", "WORK-000000000001", "ATT-000000000001")
    assert can_access_incident(agent, incident)
    assert not can_access_incident(agent, {"run_id": OTHER_RUN, "id": "INC-000000000001"})


@pytest.mark.parametrize(
    "extra",
    [
        {"actor": "verifier"},
        {"status": "RESOLVED"},
        {"role": "operator"},
        {"model": "fake-model"},
        {"policy_version": "v0"},
        {"X-Operator": True},
    ],
)
def test_t_auth_03_actor_or_status_in_body_is_422(api, extra):
    incident = seed_pr_opened(api)["incident"]
    response = escalate(api, incident, {**escalate_body(), **extra})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert {"loc": [next(iter(extra))], "type": "extra_forbidden"} in error["details"]["errors"]
    assert_db_untouched(api, incident)
