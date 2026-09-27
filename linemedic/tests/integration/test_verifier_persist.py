"""W05 2부 통합 테스트: verifier 결과 저장과 verifier 전용 전이 (실제 SQLite).

- PASS → incident RESOLVED·work SUCCEEDED·RECOVERY_VERIFIED intent
- FAIL·INCONCLUSIVE → ESCALATED·BLOCKED·RECOVERY_NOT_VERIFIED intent
- 저장·전이·알림·감사는 한 트랜잭션이다. 전이가 거부되면 verification 행도 남지 않는다
- RESOLVED를 쓰는 제품 경로는 verifier 모듈 하나다(INV-01, T-STATE-02와 함께)
- S1b 결과(origin=human_injected_negative)는 agent 성과 집계에서 빠진다
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from linemedic.common.ids import new_id
from linemedic.control_plane.state import TransitionDenied
from linemedic.control_plane.store import StateConflict
from linemedic.control_plane.verifier import (
    VerificationResult,
    agent_performance_verifications,
    persist_result,
)
from linemedic.tests.helpers.db_rows import (
    count,
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
    row,
)
from linemedic.tests.helpers.demo_states import prepare_verifying_incident

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN = "r-20260927-060000-abcd"
ROUTE = "github-issue-primary"


def make_result(
    verdict: str, origin: str = "agent_release", reason: str | None = None
) -> VerificationResult:
    return VerificationResult(
        verification_id=new_id("VER"),
        origin=origin,
        contract_id="defect-summary-v1",
        contract_sha256="c" * 64,
        verdict=verdict,
        reason=reason
        or {"PASS": "all_checks_passed", "FAIL": "content_mismatch"}.get(verdict, "observer_gap"),
        samples_completed=4 if verdict == "PASS" else 1,
        samples_required=4,
        observation_complete=verdict == "PASS",
        failed_assertions=[],
        resolved_written=False,
        started_at="2026-09-27T00:00:00.000000Z",
        ended_at="2026-09-27T00:01:00.000000Z",
        elapsed_seconds=60.0,
        target={"container": "linemedic-mes-x", "image_id": "sha256:" + "d" * 64},
    )


@pytest.fixture
def seeded(conn):
    insert_run(conn, RUN)
    insert_issue(conn, 5)
    incident = insert_incident(conn, RUN, "VERIFYING")
    work = insert_work(conn, RUN, incident, 5, "WAITING_VERIFICATION")
    return {"incident": incident, "work": work}


def persist(store, result, incident, version=0):
    with store.tx() as tx:
        return persist_result(
            tx,
            result,
            run_id=RUN,
            incident_id=incident,
            expected_incident_version=version,
            route_id=ROUTE,
        )


def audit_of(conn, incident):
    return [
        (r["actor"], r["event_type"])
        for r in conn.execute(
            "SELECT actor, event_type FROM audit_events WHERE incident_id = ? ORDER BY seq",
            (incident,),
        )
    ]


def test_pass_resolves_incident_and_work_with_recovery_verified(store, conn, seeded):
    result = make_result("PASS")
    persisted = persist(store, result, seeded["incident"])
    assert (persisted.incident_status, persisted.work_status) == ("RESOLVED", "SUCCEEDED")
    assert persisted.resolved_written is True
    incident = row(conn, "incidents", seeded["incident"])
    assert (incident["status"], incident["version"], incident["reason_code"]) == (
        "RESOLVED",
        1,
        None,
    )
    assert row(conn, "work_items", seeded["work"])["status"] == "SUCCEEDED"
    stored = row(conn, "verifications", result.verification_id)
    assert (stored["verdict"], stored["origin"], stored["contract_sha256"]) == (
        "PASS",
        "agent_release",
        "c" * 64,
    )
    assert json.loads(stored["result_json"])["resolved_written"] is True
    note = conn.execute("SELECT * FROM notifications").fetchone()
    assert note["id"] == persisted.notification_id
    assert note["event_type"] == "RECOVERY_VERIFIED"
    payload = json.loads(note["payload_json"])
    assert (payload["verification_id"], payload["verdict"], payload["contract_id"]) == (
        result.verification_id,
        "PASS",
        "defect-summary-v1",
    )
    assert audit_of(conn, seeded["incident"]) == [
        ("verifier", "INCIDENT_TRANSITION"),
        ("verifier", "WORK_TRANSITION"),
        ("verifier", "VERIFICATION_RECORDED"),
    ]


@pytest.mark.parametrize(
    ("verdict", "reason_code"),
    [("FAIL", "VERIFICATION_FAILED"), ("INCONCLUSIVE", "OBSERVATION_INCONCLUSIVE")],
)
def test_fail_or_inconclusive_escalates_with_recovery_not_verified(
    store, conn, seeded, verdict, reason_code
):
    result = make_result(verdict)
    persisted = persist(store, result, seeded["incident"])
    assert (persisted.incident_status, persisted.work_status) == ("ESCALATED", "BLOCKED")
    assert persisted.resolved_written is False
    incident = row(conn, "incidents", seeded["incident"])
    assert (incident["status"], incident["reason_code"]) == ("ESCALATED", reason_code)
    work = row(conn, "work_items", seeded["work"])
    assert (work["status"], work["reason_code"]) == ("BLOCKED", reason_code)
    assert conn.execute("SELECT event_type FROM notifications").fetchone()[0] == (
        "RECOVERY_NOT_VERIFIED"
    )
    stored = json.loads(row(conn, "verifications", result.verification_id)["result_json"])
    assert stored["resolved_written"] is False and stored["verdict"] == verdict


def test_incident_without_work_transitions_alone(store, conn):
    insert_run(conn, RUN)
    incident = insert_incident(conn, RUN, "VERIFYING")
    persisted = persist(store, make_result("FAIL", origin="human_injected_negative"), incident)
    assert (persisted.work_id, persisted.work_status, persisted.notification_id) == (
        None,
        None,
        None,
    )
    assert row(conn, "incidents", incident)["status"] == "ESCALATED"
    assert count(conn, "notifications") == 0


@pytest.mark.parametrize("status", ["PR_OPENED", "DEPLOYING", "RESOLVED", "ESCALATED"])
def test_incident_not_verifying_is_rejected_and_nothing_is_saved(store, conn, status):
    insert_run(conn, RUN)
    incident = insert_incident(conn, RUN, status)
    with pytest.raises(TransitionDenied):
        persist(store, make_result("PASS"), incident)
    assert row(conn, "incidents", incident)["status"] == status
    assert count(conn, "verifications") == 0 and count(conn, "audit_events") == 0


def test_stale_version_is_rejected_and_nothing_is_saved(store, conn, seeded):
    with pytest.raises(StateConflict):
        persist(store, make_result("PASS"), seeded["incident"], version=3)
    assert count(conn, "verifications") == 0
    assert row(conn, "incidents", seeded["incident"])["status"] == "VERIFYING"


def test_same_result_cannot_be_persisted_twice(store, conn, seeded):
    result = make_result("FAIL")
    persist(store, result, seeded["incident"])
    with pytest.raises((sqlite3.IntegrityError, TransitionDenied, StateConflict)):
        persist(store, result, seeded["incident"], version=1)
    assert count(conn, "verifications") == 1 and count(conn, "notifications") == 1


def test_invalid_inputs_are_rejected(store, conn, seeded):
    with pytest.raises(ValueError):
        persist(store, make_result("PASS", origin="operator_note"), seeded["incident"])
    with pytest.raises(ValueError):
        persist(store, make_result("RUNNING"), seeded["incident"])
    insert_run(conn, "r-20260926-060000-beef", active=0)
    other = insert_incident(conn, "r-20260926-060000-beef", "VERIFYING")
    with pytest.raises(ValueError):
        persist(store, make_result("PASS"), other)  # 다른 run의 사건
    assert count(conn, "verifications") == 0


def test_s1b_origin_is_excluded_from_agent_performance(store, conn):
    insert_run(conn, RUN)
    results = {}
    for origin in ("agent_release", "human_injected_negative", "manual_integration"):
        incident = insert_incident(conn, RUN, "VERIFYING")
        result = make_result("FAIL", origin=origin)
        persist(store, result, incident)
        results[origin] = result.verification_id
    with store.read() as tx:
        rows = agent_performance_verifications(tx, RUN)
    assert [r["id"] for r in rows] == [results["agent_release"]]


# ── demo 도우미와 정적 검사 ──────────────────────────────────


def test_prepare_verifying_incident_is_audited(store, conn):
    insert_run(conn, RUN)
    with store.tx() as tx:
        incident = prepare_verifying_incident(tx, RUN, purpose="unit_test", fingerprint="fp-demo")
    current = row(conn, "incidents", incident)
    assert (current["status"], current["source_kind"], current["version"]) == (
        "VERIFYING",
        "OPERATOR",
        0,
    )
    assert audit_of(conn, incident) == [("trusted_harness", "DEMO_STATE_PREPARED")]
    with pytest.raises(ValueError), store.tx() as tx:
        prepare_verifying_incident(tx, "r-20990101-000000-dead", purpose="x", fingerprint="y")


def product_modules():
    for path in sorted((REPO_ROOT / "linemedic").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not rel.startswith("linemedic/tests/"):
            yield rel, path.read_text(encoding="utf-8")


def test_verifier_module_is_the_only_resolved_writer():
    """INV-01: verifier 주체를 쓰는 제품 모듈은 verifier.py뿐이다(정의 모듈 state.py 제외)."""
    pattern = re.compile(r"Actor\.VERIFIER")
    users = [
        rel
        for rel, text in product_modules()
        if pattern.search(text) and rel != "linemedic/control_plane/state.py"
    ]
    assert users == ["linemedic/control_plane/verifier.py"]


def test_demo_state_helper_is_not_reachable_from_operating_api():
    """VERIFYING 준비 도우미는 테스트와 trusted harness만 쓴다(spec 08 §7)."""
    users = [rel for rel, text in product_modules() if "demo_states" in text]
    assert users == ["linemedic/factory_sim/negative/harness.py"]
