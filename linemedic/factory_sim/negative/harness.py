"""S1b 거짓 정상 시험 harness (W05, trusted harness 전용).

1. 제어 DB의 활성 run을 확인한다(`make run-new`가 만든 run).
2. 신뢰 레시피 MES 이미지 위에 잘못된 집계를 덮은 S1b 이미지를 만든다.
3. 내부 network에 S1b MES 컨테이너와 신뢰 prober 컨테이너를 띄운다.
4. 관찰을 시작하고, 시험 사건을 VERIFYING으로 준비한 뒤(t0) verifier를 실행한다.
5. `verifier.persist_result`로 결과를 저장하고 verifier 주체로 사건을 전이한다(S1b는 ESCALATED).
   같은 결과를 `runs/<run_id>/verifications/<VER-id>.json`에도 남긴다.
6. 이 run의 정확한 컨테이너·network만 정리한다.

결과 origin은 human_injected_negative이며 에이전트 성과 집계에서 빠진다.
제품 broker·/tools에는 이 경로가 없다. 사건 준비는 테스트·demo 전용 도우미(`demo_states`)만 쓴다.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.ids import is_valid_run_id
from linemedic.control_plane.observer import ContainerObserver, RecurrenceSignature
from linemedic.control_plane.store import Store
from linemedic.control_plane.verifier import (
    DEFAULT_CONTRACT,
    PROBER_IMAGE,
    FixtureGuard,
    HttpClient,
    ProberHttp,
    VerificationRun,
    drive,
    load_contract,
    persist_result,
    prober_options,
    resolve_cases,
    wait_until_healthy,
)
from linemedic.factory_sim.scenarios import (
    DEFAULT_MES_IMAGE,
    HOLDOUT_FIXTURE,
    mes_container_options,
    write_contract_data,
)
from linemedic.integrations.docker import CliDocker, DockerPort
from linemedic.tests.helpers.demo_states import prepare_verifying_incident

NEGATIVE_DIR = Path(__file__).resolve().parent
S1B_IMAGE = "linemedic-mes:s1b-negative"
ORIGIN = "human_injected_negative"
HOLDOUT = HOLDOUT_FIXTURE
# S1 사건의 오류 signature. 관찰 구간에 같은 오류가 다시 나면 재발로 센다.
S1_SIGNATURE = RecurrenceSignature(
    error_type="KeyError", top_frame="app.defects:summarize", path="/defects/summary"
)
DEFAULT_ROUTE_ID = "github-issue-primary"
DEMO_PURPOSE = "verifier_negative_test"
DEMO_FINGERPRINT = "verifier-negative:defect-summary-v1"


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
    return data_dir, write_contract_data(data_dir)


def wait_healthy(http: HttpClient, clock: Clock, sleep: Callable[[float], None]) -> None:
    if not wait_until_healthy(http, clock, sleep):
        raise HarnessError("S1b MES가 제한 시간 안에 준비되지 않았다")


def _active_run(store: Store, run_id: str) -> None:
    with store.read() as tx:
        run = tx.one("SELECT active FROM demo_runs WHERE id = ?", (run_id,))
        stale = tx.one(
            "SELECT id FROM incidents"
            " WHERE run_id = ? AND fingerprint = ? AND status = 'VERIFYING'",
            (run_id, DEMO_FINGERPRINT),
        )
    if run is None:
        raise HarnessError(f"run이 제어 DB에 없다: {run_id} (먼저 make run-new)")
    if run["active"] != 1:
        raise HarnessError(f"활성 run이 아니다: {run_id}")
    if stale is not None:
        raise HarnessError(
            f"이 run에 끝나지 않은 S1b 시험 사건이 있다: {stale['id']} "
            "(이전 실행이 강제 종료됨). 새 run(make run-new)에서 다시 실행한다"
        )


def run_verify_negative(
    run_id: str,
    runs_dir: Path,
    *,
    store: Store,
    docker: DockerPort | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], None] = time.sleep,
    mes_image: str = DEFAULT_MES_IMAGE,
    expected_mes_image_id: str | None = None,
    prober_image: str = PROBER_IMAGE,
    route_id: str = DEFAULT_ROUTE_ID,
) -> dict[str, Any]:
    """S1b 시험 1회. `mes_image`는 태그여야 한다(BuildKit `FROM`은 image ID를 받지 않는다).

    `expected_mes_image_id`(env `MES_BASE_IMAGE_ID`)가 있으면 태그가 그 ID를 가리킬 때만 진행한다.
    """
    if not is_valid_run_id(run_id):
        raise HarnessError(f"run_id 형식이 아니다: {run_id!r}")
    _active_run(store, run_id)
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
    incident_id = None
    interrupted: BaseException | None = None
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
        with store.tx() as tx:
            incident_id = prepare_verifying_incident(
                tx, run_id, purpose=DEMO_PURPOSE, fingerprint=DEMO_FINGERPRINT
            )
        run = VerificationRun(
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
        try:
            result = drive(run)
        except (
            BaseException
        ) as exc:  # Ctrl-C 등: 사건을 VERIFYING에 남기지 않고 기록한 뒤 다시 올린다
            interrupted = exc
            result = run.abort(f"interrupted:{type(exc).__name__}")
    finally:
        if observer is not None:
            observer.stop()
        docker.stop(names["mes"])
        docker.stop(names["prober"])
        docker.network_remove(names["network"])

    out_dir = runs_dir / run_id / "verifications"
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{result.verification_id}.json"
    stored = result.to_dict()
    try:
        with store.tx() as tx:
            persisted = persist_result(
                tx,
                result,
                run_id=run_id,
                incident_id=incident_id,
                expected_incident_version=0,
                route_id=route_id,
            )
        stored = persisted.result
    finally:
        result_path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if interrupted is not None:
        raise interrupted
    return {
        "result": stored,
        "result_path": str(result_path),
        "incident_id": incident_id,
        "incident_status": persisted.incident_status,
        "incident_version": persisted.incident_version,
        "notification_id": persisted.notification_id,
        "mes_image": mes_image,
        "mes_image_id": mes_image_id,
        "s1b_image_id": s1b_image,
        "prober_image": prober_image,
        "prober_image_id": docker.image_id(prober_image),
    }


def expected_outcome(outcome: dict[str, Any]) -> bool:
    """S1b를 기대대로 거절했는가: FAIL/content_mismatch, 사건 ESCALATED, 복구 기록 없음."""
    result = outcome.get("result", {})
    return (
        result.get("verdict") == "FAIL"
        and result.get("reason") == "content_mismatch"
        and result.get("resolved_written") is False
        and result.get("observation_complete") is False
        and outcome.get("incident_status") == "ESCALATED"
    )
