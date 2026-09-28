"""W06 단위 테스트: incident·work 전이 표·주체 권한·CAS·결합 전이·감사 (T-STATE-02·03, INV-01).

표에 있는 모든 (출발, 도착) 쌍과 표 밖의 모든 쌍을 모든 주체로 시험한다.
행은 테스트 도우미로 원하는 출발 상태에 넣고, 전이는 제품 함수로만 한다.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from linemedic.control_plane.state import (
    COUPLED_WORK_STATUS,
    INCIDENT_STATUSES,
    INCIDENT_TRANSITIONS,
    WORK_STATUSES,
    WORK_TRANSITIONS,
    Actor,
    TransitionDenied,
    coupled_outbox_event,
    coupled_transition,
    transition_incident,
    transition_work,
)
from linemedic.control_plane.store import StateConflict, cas_update
from linemedic.tests.helpers.db_rows import (
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
    row,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN = "r-20260927-010000-abcd"
A = Actor

# docs/03 §2·§3 표를 테스트 쪽에서 한 번 더 손으로 옮긴 기대값(이중 기재 확인)
EXPECTED_INCIDENT = {
    ("NEW", "INVESTIGATING"): {A.SUPERVISOR},
    ("NEW", "ESCALATED"): {A.ROUTER, A.SUPERVISOR},
    ("NEW", "EXECUTION_UNKNOWN"): {A.ROUTER},
    ("INVESTIGATING", "VALIDATING"): {A.BROKER},
    ("INVESTIGATING", "ESCALATED"): {A.SUPERVISOR, A.BROKER},
    ("VALIDATING", "INVESTIGATING"): {A.BROKER},
    ("VALIDATING", "PR_OPENED"): {A.BROKER},
    ("VALIDATING", "WORK_ORDER_DRAFTED"): {A.BROKER},
    ("VALIDATING", "ESCALATED"): {A.BROKER},
    ("VALIDATING", "EXECUTION_UNKNOWN"): {A.BROKER},
    ("PR_OPENED", "DEPLOYING"): {A.RELEASE_EXECUTOR},
    ("PR_OPENED", "ESCALATED"): {A.OPERATOR},
    ("DEPLOYING", "VERIFYING"): {A.RELEASE_EXECUTOR},
    ("DEPLOYING", "ESCALATED"): {A.RELEASE_EXECUTOR},
    ("DEPLOYING", "EXECUTION_UNKNOWN"): {A.RELEASE_EXECUTOR},
    ("VERIFYING", "RESOLVED"): {A.VERIFIER},
    ("VERIFYING", "ESCALATED"): {A.VERIFIER},
    ("EXECUTION_UNKNOWN", "NEW"): {A.RECONCILER},
    ("EXECUTION_UNKNOWN", "PR_OPENED"): {A.RECONCILER},
    ("EXECUTION_UNKNOWN", "VERIFYING"): {A.RECONCILER},
    ("EXECUTION_UNKNOWN", "ESCALATED"): {A.RECONCILER},
}
EXPECTED_WORK = {
    ("WAITING_APPROVAL", "WAITING_NOTIFICATION"): {A.OPERATOR, A.ROUTER},
    ("WAITING_APPROVAL", "BLOCKED"): {A.ROUTER, A.OPERATOR},
    ("WAITING_APPROVAL", "CANCELLED"): {A.OPERATOR},
    ("WAITING_NOTIFICATION", "READY"): {A.NOTIFIER, A.SUPERVISOR},
    ("WAITING_NOTIFICATION", "BLOCKED"): {A.SUPERVISOR},
    ("WAITING_NOTIFICATION", "CANCELLED"): {A.OPERATOR},
    ("READY", "RUNNING"): {A.SUPERVISOR},
    ("READY", "BLOCKED"): {A.SUPERVISOR},
    ("READY", "CANCELLED"): {A.SUPERVISOR},
    ("RUNNING", "WAITING_REVIEW"): {A.BROKER},
    ("RUNNING", "HANDED_OFF"): {A.BROKER},
    ("RUNNING", "BLOCKED"): {A.BROKER, A.SUPERVISOR},
    ("RUNNING", "EXECUTION_UNKNOWN"): {A.BROKER},
    ("WAITING_REVIEW", "WAITING_VERIFICATION"): {A.RELEASE_EXECUTOR},
    ("WAITING_REVIEW", "BLOCKED"): {A.OPERATOR},
    ("WAITING_VERIFICATION", "SUCCEEDED"): {A.VERIFIER},
    ("WAITING_VERIFICATION", "BLOCKED"): {A.VERIFIER, A.RELEASE_EXECUTOR},
    ("WAITING_VERIFICATION", "EXECUTION_UNKNOWN"): {A.RELEASE_EXECUTOR},
    ("EXECUTION_UNKNOWN", "WAITING_REVIEW"): {A.RECONCILER},
    ("EXECUTION_UNKNOWN", "WAITING_VERIFICATION"): {A.RECONCILER},
    ("EXECUTION_UNKNOWN", "BLOCKED"): {A.RECONCILER},
}


@pytest.fixture
def db(store, conn):
    insert_run(conn, RUN)
    return store, conn


class Issues:
    """work마다 새 Issue 번호를 준다(Issue당 활성 work 1개 제약)."""

    def __init__(self, conn):
        self.conn = conn
        self.next = 1

    def new(self) -> int:
        number = self.next
        self.next += 1
        insert_issue(self.conn, number)
        return number


def audit_rows(conn, event_type):
    return conn.execute(
        "SELECT actor, incident_id, payload_json FROM audit_events"
        " WHERE event_type = ? ORDER BY seq",
        (event_type,),
    ).fetchall()


# ── 표 자체 ───────────────────────────────────────────────────


def test_tables_match_docs_03():
    assert {key: set(value) for key, value in INCIDENT_TRANSITIONS.items()} == EXPECTED_INCIDENT
    assert {key: set(value) for key, value in WORK_TRANSITIONS.items()} == EXPECTED_WORK
    assert dict(COUPLED_WORK_STATUS) == {
        "INVESTIGATING": "RUNNING",
        "PR_OPENED": "WAITING_REVIEW",
        "WORK_ORDER_DRAFTED": "HANDED_OFF",
        "DEPLOYING": "WAITING_VERIFICATION",
        "VERIFYING": "WAITING_VERIFICATION",
        "RESOLVED": "SUCCEEDED",
        "ESCALATED": "BLOCKED",
        "EXECUTION_UNKNOWN": "EXECUTION_UNKNOWN",
    }


def test_terminal_states_have_no_exit():
    for status in ("RESOLVED", "ESCALATED", "WORK_ORDER_DRAFTED"):
        assert not [pair for pair in INCIDENT_TRANSITIONS if pair[0] == status]
    for status in ("HANDED_OFF", "SUCCEEDED", "BLOCKED", "CANCELLED"):
        assert not [pair for pair in WORK_TRANSITIONS if pair[0] == status]


# ── 모든 쌍 × 모든 주체 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [(f, t) for f in INCIDENT_STATUSES for t in INCIDENT_STATUSES],
)
def test_incident_transition_matrix(db, from_status, to_status):
    store, conn = db
    allowed = EXPECTED_INCIDENT.get((from_status, to_status), set())
    for actor in Actor:
        incident = insert_incident(conn, RUN, from_status)
        if actor in allowed:
            with store.tx() as tx:
                assert transition_incident(tx, incident, 0, to_status, actor) == 1
            current = row(conn, "incidents", incident)
            assert (current["status"], current["version"]) == (to_status, 1)
            last = audit_rows(conn, "INCIDENT_TRANSITION")[-1]
            payload = json.loads(last["payload_json"])
            assert (last["actor"], last["incident_id"]) == (actor.value, incident)
            assert (payload["from"], payload["to"], payload["version"]) == (
                from_status,
                to_status,
                1,
            )
        else:
            with pytest.raises(TransitionDenied), store.tx() as tx:
                transition_incident(tx, incident, 0, to_status, actor)
            current = row(conn, "incidents", incident)
            assert (current["status"], current["version"]) == (from_status, 0)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [(f, t) for f in WORK_STATUSES for t in WORK_STATUSES],
)
def test_work_transition_matrix(db, from_status, to_status):
    store, conn = db
    issues = Issues(conn)
    allowed = EXPECTED_WORK.get((from_status, to_status), set())
    audits_before = len(audit_rows(conn, "WORK_TRANSITION"))
    for actor in Actor:
        incident = insert_incident(conn, RUN)
        work = insert_work(conn, RUN, incident, issues.new(), from_status)
        if actor in allowed:
            with store.tx() as tx:
                assert transition_work(tx, work, 0, to_status, actor) == 1
            current = row(conn, "work_items", work)
            assert (current["status"], current["version"]) == (to_status, 1)
            assert current["updated_at"] == "2026-09-27T00:00:00.000000Z"
        else:
            with pytest.raises(TransitionDenied), store.tx() as tx:
                transition_work(tx, work, 0, to_status, actor)
            current = row(conn, "work_items", work)
            assert (current["status"], current["version"]) == (from_status, 0)
        conn.execute("DELETE FROM work_items WHERE id = ?", (work,))  # RUNNING 슬롯 비우기
    assert len(audit_rows(conn, "WORK_TRANSITION")) - audits_before == len(allowed)


# ── T-STATE-02·03 ────────────────────────────────────────────


@pytest.mark.parametrize("actor", [a for a in Actor if a is not A.VERIFIER])
def test_t_state_02_only_verifier_can_resolve(db, actor):
    store, conn = db
    incident = insert_incident(conn, RUN, "VERIFYING")
    work = insert_work(conn, RUN, incident, Issues(conn).new(), "WAITING_VERIFICATION")
    with pytest.raises(TransitionDenied), store.tx() as tx:
        transition_incident(tx, incident, 0, "RESOLVED", actor)
    with pytest.raises(TransitionDenied), store.tx() as tx:
        transition_work(tx, work, 0, "SUCCEEDED", actor)
    with pytest.raises(TransitionDenied), store.tx() as tx:
        coupled_transition(
            tx,
            incident_id=incident,
            expected_incident_version=0,
            incident_to="RESOLVED",
            work_id=work,
            expected_work_version=0,
            work_to="SUCCEEDED",
            actor=actor,
        )
    assert row(conn, "incidents", incident)["status"] == "VERIFYING"
    assert row(conn, "work_items", work)["status"] == "WAITING_VERIFICATION"


@pytest.mark.parametrize("to_status", INCIDENT_STATUSES)
def test_t_state_03_work_order_drafted_has_no_recovery_transition(db, to_status):
    store, conn = db
    incident = insert_incident(conn, RUN, "WORK_ORDER_DRAFTED")
    for actor in Actor:
        with pytest.raises(TransitionDenied), store.tx() as tx:
            transition_incident(tx, incident, 0, to_status, actor, "HUMAN_WORK_IN_PROGRESS")
    assert row(conn, "incidents", incident)["status"] == "WORK_ORDER_DRAFTED"


# ── 주체·CAS·reason ──────────────────────────────────────────


def test_actor_must_be_server_enum_not_request_string(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "VERIFYING")
    with pytest.raises(TypeError), store.tx() as tx:
        transition_incident(tx, incident, 0, "RESOLVED", "verifier")  # type: ignore[arg-type]
    assert row(conn, "incidents", incident)["status"] == "VERIFYING"


def test_stale_version_is_state_conflict_with_current_state(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "PR_OPENED", version=3)
    with pytest.raises(StateConflict) as caught, store.tx() as tx:
        transition_incident(tx, incident, 2, "ESCALATED", A.OPERATOR)
    assert (caught.value.current_status, caught.value.current_version) == ("PR_OPENED", 3)
    assert row(conn, "incidents", incident)["version"] == 3


def test_cas_update_checks_status_and_allowlists_columns(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "NEW")
    with pytest.raises(StateConflict), store.tx() as tx:
        cas_update(tx, "incidents", incident, 0, "INVESTIGATING", "ESCALATED")
    with pytest.raises(ValueError), store.tx() as tx:
        cas_update(tx, "demo_runs", RUN, 0, "x", "y")
    with pytest.raises(ValueError), store.tx() as tx:
        cas_update(tx, "incidents", incident, 0, "NEW", "ESCALATED", run_id="r-other")
    with store.tx() as tx:
        assert cas_update(tx, "incidents", incident, 0, "NEW", "ESCALATED", reason_code="X_Y") == 1


def test_reason_code_is_stored_and_validated(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "NEW")
    with pytest.raises(ValueError), store.tx() as tx:
        transition_incident(tx, incident, 0, "ESCALATED", A.ROUTER, "not a code; DROP TABLE")
    with store.tx() as tx:
        transition_incident(tx, incident, 0, "ESCALATED", A.ROUTER, "UNSUPPORTED_ACTION")
    assert row(conn, "incidents", incident)["reason_code"] == "UNSUPPORTED_ACTION"


def test_audit_payload_masks_secrets(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "NEW")
    token = "ghp_" + "A" * 36
    with store.tx() as tx:
        transition_incident(tx, incident, 0, "ESCALATED", A.ROUTER, details={"note": f"x {token}"})
    payload = audit_rows(conn, "INCIDENT_TRANSITION")[-1]["payload_json"]
    assert token not in payload and "[REDACTED:github_token]" in payload


# ── 결합 전이 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("incident_from", "incident_to", "work_from", "work_to", "actor", "event"),
    [
        ("NEW", "INVESTIGATING", "READY", "RUNNING", A.SUPERVISOR, None),
        ("VALIDATING", "PR_OPENED", "RUNNING", "WAITING_REVIEW", A.BROKER, "PR_READY"),
        ("VALIDATING", "WORK_ORDER_DRAFTED", "RUNNING", "HANDED_OFF", A.BROKER, "HANDOFF_DRAFTED"),
        (
            "PR_OPENED",
            "DEPLOYING",
            "WAITING_REVIEW",
            "WAITING_VERIFICATION",
            A.RELEASE_EXECUTOR,
            None,
        ),
        (
            "VERIFYING",
            "RESOLVED",
            "WAITING_VERIFICATION",
            "SUCCEEDED",
            A.VERIFIER,
            "RECOVERY_VERIFIED",
        ),
        (
            "VERIFYING",
            "ESCALATED",
            "WAITING_VERIFICATION",
            "BLOCKED",
            A.VERIFIER,
            "RECOVERY_NOT_VERIFIED",
        ),
        ("PR_OPENED", "ESCALATED", "WAITING_REVIEW", "BLOCKED", A.OPERATOR, "WORK_BLOCKED"),
        ("INVESTIGATING", "ESCALATED", "RUNNING", "BLOCKED", A.SUPERVISOR, "WORK_BLOCKED"),
        ("VALIDATING", "EXECUTION_UNKNOWN", "RUNNING", "EXECUTION_UNKNOWN", A.BROKER, None),
        (
            "EXECUTION_UNKNOWN",
            "VERIFYING",
            "EXECUTION_UNKNOWN",
            "WAITING_VERIFICATION",
            A.RECONCILER,
            None,
        ),
    ],
)
def test_coupled_transition(db, incident_from, incident_to, work_from, work_to, actor, event):
    store, conn = db
    incident = insert_incident(conn, RUN, incident_from)
    work = insert_work(conn, RUN, incident, Issues(conn).new(), work_from)
    with store.tx() as tx:
        result = coupled_transition(
            tx,
            incident_id=incident,
            expected_incident_version=0,
            incident_to=incident_to,
            work_id=work,
            expected_work_version=0,
            work_to=work_to,
            actor=actor,
        )
    assert (result.incident_version, result.work_version, result.outbox_event) == (1, 1, event)
    assert row(conn, "incidents", incident)["status"] == incident_to
    assert row(conn, "work_items", work)["status"] == work_to
    assert len(audit_rows(conn, "INCIDENT_TRANSITION")) == 1
    assert len(audit_rows(conn, "WORK_TRANSITION")) == 1


def test_coupled_transition_rejects_pair_outside_table(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "PR_OPENED")
    work = insert_work(conn, RUN, incident, Issues(conn).new(), "WAITING_REVIEW")
    with pytest.raises(TransitionDenied), store.tx() as tx:
        coupled_transition(
            tx,
            incident_id=incident,
            expected_incident_version=0,
            incident_to="ESCALATED",
            work_id=work,
            expected_work_version=0,
            work_to="CANCELLED",
            actor=A.OPERATOR,
        )


def test_coupled_transition_is_atomic_when_work_side_fails(db):
    """incident 전이는 허용돼도 work 전이가 거부되면 둘 다 바뀌지 않는다."""
    store, conn = db
    incident = insert_incident(conn, RUN, "PR_OPENED")
    work = insert_work(conn, RUN, incident, Issues(conn).new(), "RUNNING")  # 결합 불일치
    with pytest.raises(TransitionDenied), store.tx() as tx:
        coupled_transition(
            tx,
            incident_id=incident,
            expected_incident_version=0,
            incident_to="ESCALATED",
            work_id=work,
            expected_work_version=0,
            work_to="BLOCKED",
            actor=A.OPERATOR,
        )
    assert row(conn, "incidents", incident)["status"] == "PR_OPENED"
    assert row(conn, "work_items", work)["status"] == "RUNNING"
    assert audit_rows(conn, "INCIDENT_TRANSITION") == []


def test_coupled_transition_rejects_work_of_other_incident(db):
    store, conn = db
    incident = insert_incident(conn, RUN, "PR_OPENED")
    other = insert_incident(conn, RUN, "PR_OPENED")
    work = insert_work(conn, RUN, other, Issues(conn).new(), "WAITING_REVIEW")
    with pytest.raises(TransitionDenied), store.tx() as tx:
        coupled_transition(
            tx,
            incident_id=incident,
            expected_incident_version=0,
            incident_to="ESCALATED",
            work_id=work,
            expected_work_version=0,
            work_to="BLOCKED",
            actor=A.OPERATOR,
        )
    assert row(conn, "incidents", incident)["status"] == "PR_OPENED"


def test_coupled_outbox_event_mapping():
    assert coupled_outbox_event("RUNNING", "BLOCKED") == "WORK_BLOCKED"
    assert coupled_outbox_event("WAITING_VERIFICATION", "BLOCKED") == "RECOVERY_NOT_VERIFIED"
    assert coupled_outbox_event("EXECUTION_UNKNOWN", "WAITING_REVIEW") == "PR_READY"
    assert coupled_outbox_event("READY", "RUNNING") is None


# ── INV-01 정적 검사 ─────────────────────────────────────────


def _code_strings(tree):
    """docstring을 뺀 문자열 상수들."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


