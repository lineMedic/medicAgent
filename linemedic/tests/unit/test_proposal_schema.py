"""W09 단위 테스트: 제안 schema·JSON Schema 파일·정비 초안·진행 불가 보고 payload.

- T-V4-01: `linemedic.v2` 거부, 사용하지 않는 필드(`actions`, `confidence`, 역할·모델 필드) 거부
- category ↔ action 표 18조합(INV-05), 근거 개수·중복·형식
- 정비 초안에 자유 절차·제어값·URL·수신자 필드 거부, create_pr에 명령·branch·URL 필드 거부
- `linemedic/contracts/api/*.schema.json`이 모델에서 생성한 내용과 같고,
  if/then 규칙이 모델 검사와 일치
- 초안은 spec 06 §7 필드만(`delivery_status: not_sent`, `review_required: true`) + 승인 문구
"""

import copy
import json
from itertools import product
from typing import Any

import pytest
from pydantic import ValidationError

from linemedic.control_plane.broker.proposals import (
    Proposal,
    ProposalReceipt,
    ProposalStatus,
    WorkOrderDraftAction,
)
from linemedic.control_plane.broker.work_order import build_draft
from linemedic.control_plane.codes import BLOCKER_CODES, CATEGORY_ACTIONS
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.scripts import api_schema

RUN = "r-20260927-020000-abcd"
IDS = {
    "run_id": RUN,
    "incident_id": "INC-00000000000A",
    "work_id": "WORK-00000000000A",
    "attempt_id": "ATT-00000000000A",
}
EV = ["EV-000000000001", "EV-000000000002"]
ACTIONS: dict[str, dict[str, Any]] = {
    "create_pr": {
        "type": "create_pr",
        "base_sha": "1" * 40,
        "root_cause_hypothesis": "필수라고 가정한 필드를 직접 조회합니다.",
        "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x\n+y\n",
        "new_test_path": "tests/repro/test_missing_inspector.py",
        "local_test_observation": {"before": "failed", "after": "passed"},
    },
    "create_work_order_draft": {
        "type": "create_work_order_draft",
        "equipment_id": "L3-CAM-2",
        "symptom": "다른 카메라 대비 밝기와 판정 신뢰도가 낮음",
        "probable_cause": "원인은 미확정",
        "manual_ref_id": "MANUAL-L3-VISION-4.2",
        "open_questions": ["현장 담당자의 승인된 절차에 따른 점검이 필요함"],
    },
    "escalate": {
        "type": "escalate",
        "reason": "INSUFFICIENT_EVIDENCE",
        "open_questions": ["현재 의존 서비스 상태를 확인하지 못함"],
        "missing_requirements": ["현재 의존 서비스의 상태를 확인할 자료"],
        "retry_condition": "담당자가 자료를 제공하고 새 generation을 승인한 뒤",
    },
}
CATEGORY_OF = {
    "create_pr": "code_bug",
    "create_work_order_draft": "equipment",
    "escalate": "unknown",
}


def body(action_type: str = "create_work_order_draft", **overrides: Any) -> dict[str, Any]:
    data = {
        "schema_version": "linemedic.v4",
        **IDS,
        "category": CATEGORY_OF[action_type],
        "summary": "관찰한 현상 요약",
        "evidence_ids": list(EV),
        "action": copy.deepcopy(ACTIONS[action_type]),
    }
    data.update(overrides)
    return data


def rejects(data: dict[str, Any]) -> bool:
    try:
        Proposal.model_validate(data)
    except ValidationError:
        return True
    return False


# ── 공통 필드 ─────────────────────────────────────────────────


@pytest.mark.parametrize("action_type", sorted(ACTIONS))
def test_spec_examples_validate(action_type):
    proposal = Proposal.model_validate(body(action_type))
    assert proposal.action.type == action_type


def test_schema_version_v2_is_rejected():  # T-V4-01
    assert rejects(body(schema_version="linemedic.v2"))
    assert rejects({k: v for k, v in body().items() if k != "schema_version"})


@pytest.mark.parametrize(
    "field", ["actions", "confidence", "actor", "role", "status", "model", "policy_version"]
)
def test_unused_v4_fields_are_rejected(field):
    assert rejects(body(**{field: "x"}))


def test_actions_array_instead_of_one_action_is_rejected():
    data = body()
    data["actions"] = [data.pop("action")]
    assert rejects(data)


@pytest.mark.parametrize(
    "change",
    [
        {"summary": ""},
        {"summary": "가" * 2001},
        {"evidence_ids": "EV-000000000001"},  # 배열 자리의 문자열
        {"action": "create_work_order_draft"},  # 객체 자리의 문자열
        {"category": "hardware"},
        {"run_id": "run-001"},
        {"incident_id": "INC-001"},
        {"attempt_id": 1},
    ],
    ids=[
        "empty_summary",
        "long_summary",
        "evidence_string",
        "action_string",
        "bad_category",
        "bad_run_id",
        "bad_incident_id",
        "attempt_int",
    ],
)
def test_types_lengths_and_formats_are_strict(change):
    assert rejects(body(**change))


