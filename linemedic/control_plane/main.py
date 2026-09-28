"""`make start RUN_ID=` 진입점 (W13, docs/05 전체, spec 11 §4, D48·D53·D83).

한 프로세스·한 활성 run에서 Control API(uvicorn)와 루프를 함께 돌린다.

- 루프(thread): MES 로그 감지(W07, 상시), Issue poll(W23), incident → Issue(W24), outbox(W26),
  supervisor(시작 알림 만료·attempt 실행·만료, W25·W26·W13), broker(W09~W11),
  case builder(원본 event → 사례 노트, W27)
- 사례 검색(W27): config `memory.mode`. memory_assisted면 `MEMORY_SNAPSHOT_PATH`의 manifest를 읽는다
- 기동 복구(자동 재실행 없음): 끝나지 않은 API 요청·SENDING 알림 → UNKNOWN,
  CREATE_ISSUE·CREATE_PR·DEPLOY INTENDED/RUNNING → UNKNOWN, CHECKING 제안 → 다시 검사,
  끊긴 attempt → 이관, RUNNING 검증 → INCONCLUSIVE
- 외부 연결이 설정되지 않으면(G2 전 GitHub, RUNNER_IMAGE_ID 없음) 그 기능만 꺼진 채 뜬다.
  GitHub 쓰기는 config `github.write_enabled`(G10)가 켤 때만 한다
- `ControlPlane.step()`은 모든 루프를 한 번씩 순서대로 돈다(fake E2E·점검용)
- run이 비활성이 되면(W19 archive·run-new) 새 intake·dispatch·외부 쓰기 루프를 멈춘다
  (사례 노트만 계속). supervisor·broker·outbox는 이 run의 행만 다룬다(과거 run 작업·알림을
  이어받지 않는다)
- 종료: SIGTERM·SIGINT(`make stop`). pid 파일 `RUNS_DIR/<run>/control-plane.pid`
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.agent.adapter import AgentAdapter, ScriptedAdapter, http_tools_client
from linemedic.common.clock import Clock, SystemClock
from linemedic.common.config import Settings
from linemedic.common.ids import is_valid_run_id
from linemedic.control_plane import idempotency
from linemedic.control_plane.app import AppContext, create_app
from linemedic.control_plane.auth import TokenRegistry, host_operator
from linemedic.control_plane.broker.github_pr import PrOpener
from linemedic.control_plane.broker.intake import Broker
from linemedic.control_plane.broker.patch_gate import MIRROR_NAME, PatchGate
from linemedic.control_plane.broker.reconcile import ExecutionReconciler
from linemedic.control_plane.broker.runner import Runner, RunnerProfile
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import Detector, run_detect_once, settings_for_run
from linemedic.control_plane.issue_router import IssueRouter
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.log_store import FileLogStore
from linemedic.control_plane.memory.builder import CaseBuilder
from linemedic.control_plane.memory.search import CaseSearch
from linemedic.control_plane.metrics_store import FileMetricsStore
from linemedic.control_plane.notifications.github_comment import GitHubCommentAdapter
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.control_plane.release import ReleaseExecutor
from linemedic.control_plane.store import Store
from linemedic.control_plane.supervisor import AttemptRuntime, Supervisor
from linemedic.control_plane.verifier import DEFAULT_CONTRACT, load_contract
from linemedic.factory_sim.scenarios import MES_SERVICE, resource_names
from linemedic.integrations.docker import DockerError, DockerPort
from linemedic.integrations.git_fetch import CommitFetcher
from linemedic.integrations.git_push import BranchPusher
from linemedic.integrations.github import GitHubPort
from linemedic.integrations.sandbox import SandboxPort

REPO_ROOT = Path(__file__).resolve().parents[2]
PID_FILE = "control-plane.pid"
DEFAULT_MANUAL_PROPOSAL = REPO_ROOT / "linemedic" / "eval" / "manual_proposals" / "s1_manual.json"
DETECT_INTERVAL_SECONDS = 2.0
ROUTER_INTERVAL_SECONDS = 5.0
LOOP_INTERVAL_SECONDS = 1.0
STOP_WAIT_SECONDS = 30.0


class ControlPlaneError(RuntimeError):
    """기동할 수 없음(활성 run 아님·이미 실행 중 등)."""


def git_remote_url(settings: Settings) -> str:
    """등록 repo의 git 원격(push·fetch). github.com만 지원한다(GHE는 P0 범위 밖)."""
    repo, gh = settings.config.repository, settings.config.github
    if repo.full_name is None:
        raise ControlPlaneError("등록 repo가 없다(G2 전)")
    if gh.base_url.rstrip("/") != "https://api.github.com":
        raise ControlPlaneError("github.com이 아닌 GitHub의 git 원격은 설정하지 않았다")
    return f"https://github.com/{repo.full_name}.git"


@dataclass
class ControlPlane:
    run_id: str
    store: Store
    clock: Clock
    context: AppContext
    supervisor: Supervisor
    broker: Broker
    docker: DockerPort
    container: str
    detector: Detector | None = None
    cases: CaseBuilder | None = None
    sync: IssueSync | None = None
    router: IssueRouter | None = None
    worker: OutboxWorker | None = None
    opener: PrOpener | None = None
    release: ReleaseExecutor | None = None
    features: dict[str, str] = field(default_factory=dict)

    # 기동 복구

    def recover(self) -> dict[str, Any]:
        """재시작 때 결과를 모르는 것은 UNKNOWN으로, 끊긴 attempt는 이관한다.

        아무것도 다시 실행하지 않는다.
        """
        with self.store.tx() as tx:
            api_unknown = idempotency.mark_unknown(tx)
        return {
            "api_requests_unknown": api_unknown,
            "notifications_unknown": self.worker.recover_sending() if self.worker else 0,
            "issue_creates_unknown": self.router.recover() if self.router else [],
            "proposals_rechecked": self.broker.recover_checking(),
            "release": self.release.recover() if self.release else None,
            "attempts_closed": self.supervisor.recover_attempts(),
        }

    # 루프 한 번

    def intake_open(self) -> bool:
        """이 run이 아직 활성인가. archive·run-new 뒤에는 새 일·외부 쓰기를 하지 않는다(W19)."""
        with self.store.read() as tx:
            row = tx.one("SELECT active FROM demo_runs WHERE id = ?", (self.run_id,))
        return row is not None and row["active"] == 1

    def detect_once(self) -> dict[str, Any] | None:
        if self.detector is None or not self.intake_open():
            return None
        try:
            return run_detect_once(self.store, self.detector, self.docker, self.container)
        except DockerError:
            return None  # MES가 아직 없거나 교체 중이다

    def poll_once(self) -> dict[str, Any] | None:
        if self.sync is None or not self.intake_open():
            return None
        return self.sync.poll_once().as_dict()

    def route_once(self) -> list[dict[str, Any]]:
        if self.router is None or not self.intake_open():
            return []
        return [r.as_dict() for r in self.router.route_pending()]

    def outbox_once(self) -> list[dict[str, Any]]:
        if self.worker is None or not self.intake_open():
            return []
        return self.worker.process_pending()

    def supervise_once(self) -> dict[str, Any]:
        if not self.intake_open():
            return {"start_notices_expired": [], "attempts_closed": [], "attempts": []}
        return {
            "start_notices_expired": self.supervisor.expire_start_notices(),
            "attempts_closed": self.supervisor.expire_attempts(),
            "attempts": self.supervisor.run_ready() if self.supervisor.runtime else [],
        }

    def broker_once(self) -> list[str]:
        if not self.intake_open():
            return []
        return self.broker.process_pending(keep_going=True)

    def cases_once(self) -> list[str]:
        return self.cases.process_pending() if self.cases else []

    def step(self) -> dict[str, Any]:
        """모든 루프를 한 번씩 순서대로 돈다."""
        return {
            "intake_open": self.intake_open(),
            "detect": self.detect_once(),
            "poll": self.poll_once(),
            "route": self.route_once(),
            "outbox": self.outbox_once(),
            "supervisor": self.supervise_once(),
            "broker": self.broker_once(),
            "cases": self.cases_once(),
        }

    # thread 루프

    def start(self, stop: threading.Event) -> list[threading.Thread]:
        loops: list[tuple[str, Callable[[], Any], float]] = [
            ("detector", self.detect_once, DETECT_INTERVAL_SECONDS),
            ("router", self.route_once, ROUTER_INTERVAL_SECONDS),
            ("outbox", self.outbox_once, LOOP_INTERVAL_SECONDS),
            ("supervisor", self.supervise_once, LOOP_INTERVAL_SECONDS),
            ("broker", self.broker_once, LOOP_INTERVAL_SECONDS),
            ("cases", self.cases_once, LOOP_INTERVAL_SECONDS),
        ]
        threads = [_loop(name, body, interval, stop) for name, body, interval in loops]
        if self.sync is not None:
            poll = threading.Thread(
                target=self._poll_loop, args=(stop,), name="linemedic-poll", daemon=True
            )
            poll.start()
            threads.append(poll)
        return threads

    def _poll_loop(self, stop: threading.Event) -> None:
        """Issue poll 루프(간격·backoff는 IssueSync). run이 비활성이면 조회하지 않는다."""
        assert self.sync is not None
        while True:
            wait = float(self.sync.intake.poll_interval_seconds)
            try:
                if self.intake_open():
                    wait = self.sync.next_wait(self.sync.poll_once())
            except Exception as exc:  # noqa: BLE001 — 한 번의 오류로 루프를 멈추지 않는다
                print(f"[poll] {type(exc).__name__}", file=sys.stderr)
            if stop.wait(wait):
                return


def _loop(
    name: str, body: Callable[[], Any], interval: float, stop: threading.Event
) -> threading.Thread:
    def run() -> None:
        while not stop.is_set():
            try:
                body()
            except Exception as exc:  # noqa: BLE001 — 한 번의 오류로 루프를 멈추지 않는다
                print(f"[{name}] {type(exc).__name__}", file=sys.stderr)
            if stop.wait(interval):
                return

    thread = threading.Thread(target=run, name=f"linemedic-{name}", daemon=True)
    thread.start()
    return thread


def build_control_plane(
    settings: Settings,
    run_id: str,
    *,
    store: Store,
    clock: Clock,
    tokens: TokenRegistry,
    runs_dir: Path,
    docker: DockerPort,
    github: GitHubPort | None = None,
    pusher: BranchPusher | None = None,
    fetcher: CommitFetcher | None = None,
    adapter: AgentAdapter | None = None,
    sandbox: SandboxPort | None = None,
    patch_gate: PatchGate | None = None,
    release_runner: Runner | None = None,
    dispatch: Callable[[Callable[[], Any]], None] | None = None,
) -> ControlPlane:
    """설정과 주입한 외부 연결로 구성요소를 조립한다. 연결이 없는 기능은 끈다."""
    config = settings.config
    with store.read() as tx:
        run = tx.one("SELECT active, config_json FROM demo_runs WHERE id = ?", (run_id,))
    if run is None or run["active"] != 1:
        raise ControlPlaneError(f"활성 run이 아니다: {run_id} (먼저 make run-new)")
    manifest = json.loads(run["config_json"])
    routing_scope = manifest.get("routing_scope") or f"eval:{run_id}"
    route_id = config.notifications.required_start_route_id
    catalog = Catalog.from_config(config)
    knowledge = KnowledgeBase()
    log_store = FileLogStore(runs_dir)
    mirror = runs_dir / "mirror" / MIRROR_NAME
    api = config.control_api
    features: dict[str, str] = {}

    detector = Detector(
        store,
        settings_for_run(config, run_id, routing_scope, MES_SERVICE),
        clock,
        log_store,
        eval_identifiers(),
    )
    terms = eval_identifiers()
    contract, contract_sha256 = load_contract(DEFAULT_CONTRACT)
    case_search = CaseSearch.from_config(
        store,
        config.memory,
        contract_id=contract.contract_id,
        contract_sha256=contract_sha256,
        related_services=catalog.related_services,
        terms=terms,
    )
    features["memory"] = case_search.describe()
    runtime = None
    if adapter is not None:
        runtime = AttemptRuntime(
            adapter=adapter,
            tokens=tokens,
            mirror=mirror,
            runs_dir=runs_dir,
            tools_base_url=f"http://{api.host}:{api.port}",
            eval_terms=tuple(sorted(terms)),
            sandbox=sandbox,
            case_search=case_search,  # attempt 시작 때 host 초기 사례 검색(W28)
        )
        features["agent"] = f"on: {adapter.name} (origin {adapter.origin})"
        if config.agent.mode == "sandbox":  # sandbox를 준비하지 못하면 local로 돌리지 않는다
            features["sandbox"] = (
                f"on: {sandbox.name}"
                if sandbox is not None
                else "off: sandbox 구현 없음(G5 뒤) — sandbox 모드 attempt는 시작하지 않는다"
            )
    else:
        features["agent"] = "off: adapter 없음(attempt는 만들지만 실행하지 않는다)"
    supervisor = Supervisor(
        store, config=config, clock=clock, route_id=route_id, runtime=runtime, run_id=run_id
    )

    sync = router = worker = opener = release = None
    if github is not None:
        sync = IssueSync(
            store,
            github,
            run_id=run_id,
            config=config,
            catalog=catalog,
            clock=clock,
            routing_scope=routing_scope,
            auto_approve=supervisor.auto_approve,
            on_scope_changed=supervisor.on_scope_changed,
        )
        router = IssueRouter(store, github, sync, catalog=catalog, clock=clock)
        worker = OutboxWorker(
            store,
            adapters={"github_comment": GitHubCommentAdapter(github)},
            config=config,
            repo=github.full_name,
            clock=clock,
            run_id=run_id,
        )
        features["github"] = (
            "on (쓰기 " + ("켜짐" if github.write_enabled else "꺼짐: shadow") + ")"
        )
        if pusher is not None:
            opener = PrOpener(store, github, pusher, sync, route_id=route_id)
        if release_runner is not None:
            release = ReleaseExecutor(
                store,
                port=github,
                catalog=catalog,
                docker=docker,
                runner=release_runner,
                mirror=mirror,
                runs_dir=runs_dir,
                clock=clock,
                route_id=route_id,
                fetcher=fetcher,
                dispatch=dispatch,
            )
    else:
        features["github"] = "off: GitHub 연결 없음(G2 전)"
    features["patch_gate"] = "on" if patch_gate is not None else "off: RUNNER_IMAGE_ID 없음"
    features["release"] = "on" if release is not None else "off: GitHub 또는 runner 없음"

    broker = Broker(
        store,
        catalog,
        load_manual_templates(knowledge),
        route_id,
        max_submissions=config.agent.max_submissions,
        patch_gate=patch_gate,
        pr_opener=opener,
        run_id=run_id,
    )
    reconciler = ExecutionReconciler(
        store, opener=opener, issue_router=router, route_id=route_id, release=release
    )
    tools = config.tools
    context = AppContext(
        store=store,
        tokens=tokens,
        clock=clock,
        notification_route_id=route_id,
        max_body_bytes=config.proposal.max_bytes,
        log_store=log_store,
        logs_window_minutes=tools.logs.window_minutes,
        logs_max_bytes=tools.logs.max_bytes,
        deploys_window_hours=tools.deploys.window_hours,
        catalog=catalog,
        metrics_store=FileMetricsStore(runs_dir),
        metrics_max_minutes=tools.metrics.max_minutes,
        metrics_max_samples=tools.metrics.max_samples,
        knowledge=knowledge,
        max_submissions=config.agent.max_submissions,
        tool_call_budget=config.agent.tool_call_budget,
        issue_sync=sync,
        issue_router=router,
        outbox_worker=worker,
        execution_reconciler=reconciler,
        release_executor=release,
        case_search=case_search,
        settings=settings,
        runs_dir=runs_dir,
    )
    return ControlPlane(
        run_id=run_id,
        store=store,
        clock=clock,
        context=context,
        supervisor=supervisor,
        broker=broker,
        docker=docker,
        container=resource_names(run_id)["container"],
        detector=detector,
        cases=CaseBuilder(store, terms=terms),
        sync=sync,
        router=router,
        worker=worker,
        opener=opener,
        release=release,
        features=features,
    )


# ── 프로세스 ────────────────────────────────────────────────


def _process_command(pid: int) -> str | None:
    """그 pid의 명령줄(없으면 None)."""
    proc = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _ours(pid: int, run_id: str) -> bool:
    command = _process_command(pid)
    return command is not None and "linemedic" in command and run_id in command


def pid_path(runs_dir: Path, run_id: str) -> Path:
    if not is_valid_run_id(run_id):
        raise ControlPlaneError(f"run_id 형식이 아니다: {run_id!r}")
    return runs_dir / run_id / PID_FILE


def claim_pid_file(runs_dir: Path, run_id: str) -> Path:
    """이 run의 pid 파일을 쓴다. 같은 run의 프로세스가 살아 있으면 기동하지 않는다."""
    path = pid_path(runs_dir, run_id)
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text.isdigit() and _ours(int(text), run_id):
            raise ControlPlaneError(f"이미 실행 중이다: pid {text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def stop(runs_dir: Path, run_id: str, wait_seconds: float = STOP_WAIT_SECONDS) -> str:
    """`make stop`: pid 파일의 프로세스가 이 run의 LineMedic일 때만 SIGTERM을 보낸다."""
    path = pid_path(runs_dir, run_id)
    if not path.exists():
        return "not_running"
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        raise ControlPlaneError(f"pid 파일 형식이 아니다: {path}")
    pid = int(text)
    if _process_command(pid) is None:
        path.unlink(missing_ok=True)  # 남은 파일: 프로세스가 이미 없다
        return "stale_pid_removed"
    if not _ours(pid, run_id):
        raise ControlPlaneError(f"pid {pid}는 이 run의 LineMedic 프로세스가 아니다(보내지 않음)")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _process_command(pid) is None:
            return "stopped"
        time.sleep(0.2)
    return "signal_sent"


def start(
    settings: Settings,
    env: dict[str, str],
    run_id: str,
    *,
    db_path: Path,
    manual_proposal: Path = DEFAULT_MANUAL_PROPOSAL,
) -> int:
    """실제 연결로 조립해 기동한다. 종료 신호가 올 때까지 돌아온다."""
    import uvicorn  # 기동할 때만 필요하다

    from linemedic.control_plane.broker.patch_gate import patch_gate_from_settings
    from linemedic.integrations.docker import CliDocker
    from linemedic.integrations.git_fetch import GitFetcher
    from linemedic.integrations.git_push import GitPusher
    from linemedic.integrations.github import GitHubNotConfigured, github_from_settings

    operator_token = settings.secrets.get("CONTROL_OPERATOR_TOKEN")
    if not operator_token:
        raise ControlPlaneError("NOT_CONFIGURED: CONTROL_OPERATOR_TOKEN")
    runs_dir = Path(env.get("RUNS_DIR") or "runs").resolve()
    clock = SystemClock()
    store = Store(db_path, clock)
    store.migrate()
    tokens = TokenRegistry()
    tokens.register_operator(*host_operator(operator_token))
    docker = CliDocker()
    github = pusher = fetcher = None
    try:
        github = github_from_settings(settings)
        remote = git_remote_url(settings)
        credential = settings.secrets.get("GITHUB_BROKER_CREDENTIAL")
        pusher, fetcher = GitPusher(remote, credential), GitFetcher(remote, credential)
    except (GitHubNotConfigured, ControlPlaneError):
        github = pusher = fetcher = None
    profile = RunnerProfile.from_settings(settings)
    api = settings.config.control_api
    adapter = ScriptedAdapter(
        manual_proposal, http_tools_client(f"http://{api.host}:{api.port}"), clock
    )
    plane = build_control_plane(
        settings,
        run_id,
        store=store,
        clock=clock,
        tokens=tokens,
        runs_dir=runs_dir,
        docker=docker,
        github=github,
        pusher=pusher,
        fetcher=fetcher,
        adapter=adapter,
        patch_gate=patch_gate_from_settings(settings, docker, clock),
        release_runner=Runner(docker, profile, clock) if profile is not None else None,
    )
    path = claim_pid_file(runs_dir, run_id)
    stopping = threading.Event()
    threads: list[threading.Thread] = []
    try:
        recovered = plane.recover()
        print(
            json.dumps(
                {"run_id": run_id, "features": plane.features, "recovered": recovered},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        threads = plane.start(stopping)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(plane.context),
                host=api.host,
                port=api.port,
                log_level="warning",
                access_log=False,
            )
        )
        # uvicorn은 종료 뒤 원래 처리기를 되돌리고 받은 신호를 다시 올린다. 기본 처리기면
        # 여기서 프로세스가 끝나 아래 정리(루프 정지·pid 파일 삭제)를 건너뛴다.
        # 그래서 정지 표시만 하는 처리기를 먼저 둔다
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda received, frame: stopping.set())
        server.run()  # SIGTERM·SIGINT에서 끝난다
    finally:
        stopping.set()
        for thread in threads:
            thread.join(timeout=5)
        path.unlink(missing_ok=True)
        if github is not None:
            github.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    from linemedic import cli

    return cli.main(["start", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
