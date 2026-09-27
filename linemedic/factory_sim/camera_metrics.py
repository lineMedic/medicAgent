"""합성 카메라 지표 (W08, spec 09 §3, docs/07 §4). 실제 센서 측정·영상 인식·historian이 아니다.

run마다 30분·60 sample(30초 간격) 시계열을 만든다. 값은 설계표 그대로이고 무작위성이 없다.
L3-CAM-2만 마지막 10 sample(5분) 동안 밝기 59·신뢰도 0.61로 낮게 관찰되고 다른 카메라는 정상이다.
이 값만으로 원인을 확정하지 않는다.
"""

from datetime import datetime, timedelta

from linemedic.common.clock import to_rfc3339
from linemedic.control_plane.metrics_store import MetricSample

SAMPLE_INTERVAL_SECONDS = 30
SAMPLE_COUNT = 60
BASELINE_BRIGHTNESS = 100
ANOMALY_SAMPLES = 10

# docs/07 §4: (정상 밝기, 정상 신뢰도, 관찰 밝기, 관찰 신뢰도). 관찰 값은 마지막 구간의 값이다.
CAMERAS: dict[str, tuple[float, float, float, float]] = {
    "L3-CAM-1": (100, 0.94, 100, 0.94),
    "L3-CAM-2": (100, 0.94, 59, 0.61),
    "L3-CAM-3": (99, 0.93, 99, 0.93),
}


def generate_series(end: datetime) -> dict[str, list[MetricSample]]:
    """`end`에 끝나는 30분 시계열. 이상 구간은 마지막 `ANOMALY_SAMPLES`개다."""
    series = {}
    first_anomaly = SAMPLE_COUNT - ANOMALY_SAMPLES
    for equipment_id, (brightness, confidence, observed_b, observed_c) in CAMERAS.items():
        samples = []
        for index in range(SAMPLE_COUNT):
            ts = end - timedelta(seconds=(SAMPLE_COUNT - 1 - index) * SAMPLE_INTERVAL_SECONDS)
            late = index >= first_anomaly
            samples.append(
                MetricSample(
                    ts=to_rfc3339(ts),
                    equipment_id=equipment_id,
                    brightness=observed_b if late else brightness,
                    confidence=observed_c if late else confidence,
                    baseline_brightness=BASELINE_BRIGHTNESS,
                )
            )
        series[equipment_id] = samples
    return series
