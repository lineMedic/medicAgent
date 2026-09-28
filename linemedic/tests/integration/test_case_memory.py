"""W27 사례 기억: case builder·outcome·revision·snapshot·검색·projection (T-MEM-01~05, N13, D84).

실제 SQLite(FTS5)·migration·store로 시험한다. 원본 event 행(proposal·execution·verification·
WORK_BLOCKED 알림)은 테스트 도우미로 넣고, 노트·색인·snapshot·검색·projection·검색 기록은 제품
코드(`CaseBuilder`·`build_snapshot`·`CaseSearch`)로만 만든다.

`LINEMEDIC_RECORD_EVIDENCE=1`이면 N13 검색 결과를 `evidence/N13-case-search.md`에 남긴다
(검색 품질을 주장하지 않는다).
"""

import argparse
import json
import os
import platform
import sqlite3
from pathlib import Path

import httpx
import pytest

from linemedic import cli
from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.control_plane import store as store_module
from linemedic.control_plane.auth import AgentPrincipal, OperatorPrincipal
from linemedic.control_plane.memory import builder as case_builder
from linemedic.control_plane.memory import search as case_search
from linemedic.control_plane.memory import snapshot as memory_snapshot
from linemedic.control_plane.memory.builder import rebuild_index, retract
from linemedic.control_plane.memory.text import fts_query, query_tokens
from linemedic.tests.helpers.api import OPERATOR_TOKEN, RUN, make_api
from linemedic.tests.helpers.case_world import (
    ACTUAL_VALUE,
    ATTEMPT,
    BASE,
    CANDIDATE,
    CONTRACT_SHA,
    CREATE_PR_ACTION,
    EXPECTED_VALUE,
    FP,
    HYPOTHESIS,
    IMAGE,
    MERGE,
    OLD_ATTEMPT,
    OLD_RUN,
    OTHER_IMAGE,
    OTHER_REPO,
    T0,
    T1,
    T2,
    T3,
    TERMS,
    World,
    fail_result,
    pass_result,
)
from linemedic.tests.helpers.db_rows import (
    REPOSITORY_ID,
    insert_run,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = REPO_ROOT / "evidence" / "N13-case-search.md"


@pytest.fixture
def w(store, conn, fake_clock):
    insert_run(conn, OLD_RUN, active=0)
    insert_run(conn, RUN)
    return World(store, conn, fake_clock)


def only(notes, **match):
    found = [n for n in notes if all(n[k] == v for k, v in match.items())]
    assert len(found) == 1, found
    return found[0]


# ── T-MEM-01: outcome 규칙 ─────────────────────────────────────


def test_verifier_pass_with_identity_is_verified_success(w):
    incident, work, verification = w.verified("PASS")
    created = w.build()
    assert len(created) == 2  # PR 준비(UNVERIFIED) → 업무 검증(VERIFIED_SUCCESS)
    note = only(w.notes(), outcome="VERIFIED_SUCCESS")
    assert (note["phase"], note["origin"], note["publish_status"]) == (
        "verification",
        "agent_release",
        "PUBLISHED",
    )
    payload = note["payload"]
    assert payload["schema_version"] == "linemedic.case.v4"
    assert payload["source_event_key"] == f"verification:{verification}:final"
    assert payload["applicability"] == {
        "contract_id": "defect-summary-v1",
        "contract_sha256": CONTRACT_SHA,
        "fixture_sha256": "f" * 64,
        "image_id": IMAGE,
        "container_id": "d" * 64,
        "approved_merge_sha": MERGE,
        "approved_tree": "4" * 40,
        "base_sha": BASE,
        "candidate_sha": CANDIDATE,
    }
    assert payload["failure_conditions"] == []
    assert payload["hypothesis"] == HYPOTHESIS  # 배포한 제안의 가설·변경을 같이 남긴다
    assert payload["attempted_change"].startswith("app/defects.py (+3/-1)")
    assert payload["evidence_ids"] == ["EV-0000000000E1"]
    assert payload["artifact_refs"]["verification_id"] == verification
    assert payload["work_id"] == work and payload["source_incident_id"] == incident
    assert "관찰 범위" in payload["summary"] and payload["limitations"]
    assert note["id"] in w.indexed()  # 게시와 색인이 같이 된다


def test_business_fail_is_verified_failure_without_expected_or_actual_values(w):
    w.verified("FAIL", result=fail_result())
    w.build()
    note = only(w.notes(), outcome="VERIFIED_FAILURE")
    conditions = note["payload"]["failure_conditions"]
    assert "판정 사유 content_mismatch" in conditions
    assert "missing-inspector: exact_by_inspector_mapping 불일치(by_inspector)" in conditions
    assert "held-out case: exact_total_defects 불일치(total_defects)" in conditions
    raw = note["payload_json"]
    assert str(EXPECTED_VALUE) not in raw and str(ACTUAL_VALUE) not in raw
    assert "variant-held-out" not in raw  # holdout case는 ID도 남기지 않는다
    assert "시도: app/defects.py (+3/-1)" in note["payload"]["summary"]  # 무엇이 실패했는가
    assert "다른 버전의 결과가 아니다" in note["payload"]["limitations"][0]


def test_pr_only_is_unverified_never_success(w):
    w.pr_opened()
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["phase"]) == ("UNVERIFIED", "review")
    payload = note["payload"]
    assert payload["artifact_refs"]["pr_number"] == 51
    assert payload["hypothesis_by"] == "에이전트(검증되지 않은 가설)"
    assert "app/defects.py (+3/-1)" in payload["attempted_change"]
    assert "재현 테스트 tests/repro/test_missing_inspector.py" in payload["attempted_change"]
    assert payload["applicability"]["candidate_sha"] == CANDIDATE
    assert "업무 복구는 확인하지 않았다" in payload["limitations"][0]


def test_permission_block_is_blocked_not_a_wrong_answer(w):
    incident = w.incident()
    work = w.work(OLD_RUN, incident, status="BLOCKED")
    w.blocked(
        OLD_RUN,
        incident,
        work,
        {
            "blocker_code": "PERMISSION_REQUIRED",
            "stage": "external_write",
            "reason_detail": "봇 PR 생성 권한이 없다",
            "attempted_actions": ["create_pr EXE-0000000000F1 → FAILED(permission)"],
            "missing_requirements": ["GitHub App pull_requests:write"],
            "agent_summary": "권한만 해결되면 같은 패치를 다시 검사할 수 있다",
            "evidence_ids": ["EV-0000000000E1"],
            "observed_at": T1,
        },
    )
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["phase"], note["origin"]) == (
        "BLOCKED",
        "external_write",
        "agent_release",
    )
    payload = note["payload"]
    assert payload["failure_conditions"] == [
        "PERMISSION_REQUIRED: 봇 PR 생성 권한이 없다",
        "부족한 조건: GitHub App pull_requests:write",
    ]
    assert payload["hypothesis"].startswith("권한만 해결되면")
    assert any("틀린 코드라는 뜻이 아니다" in item for item in payload["limitations"])
    assert any("영구 금지가 아니다" in item for item in payload["limitations"])


