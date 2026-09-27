"""W08 단위 테스트: 합성 카메라 지표·설비 이상 규칙·설비 catalog·가상 매뉴얼·승인 문구."""

import copy
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linemedic.common.clock import to_rfc3339
from linemedic.common.config import ConfigError, LineMedicConfig, validate_model
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import MetricRule, metric_anomaly, metric_signature
from linemedic.control_plane.knowledge import (
    KnowledgeBase,
    KnowledgeError,
    load_manual,
    load_manual_templates,
)
from linemedic.control_plane.metrics_store import FileMetricsStore, MetricSample
from linemedic.factory_sim.camera_metrics import (
    ANOMALY_SAMPLES,
    SAMPLE_COUNT,
    SAMPLE_INTERVAL_SECONDS,
    generate_series,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "config" / "linemedic.toml"
MANUAL = REPO_ROOT / "linemedic" / "factory_sim" / "manuals" / "MANUAL-L3-VISION-4.2.md"
END = datetime(2026, 9, 27, 9, 0, 0, tzinfo=UTC)
RULE = MetricRule(brightness_drop_ratio=0.3, confidence_min=0.7, consecutive_samples=3)


def sample(i: int, brightness: float, confidence: float) -> MetricSample:
    ts = to_rfc3339(END + timedelta(seconds=i * 30))
    return MetricSample(ts, "L3-CAM-2", brightness, confidence, 100)


# ── 합성 지표 ─────────────────────────────────────────────────


def test_series_shape_is_30_minutes_of_60_samples():
    series = generate_series(END)
    assert sorted(series) == ["L3-CAM-1", "L3-CAM-2", "L3-CAM-3"]
    for samples in series.values():
        assert len(samples) == SAMPLE_COUNT == 60
        assert samples[-1].ts == to_rfc3339(END)
        assert samples[0].ts == to_rfc3339(END - timedelta(seconds=59 * SAMPLE_INTERVAL_SECONDS))
        assert {s.baseline_brightness for s in samples} == {100}


def test_observed_values_match_design_table():
    series = generate_series(END)
    last = {eid: (s[-1].brightness, s[-1].confidence) for eid, s in series.items()}
    assert last == {"L3-CAM-1": (100, 0.94), "L3-CAM-2": (59, 0.61), "L3-CAM-3": (99, 0.93)}


def test_only_cam_2_has_the_anomaly_in_the_last_window():
    series = generate_series(END)
    cam2 = series["L3-CAM-2"]
    assert all((s.brightness, s.confidence) == (100, 0.94) for s in cam2[:-ANOMALY_SAMPLES])
    assert all((s.brightness, s.confidence) == (59, 0.61) for s in cam2[-ANOMALY_SAMPLES:])
    for normal in ("L3-CAM-1", "L3-CAM-3"):
        assert len({(s.brightness, s.confidence) for s in series[normal]}) == 1


def test_series_is_deterministic():
    assert generate_series(END) == generate_series(END)


def test_file_metrics_store_roundtrip(tmp_path):
    store = FileMetricsStore(tmp_path)
    samples = generate_series(END)["L3-CAM-2"]
    store.write_series("r-20260927-090000-abcd", "L3-CAM-2", samples)
    window = store.read("r-20260927-090000-abcd", "L3-CAM-2", samples[50].ts, samples[-1].ts)
    assert window == samples[50:]
    with pytest.raises(ValueError):
        store.path("r-20260927-090000-abcd", "../etc")


# ── 이상 규칙 ─────────────────────────────────────────────────


def test_rule_finds_cam_2_brightness_drop_only():
    series = generate_series(END)
    anomaly = metric_anomaly(series["L3-CAM-2"], RULE)
    assert anomaly is not None
    assert anomaly.kind == "brightness_drop"
    assert anomaly.samples == tuple(series["L3-CAM-2"][-ANOMALY_SAMPLES:])
    assert metric_anomaly(series["L3-CAM-1"], RULE) is None
    assert metric_anomaly(series["L3-CAM-3"], RULE) is None


def test_rule_needs_three_consecutive_samples():
    two = [sample(0, 100, 0.94), sample(1, 59, 0.61), sample(2, 59, 0.61), sample(3, 100, 0.94)]
    assert metric_anomaly(two, RULE) is None
    three = two[:3] + [sample(3, 59, 0.61)]
    assert metric_anomaly(three, RULE) is not None


def test_rule_takes_latest_qualifying_streak():
    samples = [sample(i, 59, 0.9) for i in range(3)] + [sample(3, 100, 0.94)]
    samples += [sample(4 + i, 100, 0.5) for i in range(4)]
    anomaly = metric_anomaly(samples, RULE)
    assert anomaly.kind == "confidence_drop" and len(anomaly.samples) == 4


@pytest.mark.parametrize(
    ("brightness", "confidence", "low"),
    [(70, 0.94, True), (71, 0.94, False), (100, 0.7, False), (100, 0.69, True)],
)
def test_rule_boundaries(brightness, confidence, low):
    samples = [sample(i, brightness, confidence) for i in range(3)]
    assert (metric_anomaly(samples, RULE) is not None) is low


def test_metric_signature_uses_metric_endpoint():
    sig = metric_signature("vision-inspection", "L3-CAM-2", "brightness_drop")
    assert sig.endpoint == "metric:L3-CAM-2:brightness_drop"
    assert sig.top_frame == "equipment:L3-CAM-2"


# ── 설비 catalog ──────────────────────────────────────────────


def raw_config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_catalog_scopes_equipment_manuals_and_related_services():
    catalog = Catalog.from_config(validate_model(LineMedicConfig, raw_config(), "config"))
    assert sorted(catalog.equipment_of("vision-inspection")) == ["L3-CAM-1", "L3-CAM-2", "L3-CAM-3"]
    assert catalog.equipment_of("mes-api") == {}
    assert catalog.manuals_for("vision-inspection") == {"MANUAL-L3-VISION-4.2"}
    assert catalog.manuals_for("mes-api") == frozenset()
    assert catalog.related_services("vision-inspection") == {"mes-api", "vision-inspection"}
    assert catalog.services["vision-inspection"].code_paths == []


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["equipment"]["L3-CAM-2"].update(service="unknown-service"),
        lambda c: c["equipment"]["L3-CAM-2"].update(line_id="L9"),
        lambda c: c["equipment"].update({"l3-cam-9": copy.deepcopy(c["equipment"]["L3-CAM-1"])}),
        lambda c: c["equipment"]["L3-CAM-2"].update(kind="plc"),
    ],
    ids=["unknown_service", "line_mismatch", "bad_id", "unknown_kind"],
)
def test_invalid_equipment_catalog_is_rejected(change):
    data = raw_config()
    change(data)
    with pytest.raises(ConfigError):
        validate_model(LineMedicConfig, data, "config")


