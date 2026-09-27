"""W08 통합 테스트: S2-lite 주입 → 설비 사건 → 조회 도구(지표·매뉴얼·배포).

- L3-CAM-2 지표 이상 → vision-inspection 사건 1개(metric:L3-CAM-2:brightness_drop), 증거 3개
- query_equipment_metrics: 등록 설비만(미등록·다른 서비스 설비 404), 다른 사건 범위 404
- get_knowledge: 허용 매뉴얼 절만, URL·경로 없음
- recent-deploy 변형에서만 24시간 안의 mes-api 배포가 보인다
- 도구 응답에 원인 확정 문구·시나리오 이름이 없다
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import metric_signature, problem_fingerprint
from linemedic.control_plane.knowledge import KnowledgeBase
from linemedic.control_plane.log_store import MemoryLogStore
from linemedic.control_plane.metrics_store import MemoryMetricsStore
from linemedic.factory_sim import camera_metrics, scenarios
from linemedic.tests.helpers.api import RUN, make_api
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_run, insert_work

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = load_settings(REPO_ROOT / "config" / "linemedic.toml", {}).config
ATTEMPT = "ATT-00000000000A"
FORBIDDEN = re.compile(r"(?i)\bs2\b|s2-lite|recent-deploy|렌즈|오염|lens|expected|정답")


def start_attempt(conn, incident_id: str, issue_number: int = 1) -> AgentPrincipal:
    """supervisor의 attempt 시작을 흉내 낸다(테스트 전용 직접 준비)."""
    insert_issue(conn, issue_number)
    conn.execute(
        "UPDATE incidents SET status = 'INVESTIGATING', attempt_id = ? WHERE id = ?",
        (ATTEMPT, incident_id),
    )
    work = insert_work(conn, RUN, incident_id, issue_number, "RUNNING", attempt_id=ATTEMPT)
    return AgentPrincipal(RUN, incident_id, work, ATTEMPT)


class S2World:
    def __init__(self, store, conn, clock, tmp_path, recent_deploy=False):
        insert_run(conn, RUN)
        self.store, self.conn = store, conn
        self.metrics = MemoryMetricsStore()
        self.result = scenarios.inject_s2_lite(
            RUN,
            runs_dir=tmp_path,
            store=store,
            config=CONFIG,
            clock=clock,
            recent_deploy=recent_deploy,
            metrics_store=self.metrics,
            base_sha="a" * 40,
        )
        self.api = make_api(
            store,
            conn,
            log_store=MemoryLogStore(),
            catalog=Catalog.from_config(CONFIG),
            metrics_store=self.metrics,
            knowledge=KnowledgeBase(),
        )

    @property
    def incident_id(self) -> str:
        return self.result["incidents"][0]

    def get(self, path: str, principal: AgentPrincipal, **params):
        return self.api.client.get(path, params=params, headers=self.api.agent(principal))


@pytest.fixture
def world(store, conn, fake_clock, tmp_path):
    return S2World(store, conn, fake_clock, tmp_path)


@pytest.fixture
def agent(world):
    return start_attempt(world.conn, world.incident_id)


def test_injection_creates_one_vision_incident_with_metric_evidence(world):
    assert len(world.result["incidents"]) == 1
    incident = world.conn.execute("SELECT * FROM incidents").fetchone()
    assert (incident["service"], incident["line_id"], incident["status"]) == (
        "vision-inspection",
        "L3",
        "NEW",
    )
    assert incident["count"] == camera_metrics.ANOMALY_SAMPLES
    assert incident["fingerprint"] == problem_fingerprint(
        metric_signature("vision-inspection", "L3-CAM-2", "brightness_drop")
    )
    details = json.loads(incident["details_json"])
    assert details["signature"]["endpoint"] == "metric:L3-CAM-2:brightness_drop"
    assert details["metric"]["equipment_id"] == "L3-CAM-2"
    rows = world.conn.execute("SELECT kind, source_identity, payload_json FROM evidence").fetchall()
    assert {r["kind"] for r in rows} == {"equipment_metric"}
    assert sorted(r["source_identity"] for r in rows) == [
        "metrics:L3-CAM-1",
        "metrics:L3-CAM-2",
        "metrics:L3-CAM-3",
    ]
    windows = {json.dumps(json.loads(r["payload_json"])["window"]) for r in rows}
    assert len(windows) == 1  # 비교 설비도 같은 시간대


def test_reinjection_merges_into_same_incident(world, fake_clock, tmp_path):
    again = scenarios.inject_s2_lite(
        RUN, tmp_path, world.store, CONFIG, clock=fake_clock, metrics_store=world.metrics
    )
    assert again["incidents"] == world.result["incidents"]
    incident = world.conn.execute("SELECT count FROM incidents").fetchone()
    assert incident["count"] == 2 * camera_metrics.ANOMALY_SAMPLES


def test_injection_requires_active_run(store, conn, fake_clock, tmp_path):
    with pytest.raises(scenarios.ScenarioError, match="make run-new"):
        scenarios.inject_s2_lite(RUN, tmp_path, store, CONFIG, clock=fake_clock)


# ── query_equipment_metrics ─────────────────────────────────


def test_equipment_metrics_match_design_table(world, agent):
    base = f"/tools/incidents/{world.incident_id}/equipment"
    expected = {"L3-CAM-1": (100, 0.94), "L3-CAM-2": (59, 0.61), "L3-CAM-3": (99, 0.93)}
    for equipment_id, (brightness, confidence) in expected.items():
        response = world.get(f"{base}/{equipment_id}/metrics", agent)
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["samples"][-1]["brightness"], data["samples"][-1]["confidence"]) == (
            brightness,
            confidence,
        )
        assert data["baseline"] == {"brightness": 100}
        assert 0 < len(data["samples"]) <= 60
        assert data["quality"]["max_samples"] == 60 and data["quality"]["window_minutes"] == 30
        assert data["window"]["to"] == data["samples"][-1]["ts"]


@pytest.mark.parametrize("equipment_id", ["L3-CAM-9", "l3-cam-2", "PLC-1"])
def test_unregistered_equipment_is_404(world, agent, equipment_id):
    response = world.get(
        f"/tools/incidents/{world.incident_id}/equipment/{equipment_id}/metrics", agent
    )
    assert response.status_code == 404


def test_equipment_of_other_service_is_404(store, conn, fake_clock, tmp_path):
    world = S2World(store, conn, fake_clock, tmp_path)
    mes = insert_incident(conn, RUN, "NEW", service="mes-api")
    agent = start_attempt(conn, mes, issue_number=2)
    response = world.get(f"/tools/incidents/{mes}/equipment/L3-CAM-2/metrics", agent)
    assert response.status_code == 404
    knowledge = world.get(f"/tools/incidents/{mes}/knowledge", agent).json()["data"]
    assert knowledge["results"] == []


def test_other_incident_scope_is_hidden(store, conn, fake_clock, tmp_path):
    world = S2World(store, conn, fake_clock, tmp_path)
    other = insert_incident(conn, RUN, "NEW", service="mes-api")
    agent = start_attempt(conn, other, issue_number=2)
    for suffix in ("/equipment/L3-CAM-2/metrics", "/knowledge", "/deploys", ""):
        assert world.get(f"/tools/incidents/{world.incident_id}{suffix}", agent).status_code == 404


# ── get_knowledge ────────────────────────────────────────────


def test_knowledge_returns_allowed_manual_sections_only(world, agent):
    data = world.get(f"/tools/incidents/{world.incident_id}/knowledge", agent, q="점검").json()[
        "data"
    ]
    assert data["results"]
    for result in data["results"]:
        assert result["manual_ref_id"] == "MANUAL-L3-VISION-4.2"
        assert "실제 산업 매뉴얼·안전 절차가 아님" in result["disclaimer"]
        assert not re.search(r"https?://|www\.|\.md\b|linemedic/", json.dumps(result))


@pytest.mark.parametrize("q", ["../../etc/passwd", "https://evil.example", "없는 단어"])
def test_knowledge_query_is_only_a_search_string(world, agent, q):
    response = world.get(f"/tools/incidents/{world.incident_id}/knowledge", agent, q=q)
    assert response.status_code == 200 and response.json()["data"]["results"] == []


@pytest.mark.parametrize("params", [{"q": "x" * 201}, {"url": "http://x"}])
def test_knowledge_rejects_bad_parameters(world, agent, params):
    response = world.get(f"/tools/incidents/{world.incident_id}/knowledge", agent, **params)
    assert response.status_code == 422


# ── get_deploys·get_incident: 기본 변형과 recent-deploy 변형 ──


def test_default_variant_has_no_recent_deploy(world, agent):
    deploys = world.get(f"/tools/incidents/{world.incident_id}/deploys", agent).json()["data"]
    assert deploys["deploys"] == [] and deploys["services"] == ["mes-api", "vision-inspection"]
    view = world.get(f"/tools/incidents/{world.incident_id}", agent).json()["data"]
    assert view["features"] == {"recent_deploy": False, "scope": "equipment"}
    assert view["symptom"] == "L3-CAM-2 밝기가 기준보다 낮게 관찰됨"


def test_recent_deploy_variant_shows_mes_deploy_before_the_anomaly(
    store, conn, fake_clock, tmp_path
):
    world = S2World(store, conn, fake_clock, tmp_path, recent_deploy=True)
    agent = start_attempt(conn, world.incident_id)
    deploys = world.get(f"/tools/incidents/{world.incident_id}/deploys", agent).json()["data"]
    [deploy] = deploys["deploys"]
    incident = conn.execute("SELECT first_seen FROM incidents").fetchone()
    assert deploy["service"] == "mes-api" and deploy["base_sha"] == "a" * 40
    assert deploy["observed_at"] == world.result["deployed_at"] < incident["first_seen"]
    view = world.get(f"/tools/incidents/{world.incident_id}", agent).json()["data"]
    assert view["features"]["recent_deploy"] is True


def test_tool_responses_have_no_cause_or_scenario_text(store, conn, fake_clock, tmp_path):
    world = S2World(store, conn, fake_clock, tmp_path, recent_deploy=True)
    agent = start_attempt(conn, world.incident_id)
    base = f"/tools/incidents/{world.incident_id}"
    texts = [
        world.get(base, agent).text,
        world.get(f"{base}/deploys", agent).text,
        world.get(f"{base}/knowledge", agent).text,
        *(world.get(f"{base}/equipment/{e}/metrics", agent).text for e in ("L3-CAM-1", "L3-CAM-2")),
    ]
    for text in texts:
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text)


# ── CLI ──────────────────────────────────────────────────────


def test_cli_scenario_s2_lite(tmp_path):
    db = tmp_path / "runs" / "linemedic.db"
    env_file = tmp_path / ".env"
    env_file.write_text(f"RUNS_DIR={tmp_path / 'runs'}\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT)}

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "linemedic.cli", *args, "--env-file", str(env_file)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )

    created = cli("run-new", "--db", str(db))
    assert created.returncode == 0, created.stderr
    run_id = json.loads(created.stdout)["run_id"]
    result = cli("scenario-s2-lite", "--run-id", run_id, "--db", str(db), "--recent-deploy")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert len(summary["incidents"]) == 1 and summary["recent_deploy"] is True
    assert summary["equipment"]["L3-CAM-2"] == {"brightness": 59, "confidence": 0.61}
    assert (tmp_path / "runs" / run_id / "metrics" / "L3-CAM-2.jsonl").exists()
    missing = cli("scenario-s2-lite", "--run-id", "r-20990101-000000-dead", "--db", str(db))
    assert missing.returncode == 2 and "make run-new" in missing.stderr
