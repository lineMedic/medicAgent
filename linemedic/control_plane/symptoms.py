"""사건 details에서 관찰 사실만으로 증상 문장을 만든다 (W07·W08 도구 응답, W09 차단 보고).

원인을 추정하는 문장을 만들지 않는다. 모델이 쓴 요약은 증상으로 쓰지 않는다.
"""

from typing import Any

METRIC_SYMPTOMS = {
    "brightness_drop": "{equipment} 밝기가 기준보다 낮게 관찰됨",
    "confidence_drop": "{equipment} 판정 신뢰도가 기준보다 낮게 관찰됨",
}


def observed_symptom(details: dict[str, Any]) -> str | None:
    """설비 지표 이상 또는 오류 signature의 관찰 사실. 만들 수 없으면 None."""
    metric = details.get("metric")
    if isinstance(metric, dict) and metric.get("anomaly") in METRIC_SYMPTOMS:
        return METRIC_SYMPTOMS[metric["anomaly"]].format(equipment=metric.get("equipment_id"))
    sig = details.get("signature")
    if not isinstance(sig, dict) or not sig.get("endpoint") or not sig.get("error_type"):
        return None
    error_type = str(sig["error_type"]).split(":", 1)[0]
    return f"{sig['endpoint']} 요청에서 {error_type} 오류 반복 관찰"
