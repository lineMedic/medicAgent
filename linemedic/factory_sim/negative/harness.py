"""S1b 거짓 정상 시험 harness (W05 1부, trusted harness 전용).

1. 신뢰 레시피 MES 이미지 위에 잘못된 집계를 덮은 S1b 이미지를 만든다.
2. 내부 network에 S1b MES 컨테이너와 신뢰 prober 컨테이너를 띄운다.
3. 관찰을 시작한 뒤(t0) verifier를 실행하고
   결과를 `runs/<run_id>/verifications/<VER-id>.json`에 저장한다.
4. 이 run의 정확한 컨테이너·network만 정리한다.

1부에서는 DB에 쓰지 않는다(2부에서 incident ESCALATED로 기록한다).
결과 origin은 human_injected_negative이며 에이전트 성과 집계에서 빠진다.
제품 broker·/tools에는 이 경로가 없다.
"""

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.ids import is_valid_run_id
from linemedic.control_plane.observer import ContainerObserver, RecurrenceSignature
from linemedic.control_plane.verifier import (
    DEFAULT_CONTRACT,
    EVAL_DIR,
    FixtureGuard,
    HttpClient,
    ProberHttp,
    RequestFailed,
    RequestTimeout,
    load_contract,
    resolve_cases,
    verify,
)
from linemedic.factory_sim.scenarios import (
    BUG_LOT,
    DEFAULT_MES_IMAGE,
    NORMAL_LOT,
    SEED_LOTS_DIR,
    mes_container_options,
)
from linemedic.integrations.docker import CliDocker, DockerPort

NEGATIVE_DIR = Path(__file__).resolve().parent
S1B_IMAGE = "linemedic-mes:s1b-negative"
PROBER_IMAGE = "python:3.12-slim"
ORIGIN = "human_injected_negative"
HOLDOUT = EVAL_DIR / "holdout-defects-v1.json"
# S1 사건의 오류 signature. 관찰 구간에 같은 오류가 다시 나면 재발로 센다.
S1_SIGNATURE = RecurrenceSignature(
    error_type="KeyError", top_frame="app.defects:summarize", path="/defects/summary"
)
HEALTH_TIMEOUT_SECONDS = 30.0


class HarnessError(RuntimeError):
    """S1b 시험을 진행할 수 없음."""


def resource_names(run_id: str) -> dict[str, str]:
    return {
        "mes": f"linemedic-s1b-{run_id}",
        "prober": f"linemedic-prober-{run_id}",
        "network": f"linemedic-vnet-{run_id}",
    }