# ── 가상 매뉴얼·승인 문구 ─────────────────────────────────────


def test_manual_is_marked_fictional_and_has_no_causal_or_operational_text():
    text = MANUAL.read_text(encoding="utf-8")
    assert "실제 산업 매뉴얼·안전 절차가 아님" in text.splitlines()[0]
    assert not re.search(r"https?://|www\.", text)
    for phrase in ("렌즈", "오염", "원인이다", "원인은 ", "교체", "청소"):
        assert phrase not in text
    manual = load_manual(MANUAL)
    assert [s.section_id for s in manual.sections] == ["4.2.1", "4.2.2", "4.2.3", "4.2.4"]


def test_knowledge_search_is_literal_and_scoped():
    knowledge = KnowledgeBase()
    found = knowledge.search({"MANUAL-L3-VISION-4.2"}, "배포")
    assert [section.section_id for _, section in found] == ["4.2.2"]
    assert len(knowledge.search({"MANUAL-L3-VISION-4.2"}, None)) == 4
    assert knowledge.search({"MANUAL-L3-VISION-4.2"}, ".*") == []
    assert knowledge.search({"MANUAL-OTHER"}, "점검") == []


def test_manual_templates_reference_existing_sections(tmp_path):
    templates = load_manual_templates(KnowledgeBase())
    assert list(templates) == ["MANUAL-L3-VISION-4.2"]
    assert "가상" in templates["MANUAL-L3-VISION-4.2"].disclaimer
    bad = tmp_path / "templates.toml"
    bad.write_text(
        '[templates."MANUAL-L3-VISION-4.2"]\ntitle = "t"\nsection_ids = ["9.9"]\n'
        'guidance = "g"\ndisclaimer = "d"\n',
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeError):
        load_manual_templates(KnowledgeBase(), bad)


def test_manual_without_disclaimer_is_rejected(tmp_path):
    path = tmp_path / "MANUAL-X-1.md"
    path.write_text("# 제목\n\n## 1.1 절\n본문\n", encoding="utf-8")
    with pytest.raises(KnowledgeError):
        load_manual(path)
