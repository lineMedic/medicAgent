"""여러 모듈이 같이 쓰는 고정 코드 (docs/03 §4·§5).

blocker_code는 work·알림 payload의 진행 불가 사유다. incident 상태를 늘리지 않는다(spec 16 §6).
"""

from typing import Literal, get_args

BlockerCode = Literal[
    "UNSUPPORTED_ACTION",
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_REQUIREMENTS",
    "PERMISSION_REQUIRED",
    "HUMAN_WORK_IN_PROGRESS",
    "LOOKUP_INCOMPLETE",
    "START_NOTICE_UNCONFIRMED",
    "MODEL_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "VALIDATION_FAILED",
    "SOURCE_CHANGED",
    "EXTERNAL_RESULT_UNKNOWN",
    "VERIFICATION_FAILED",
    "OBSERVATION_INCONCLUSIVE",
]
BLOCKER_CODES = frozenset(get_args(BlockerCode))

Category = Literal["code_bug", "equipment", "config", "infra", "external_dependency", "unknown"]

# docs/03 §4 category ↔ action. 표에 없는 조합은 거절한다(INV-05).
CATEGORY_ACTIONS: dict[str, frozenset[str]] = {
    "code_bug": frozenset({"create_pr", "escalate"}),
    "equipment": frozenset({"create_work_order_draft", "escalate"}),
    "config": frozenset({"escalate"}),
    "infra": frozenset({"escalate"}),
    "external_dependency": frozenset({"escalate"}),
    "unknown": frozenset({"escalate"}),
}

RUN_ID_PATTERN = r"^r-\d{8}-\d{6}-[0-9a-f]{4}$"