def prepare_data(runs_dir: Path, run_id: str) -> tuple[Path, list[Path]]:
    """공개 로트와 holdout **입력**만 MES 데이터로 둔다. 기대값은 넣지 않는다."""
    data_dir = runs_dir / run_id / "verify-negative" / "mes-data"
    lots = data_dir / "lots"
    lots.mkdir(parents=True, exist_ok=True)
    files = []
    for lot_id in (BUG_LOT, NORMAL_LOT):
        target = lots / f"{lot_id}.json"
        shutil.copyfile(SEED_LOTS_DIR / f"{lot_id}.json", target)
        files.append(target)
    holdout_input = json.loads(HOLDOUT.read_text(encoding="utf-8"))["input"]
    target = lots / f"{holdout_input['lot_id']}.json"
    target.write_text(
        json.dumps(holdout_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files.append(target)
    return data_dir, files


def prober_options(name: str, run_id: str, network: str) -> list[str]:
    return [
        "--name",
        name,
        "--label",
        f"linemedic.run_id={run_id}",
        "--label",
        "linemedic.role=prober",
        "--network",
        network,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65534:65534",
        "--pids-limit",
        "32",
        "--memory",
        "128m",
    ]


def wait_healthy(http: HttpClient, clock: Clock, sleep: Callable[[float], None]) -> None:
    deadline = clock.monotonic() + HEALTH_TIMEOUT_SECONDS
    while True:
        try:
            if http.get("/healthz", {}).status == 200:
                return
        except (RequestTimeout, RequestFailed):
            pass
        if clock.monotonic() >= deadline:
            raise HarnessError("S1b MES가 제한 시간 안에 준비되지 않았다")
        sleep(0.5)


def run_verify_negative(
    run_id: str,
    runs_dir: Path,
    docker: DockerPort | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], None] = time.sleep,
    mes_image: str = DEFAULT_MES_IMAGE,
    expected_mes_image_id: str | None = None,
    prober_image: str = PROBER_IMAGE,
) -> dict[str, Any]:
    """S1b 시험 1회. `mes_image`는 태그여야 한다(BuildKit `FROM`은 image ID를 받지 않는다).

    `expected_mes_image_id`(env `MES_BASE_IMAGE_ID`)가 있으면 태그가 그 ID를 가리킬 때만 진행한다.
    """
    if not is_valid_run_id(run_id):
        raise HarnessError(f"run_id 형식이 아니다: {run_id!r}")
    docker = docker or CliDocker()
    clock = clock or SystemClock()
    names = resource_names(run_id)
    mes_image_id = docker.image_id(mes_image)
    if mes_image_id is None:
        raise HarnessError(f"MES 이미지가 없다: {mes_image} (먼저 make mes-image)")
    if expected_mes_image_id and mes_image_id != expected_mes_image_id:
        raise HarnessError(
            f"{mes_image}가 기록된 MES_BASE_IMAGE_ID와 다른 이미지를 가리킨다: {mes_image_id}"
        )
    s1b_image = docker.build(
        NEGATIVE_DIR, NEGATIVE_DIR / "s1b.Dockerfile", S1B_IMAGE, {"MES_IMAGE": mes_image}
    )
    data_dir, lot_files = prepare_data(runs_dir, run_id)

    observer = None
    try:
        created = docker.network_create(names["network"], {"linemedic.run_id": run_id})
        if created.returncode != 0:
            raise HarnessError(f"내부 network 생성 실패: {created.stderr.strip()[:200]}")
        docker.run(
            mes_container_options(names["mes"], run_id, names["network"], data_dir), s1b_image
        )
        docker.run(
            prober_options(names["prober"], run_id, names["network"]),
            prober_image,
            ["sleep", "infinity"],
        )
        http = ProberHttp(docker, names["prober"], f"http://{names['mes']}:8000")
        wait_healthy(http, clock, sleep)

        contract, contract_sha256 = load_contract(DEFAULT_CONTRACT)
        observer = ContainerObserver(docker, names["mes"], S1_SIGNATURE)
        identity = observer.start(since=to_rfc3339(clock.utc_now()))
        result = verify(
            contract=contract,
            contract_sha256=contract_sha256,
            cases=resolve_cases(contract),
            http=http,
            observer=observer,
            clock=clock,
            origin=ORIGIN,
            target={"container": names["mes"], "image": S1B_IMAGE, **identity},
            fixture_guard=FixtureGuard([DEFAULT_CONTRACT, HOLDOUT, *lot_files]),
        )
    finally:
        if observer is not None:
            observer.stop()
        docker.stop(names["mes"])
        docker.stop(names["prober"])
        docker.network_remove(names["network"])

    out_dir = runs_dir / run_id / "verifications"
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{result.verification_id}.json"
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "result": result.to_dict(),
        "result_path": str(result_path),
        "mes_image": mes_image,
        "mes_image_id": mes_image_id,
        "s1b_image_id": s1b_image,
        "prober_image": prober_image,
        "prober_image_id": docker.image_id(prober_image),
    }


def expected_outcome(result: dict[str, Any]) -> bool:
    """S1b에서 verifier가 기대대로 거절했는가: FAIL/content_mismatch, 복구 기록 없음."""
    return (
        result.get("verdict") == "FAIL"
        and result.get("reason") == "content_mismatch"
        and result.get("resolved_written") is False
        and result.get("observation_complete") is False
    )