def test_operator_escalation_is_blocked_with_operator_note_origin(w):
    incident = w.incident(status="ESCALATED")
    work = w.work(OLD_RUN, incident, status="BLOCKED")
    w.blocked(
        OLD_RUN,
        incident,
        work,
        {
            "blocker_code": "PERMISSION_REQUIRED",
            "operator_note": "이번 run에서는 배포하지 않음",
            "requested_by": "operator:host-operator",
            "observed_at": T1,
        },
    )
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["phase"], note["origin"]) == (
        "BLOCKED",
        "operator",
        "operator_note",
    )
    assert note["payload"]["failure_conditions"] == [
        "PERMISSION_REQUIRED: 이번 run에서는 배포하지 않음"
    ]


@pytest.mark.parametrize(
    ("check", "code", "reason"),
    [
        ("PATCH_POLICY", "PATCH_PATH_DENIED", "protected_path"),
        ("R2", "REGRESSION_FAILED", "regression_tests_failed"),  # runner 회귀 FAIL
    ],
)
def test_policy_rejection_and_runner_regression_are_blocked_validation(w, check, code, reason):
    incident = w.incident(status="ESCALATED")
    work = w.work(OLD_RUN, incident, status="BLOCKED")
    failed = {"check": check, "result": code, "reason": reason}
    checks = [{"check": "B01", "result": "PASS"}, failed]
    w.proposal(OLD_RUN, incident, work, decision="REJECTED", checks=checks, reason=code)
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["phase"]) == ("BLOCKED", "validation")  # VERIFIED_FAILURE 아님
    expected = f"브로커 검사 {check} 단계에서 {code}({reason})"
    assert note["payload"]["failure_conditions"] == [expected]


def test_work_order_draft_is_handoff_not_repair(w):
    incident = w.incident(fingerprint="metric:L3-CAM-2", details={"metric": {}})
    work = w.work(OLD_RUN, incident, status="HANDED_OFF")
    action = {
        "type": "create_work_order_draft",
        "equipment_id": "L3-CAM-2",
        "symptom": "밝기 저하",
        "probable_cause": "조명 노화 가능성",
        "manual_ref_id": "MANUAL-CAM-LIGHT",
        "open_questions": [],
    }
    proposal = w.proposal(OLD_RUN, incident, work, action=action, checks=[])
    w.execution(OLD_RUN, incident, work, proposal, "DRAFT_WORK_ORDER")
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["phase"]) == ("HANDOFF", "handoff")
    assert note["payload"]["attempted_change"] == "정비 요청 초안: L3-CAM-2 / MANUAL-CAM-LIGHT"
    assert "실제 정비·복구를 확인하지 않았다" in note["payload"]["limitations"][0]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"result": pass_result(target={"image_id": IMAGE})}, "target_unconfirmed"),
        ({"result": pass_result(observation_complete=False)}, "observation_incomplete"),
        ({"result": pass_result(samples_completed=3)}, "observation_incomplete"),
        ({"deploy_image": OTHER_IMAGE}, "image_mismatch"),
        ({"deploy_status": "UNKNOWN"}, "deploy_unconfirmed"),
        ({"request": {"service": "mes-api"}}, "code_unconfirmed"),  # 배포한 merge SHA 없음
    ],
)
def test_pass_without_confirmed_identity_is_not_success(w, change, problem):
    w.verified("PASS", **change)
    w.build()
    note = only(w.notes(), phase="verification")
    assert note["outcome"] == "INCONCLUSIVE"
    assert note["payload"]["failure_conditions"] == [f"성공으로 올리지 않음: {problem}"]


def test_pass_without_deploy_execution_is_not_success(w):
    incident = w.incident()
    w.work(OLD_RUN, incident)
    w.verification(OLD_RUN, incident, None, "PASS")
    w.build()
    (note,) = w.notes()
    assert note["outcome"] == "INCONCLUSIVE"
    assert note["payload"]["failure_conditions"] == ["성공으로 올리지 않음: code_unconfirmed"]


def test_pass_linked_to_a_non_deploy_execution_is_not_success(w):
    incident, work, proposal = w.pr_opened()
    look_alike = w.execution(  # 배포가 아닌 실행에 배포 결과처럼 보이는 값이 있어도
        OLD_RUN,
        incident,
        work,
        proposal,
        "DRAFT_WORK_ORDER",
        request={"approved_merge_sha": MERGE},
        result={"target": {"image_id": IMAGE}},
        at=T2,
    )
    w.verification(OLD_RUN, incident, look_alike, "PASS", at=T3)
    w.build()
    note = only(w.notes(), phase="verification")
    assert note["outcome"] == "INCONCLUSIVE"
    assert note["payload"]["failure_conditions"] == ["성공으로 올리지 않음: code_unconfirmed"]


def test_s1b_negative_is_verified_failure_in_its_own_origin(w):
    incident = w.incident(fingerprint="verifier-negative:defect-summary-v1", status="ESCALATED")
    w.verification(
        OLD_RUN, incident, None, "FAIL", origin="human_injected_negative", result=fail_result()
    )
    w.build()
    (note,) = w.notes()
    assert (note["outcome"], note["origin"]) == ("VERIFIED_FAILURE", "human_injected_negative")
    assert note["work_id"] is None and note["payload"]["seed"] is False
    assert "S1b" in note["payload"]["attempted_change"]
    assert "에이전트 산출물 아님" in note["payload"]["attempted_change"]


def test_manual_integration_attempt_is_recorded_as_human_proposal(w):
    incident, _, _ = w.pr_opened()
    w.started(OLD_RUN, incident, OLD_ATTEMPT, "manual_integration")
    w.build()
    (note,) = w.notes()
    assert note["origin"] == "manual_integration"
    assert note["payload"]["hypothesis_by"] == "사람이 미리 작성한 제안(검증되지 않은 가설)"


