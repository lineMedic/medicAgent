"""테스트 전용 Control API 준비 도우미 (W06). token 값은 테스트용 가짜 값이다."""

import sqlite3
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from linemedic.control_plane.app import AppContext, create_app
from linemedic.control_plane.auth import AgentPrincipal, TokenRegistry, host_operator
from linemedic.control_plane.store import Store
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run, insert_work

OPERATOR_TOKEN = "test-operator-token-" + "0123456789abcdef" * 2
ROUTE_ID = "github-issue-primary"
RUN = "r-20260927-020000-abcd"
OTHER_RUN = "r-20260926-020000-beef"


@dataclass
class Api:
    client: TestClient
    store: Store
    conn: sqlite3.Connection
    tokens: TokenRegistry
    seeded: dict[str, str] = field(default_factory=dict)

    @property
    def operator(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

    def agent(self, principal: AgentPrincipal) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.issue_agent_token(principal)}"}


def make_api(store: Store, conn: sqlite3.Connection, **ctx_overrides) -> Api:
    tokens = TokenRegistry()
    tokens.register_operator(*host_operator(OPERATOR_TOKEN))
    ctx = AppContext(
        store=store,
        tokens=tokens,
        clock=store.clock,
        notification_route_id=ROUTE_ID,
        **ctx_overrides,
    )
    client = TestClient(create_app(ctx), raise_server_exceptions=False)
    return Api(client=client, store=store, conn=conn, tokens=tokens)


def seed_pr_opened(api: Api, issue_number: int = 7) -> dict[str, str]:
    """PR_OPENED incident와 WAITING_REVIEW work(운영자가 중단할 수 있는 상태)를 넣는다."""
    conn = api.conn
    if conn.execute("SELECT 1 FROM demo_runs WHERE id = ?", (RUN,)).fetchone() is None:
        insert_run(conn, RUN)
    insert_issue(conn, issue_number)
    incident = insert_incident(conn, RUN, "PR_OPENED", attempt_id="ATT-00000000000A")
    work = insert_work(
        conn, RUN, incident, issue_number, "WAITING_REVIEW", attempt_id="ATT-00000000000A"
    )
    api.seeded.update(incident=incident, work=work)
    return {"incident": incident, "work": work}


ATTEMPT = "ATT-00000000000A"


def seed_running(api: Api, issue_number: int = 9) -> AgentPrincipal:
    """조사 중인 사건(INVESTIGATING)·RUNNING work를 넣고 그 attempt의 agent principal을 돌려준다."""
    conn = api.conn
    if conn.execute("SELECT 1 FROM demo_runs WHERE id = ?", (RUN,)).fetchone() is None:
        insert_run(conn, RUN)
    insert_issue(conn, issue_number)
    incident = insert_incident(conn, RUN, "INVESTIGATING", attempt_id=ATTEMPT)
    work = insert_work(conn, RUN, incident, issue_number, "RUNNING", attempt_id=ATTEMPT)
    api.seeded.update(incident=incident, work=work)
    return AgentPrincipal(RUN, incident, work, ATTEMPT)


def escalate_body(**overrides) -> dict:
    body = {
        "schema_version": "linemedic.v4",
        "run_id": RUN,
        "expected_incident_version": 0,
        "reason_code": "PERMISSION_REQUIRED",
        "note": "PR 검토 결과 이번 run에서는 배포하지 않음",
    }
    body.update(overrides)
    return body


def escalate(api: Api, incident: str, body: dict, key: str = "escalate-1", headers=None):
    return api.client.post(
        f"/ops/incidents/{incident}/escalate",
        json=body,
        headers={**(headers if headers is not None else api.operator), "Idempotency-Key": key},
    )
