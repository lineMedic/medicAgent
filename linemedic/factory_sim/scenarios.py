"""합성 장애 주입 (W04: S1). 감지 연결은 W07, S2-lite 주입은 W08에서 추가한다.

`inject_s1(run_id)`는 버그 base MES 이미지로 컨테이너를 띄우고, 로트 118 요청 3회(60초 안)와
로트 101 요청 1회를 보낸다.

- 컨테이너: `linemedic.run_id` 라벨, 외부로 나갈 수 없는 내부 network(`--internal`), 비루트,
  read-only root, capability 제거, 자원 상한, 데이터는 read-only mount
- 요청은 컨테이너 안에서 자기 자신(127.0.0.1)에 보낸다. 내부 network라 호스트 port를 열지 않는다
- Docker는 고정 argv 리스트로만 호출한다(shell=False, D47)
- 정리는 run ID가 붙은 정확한 컨테이너·network 이름만 대상으로 한다
"""

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.ids import is_valid_run_id

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_LOTS_DIR = REPO_ROOT / "l3-mes-api-seed" / "data" / "lots"
DEFAULT_MES_IMAGE = "linemedic-mes:base"
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
            "--name",
            names["container"],
            "--label",
            label,
            "--label",
            "linemedic.role=mes",
            "--network",
            names["network"],
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
            f"{data_dir}:/data:ro",
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
        "note": "감지 연결(W07)이 docker logs로 이 컨테이너의 JSON 로그를 읽는다",
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