# ── T-MEM-02: 한 번만·revision·철회 ─────────────────────────────


def test_same_event_is_processed_once(w):
    w.pr_opened()
    assert len(w.build()) == 1
    assert w.build() == []
    assert len(w.notes()) == 1
    with pytest.raises(sqlite3.IntegrityError):  # DDL도 같은 source_event_key를 막는다
        w.conn.execute(
            "INSERT INTO case_notes SELECT 'CASE-000000000000-R9', series_id, 9, NULL,"
            " repository_id, service, problem_fingerprint, source_run_id, source_incident_id,"
            " work_id, source_event_key, outcome, phase, origin, publish_status, observed_at,"
            " created_at, content_sha256, payload_json FROM case_notes"
        )


def test_later_result_is_the_next_revision_of_the_same_series(w):
    w.verified("PASS")
    w.build()
    first, second = w.notes()
    assert (first["outcome"], first["revision"], first["supersedes_id"]) == ("UNVERIFIED", 1, None)
    assert (second["outcome"], second["revision"], second["supersedes_id"]) == (
        "VERIFIED_SUCCESS",
        2,
        first["id"],
    )
    assert first["series_id"] == second["series_id"] and second["id"].endswith("-R2")
    assert first["publish_status"] == "PUBLISHED"  # 이전 판정은 지우지 않는다
    assert w.indexed() == {first["id"], second["id"]}  # 색인도 과거 revision을 보존한다


def test_live_snapshot_takes_latest_and_frozen_snapshot_keeps_its_revision(w):
    incident, work, proposal = w.pr_opened()
    w.build()
    frozen = w.snapshot()
    (r1,) = w.notes()
    assert frozen.members == {r1["id"]: r1["content_sha256"]}
    deploy = w.execution(
        OLD_RUN,
        incident,
        work,
        proposal,
        "DEPLOY",
        request={"service": "mes-api", "approved_merge_sha": MERGE},
        result={"target": {"image_id": IMAGE}},
        at=T2,
    )
    w.verification(OLD_RUN, incident, deploy, "FAIL", result=fail_result(), at=T3)
    w.clock.advance(60)
    w.build()
    r2 = only(w.notes(), revision=2)
    w.clock.advance(60)
    live = w.snapshot()
    assert live.members == {r2["id"]: r2["content_sha256"]}  # series별 최신 PUBLISHED
    current, current_work = w.current()
    frozen_hits = w.search(w.searcher(frozen), current, current_work).data["hits"]
    live_hits = w.search(w.searcher(live), current, current_work).data["hits"]
    assert [h["note_id"] for h in frozen_hits] == [r1["id"]]  # 미래 revision으로 바꾸지 않는다
    assert [(h["note_id"], h["outcome"]) for h in live_hits] == [(r2["id"], "VERIFIED_FAILURE")]


def test_retracted_note_is_excluded_even_inside_the_snapshot_and_reported(w):
    w.verified("FAIL", result=fail_result())
    w.build()
    frozen = w.snapshot()
    note = only(w.notes(), outcome="VERIFIED_FAILURE")
    with w.store.tx() as tx:
        changed = retract(tx, note["id"], reason="잘못된 판정 근거", principal="operator:host")
        again = retract(tx, note["id"], reason="다시", principal="operator:host")
    assert (changed["changed"], again["changed"]) == (True, False)
    retracted = w.note(note["id"])
    assert retracted["publish_status"] == "RETRACTED"
    assert retracted["payload"]["retraction"]["reason"] == "잘못된 판정 근거"
    assert retracted["content_sha256"] == note["content_sha256"]  # 내용 hash는 그대로
    assert note["id"] not in w.indexed()
    current, work = w.current()
    data = w.search(w.searcher(frozen), current, work).data
    assert note["id"] not in [h["note_id"] for h in data["hits"]]
    assert data["input_set_changed"]["retracted"] == 1
    assert w.retrievals()[-1]["results"]["input_set_changed"]["retracted"] == [note["id"]]
    events = [
        row[0]
        for row in w.conn.execute(
            "SELECT event_type FROM audit_events WHERE event_type LIKE 'CASE_NOTE_%' ORDER BY seq"
        )
    ]
    assert events.count("CASE_NOTE_RETRACTED") == 1


def test_masked_secret_keeps_the_note_draft_and_unindexed(w):
    incident = w.incident()
    work = w.work(OLD_RUN, incident)
    action = {**CREATE_PR_ACTION, "root_cause_hypothesis": "token ghp_" + "A" * 36 + " 노출"}
    proposal = w.proposal(OLD_RUN, incident, work, action=action)
    w.execution(OLD_RUN, incident, work, proposal, "CREATE_PR", result={"pr_number": 52})
    w.build()
    (note,) = w.notes()
    assert note["publish_status"] == "DRAFT"
    assert note["payload"]["publish_check"] == {"problems": ["secret_masked"]}
    assert "ghp_" not in note["payload_json"] and "[REDACTED:github_token]" in note["payload_json"]
    assert w.indexed() == set() and w.snapshot().members == {}


def test_note_text_is_not_html_escaped_and_urls_are_disabled(w):
    incident = w.incident()
    work = w.work(OLD_RUN, incident)
    hypothesis = 'row["inspector_id"] 직접 조회. 참고 https://example.com/x'
    action = {**CREATE_PR_ACTION, "root_cause_hypothesis": hypothesis}
    proposal = w.proposal(OLD_RUN, incident, work, action=action)
    w.execution(OLD_RUN, incident, work, proposal, "CREATE_PR", result={"pr_number": 53})
    w.build()
    (note,) = w.notes()
    assert note["payload"]["hypothesis"].startswith('row["inspector_id"]')
    raw = note["payload_json"]
    assert "https://example.com" not in raw and "&quot;" not in raw


# ── T-MEM-03: 다른 repo·cutoff·holdout·현재 run ─────────────────