# ── category ↔ action, 근거 ───────────────────────────────────


@pytest.mark.parametrize(
    ("category", "action_type"),
    list(product(sorted(CATEGORY_ACTIONS), sorted(ACTIONS))),
)
def test_category_action_matrix(category, action_type):  # INV-05
    data = body(action_type, category=category)
    assert rejects(data) is (action_type not in CATEGORY_ACTIONS[category])


def test_card_examples_of_category_action_and_evidence():
    assert rejects(body("create_pr", category="equipment"))
    assert rejects(body("create_work_order_draft", category="unknown"))
    assert rejects(body("create_pr", evidence_ids=[]))
    assert rejects(body("create_work_order_draft", evidence_ids=[]))
    assert not rejects(body("escalate", evidence_ids=[]))


@pytest.mark.parametrize(
    "evidence_ids",
    [
        [EV[0], EV[0]],
        [f"EV-{i:012X}" for i in range(21)],
        ["EV-001"],
        ["ev-000000000001"],
        ["INC-000000000001"],
    ],
    ids=["duplicate", "over_20", "short", "lowercase", "other_prefix"],
)
def test_evidence_ids_rules(evidence_ids):
    assert rejects(body("escalate", evidence_ids=evidence_ids))


def test_twenty_evidence_ids_are_allowed():
    assert not rejects(body(evidence_ids=[f"EV-{i:012X}" for i in range(20)]))


# ── 액션별 필드 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    ["procedure", "control_values", "url", "recipient", "shell", "command", "work_instruction"],
)
def test_draft_rejects_free_procedure_control_url_and_recipient_fields(field):
    data = body()
    data["action"][field] = "x"
    assert rejects(data)


@pytest.mark.parametrize(
    "change",
    [
        {"equipment_id": "l3-cam-2"},
        {"equipment_id": "../L3-CAM-2"},
        {"manual_ref_id": "https://example.invalid/manual"},
        {"manual_ref_id": "MANUAL-../etc"},
        {"symptom": "가" * 501},
        {"open_questions": ["q"] * 11},
    ],
    ids=["lower_equipment", "path_equipment", "url_manual", "path_manual", "long", "many_q"],
)
def test_draft_field_formats(change):
    data = body()
    data["action"].update(change)
    assert rejects(data)


@pytest.mark.parametrize(
    "field", ["pytest_command", "branch", "github_url", "environment", "base_ref"]
)
def test_create_pr_rejects_client_chosen_command_branch_and_url(field):
    data = body("create_pr")
    data["action"][field] = "x"
    assert rejects(data)


@pytest.mark.parametrize(
    "change",
    [
        {"base_sha": "1" * 7},
        {"base_sha": "G" * 40},
        {"new_test_path": "tests/test_missing.py"},
        {"new_test_path": "tests/repro/../test_x.py"},
        {"new_test_path": "tests/repro/test_x.txt"},
        {"diff": ""},
        {"local_test_observation": {"before": "failed", "after": "passed", "command": "x"}},
    ],
    ids=["short_sha", "upper_sha", "not_repro", "traversal", "not_py", "empty_diff", "obs_extra"],
)
def test_create_pr_field_formats(change):
    data = body("create_pr")
    data["action"].update(change)
    assert rejects(data)


def test_escalate_reason_must_be_a_fixed_blocker_code():
    data = body("escalate")
    data["action"]["reason"] = "LENS_DIRTY"
    assert rejects(data)
    for code in sorted(BLOCKER_CODES):
        data["action"]["reason"] = code
        assert not rejects(data)


def test_escalate_optional_fields_may_be_omitted():
    data = body("escalate", evidence_ids=[])
    del data["action"]["missing_requirements"], data["action"]["retry_condition"]
    assert not rejects(data)


# ── JSON Schema 파일 ──────────────────────────────────────────

_KNOWN = {"properties", "required", "const", "enum", "minItems"}


def _matches(condition: dict[str, Any], value: Any) -> bool:
    """이 schema의 if/then이 쓰는 키워드만 해석하는 작은 평가기."""
    assert set(condition) <= _KNOWN, set(condition) - _KNOWN
    if "const" in condition and value != condition["const"]:
        return False
    if "enum" in condition and value not in condition["enum"]:
        return False
    if "minItems" in condition and len(value) < condition["minItems"]:
        return False
    if any(key not in value for key in condition.get("required", [])):
        return False
    return all(
        _matches(sub, value[key])
        for key, sub in condition.get("properties", {}).items()
        if key in value
    )


def _schema_allows(schema: dict[str, Any], data: dict[str, Any]) -> bool:
    for rule in schema["allOf"]:
        assert set(rule) == {"if", "then"}
        if _matches(rule["if"], data) and not _matches(rule["then"], data):
            return False
    return True