# 상태를 표시만 하는 모듈(W18 대시보드 읽기 모델, D85). 표시 문구 표에 상태 이름 문자열이 있어
# RESOLVED 문자열 규칙만 빼고(Actor·cas_update 규칙은 그대로), 아래 테스트로 쓰기가 없음을 확인한다.
DISPLAY_ONLY = {"linemedic/dashboard/readmodel.py"}
WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|PRAGMA)\b", re.IGNORECASE
)


def test_inv_01_verifier_actor_only_in_verifier_module():
    """RESOLVED·SUCCEEDED(work)를 쓸 수 있는 곳은 state.py(표)와 verifier.py뿐이다.

    - `Actor.VERIFIER`·`Actor("verifier")` 사용
    - 코드 문자열의 `RESOLVED`(사건 상태에서만 쓰는 값)
    - `work_items`와 `SUCCEEDED`를 함께 쓰는 SQL, `cas_update(..., "SUCCEEDED"/"RESOLVED")`
    """
    allowed = {"linemedic/control_plane/verifier.py", "linemedic/control_plane/state.py"}
    actor = re.compile(r"Actor\.VERIFIER|Actor\(\s*['\"]verifier['\"]\s*\)")
    offenders = []
    for path in sorted((REPO_ROOT / "linemedic").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("linemedic/tests/") or rel in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = bool(actor.search(source))
        for node in [] if rel in DISPLAY_ONLY else _code_strings(tree):
            text = node.value
            if re.search(r"\bRESOLVED\b", text) or ("work_items" in text and "SUCCEEDED" in text):
                found = True
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "cas_update"
            ):
                values = [a.value for a in node.args if isinstance(a, ast.Constant)]
                found = found or bool({"RESOLVED", "SUCCEEDED"} & set(values))
        if found:
            offenders.append(rel)
    assert offenders == []


def test_display_only_modules_never_write():
    """표시 전용 모듈의 SQL은 SELECT(WITH … SELECT)뿐이고 execute·전이 함수를 부르지 않는다."""
    for rel in sorted(DISPLAY_ONLY):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        strings = [node.value for node in _code_strings(ast.parse(source))]
        assert any(re.match(r"\s*(SELECT|WITH)\b", text) for text in strings), rel
        assert [text for text in strings if WRITE_SQL.search(text)] == [], rel
        for call in ("execute(", "cas_update(", "transition_incident(", "coupled_transition("):
            assert call not in source, (rel, call)