def test_snapshot_excludes_other_repo_future_holdout_and_current_run_notes(w):
    w.pr_opened()  # 포함
    w.pr_opened(repository_id=OTHER_REPO, fingerprint="fp-other")  # 다른 repo
    current_incident, current_work = w.current()
    proposal = w.proposal(RUN, current_incident, current_work, attempt=ATTEMPT)
    w.execution(RUN, current_incident, current_work, proposal, "CREATE_PR")  # 현재 run
    holdout = w.incident(fingerprint="fp-holdout")
    holdout_work = w.work(OLD_RUN, holdout)
    action = {**CREATE_PR_ACTION, "root_cause_hypothesis": f"{TERMS[0]} 입력에서만 실패"}
    holdout_proposal = w.proposal(OLD_RUN, holdout, holdout_work, action=action)
    w.execution(OLD_RUN, holdout, holdout_work, holdout_proposal, "CREATE_PR")
    w.build()
    holdout_note = only(w.notes(), source_incident_id=holdout)
    assert holdout_note["publish_status"] == "DRAFT"  # 평가 식별자를 가렸으니 게시하지 않는다
    assert TERMS[0] not in holdout_note["payload_json"]
    w.conn.execute(  # 게시됐다고 가정해도 snapshot이 다시 거른다(원문 대조)
        "UPDATE case_notes SET publish_status = 'PUBLISHED', payload_json = ? WHERE id = ?",
        (holdout_note["payload_json"].replace("[REDACTED:eval]", TERMS[0]), holdout_note["id"]),
    )
    cutoff = to_rfc3339(w.clock.utc_now())
    w.clock.advance(3600)
    future = w.pr_opened(fingerprint="fp-future")
    w.build()
    snapshot = w.snapshot(cutoff=cutoff, repository_id=REPOSITORY_ID)
    manifest = snapshot.manifest
    kept = {n["note_id"] for n in manifest["notes"]}
    by_incident = {n["source_incident_id"]: n["id"] for n in w.notes()}
    old_s1 = {
        n["id"]
        for n in w.notes()
        if (n["problem_fingerprint"], n["source_run_id"]) == (FP, OLD_RUN)
    }
    assert kept == old_s1
    assert by_incident[future[0]] not in kept
    assert manifest["counts"]["excluded"] == {
        "not_published": 0,
        "target_run": 1,
        "after_cutoff": 1,
        "other_repository": 1,
        "eval_identifier": 1,
        "origin_requires_selection": 0,
        "not_selected": 0,
    }
    assert manifest["cutoff"] == cutoff and manifest["scope"] == {"repository_id": REPOSITORY_ID}
    assert manifest["search"]["tokenizer"] == "unicode61"
    assert set(manifest["notes"][0]) == set(memory_snapshot.NOTE_FIELDS)
    assert "summary" not in json.dumps(manifest)  # 노트 본문은 manifest에 넣지 않는다


def test_note_with_a_later_added_eval_identifier_is_not_shown(w):
    """snapshot을 만든 뒤 평가 식별자가 늘었으면(새 holdout 로트) 검색 때 다시 거른다."""
    new_lot = "L3-NEWLOT-999"
    incident = w.incident()
    work = w.work(OLD_RUN, incident)
    action = {**CREATE_PR_ACTION, "root_cause_hypothesis": f"{new_lot} 입력에서 KeyError"}
    proposal = w.proposal(OLD_RUN, incident, work, action=action)
    w.execution(OLD_RUN, incident, work, proposal, "CREATE_PR")
    w.build()
    snapshot = w.snapshot()  # 그때는 평가 식별자가 아니었다
    assert len(snapshot.members) == 1
    current, current_work = w.current()
    shown = w.search(w.searcher(snapshot), current, current_work).data["hits"]
    assert len(shown) == 1
    later = w.searcher(snapshot, terms=(*TERMS, new_lot))
    assert w.search(later, current, current_work).data["hits"] == []


def test_search_filters_scope_before_choosing_top_k(w):
    for index in range(6):  # 더 잘 맞는 다른 repo·다른 서비스 노트
        repo, service = (OTHER_REPO, "mes-api") if index % 2 else (REPOSITORY_ID, "vision-api")
        w.verified("FAIL", result=fail_result(), repository_id=repo, service=service)
    w.pr_opened(fingerprint="fp-other-shape")  # 같은 scope, 약하게 맞음
    w.build()
    snapshot = w.snapshot()
    current, work = w.current(fingerprint="fp-new")
    data = w.search(w.searcher(snapshot), current, work, q="KeyError inspector_id", limit=1).data
    assert data["status"] == "OK" and len(data["hits"]) == 1
    hit = only(w.notes(), id=data["hits"][0]["note_id"])
    assert (hit["repository_id"], hit["service"]) == (REPOSITORY_ID, "mes-api")


def test_cold_start_is_disabled_and_recorded(w):
    w.verified("PASS")
    w.build()
    current, work = w.current()
    result = w.search(w.searcher(w.snapshot(), mode="cold_start"), current, work)
    assert result.data == {
        "retrieval_id": result.data["retrieval_id"],
        "mode": "cold_start",
        "snapshot_id": None,
        "engine": "none",
        "status": "DISABLED",
        "hits": [],
    }
    assert result.evidence_ids == []
    (record,) = w.retrievals()
    assert (record["mode"], record["status"], record["work_id"]) == ("cold_start", "DISABLED", work)
    assert w.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


# ── T-MEM-04: exact·keyword·no-hit·unavailable ─────────────────


def test_exact_and_keyword_hits_return_citable_projection_ids(w):
    w.verified("PASS")  # 같은 fingerprint
    w.verified("FAIL", result=fail_result(), fingerprint="fp-keyerror-older")  # 키워드만
    w.build()
    snapshot = w.snapshot()
    current, work = w.current()
    result = w.search(w.searcher(snapshot), current, work)
    data = result.data
    assert (data["status"], data["engine"], data["snapshot_id"]) == (
        "OK",
        "sqlite_fts5",
        snapshot.snapshot_id,
    )
    matches = {hit["outcome"]: hit["match"] for hit in data["hits"]}
    assert matches == {"VERIFIED_SUCCESS": "exact+keyword", "VERIFIED_FAILURE": "keyword"}
    assert result.evidence_ids == [hit["evidence_id"] for hit in data["hits"]]
    for hit in data["hits"]:
        row = w.conn.execute(
            "SELECT * FROM evidence WHERE id = ?", (hit["evidence_id"],)
        ).fetchone()
        assert (row["run_id"], row["incident_id"], row["kind"]) == (
            RUN,
            current,
            "history_projection",
        )
        projection = json.loads(row["payload_json"])
        note = w.note(hit["note_id"])
        assert projection["note_id"] == note["id"]
        assert projection["content_sha256"] == note["content_sha256"]
        assert projection["source_event_key"] == note["source_event_key"]
        assert projection["snapshot_id"] == snapshot.snapshot_id
        assert hit["source_ref"]["run_id"] == OLD_RUN
    (record,) = w.retrievals()
    assert (record["mode"], record["snapshot_id"], record["status"]) == (
        "memory_assisted",
        snapshot.snapshot_id,
        "OK",
    )
    assert record["query"]["fingerprint"] == FP and record["query"]["tokens"]
    assert [pair[0] for pair in record["results"]["hits"]] == [h["note_id"] for h in data["hits"]]


