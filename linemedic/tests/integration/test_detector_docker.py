"""W07 docker 시험: 실제 S1 MES 컨테이너 로그 → 감지기 → 사건 → 조회 도구 (make test-docker).

- S1 주입이 제어 DB에 `DEPLOY_OBSERVED`를 남긴다
- 컨테이너 로그(`docker logs --timestamps`, 로트 118 오류 3회)를 감지하면 사건 NEW 1개가 생기고,
  다시 실행해도 checkpoint 덕분에 같은 줄을 다시 세지 않는다
- attempt를 시작한 agent token으로 get_incident·search_logs·get_deploys가 자기 사건을 읽는다
정리는 이 테스트가 만든 run ID의 정확한 컨테이너·network만 지운다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock
from linemedic.common.ids import new_run_id
from linemedic.control_plane import runs
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.detector import Detector, DetectorSettings, run_detect_once
from linemedic.control_plane.log_store import FileLogStore
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.control_plane.store import Store
from linemedic.factory_sim import scenarios
from linemedic.integrations.docker import CliDocker
from linemedic.tests.helpers.api import make_api
from linemedic.tests.helpers.db_rows import insert_issue, insert_work

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_IMAGE = "linemedic-mes:test-w07"
BASE_SHA = "19045b62f292dedff24529cab505e6d86a91ed8c"  # W04 시드 커밋(결정적)


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="module")
def mes_image() -> str:
    if not docker_ready():
        pytest.skip("Docker daemon을 쓸 수 없음")
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "linemedic/runner/mes.Dockerfile",
            "-t",
            TEST_IMAGE,
            "l3-mes-api-seed",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    return TEST_IMAGE


def test_s1_container_logs_become_one_incident_readable_by_tools(mes_image, tmp_path):
    clock = SystemClock()
    run_id = new_run_id(clock)
    store = Store(tmp_path / "linemedic.db", clock)
    store.migrate()
    with store.tx() as tx:
        runs.create_run(tx, run_id, {"schema_version": "linemedic.v4", "routing_scope": "x"})
    try:
        injected = scenarios.inject_s1(
            run_id, runs_dir=tmp_path, image=mes_image, store=store, base_sha=BASE_SHA
        )
        assert [r["status"] for r in injected["requests"]] == [500, 500, 500, 200]
        container = scenarios.resource_names(run_id)["container"]
        logs = FileLogStore(tmp_path)
        watcher = Detector(
            store,
            DetectorSettings(
                run_id=run_id,
                routing_scope=f"eval:{run_id}",
                service="mes-api",
                line_id="L3",
                repository_id=0,
            ),
            clock,
            logs,
            eval_identifiers(),
        )
        # 실제 `docker logs --timestamps` 형식을 파싱하고, 두 번째 실행은 checkpoint로 건너뛴다.
        summary = run_detect_once(store, watcher, CliDocker(), container)
        again = run_detect_once(store, watcher, CliDocker(), container)
    finally:
        scenarios.stop_s1(run_id)

    assert summary["errors"] == 3 and len(summary["created"]) == 1 and summary["updated"] == []
    assert summary["unparsed"] == 0
    assert (again["lines"], again["skipped"]) == (0, summary["lines"])
    incident_id = summary["created"][0]

    connection = store.connect()
    try:
        incident = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        incident = incident.fetchone()
        assert (incident["status"], incident["count"], incident["source_kind"]) == ("NEW", 3, "LOG")
        assert json.loads(incident["details_json"])["signature"]["error_type"] == (
            "KeyError:inspector_id"
        )
        # supervisor의 attempt 시작을 흉내 낸다(테스트 전용 직접 준비)
        insert_issue(connection, 1, repository_id=0)
        connection.execute(
            "UPDATE incidents SET status = 'INVESTIGATING', attempt_id = 'ATT-00000000000A'"
            " WHERE id = ?",
            (incident_id,),
        )
        work = insert_work(
            connection,
            run_id,
            incident_id,
            1,
            "RUNNING",
            attempt_id="ATT-00000000000A",
            repository_id=0,
        )
        api = make_api(store, connection, log_store=logs)
        headers = api.agent(AgentPrincipal(run_id, incident_id, work, "ATT-00000000000A"))
        view = api.client.get(f"/tools/incidents/{incident_id}", headers=headers).json()
        assert view["data"]["features"] == {"recent_deploy": True, "scope": "service"}
        assert view["data"]["base_sha"] == BASE_SHA and len(view["evidence_ids"]) == 3
        found = api.client.get(
            f"/tools/incidents/{incident_id}/logs", headers=headers, params={"q": "KeyError"}
        ).json()["data"]["lines"]
        assert len(found) == 3
        deploys = api.client.get(f"/tools/incidents/{incident_id}/deploys", headers=headers)
        assert deploys.json()["data"]["current_base_sha"] == BASE_SHA
    finally:
        connection.close()

    label = f"label=linemedic.run_id={run_id}"
    leftovers = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", label], capture_output=True, text=True
    )
    assert leftovers.stdout.split() == []
