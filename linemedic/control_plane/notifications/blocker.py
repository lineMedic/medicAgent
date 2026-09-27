"""진행 불가 보고 payload (W09, spec 16 §6, spec templates/blocker-report.md).

host가 관찰 사실로 필드를 채운다. 모델 API가 죽어도 만들 수 있어야 한다.
본문 문장·발송은 W26 템플릿이 맡는다.
실제로 하지 않은 일을 한 것처럼 적지 않는다(attempted_actions는 host가 확인한 동작만).

- `symptom_impact`: 사건 details의 관찰 사실(모델 요약이 아님)
- `reason_detail`: host가 쓴 진행 불가 사유 설명(`blocker_code`의 보충)
- `agent_summary`: 에이전트가 이관하며 쓴 요약. 관찰 사실이 아닌 모델의 판단이며 없을 수 있다
"""

from collections.abc import Iterable, Mapping
from typing import Any

from linemedic.control_plane.codes import BLOCKER_CODES

STAGES = frozenset({"intake", "preflight", "agent", "validation", "external_write", "verification"})


def blocker_report(
    *,
    blocker_code: str,
    stage: str,
    incident: Mapping[str, Any],
    work: Mapping[str, Any] | None,
    symptom_impact: str,
    evidence_ids: Iterable[str],
    owner_route_id: str,
    observed_at: str,
    attempted_actions: Iterable[str] = (),
    side_effect_state: str = "NONE",
    side_effect_identities: Iterable[str] = (),
    missing_requirements: Iterable[str] = (),
    operator_next_step: Iterable[str] = (),
    retry_condition: str | None = None,
    reason_detail: str | None = None,
    agent_summary: str | None = None,
) -> dict[str, Any]:
    if blocker_code not in BLOCKER_CODES:
        raise ValueError(f"알 수 없는 blocker_code: {blocker_code!r}")
    if stage not in STAGES:
        raise ValueError(f"알 수 없는 stage: {stage!r}")
    if side_effect_state not in {"NONE", "OBSERVED", "UNKNOWN"}:
        raise ValueError(f"알 수 없는 side_effect_state: {side_effect_state!r}")
    return {
        "schema_version": "linemedic.v4",
        "event_type": "WORK_BLOCKED",
        "run_id": incident["run_id"],
        "incident_id": incident["id"],
        # work 없이 멈춘 사건(Issue 연결 전 모호·조회 불완전)은 미연결로 남긴다
        "work_id": work["id"] if work is not None else None,
        "generation": work["generation"] if work is not None else None,
        "attempt_id": work["attempt_id"] if work is not None else None,
        "repository_id": (work or incident)["repository_id"],
        "issue_number": work["issue_number"] if work is not None else None,
        "blocker_code": blocker_code,
        "stage": stage,
        "observed_at": observed_at,
        "symptom_impact": symptom_impact,
        "reason_detail": reason_detail,
        "agent_summary": agent_summary,
        "attempted_actions": list(attempted_actions),
        "evidence_ids": list(evidence_ids),
        "side_effect_state": {
            "state": side_effect_state,
            "identities": list(side_effect_identities),
        },
        "missing_requirements": list(missing_requirements),
        "operator_next_step": list(operator_next_step),
        "owner_route_id": owner_route_id,
        "retry_condition": retry_condition,
    }