def test_repeated_search_reuses_the_same_projection(w):
    w.verified("PASS")
    w.build()
    searcher = w.searcher(w.snapshot())
    current, work = w.current()
    first = w.search(searcher, current, work).evidence_ids
    second = w.search(searcher, current, work).evidence_ids
    assert first == second
    assert w.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    assert len(w.retrievals()) == 2  # 검색 기록은 매번 남는다


def test_no_hit_is_recorded_as_no_hit(w):
    w.verified("PASS")
    w.build()
    current, work = w.current(fingerprint="fp-unrelated", details={})
    data = w.search(w.searcher(w.snapshot()), current, work, q="온도센서 drift").data
    assert (data["status"], data["hits"]) == ("NO_HIT", [])
    assert w.retrievals()[-1]["status"] == "NO_HIT"


def test_damaged_index_is_unavailable_not_no_hit_and_rebuild_recovers(w):
    w.verified("PASS")
    w.build()
    searcher = w.searcher(w.snapshot())
    current, work = w.current(fingerprint="fp-keyword-only")
    w.conn.execute("UPDATE case_search_data SET block = X'00' WHERE id != 10")  # 색인 손상
    data = w.search(searcher, current, work, q="KeyError").data
    assert (data["status"], data["reason"], data["hits"]) == (
        "UNAVAILABLE",
        "db_error:DatabaseError",
        [],
    )
    assert w.retrievals()[-1]["status"] == "UNAVAILABLE"
    with w.store.tx() as tx:
        rebuilt = rebuild_index(tx)
    assert rebuilt == {"engine": "sqlite_fts5", "indexed": 2}
    assert w.search(searcher, current, work, q="KeyError").data["status"] == "OK"


def test_missing_index_is_unavailable_until_rebuilt(w):
    w.verified("PASS")
    w.build()
    searcher = w.searcher(w.snapshot())
    current, work = w.current(fingerprint="fp-keyword-only")
    w.conn.execute("DROP TABLE case_search")
    data = w.search(searcher, current, work, q="KeyError").data
    assert (data["status"], data["reason"]) == ("UNAVAILABLE", "index_missing")
    with w.store.tx() as tx:
        assert rebuild_index(tx)["indexed"] == 2
    assert w.search(searcher, current, work, q="KeyError").data["status"] == "OK"


def test_memory_assisted_without_a_valid_snapshot_is_unavailable(w, tmp_path):
    w.verified("PASS")
    w.build()
    current, work = w.current()
    data = w.search(w.searcher(None, snapshot_error="snapshot_missing"), current, work).data
    assert (data["status"], data["reason"], data["snapshot_id"]) == (
        "UNAVAILABLE",
        "snapshot_missing",
        None,
    )
    path = memory_snapshot.write_snapshot(w.snapshot(), tmp_path)
    assert memory_snapshot.load_snapshot(path).snapshot_id == path.stem
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["cutoff"] = "2099-01-01T00:00:00.000000Z"  # 만든 뒤 바꿨다
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(memory_snapshot.SnapshotError, match="ID와 다르다"):
        memory_snapshot.load_snapshot(path)


def test_snapshot_file_is_never_overwritten(w, tmp_path):
    snapshot = w.snapshot()
    path = memory_snapshot.write_snapshot(snapshot, tmp_path)
    assert memory_snapshot.write_snapshot(snapshot, tmp_path) == path  # 같은 내용이면 그대로
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(memory_snapshot.SnapshotError, match="다른 manifest"):
        memory_snapshot.write_snapshot(snapshot, tmp_path)
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_snapshot_cutoff_is_compared_as_a_time(w):
    w.pr_opened()
    w.build()  # created_at 2026-09-27T00:00:00.000000Z
    assert w.snapshot(cutoff="2026-09-27T00:00:00Z").members  # 같은 시각은 포함
    assert not w.snapshot(cutoff="2026-09-26T23:59:59Z").members
    assert w.snapshot(cutoff="2026-09-27T09:00:00+09:00").members  # 시간대 변환
    with pytest.raises(memory_snapshot.SnapshotError):
        w.snapshot(cutoff="2026-09-27 00:00")


# ── G9: 사람이 고른 노트만 (PR #55 리뷰) ─────────────────────────


def _three_origins(w) -> dict[str, str]:
    """사람 제안·에이전트·S1b 주입 노트 하나씩(모두 이전 run). origin → note ID."""
    manual, _, _ = w.pr_opened()
    w.started(OLD_RUN, manual, OLD_ATTEMPT, "manual_integration")
    w.pr_opened(fingerprint="fp-agent-other")
    negative = w.incident(fingerprint="verifier-negative:defect-summary-v1", status="ESCALATED")
    w.verification(
        OLD_RUN, negative, None, "FAIL", origin="human_injected_negative", result=fail_result()
    )
    w.build()
    return {note["origin"]: note["id"] for note in w.notes()}


def test_rule_only_snapshot_leaves_out_human_proposals_and_injected_negatives(w):
    """사람 선택 없이는 사람 제안·S1b 노트가 들어가지 않는다(W13 카드 금지 항목·G9)."""
    ids = _three_origins(w)
    snapshot = w.snapshot()
    assert set(snapshot.members) == {ids["agent_release"]}
    selection = snapshot.manifest["selection"]
    assert (selection["mode"], selection["selected_note_ids"]) == ("rule_only", None)
    assert snapshot.manifest["counts"]["excluded"]["origin_requires_selection"] == 2


def test_human_selected_snapshot_keeps_only_the_chosen_notes_with_their_origin(w):
    ids = _three_origins(w)
    chosen = [ids["manual_integration"], ids["agent_release"]]
    snapshot = w.snapshot(selected=chosen)
    assert set(snapshot.members) == set(chosen)
    origins = {note["note_id"]: note["origin"] for note in snapshot.manifest["notes"]}
    assert origins[ids["manual_integration"]] == "manual_integration"  # origin 유지
    selection = snapshot.manifest["selection"]
    assert (selection["mode"], selection["selected_note_ids"]) == ("human_selected", sorted(chosen))
    assert snapshot.manifest["counts"]["excluded"]["not_selected"] == 1  # 고르지 않은 S1b