def test_schema_files_are_generated_from_models():
    assert api_schema.stale_files() == [], "make api-schema로 다시 생성"


def test_schema_file_cross_field_rules_match_model_validator():
    schema = json.loads((api_schema.SCHEMA_DIR / "proposal.schema.json").read_text("utf-8"))
    for category, action_type, evidence in product(CATEGORY_ACTIONS, ACTIONS, ([], EV)):
        data = body(action_type, category=category, evidence_ids=evidence)
        assert _schema_allows(schema, data) is not rejects(data), (category, action_type, evidence)


def test_schema_files_forbid_unknown_fields_and_unused_v4_fields():
    for name in api_schema.SCHEMAS:
        schema = json.loads((api_schema.SCHEMA_DIR / name).read_text("utf-8"))
        objects = [schema, *schema.get("$defs", {}).values()]
        for item in objects:
            if item.get("type") == "object":
                assert item["additionalProperties"] is False, (name, item.get("title"))
    proposal = json.loads((api_schema.SCHEMA_DIR / "proposal.schema.json").read_text("utf-8"))
    unused = {"actions", "confidence", "actor", "role", "status", "model", "policy_version"}
    assert not unused & set(proposal["properties"])
    assert proposal["properties"]["schema_version"]["const"] == "linemedic.v4"
    assert proposal["properties"]["evidence_ids"]["uniqueItems"] is True
    assert set(proposal["properties"]["action"]["discriminator"]["mapping"]) == set(ACTIONS)


def test_receipt_has_no_execution_or_success_fields():
    assert set(ProposalReceipt.model_fields) == {"proposal_id", "decision"}
    receipt = json.loads((api_schema.SCHEMA_DIR / "proposal-receipt.schema.json").read_text())
    assert receipt["properties"]["decision"]["const"] == "RECEIVED"
    assert set(ProposalStatus.model_fields["decision"].annotation.__args__) == {
        "RECEIVED",
        "CHECKING",
        "ALLOWED",
        "REJECTED",
    }


def test_check_mode_reports_stale_files(tmp_path):
    assert api_schema.main(["--dir", str(tmp_path)]) == 0
    assert api_schema.main(["--check", "--dir", str(tmp_path)]) == 0
    (tmp_path / "proposal.schema.json").write_text("{}\n", encoding="utf-8")
    assert api_schema.main(["--check", "--dir", str(tmp_path)]) == 1


# ── 정비 초안·진행 불가 보고 ──────────────────────────────────


def test_draft_has_only_spec_fields_and_template_guidance():
    templates = load_manual_templates(KnowledgeBase())
    template = templates["MANUAL-L3-VISION-4.2"]
    action = WorkOrderDraftAction.model_validate(ACTIONS["create_work_order_draft"])
    draft = build_draft(action, EV, template)
    assert set(draft) == {
        "equipment_id",
        "symptom",
        "probable_cause",
        "probable_cause_is_hypothesis",
        "evidence_ids",
        "manual_ref_id",
        "open_questions",
        "review_required",
        "delivery_status",
        "guidance",
    }
    assert draft["delivery_status"] == "not_sent"
    assert draft["review_required"] is True
    assert draft["probable_cause_is_hypothesis"] is True
    assert draft["guidance"] == {
        "title": template.title,
        "section_ids": template.section_ids,
        "text": template.guidance,
        "disclaimer": template.disclaimer,
    }


def _report(**overrides: Any) -> dict[str, Any]:
    incident = {"run_id": RUN, "id": IDS["incident_id"]}
    work = {
        "id": IDS["work_id"],
        "generation": 1,
        "attempt_id": IDS["attempt_id"],
        "repository_id": 100001,
        "issue_number": 9,
    }
    kwargs: dict[str, Any] = {
        "blocker_code": "INSUFFICIENT_EVIDENCE",
        "stage": "agent",
        "incident": incident,
        "work": work,
        "symptom_impact": "관찰한 현상",
        "evidence_ids": [],
        "owner_route_id": "github-issue-primary",
        "observed_at": "2026-09-27T00:00:00.000000Z",
    }
    kwargs.update(overrides)
    return blocker_report(**kwargs)


def test_blocker_report_has_spec_16_fields():
    report = _report()
    assert {
        "symptom_impact",
        "blocker_code",
        "stage",
        "attempted_actions",
        "evidence_ids",
        "side_effect_state",
        "missing_requirements",
        "operator_next_step",
        "owner_route_id",
        "retry_condition",
    } <= set(report)
    assert report["event_type"] == "WORK_BLOCKED"
    assert report["side_effect_state"] == {"state": "NONE", "identities": []}


@pytest.mark.parametrize(
    "change",
    [{"blocker_code": "LENS_DIRTY"}, {"stage": "done"}, {"side_effect_state": "MAYBE"}],
)
def test_blocker_report_rejects_unknown_values(change):
    with pytest.raises(ValueError):
        _report(**change)
