"""에이전트 제안 schema (W09, spec 03 §3, docs/04 §3).

- 공통 필드 + `action` discriminated union(`create_pr`, `create_work_order_draft`, `escalate`)
- `extra="forbid"`·`strict=True`: `actions[]`, `confidence`, `actor`, `role`, `status`, `model`,
  `policy_version`, 자유 절차·제어값·URL·수신자 필드는 정의하지 않은 필드라 거부된다
- category ↔ action 대응(INV-05)과 근거 개수(create_pr·정비 초안은 1개 이상)를 schema에서 검사한다.
  JSON Schema(`make api-schema`)에도 같은 규칙을 `allOf`의 if/then으로 싣는다
- 필드 값은 비신뢰 텍스트다. 브로커가 다시 검사하고, 모델 문장을 작업자 지시로 옮기지 않는다
- 응답 모델: 202 `ProposalReceipt`(접수일 뿐 허용·실행 성공이 아님),
  `get_proposal`의 `ProposalStatus`
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from linemedic.control_plane.codes import CATEGORY_ACTIONS, RUN_ID_PATTERN, BlockerCode, Category

MAX_EVIDENCE_IDS = 20  # docs/07 proposal.max_evidence_ids
SUMMARY_MAX_CHARS = 2000  # docs/07 proposal.summary_max_chars
MAX_DIFF_CHARS = 120_000  # body 128 KiB 안
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Question = Annotated[str, Field(min_length=1, max_length=300)]
EvidenceId = Annotated[str, Field(pattern=r"^EV-[0-9A-F]{12}$")]
ActionType = Literal["create_pr", "create_work_order_draft", "escalate"]
EVIDENCE_REQUIRED: frozenset[str] = frozenset({"create_pr", "create_work_order_draft"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LocalTestObservation(_Model):
    before: Literal["failed", "passed", "error", "not_run"]
    after: Literal["failed", "passed", "error", "not_run"]


class CreatePrAction(_Model):
    type: Literal["create_pr"]
    base_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    root_cause_hypothesis: Annotated[str, Field(min_length=1, max_length=2000)]
    diff: Annotated[str, Field(min_length=1, max_length=MAX_DIFF_CHARS)]
    new_test_path: Annotated[str, Field(pattern=r"^tests/repro/test_[A-Za-z0-9_]{1,64}\.py$")]
    local_test_observation: LocalTestObservation | None = None


class WorkOrderDraftAction(_Model):
    type: Literal["create_work_order_draft"]
    equipment_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{0,31}$")]
    symptom: ShortText
    probable_cause: ShortText  # 가설. 원인 확정이 아니다
    manual_ref_id: Annotated[str, Field(pattern=r"^MANUAL-[A-Z0-9][A-Z0-9.-]{0,47}$")]
    open_questions: Annotated[list[Question], Field(max_length=10)]


class EscalateAction(_Model):
    type: Literal["escalate"]
    reason: BlockerCode
    open_questions: Annotated[list[Question], Field(max_length=10)]
    missing_requirements: Annotated[list[Question], Field(max_length=10)] | None = None
    retry_condition: ShortText | None = None


Action = Annotated[
    CreatePrAction | WorkOrderDraftAction | EscalateAction, Field(discriminator="type")
]


def _cross_field_rules(schema: dict[str, Any]) -> None:
    """model_validator의 category↔action·근거 개수 규칙을 JSON Schema if/then으로 옮긴다."""
    rules: list[dict[str, Any]] = []
    for category, actions in CATEGORY_ACTIONS.items():
        rules.append(
            {
                "if": {"properties": {"category": {"const": category}}, "required": ["category"]},
                "then": {
                    "properties": {"action": {"properties": {"type": {"enum": sorted(actions)}}}}
                },
            }
        )
    rules.append(
        {
            "if": {
                "properties": {
                    "action": {"properties": {"type": {"enum": sorted(EVIDENCE_REQUIRED)}}}
                },
                "required": ["action"],
            },
            "then": {"properties": {"evidence_ids": {"minItems": 1}}},
        }
    )
    schema["allOf"] = rules


class Proposal(_Model):
    model_config = ConfigDict(json_schema_extra=_cross_field_rules)

    schema_version: Literal["linemedic.v4"]
    run_id: Annotated[str, Field(pattern=RUN_ID_PATTERN)]
    incident_id: Annotated[str, Field(pattern=r"^INC-[0-9A-F]{12}$")]
    work_id: Annotated[str, Field(pattern=r"^WORK-[0-9A-F]{12}$")]
    attempt_id: Annotated[str, Field(pattern=r"^ATT-[0-9A-F]{12}$")]
    category: Category
    summary: Annotated[str, Field(min_length=1, max_length=SUMMARY_MAX_CHARS)]
    evidence_ids: Annotated[
        list[EvidenceId],
        Field(max_length=MAX_EVIDENCE_IDS, json_schema_extra={"uniqueItems": True}),
    ]
    action: Action

    @model_validator(mode="after")
    def _category_action_and_evidence(self) -> "Proposal":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids에 중복이 있다")
        if self.action.type not in CATEGORY_ACTIONS[self.category]:
            raise ValueError(f"category {self.category}에는 {self.action.type}를 쓸 수 없다")
        if self.action.type in EVIDENCE_REQUIRED and not self.evidence_ids:
            raise ValueError(f"{self.action.type}에는 근거 evidence_id가 1개 이상 필요하다")
        return self


# ── 응답 ──────────────────────────────────────────────────────

ProposalId = Annotated[str, Field(pattern=r"^PROP-[0-9A-F]{12}$")]
# 검사 결과: PASS 또는 거절 코드(docs/03 §5). 패치 검사 코드(W10)를 포함한다.
CheckCode = Literal[
    "PASS",
    "EVIDENCE_SCOPE_MISMATCH",
    "SENSITIVE_CONTENT",
    "STATE_CONFLICT",
    "PROTECTION_UNAVAILABLE",
    "PATCH_PATH_DENIED",
    "SOURCE_CHANGED",
    "REPRO_NOT_FAILING",
    "REGRESSION_FAILED",
]
DecisionReason = Literal[
    "WORK_ORDER_DRAFTED",
    "ESCALATED",
    "EVIDENCE_SCOPE_MISMATCH",
    "SENSITIVE_CONTENT",
    "STATE_CONFLICT",
    "PROTECTION_UNAVAILABLE",
    "PATCH_PATH_DENIED",
    "SOURCE_CHANGED",
    "REPRO_NOT_FAILING",
    "REGRESSION_FAILED",
]
# 브로커 검사 이름. 패치 게이트(W10): 정책 → 기준 base → candidate → runner image → R0 → R1 → R2
CheckName = Literal[
    "B01",
    "B02",
    "B03",
    "B04",
    "B05",
    "B06",
    "TEMPLATE",
    "PATCH_GATE",
    "PATCH_POLICY",
    "BASE",
    "CANDIDATE",
    "RUNNER",
    "R0",
    "R1",
    "R2",
    "CREATE_PR",
]
CHECK_RESULT_FIELDS = ("check", "result", "reason", "evidence_id")


class ProposalReceipt(_Model):
    """202 응답 data. 접수 기록일 뿐 허용·실행 성공을 뜻하는 필드가 없다."""

    proposal_id: ProposalId
    decision: Literal["RECEIVED"]


class CheckResult(_Model):
    """에이전트에게 보이는 검사 한 줄. 저장된 기록의 나머지(경로·container·로그)는 보이지 않는다."""

    check: CheckName
    result: CheckCode
    reason: Annotated[str, Field(max_length=200)] | None = None
    evidence_id: EvidenceId | None = None


class ProposalStatus(_Model):
    """`get_proposal` 응답 data. ALLOWED도 외부 실행 완료가 아니다."""

    proposal_id: ProposalId
    incident_id: Annotated[str, Field(pattern=r"^INC-[0-9A-F]{12}$")]
    category: Category
    action_type: ActionType
    decision: Literal["RECEIVED", "CHECKING", "ALLOWED", "REJECTED"]
    decision_reason: DecisionReason | None
    checks: list[CheckResult]
    received_at: str
    submissions_used: Annotated[int, Field(ge=0)]
    max_submissions: Annotated[int, Field(ge=1)]
    revision_allowed: bool