def test_chosen_notes_still_follow_the_snapshot_rules(w):
    w.verified("PASS")  # 같은 series R1(PR) → R2(검증)
    w.pr_opened(run=RUN, fingerprint="fp-current-run")  # 대상 run의 노트
    w.build()
    notes = {(n["source_run_id"], n["revision"]): n["id"] for n in w.notes()}
    old_r1, current = notes[(OLD_RUN, 1)], notes[(RUN, 1)]
    with pytest.raises(memory_snapshot.SnapshotSelectionError) as raised:
        w.snapshot(selected=[old_r1, current, "CASE-000000000000-R1"])
    assert raised.value.problems == {
        old_r1: "not_latest_revision",
        current: "target_run",
        "CASE-000000000000-R1": "not_found",
    }
    assert w.snapshot(selected=[notes[(OLD_RUN, 2)]]).members  # 최신 revision은 된다


def test_cli_memory_snapshot_lists_candidates_and_requires_a_selection(w, tmp_path, capsys):
    ids = _three_origins(w)
    out_dir = tmp_path / "snapshots"
    common = [
        "memory-snapshot",
        "--run-id",
        RUN,
        "--output-dir",
        str(out_dir),
        "--db",
        str(w.store.path),
        "--env-file",
        str(tmp_path / "none.env"),
    ]
    assert cli.main(common) == 2  # 고르지 않으면 쓰지 않는다
    assert "G9" in capsys.readouterr().err
    assert cli.main([*common, "--list"]) == 0
    listed = json.loads(capsys.readouterr().out)["candidates"]
    assert {c["note_id"]: c["requires_selection"] for c in listed} == {
        ids["manual_integration"]: True,
        ids["agent_release"]: False,
        ids["human_injected_negative"]: True,
    }
    assert cli.main([*common, "--notes", "CASE-000000000000-R1"]) == 2
    assert "not_found" in capsys.readouterr().err
    assert not out_dir.exists()  # 목록·거부는 파일을 쓰지 않는다
    events = w.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'MEMORY_SNAPSHOT_CREATED'"
    ).fetchone()[0]
    assert events == 0
    notes_file = tmp_path / "chosen.txt"
    notes_file.write_text(f"# G9 선택\n{ids['manual_integration']}\n", encoding="utf-8")
    assert cli.main([*common, "--notes-file", str(notes_file)]) == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    loaded = memory_snapshot.load_snapshot(path)
    assert set(loaded.members) == {ids["manual_integration"]}
    assert loaded.manifest["selection"]["mode"] == "human_selected"


# ── T-MEM-05: 실패 조건·다른 source ─────────────────────────────


def test_past_failure_shows_its_conditions_and_stale_source_warning(w):
    w.verified("FAIL", result=fail_result())
    w.build()
    w.deploy_observed(base_sha="9" * 40)  # 지금 run의 source는 기록과 다르다
    current, work = w.current()
    data = w.search(w.searcher(w.snapshot()), current, work).data
    failure = only(data["hits"], outcome="VERIFIED_FAILURE")
    assert (
        "held-out case: exact_total_defects 불일치(total_defects)"
        in (failure["failure_conditions"])
    )
    warning = failure["applicability_warning"]
    assert "source 다름(기록 2222222, 현재 9999999)" in warning
    assert warning.endswith(case_search.GENERIC_WARNING)
    assert data["notice"] == case_search.TRUST_NOTICE


def test_same_source_and_contract_has_no_warning_other_contract_does(w):
    w.verified("PASS")
    w.build()
    w.deploy_observed(base_sha=MERGE)
    current, work = w.current()
    snapshot = w.snapshot()
    same = w.search(w.searcher(snapshot), current, work).data["hits"]
    success = only(same, outcome="VERIFIED_SUCCESS")
    assert success["applicability_warning"] is None
    other = w.search(w.searcher(snapshot, contract_sha256="0" * 64), current, work).data["hits"]
    warning = only(other, outcome="VERIFIED_SUCCESS")["applicability_warning"]
    assert "계약 파일 hash 다름" in warning


def test_a_related_failure_is_included_below_the_top_k(w):
    for _ in range(3):
        w.verified("PASS")  # exact 성공 3건이 상위를 채운다
    w.verified("FAIL", result=fail_result(), fingerprint="fp-related")  # 토큰 여러 개 겹침
    w.build()
    current, work = w.current()
    hits = w.search(w.searcher(w.snapshot()), current, work, limit=2).data["hits"]
    assert [hit["outcome"] for hit in hits] == ["VERIFIED_SUCCESS", "VERIFIED_FAILURE"]
    assert hits[1]["match"] == "keyword"


def candidate(outcome, *, exact=False, overlap=0, rank=0.0, note="N", at=T0):
    row = {"id": note, "outcome": outcome, "observed_at": at}
    return case_search.Candidate(row, {"summary": note}, exact=exact, overlap=overlap, rank=rank)


def test_choose_adds_only_a_related_failure_and_never_empties_the_top_hit():
    successes = [  # 같은 조건이면 최근 관찰이 앞
        candidate("VERIFIED_SUCCESS", exact=True, note=f"S{i}", at=at)
        for i, at in enumerate((T3, T2, T1))
    ]
    related = candidate("BLOCKED", overlap=2, rank=-1.0, note="F-related")
    unrelated = candidate("VERIFIED_FAILURE", overlap=1, rank=-5.0, note="F-unrelated")
    exact_failure = candidate("INCONCLUSIVE", exact=True, rank=0.0, note="F-exact")
    choose = case_search.CaseSearch._choose
    assert [c.row["id"] for c in choose([*successes, related], 2)] == ["S0", "F-related"]
    assert [c.row["id"] for c in choose([*successes, unrelated], 2)] == ["S0", "S1"]
    assert [c.row["id"] for c in choose([*successes, related], 1)] == ["S0"]  # 1건이면 바꾸지 않음
    assert "F-exact" in [c.row["id"] for c in choose([*successes, exact_failure], 3)]
    hidden = candidate("VERIFIED_FAILURE", exact=True, note="F-eval")
    hidden.usable = False  # 평가 식별자가 든 노트
    assert "F-eval" not in [c.row["id"] for c in choose([hidden, *successes], 5)]


# ── FTS 질의 안전·fallback·N13 ─────────────────────────────────


