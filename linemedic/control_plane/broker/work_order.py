"""정비 요청 초안 (W09, spec 06 §7, D58).

초안에는 spec 06 §7의 필드만 넣는다. 안내 문구는 승인 템플릿(`manual_templates.toml`)에서만 채우고,
모델이 쓴 증상·가설은 관찰·가설로 표시해 그대로 둔다. 작업자 지시로 바꾸지 않는다.
`delivery_status`는 실제 CMMS·현장 작업지시 전송만 뜻하며 항상 `not_sent`다(INV-10).
"""

from typing import Any

from linemedic.control_plane.broker.proposals import WorkOrderDraftAction
from linemedic.control_plane.knowledge import ManualTemplate


def build_draft(
    action: WorkOrderDraftAction, evidence_ids: list[str], template: ManualTemplate
) -> dict[str, Any]:
    return {
        "equipment_id": action.equipment_id,
        "symptom": action.symptom,
        "probable_cause": action.probable_cause,
        "probable_cause_is_hypothesis": True,
        "evidence_ids": list(evidence_ids),
        "manual_ref_id": action.manual_ref_id,
        "open_questions": list(action.open_questions),
        "review_required": True,
        "delivery_status": "not_sent",
        "guidance": {
            "title": template.title,
            "section_ids": list(template.section_ids),
            "text": template.guidance,
            "disclaimer": template.disclaimer,
        },
    }
