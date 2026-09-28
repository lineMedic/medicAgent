"""합성 장애 주입 (W04: S1, W07: 배포 관찰 기록, W08: S2-lite 카메라 지표).

`inject_s1(run_id)`는 버그 base MES 이미지로 컨테이너를 띄우고, 로트 118 요청 3회(60초 안)와
로트 101 요청 1회를 보낸다.

- 컨테이너: `linemedic.run_id` 라벨, 외부로 나갈 수 없는 내부 network(`--internal`), 비루트,
  read-only root, capability 제거, 자원 상한, 데이터는 read-only mount
- 요청은 컨테이너 안에서 자기 자신(127.0.0.1)에 보낸다. 내부 network라 호스트 port를 열지 않는다
- Docker는 고정 argv 리스트로만 호출한다(shell=False, D47)
- 정리는 run ID가 붙은 정확한 컨테이너·network 이름만 대상으로 한다
- 제어 DB(`store`)를 주면 MES가 준비된 뒤 `DEPLOY_OBSERVED`(base SHA·image ID·컨테이너)를
  기록한다(D59)
"""

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, from_rfc3339, to_rfc3339
from linemedic.common.config import LineMedicConfig
from linemedic.common.ids import is_valid_run_id
from linemedic.control_plane.deploys import record_deploy_observed
from linemedic.control_plane.detector import Detector, metric_rule, settings_for_run
from linemedic.control_plane.metrics_store import FileMetricsStore, MetricsStore
from linemedic.control_plane.store import Store
from linemedic.factory_sim import camera_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_LOTS_DIR = REPO_ROOT / "l3-mes-api-seed" / "data" / "lots"
HOLDOUT_FIXTURE = REPO_ROOT / "linemedic" / "eval" / "holdout-defects-v1.json"
DEFAULT_MES_IMAGE = "linemedic-mes:base"
MES_SERVICE = "mes-api"
HARNESS_ACTOR = "trusted_harness"
BUG_LOT = "L3-0927-118"
NORMAL_LOT = "L3-0927-101"
BUG_REQUESTS = 3
HEALTH_TIMEOUT_SECONDS = 30.0
HEALTH_POLL_SECONDS = 0.5