@pytest.mark.parametrize(
    "q",
    ['KeyError" OR "x', "inspector_id*", "NEAR(KeyError inspector_id)", "OR AND NOT", '"', "*"],
)
def test_fts_operators_in_the_query_are_not_interpreted(w, q):
    w.verified("PASS")
    w.build()
    current, work = w.current(fingerprint="fp-keyword-only")
    data = w.search(w.searcher(w.snapshot()), current, work, q=q).data
    assert data["status"] in ("OK", "NO_HIT")  # 문법 오류(UNAVAILABLE)가 나지 않는다
    record = w.retrievals()[-1]["query"]
    assert record["q"] == q
    tokens = query_tokens(q, 16)
    assert record["tokens"] == tokens
    assert record["fts_query"] == (fts_query(tokens) if tokens else None)
    for token in tokens:
        assert f'"{token}"' in record["fts_query"]


def test_query_tokens_are_bounded_and_deduplicated():
    text = " ".join(f"tok{i}" for i in range(40)) + " tok1 KeyError"
    assert query_tokens(text, 16) == [f"tok{i}" for i in range(16)]
    assert query_tokens("a a b", 16) == ["a", "b"]
    assert fts_query(['a"b', "미지정"]) == '"a""b" OR "미지정"'


def test_keyword_fallback_without_fts5(fake_clock, monkeypatch, tmp_path):
    for module in (store_module, case_builder, case_search):
        monkeypatch.setattr(module, "fts5_available", lambda *args: False)
    fresh = store_module.Store(tmp_path / "nofts.db", fake_clock, backoff_seconds=0)
    assert fresh.migrate() == [1, 2]  # 적용 기록만 남기고 색인은 만들지 않는다
    connection = fresh.connect()
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert "case_search" not in tables
        insert_run(connection, OLD_RUN, active=0)
        insert_run(connection, RUN)
        world = World(fresh, connection, fake_clock)
        world.verified("PASS")
        world.build()
        assert {n["publish_status"] for n in world.notes()} == {"PUBLISHED"}
        current, work = world.current(fingerprint="fp-keyword-only")
        data = world.search(world.searcher(world.snapshot()), current, work, q="KeyError").data
        assert (data["engine"], data["status"]) == ("keyword_fallback", "OK")
        assert {hit["match"] for hit in data["hits"]} == {"keyword"}
        with fresh.tx() as tx:
            assert rebuild_index(tx) == {"engine": "keyword_fallback", "indexed": 0}
        assert world.snapshot().manifest["search"]["engine_at_creation"] == "keyword_fallback"
    finally:
        connection.close()


def test_configured_keyword_fallback_is_reported_as_such(w):
    w.verified("PASS")
    w.build()
    current, work = w.current(fingerprint="fp-keyword-only")
    searcher = w.searcher(w.snapshot(), engine="keyword_fallback")
    data = w.search(searcher, current, work, q="inspector_id").data
    assert (data["engine"], data["status"]) == ("keyword_fallback", "OK")


N13_QUERIES = ("KeyError", "inspector_id", "미지정", "미지정으로", "검사자 미지정")


def test_n13_korean_and_error_tokens_are_searched_and_recorded(w):
    w.verified("FAIL", result=fail_result())
    w.build()
    snapshot = w.snapshot()
    searcher = w.searcher(snapshot)
    current, work = w.current(fingerprint="fp-n13")
    rows = []
    for q in N13_QUERIES:
        data = w.search(searcher, current, work, q=q).data
        rows.append((q, data["status"], [(h["note_id"], h["match"]) for h in data["hits"]]))
    by_query = {q: status for q, status, _ in rows}
    assert by_query["KeyError"] == by_query["inspector_id"] == by_query["미지정"] == "OK"
    # unicode61은 한국어 조사를 떼지 않는다: '미지정으로'는 '미지정' 토큰과 맞지 않는다
    assert by_query["미지정으로"] == "NO_HIT"
    if os.environ.get("LINEMEDIC_RECORD_EVIDENCE") == "1":
        _record_n13(rows, snapshot)


def _record_n13(rows, snapshot) -> None:
    lines = [f"| `{q}` | {status} | {', '.join(f'{n}({m})' for n, m in hits) or '-'} |"
             for q, status, hits in rows]  # fmt: skip
    EVIDENCE.write_text(
        "# N13 사례 검색: SQLite FTS5·한국어/오류 토큰·ACL/snapshot 필터\n\n"
        f"- 실행 시각(UTC): {to_rfc3339(SystemClock().utc_now())}\n"
        f"- 실행 환경: 로컬 개발 Mac({platform.platform()}), Python SQLite"
        f" {sqlite3.sqlite_version}. 데모 호스트(G1) 아님\n"
        "- 명령: `LINEMEDIC_RECORD_EVIDENCE=1 make test`"
        " (`linemedic/tests/integration/test_case_memory.py`)\n"
        f"- 엔진: `sqlite_fts5`, tokenizer `unicode61`, 질의 정규화 `d54-v1`, snapshot"
        f" `{snapshot.snapshot_id}`(합성 노트 {len(snapshot.manifest['notes'])}개)\n"
        "- 결과: PASS (FTS5 사용 가능, 아래 질의가 모두 오류 없이 실행됨)\n\n"
        "| 질의 | status | 결과(note ID(match)) |\n|---|---|---|\n"
        + "\n".join(lines)
        + "\n\n- ACL·snapshot 필터: 다른 repo·다른 서비스·cutoff 뒤·현재 run·평가 식별자 노트가"
        " 결과에 나오지 않는 것은 같은 파일의 T-MEM-03 테스트가 확인한다\n"
        "- 한계: 합성 노트 소수로 토큰 일치 여부만 봤다. unicode61은 한국어 형태소·조사를"
        " 처리하지 않는다(`미지정으로` ≠ `미지정`). 검색 품질·유사 사례 정확도·RAG 효과는"
        " 측정하지 않았다\n",
        encoding="utf-8",
    )


# ── API·CLI ───────────────────────────────────────────────────

READER_TOKEN = "test-reader-token-" + "f" * 32


def search_api(w, searcher=None, **ctx):
    api = make_api(w.store, w.conn, case_search=searcher, **ctx)
    api.tokens.register_operator(READER_TOKEN, OperatorPrincipal("reader", frozenset({"read"})))
    return api