# 컨테이너 안에서 실행할 요청 코드. 경로는 argv로 넘기고 상태 코드만 출력한다.
REQUEST_CODE = (
    "import sys, urllib.request, urllib.error\n"
    "url = 'http://127.0.0.1:8000' + sys.argv[1]\n"
    "try:\n"
    "    with urllib.request.urlopen(url, timeout=5) as response:\n"
    "        print(response.status)\n"
    "except urllib.error.HTTPError as error:\n"
    "    print(error.code)\n"
    "except Exception:\n"
    "    print(0)\n"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


RunFn = Callable[[list[str]], CommandResult]


class ScenarioError(RuntimeError):
    """장애 주입을 진행할 수 없음."""


def run_command(argv: list[str], timeout: float = 120.0) -> CommandResult:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def resource_names(run_id: str) -> dict[str, str]:
    return {"container": f"linemedic-mes-{run_id}", "network": f"linemedic-net-{run_id}"}


def _checked(run: RunFn, argv: list[str], action: str) -> CommandResult:
    result = run(argv)
    if result.returncode != 0:
        raise ScenarioError(f"{action} 실패: {result.stderr.strip()[:300]}")
    return result


def _request(run: RunFn, container: str, path: str) -> int:
    result = run(["docker", "exec", container, "python", "-c", REQUEST_CODE, path])
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def mes_container_options(name: str, run_id: str, network: str, data_dir: Path) -> list[str]:
    """MES 컨테이너 격리 옵션(`docker run`의 image 앞 인자). S1 주입과 S1b harness가 같이 쓴다.

    데이터 경로는 절대 경로로 바꾼다.
    상대 경로(`RUNS_DIR=runs`)는 docker가 named volume 이름으로 해석한다.
    """
    return [
        "--name",
        name,
        "--label",
        f"linemedic.run_id={run_id}",
        "--label",
        "linemedic.role=mes",
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=16m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "10001:10001",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "1",
        "--volume",
        f"{data_dir.resolve()}:/data:ro",
    ]


def write_contract_data(data_dir: Path) -> list[Path]:
    """업무 계약 검증용 MES 데이터(W05 S1b·W12 배포): 공개 로트와 holdout **입력**만 둔다.

    기대값은 넣지 않는다. 돌려준 파일 목록은 verifier의 fixture 불변 확인에 쓴다.
    """
    lots = data_dir / "lots"
    lots.mkdir(parents=True, exist_ok=True)
    files = []
    for lot_id in (BUG_LOT, NORMAL_LOT):
        target = lots / f"{lot_id}.json"
        shutil.copyfile(SEED_LOTS_DIR / f"{lot_id}.json", target)
        files.append(target)
    holdout_input = json.loads(HOLDOUT_FIXTURE.read_text(encoding="utf-8"))["input"]
    target = lots / f"{holdout_input['lot_id']}.json"
    target.write_text(
        json.dumps(holdout_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files.append(target)
    return files


def prepare_s1_data(runs_dir: Path, run_id: str) -> Path:
    """run별 MES 데이터 디렉터리에 공개 로트 입력만 복사한다. holdout·기대값은 넣지 않는다."""
    data_dir = runs_dir / run_id / "mes-data"
    lots = data_dir / "lots"
    lots.mkdir(parents=True, exist_ok=True)
    for lot_id in (BUG_LOT, NORMAL_LOT):
        shutil.copyfile(SEED_LOTS_DIR / f"{lot_id}.json", lots / f"{lot_id}.json")
    return data_dir


def inject_s1(
    run_id: str,
    runs_dir: Path,
    image: str = DEFAULT_MES_IMAGE,
    run: RunFn = run_command,
    clock: Clock | None = None,
    sleep: Callable[[float], None] = time.sleep,
    store: Store | None = None,
    base_sha: str | None = None,
) -> dict[str, Any]:
    if not is_valid_run_id(run_id):
        raise ScenarioError(f"run_id 형식이 아니다: {run_id!r}")
    clock = clock or SystemClock()
    names = resource_names(run_id)
    label = f"linemedic.run_id={run_id}"

    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    if image_id.returncode != 0:
        raise ScenarioError(f"MES 이미지가 없다: {image} (먼저 make mes-image)")
    data_dir = prepare_s1_data(runs_dir, run_id)

    _checked(
        run,
        ["docker", "network", "create", "--internal", "--label", label, names["network"]],
        "내부 network 생성",
    )
    started = _checked(
        run,
        [
            "docker",
            "run",
            "--detach",
            *mes_container_options(names["container"], run_id, names["network"], data_dir),
            image_id.stdout.strip(),
        ],
        "MES 컨테이너 기동",
    )

    deadline = clock.monotonic() + HEALTH_TIMEOUT_SECONDS
    while _request(run, names["container"], "/healthz") != 200:
        if clock.monotonic() >= deadline:
            raise ScenarioError(
                "MES가 제한 시간 안에 준비되지 않았다 (컨테이너는 조사용으로 남긴다)"
            )
        sleep(HEALTH_POLL_SECONDS)

    if store is not None:
        with store.tx() as tx:
            record_deploy_observed(
                tx,
                run_id=run_id,
                service=MES_SERVICE,
                base_sha=base_sha,
                image_id=image_id.stdout.strip(),
                container=names["container"],
                container_id=started.stdout.strip(),
                actor=HARNESS_ACTOR,
            )

    requests = []
    for lot_id in [BUG_LOT] * BUG_REQUESTS + [NORMAL_LOT]:
        status = _request(run, names["container"], f"/defects/summary?lot_id={lot_id}")
        requests.append({"lot_id": lot_id, "status": status, "at": to_rfc3339(clock.utc_now())})

    return {
        "scenario": "s1",
        "run_id": run_id,
        "container": names["container"],
        "container_id": started.stdout.strip(),
        "image": image,
        "image_id": image_id.stdout.strip(),
        "network": names["network"],
        "data_dir": str(data_dir),
        "requests": requests,
        "deploy_recorded": store is not None,
    }


def read_mes_logs(run_id: str, run: RunFn = run_command) -> list[dict[str, Any]]:
    """컨테이너 stdout의 JSON Lines를 읽는다. JSON이 아닌 줄은 `_raw`로 표시한다."""
    result = run(["docker", "logs", resource_names(run_id)["container"]])
    lines = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            lines.append({"_raw": line})
    return lines


def stop_s1(run_id: str, run: RunFn = run_command) -> dict[str, int]:
    """이 run의 정확한 컨테이너·network만 지운다. 데이터 디렉터리는 보존한다."""
    if not is_valid_run_id(run_id):
        raise ScenarioError(f"run_id 형식이 아니다: {run_id!r}")
    names = resource_names(run_id)
    container = run(["docker", "rm", "--force", names["container"]])
    network = run(["docker", "network", "rm", names["network"]])
    return {"container_rm": container.returncode, "network_rm": network.returncode}


# ── S2-lite (W08) ─────────────────────────────────────────────

S2_SERVICE = "vision-inspection"
RECENT_DEPLOY_LEAD_MINUTES = 10  # recent-deploy 변형: 이상 시작 10분 전 mes-api 배포 기록


def inject_s2_lite(
    run_id: str,
    runs_dir: Path,
    store: Store,
    config: LineMedicConfig,
    clock: Clock | None = None,
    recent_deploy: bool = False,
    metrics_store: MetricsStore | None = None,
    base_sha: str | None = None,
    mes_image_id: str | None = None,
) -> dict[str, Any]:
    """L3 카메라 3대의 합성 지표를 run에 쓰고 감지기로 한 번 관찰한다(이상은 L3-CAM-2).

    `recent_deploy`면 이상 시작 전 mes-api 배포 기록을 더한다. 시뮬레이터 설계상 이 배포는 원인이
    아니지만, 그 사실은 어떤 기록·도구 응답에도 넣지 않는다(혼동 사례 S2-recent-deploy).
    """
    if not is_valid_run_id(run_id):
        raise ScenarioError(f"run_id 형식이 아니다: {run_id!r}")
    with store.read() as tx:
        run = tx.one("SELECT active, config_json FROM demo_runs WHERE id = ?", (run_id,))
    if run is None or run["active"] != 1:
        raise ScenarioError(f"활성 run이 아니다: {run_id} (먼저 make run-new)")
    routing_scope = json.loads(run["config_json"]).get("routing_scope") or f"eval:{run_id}"
    clock = clock or SystemClock()
    series = camera_metrics.generate_series(clock.utc_now())
    metrics_store = metrics_store or FileMetricsStore(runs_dir)
    for equipment_id, samples in series.items():
        metrics_store.write_series(run_id, equipment_id, samples)

    deployed_at = None
    if recent_deploy:
        onset = series["L3-CAM-2"][-camera_metrics.ANOMALY_SAMPLES].ts
        deployed_at = to_rfc3339(
            from_rfc3339(onset) - timedelta(minutes=RECENT_DEPLOY_LEAD_MINUTES)
        )
        with store.tx() as tx:
            record_deploy_observed(
                tx,
                run_id=run_id,
                service=MES_SERVICE,
                base_sha=base_sha,
                image_id=mes_image_id,
                container=None,
                container_id=None,
                actor=HARNESS_ACTOR,
                deployed_at=deployed_at,
            )

    watcher = Detector(
        store, settings_for_run(config, run_id, routing_scope, S2_SERVICE), clock, None
    )
    rule = metric_rule(config)
    incidents = []
    for equipment_id, samples in series.items():
        outcome = watcher.observe_metrics(equipment_id, samples, rule, comparison=series)
        if outcome.incident_id is not None and outcome.incident_id not in incidents:
            incidents.append(outcome.incident_id)
    return {
        "scenario": "s2-lite",
        "run_id": run_id,
        "equipment": {
            equipment_id: {
                "brightness": samples[-1].brightness,
                "confidence": samples[-1].confidence,
            }
            for equipment_id, samples in series.items()
        },
        "incidents": incidents,
        "recent_deploy": recent_deploy,
        "deployed_at": deployed_at,
    }