def test_tools_search_cases_returns_projection_ids_in_the_envelope(w):
    w.verified("PASS")
    w.build()
    current, work = w.current()
    api = search_api(w, w.searcher(w.snapshot()))
    agent = api.agent(AgentPrincipal(RUN, current, work, ATTEMPT))
    path = f"/tools/incidents/{current}/cases/search"
    response = api.client.get(path, headers=agent, params={"q": "KeyError", "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "OK"
    assert body["evidence_ids"] == [hit["evidence_id"] for hit in body["data"]["hits"]]
    for params in ({"limit": 0}, {"limit": 6}, {"q": "q" * 201}, {"top_k": 1}):
        assert api.client.get(path, headers=agent, params=params).status_code == 422
    other_incident, _ = w.pr_opened(RUN, fingerprint="fp-other-current", status="ESCALATED")[:2]
    other = f"/tools/incidents/{other_incident}/cases/search"
    assert api.client.get(other, headers=agent).status_code == 404
    assert api.client.get(path, headers=api.operator).status_code == 403
    assert len(w.retrievals()) == 1  # 거절된 요청은 검색 기록을 남기지 않는다


def test_tools_search_cases_after_the_attempt_ended_is_not_found(w):
    w.verified("PASS")
    w.build()
    current, work = w.current()
    api = search_api(w, w.searcher(w.snapshot()))
    agent = api.agent(AgentPrincipal(RUN, current, work, ATTEMPT))
    w.conn.execute("UPDATE work_items SET status = 'BLOCKED' WHERE id = ?", (work,))
    response = api.client.get(f"/tools/incidents/{current}/cases/search", headers=agent)
    assert response.status_code == 404
    assert w.retrievals() == []
    assert w.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_tools_search_cases_without_the_service_is_dependency_unavailable(w):
    current, work = w.current()
    api = search_api(w)
    agent = api.agent(AgentPrincipal(RUN, current, work, ATTEMPT))
    response = api.client.get(f"/tools/incidents/{current}/cases/search", headers=agent)
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "DEPENDENCY_UNAVAILABLE",
    )


def rebuild(api, body=None, key="rebuild-1", headers=None):
    return api.client.post(
        "/ops/cases/rebuild-index",
        json=body or {"schema_version": "linemedic.v4", "run_id": RUN},
        headers={**(headers or api.operator), "Idempotency-Key": key},
    )


def test_ops_rebuild_index_is_maintenance_only_idempotent_and_keeps_outcomes(w):
    w.verified("FAIL", result=fail_result())
    w.build()
    before = [(n["id"], n["outcome"], n["publish_status"]) for n in w.notes()]
    w.conn.execute("DELETE FROM case_search")
    api = search_api(w)
    reader = {"Authorization": f"Bearer {READER_TOKEN}"}
    assert rebuild(api, headers=reader).status_code == 403
    first = rebuild(api)
    assert (first.status_code, first.json()["data"]) == (
        200,
        {"engine": "sqlite_fts5", "indexed": 2},
    )
    assert w.indexed() == {note_id for note_id, _, _ in before}
    assert rebuild(api).json() == first.json()  # 같은 키 재전송은 저장된 응답
    other_run = {"schema_version": "linemedic.v4", "run_id": OLD_RUN}
    conflict = rebuild(api, other_run)
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "STATE_CONFLICT")
    assert rebuild(api, key="rebuild-2").status_code == 200
    assert [(n["id"], n["outcome"], n["publish_status"]) for n in w.notes()] == before
    rebuilt = w.conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'CASE_INDEX_REBUILT'"
    ).fetchone()[0]
    assert rebuilt == 2


def test_ops_case_view_shows_revisions_source_and_level(w):
    w.verified("PASS")
    w.build()
    first, second = w.notes()
    api = search_api(w)
    reader = {"Authorization": f"Bearer {READER_TOKEN}"}
    response = api.client.get(f"/ops/cases/{first['id']}", headers=reader)
    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["outcome"], data["revision"], data["latest_revision"]) == ("UNVERIFIED", 1, 2)
    assert [item["id"] for item in data["series"]] == [first["id"], second["id"]]
    assert data["payload"]["source_event_key"] == first["source_event_key"]
    assert "payload_json" not in data
    for bad in ("CASE-XYZ-R1", "CASE-000000000000-R1", f"{first['id']}0x"):
        assert api.client.get(f"/ops/cases/{bad}", headers=reader).status_code == 404


def test_cli_memory_snapshot_writes_an_immutable_manifest_and_audits(w, tmp_path, capsys):
    w.verified("PASS")
    w.build()
    common = ["--db", str(w.store.path), "--env-file", str(tmp_path / "none.env")]
    out_dir = tmp_path / "snapshots"
    latest = only(w.notes(), outcome="VERIFIED_SUCCESS")["id"]  # G9: 사람이 고른 노트
    argv = ["memory-snapshot", "--run-id", RUN, "--output-dir", str(out_dir), *common]
    argv += ["--notes", latest]
    assert cli.main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    path = Path(printed["path"])
    assert path.parent == out_dir and path.stem == printed["snapshot_id"]
    assert printed["notes"] == 1
    loaded = memory_snapshot.load_snapshot(path)
    assert loaded.manifest["target_run_id"] == RUN
    audit_row = w.conn.execute(
        "SELECT payload_json FROM audit_events WHERE event_type = 'MEMORY_SNAPSHOT_CREATED'"
    ).fetchone()
    assert json.loads(audit_row[0])["snapshot_id"] == printed["snapshot_id"]
    unknown = ["memory-snapshot", "--run-id", "r-20260101-000000-dead", *common, "--notes", latest]
    assert cli.main([*unknown, "--output-dir", str(out_dir)]) == 2
    bad_cutoff = [*argv, "--cutoff", "yesterday"]
    assert cli.main(bad_cutoff) == 2


def test_cli_rebuild_case_index_posts_to_the_ops_api(w, tmp_path, monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"engine": "sqlite_fts5", "indexed": 0}})

    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args = argparse.Namespace(
        run_id=None,
        db=w.store.path,
        config=cli.DEFAULT_CONFIG_PATH,
        env_file=tmp_path / "none.env",
    )
    assert cli._rebuild_case_index(args, transport=httpx.MockTransport(handler)) == 0
    (post,) = seen
    assert (post.method, post.url.path) == ("POST", "/ops/cases/rebuild-index")
    assert json.loads(post.content) == {"schema_version": "linemedic.v4", "run_id": RUN}
    assert post.headers["idempotency-key"].startswith(f"rebuild-case-index:{RUN}:")
    assert post.headers["authorization"] == f"Bearer {OPERATOR_TOKEN}"
    missing = argparse.Namespace(**{**vars(args), "db": tmp_path / "missing.db"})
    assert cli._rebuild_case_index(missing, transport=httpx.MockTransport(handler)) == 2
