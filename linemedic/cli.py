"""운영 CLI 진입점: `python -m linemedic.cli <command>` (D48).

카드마다 subcommand를 추가한다. 구현되지 않은 명령을 있다고 안내하지 않는다.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx

from linemedic import __version__
from linemedic.common.clock import SystemClock
from linemedic.common.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    ConfigError,
    load_settings,
    process_env,
)
from linemedic.control_plane import detector, release, run_export, runs
from linemedic.control_plane import main as control_main
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.log_store import FileLogStore
from linemedic.control_plane.memory import snapshot as memory_snapshot
from linemedic.control_plane.observer import ObserverError
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.control_plane.store import Store, StoreError
from linemedic.factory_sim import scenarios
from linemedic.factory_sim.negative import harness
from linemedic.integrations.docker import CliDocker, DockerError
from linemedic.integrations.github import GitHubNotConfigured, github_from_settings
from linemedic.integrations.github_baseline import BaselineError, HttpBaseline
from linemedic.scripts import doctor, host_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linemedic")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="패키지 버전 출력")

    doctor_parser = sub.add_parser(
        "doctor", help="환경 readiness 점검 (필수 항목 실패 시 종료 코드 1)"
    )
    doctor_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    doctor_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    manifest_parser = sub.add_parser("host-manifest", help="호스트 manifest JSON 출력")
    manifest_parser.add_argument(
        "--output", type=Path, help="JSON을 저장할 경로 (예: evidence/host-manifest.json)"
    )

    s1_parser = sub.add_parser(
        "scenario-s1", help="버그 base MES 컨테이너 기동 후 로트 118 요청 3회·101 요청 1회 (W04)"
    )
    s1_parser.add_argument("--run-id", required=True, help="r-YYYYMMDD-HHMMSS-xxxx")
    s1_parser.add_argument(
        "--image",
        help=f"MES 이미지 (기본: env MES_BASE_IMAGE_ID 또는 {scenarios.DEFAULT_MES_IMAGE})",
    )
    s1_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    s1_parser.add_argument(
        "--db",
        type=Path,
        help="배포 관찰을 기록할 제어 DB (기본: <RUNS_DIR 또는 runs>/linemedic.db)",
    )

    s2_parser = sub.add_parser(
        "scenario-s2-lite",
        help="L3 카메라 합성 지표를 run에 쓰고 설비 이상을 감지 (W08, 이상은 L3-CAM-2)",
    )
    s2_parser.add_argument("--run-id", required=True, help="make run-new가 만든 활성 run")
    s2_parser.add_argument(
        "--recent-deploy", action="store_true", help="이상 시작 전 mes-api 배포 기록을 더한다"
    )
    s2_parser.add_argument("--db", type=Path, help="제어 DB 경로")
    s2_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    s2_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    sync_parser = sub.add_parser(
        "issue-sync",
        help="등록 repo Issue를 한 번 조회해 mirror·checkpoint를 갱신 (W23, 처음이면 관찰만)",
    )
    sync_parser.add_argument("--run-id", required=True, help="make run-new가 만든 활성 run")
    sync_parser.add_argument("--db", type=Path, help="제어 DB 경로")
    sync_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sync_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    bind_parser = sub.add_parser(
        "issue-bind",
        help="운영자가 incident를 등록 repo의 Issue 번호에 명시적으로 연결 (W24, Control API 호출)",
    )
    bind_parser.add_argument("--incident-id", required=True)
    bind_parser.add_argument("--issue-number", required=True, type=int)
    bind_parser.add_argument(
        "--expected-version", type=int, help="확인한 incident version (생략하면 지금 값을 읽는다)"
    )
    bind_parser.add_argument("--note", default="운영자 CLI 연결", help="연결 판단 메모")
    bind_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    bind_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    approve_parser = sub.add_parser(
        "approve-work", help="work의 정확한 Issue snapshot을 승인 (W25, Control API 호출)"
    )
    approve_parser.add_argument("--work-id", required=True)
    approve_parser.add_argument("--expected-version", required=True, type=int)
    approve_parser.add_argument(
        "--expected-snapshot", help="승인할 Issue snapshot hash (생략하면 work가 만든 때의 값)"
    )
    approve_parser.add_argument("--note", default="운영자 CLI 승인")
    retry_parser = sub.add_parser(
        "retry-work", help="terminal work를 새 generation으로 다시 승인 대기에 올림 (W25)"
    )
    retry_parser.add_argument("--work-id", required=True)
    retry_parser.add_argument("--reason", required=True, help="blocker 해소 확인 메모")
    retry_parser.add_argument("--expected-version", type=int)
    cancel_parser = sub.add_parser("cancel-work", help="work 취소 또는 취소 요청 (W25)")
    cancel_parser.add_argument("--work-id", required=True)
    cancel_parser.add_argument("--expected-version", type=int)
    cancel_parser.add_argument("--note", default="운영자 CLI 취소")
    reconcile_parser = sub.add_parser(
        "notification-reconcile",
        help="UNKNOWN 알림을 bound Issue 댓글 조회로만 조정 (W26, 다시 보내지 않음)",
    )
    reconcile_parser.add_argument("--notification-id", required=True)
    execution_parser = sub.add_parser(
        "reconcile",
        help="결과 불명 execution(CREATE_PR·CREATE_ISSUE·DEPLOY)을 외부 조회로만 조정 (W11·W12)",
    )
    execution_parser.add_argument("--run-id", required=True)
    execution_parser.add_argument("--execution-id", required=True)
    release_parser = sub.add_parser(
        "approve-release",
        help="사람이 머지한 PR의 최종 merge SHA 배포 승인 (W12·G8, 사람이 직접 실행)",
    )
    release_parser.add_argument("--run-id", required=True)
    release_parser.add_argument("--incident-id", required=True)
    release_parser.add_argument("--work-id", required=True)
    release_parser.add_argument("--pr-number", required=True, type=int)
    release_parser.add_argument("--merge-sha", required=True, help="GitHub merged=true의 최종 SHA")
    release_parser.add_argument(
        "--expected-image-id", required=True, help="지금 실행 중인 MES image ID(sha256:...)"
    )
    release_parser.add_argument("--proposal-id", help="생략하면 work의 봇 PR execution에서 읽는다")
    release_parser.add_argument("--note", default="운영자 CLI 배포 승인")
    for command_parser in (
        approve_parser,
        retry_parser,
        cancel_parser,
        reconcile_parser,
        execution_parser,
        release_parser,
    ):
        command_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
        command_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    detect_parser = sub.add_parser(
        "detect-once",
        help="run의 S1 MES 컨테이너 로그를 한 번 읽어 감지기에 넣는다 (W07, 상시 감시는 W13)",
    )
    detect_parser.add_argument("--run-id", required=True, help="make run-new가 만든 활성 run")
    detect_parser.add_argument("--db", type=Path, help="제어 DB 경로")
    detect_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    detect_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    negative_parser = sub.add_parser(
        "verify-negative",
        help="S1b 거짓 정상 이미지로 verifier 실행·DB 기록, 기대대로 거절하면 종료 코드 0 (W05)",
    )
    negative_parser.add_argument(
        "--run-id", required=True, help="make run-new가 만든 활성 run (r-YYYYMMDD-HHMMSS-xxxx)"
    )
    negative_parser.add_argument(
        "--db", type=Path, help="제어 DB 경로 (기본: <RUNS_DIR 또는 runs>/linemedic.db)"
    )
    negative_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    negative_parser.add_argument(
        "--mes-image",
        default=scenarios.DEFAULT_MES_IMAGE,
        help="S1b를 덮을 MES 이미지 태그 (env MES_BASE_IMAGE_ID가 있으면 ID 일치를 확인)",
    )
    negative_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    start_parser = sub.add_parser(
        "start",
        help="Control API와 루프를 한 프로세스로 기동 (W13 make start, SIGTERM·SIGINT로 종료)",
    )
    start_parser.add_argument("--run-id", required=True, help="make run-new가 만든 활성 run")
    start_parser.add_argument("--db", type=Path, help="제어 DB 경로")
    start_parser.add_argument(
        "--manual-proposal",
        type=Path,
        default=control_main.DEFAULT_MANUAL_PROPOSAL,
        help="ScriptedAdapter가 제출할 사람 제안(origin manual_integration)",
    )
    start_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    start_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    stop_parser = sub.add_parser(
        "stop", help="make start로 띄운 이 run의 프로세스에 종료 신호를 보낸다 (W13 make stop)"
    )
    stop_parser.add_argument("--run-id", required=True)
    stop_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    rebuild_parser = sub.add_parser(
        "rebuild-case-index",
        help="PUBLISHED 사례 노트로 검색 색인을 다시 만든다 (W27, Control API, outcome 불변)",
    )
    rebuild_parser.add_argument("--run-id", help="생략하면 제어 DB의 활성 run")
    rebuild_parser.add_argument("--db", type=Path, help="활성 run을 읽을 제어 DB 경로")
    rebuild_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    rebuild_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    snapshot_parser = sub.add_parser(
        "memory-snapshot",
        help="memory_assisted 평가용 사례 snapshot manifest를 만든다 (W27, 덮어쓰지 않음)",
    )
    snapshot_parser.add_argument("--run-id", required=True, help="이 snapshot을 쓸 평가 run")
    snapshot_parser.add_argument("--db", type=Path, help="제어 DB 경로")
    snapshot_parser.add_argument("--cutoff", help="이 시각(UTC RFC3339) 이전 기록만. 생략하면 지금")
    snapshot_parser.add_argument(
        "--output-dir", type=Path, default=memory_snapshot.SNAPSHOT_DIR, help="manifest 폴더"
    )
    choose = snapshot_parser.add_mutually_exclusive_group()  # G9: 사람이 후보를 보고 고른다
    choose.add_argument("--list", action="store_true", help="후보만 출력(파일·감사 기록 없음)")
    choose.add_argument("--notes", help="넣을 note ID(쉼표로 구분)")
    choose.add_argument("--notes-file", type=Path, help="넣을 note ID 파일(줄마다 하나, # 주석)")
    snapshot_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    snapshot_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    run_parser = sub.add_parser(
        "run-new", help="제어 DB migration 후 새 run을 활성으로 기록 (W06: DB 부분, W19에서 완성)"
    )
    run_parser.add_argument(
        "--db", type=Path, help="제어 DB 경로 (기본: <RUNS_DIR 또는 runs>/linemedic.db)"
    )
    run_parser.add_argument(
        "--host-manifest",
        type=Path,
        help="run manifest에 경로·SHA-256을 남길 host manifest (예: evidence/host-manifest.json)",
    )
    run_parser.add_argument(
        "--create-baseline",
        action="store_true",
        help="setup credential로 baseline/<run_id> 브랜치를 BASELINE_COMMIT에 만든다(G2·G10 뒤)",
    )
    run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    export_parser = sub.add_parser(
        "export-run", help="run 증거 export(공유본·비공개 원본, 덮어쓰지 않음) (W19)"
    )
    reset_parser = sub.add_parser(
        "reset",
        help="run 정지·미해결 확인·export·이 run 라벨 컨테이너·workspace 정리 (W19, DB 삭제 없음)",
    )
    for command_parser in (export_parser, reset_parser):
        command_parser.add_argument("--run-id", required=True)
        command_parser.add_argument("--db", type=Path, help="제어 DB 경로")
        command_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    return parser


def _run_manifest(store: Store, run_id: str) -> dict | None:
    """활성 run이면 manifest를, 없거나 비활성이면 None을 돌려준다."""
    with store.read() as tx:
        row = tx.one("SELECT active, config_json FROM demo_runs WHERE id = ?", (run_id,))
    if row is None or row["active"] != 1:
        return None
    return json.loads(row["config_json"])


def _run_store(db_path: Path, run_id: str) -> Store | None:
    """제어 DB에 활성 run이 있으면 그 Store. 없으면 기록 없이 진행한다(W04 동작 유지)."""
    if not db_path.is_file():
        return None
    store = Store(db_path, SystemClock())
    store.migrate()
    return store if _run_manifest(store, run_id) is not None else None


def _release_locked(store: Store | None, run_id: str, command: str) -> bool:
    """배포·업무 검증 중(W12 run lock)이면 장애를 주입하지 않는다(spec 08 §2, D83 ⑥)."""
    if store is None:
        return False
    with store.read() as tx:
        holder = release.lock_holder(tx, run_id)
    if holder is None:
        return False
    print(
        f"{command} 실패: 이 run은 배포·업무 검증 중이다(execution {holder}). 끝나거나"
        f" 결과 불명이면 조정한 뒤 다시 한다: make reconcile RUN_ID={run_id} EXECUTION_ID={holder}",
        file=sys.stderr,
    )
    return True


def _scenario_s2_lite(args: argparse.Namespace) -> int:
    env = process_env(args.env_file)
    db_path = args.db or runs.default_db_path(env)
    store = _run_store(db_path, args.run_id)
    if store is None:
        print(
            f"scenario-s2-lite 실패: 제어 DB에 활성 run이 없다: {args.run_id} (먼저 make run-new)",
            file=sys.stderr,
        )
        return 2
    if _release_locked(store, args.run_id, "scenario-s2-lite"):
        return 2
    try:
        settings = load_settings(args.config, env)
        result = scenarios.inject_s2_lite(
            args.run_id,
            runs_dir=Path(env.get("RUNS_DIR") or "runs"),
            store=store,
            config=settings.config,
            recent_deploy=args.recent_deploy,
            base_sha=env.get("BASELINE_COMMIT") or None,
            mes_image_id=env.get("MES_BASE_IMAGE_ID") or None,
        )
    except (scenarios.ScenarioError, ConfigError, StoreError, ValueError) as exc:
        print(f"scenario-s2-lite 실패: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _issue_sync(args: argparse.Namespace) -> int:
    env = process_env(args.env_file)
    try:
        settings = load_settings(args.config, env)
        port = github_from_settings(settings)
    except GitHubNotConfigured as exc:
        print(f"issue-sync: NOT_CONFIGURED (G2): {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"issue-sync 실패: {exc}", file=sys.stderr)
        return 2
    try:
        db_path = args.db or runs.default_db_path(env)
        if not db_path.is_file():
            print(
                f"issue-sync 실패: 제어 DB가 없다: {db_path} (먼저 make run-new)", file=sys.stderr
            )
            return 2
        clock = SystemClock()
        store = Store(db_path, clock)
        store.migrate()
        manifest = _run_manifest(store, args.run_id)
        if manifest is None:
            print(f"issue-sync 실패: 활성 run이 아니다: {args.run_id}", file=sys.stderr)
            return 2
        sync = IssueSync(
            store,
            port,
            run_id=args.run_id,
            config=settings.config,
            catalog=Catalog.from_config(settings.config),
            clock=clock,
            routing_scope=manifest.get("routing_scope") or f"eval:{args.run_id}",
        )
        result = sync.poll_once()
    except (StoreError, ValueError) as exc:
        print(f"issue-sync 실패: {exc}", file=sys.stderr)
        return 2
    finally:
        port.close()
    print(json.dumps({"run_id": args.run_id, **result.as_dict()}, ensure_ascii=False, indent=2))
    return 0 if result.error is None and result.mode not in ("busy", "backoff") else 1


def _ops_target(args: argparse.Namespace, command: str) -> tuple[str, dict[str, str]] | None:
    """D48: `/ops/*`에 대응하는 명령은 operator token으로 Control API를 HTTP로 부른다."""
    env = process_env(args.env_file)
    try:
        settings = load_settings(args.config, env)
    except ConfigError as exc:
        print(f"{command} 실패: {exc}", file=sys.stderr)
        return None
    token = settings.secrets.get("CONTROL_OPERATOR_TOKEN")
    if not token:
        print(f"{command}: NOT_CONFIGURED: CONTROL_OPERATOR_TOKEN", file=sys.stderr)
        return None
    api = settings.config.control_api
    return f"http://{api.host}:{api.port}", {"Authorization": f"Bearer {token}"}


def _print_response(response: httpx.Response) -> int:
    try:
        text = json.dumps(response.json(), ensure_ascii=False, indent=2)
    except ValueError:
        text = f"HTTP {response.status_code}"
    print(text, file=sys.stdout if response.status_code == 200 else sys.stderr)
    return 0 if response.status_code == 200 else 1


def _work_command(args: argparse.Namespace, transport: httpx.BaseTransport | None = None) -> int:
    """approve-work·retry-work·cancel-work: 지금 work를 읽고 기대 version·snapshot으로 POST한다."""
    command = args.command
    target = _ops_target(args, command)
    if target is None:
        return 2
    base_url, headers = target
    path = f"/ops/work-items/{args.work_id}"
    try:
        with httpx.Client(base_url=base_url, timeout=15.0, transport=transport) as client:
            current = client.get(path, headers=headers)
            if current.status_code != 200:
                return _print_response(current)
            work = current.json()["data"]
            version = work["version"] if args.expected_version is None else args.expected_version
            body: dict[str, Any] = {
                "schema_version": "linemedic.v4",
                "expected_work_version": version,
            }
            if command == "approve-work":
                body["expected_issue_snapshot_sha256"] = (
                    args.expected_snapshot or work["issue_snapshot_sha256"]
                )
                body["approval_note"] = args.note
                action = "approve"
            elif command == "retry-work":
                body["blocker_resolution_note"] = args.reason
                action = "retry"
            else:
                body["cancel_note"] = args.note
                action = "cancel"
            key = f"{command}:{args.work_id}:{version}"  # 같은 명령은 멱등 재전송
            response = client.post(
                f"{path}/{action}", json=body, headers={**headers, "Idempotency-Key": key}
            )
    except httpx.HTTPError as exc:
        print(
            f"{command} 실패: Control API에 연결하지 못했다({type(exc).__name__})", file=sys.stderr
        )
        return 2
    return _print_response(response)


def _notification_reconcile(
    args: argparse.Namespace, transport: httpx.BaseTransport | None = None
) -> int:
    target = _ops_target(args, "notification-reconcile")
    if target is None:
        return 2
    base_url, headers = target
    try:
        with httpx.Client(base_url=base_url, timeout=30.0, transport=transport) as client:
            response = client.post(
                f"/ops/notifications/{args.notification_id}/reconcile",
                json={"schema_version": "linemedic.v4"},
                headers={
                    **headers,
                    "Idempotency-Key": f"notification-reconcile:{args.notification_id}",
                },
            )
    except httpx.HTTPError as exc:
        print(
            f"notification-reconcile 실패: Control API에 연결하지 못했다({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    return _print_response(response)


def _execution_reconcile(
    args: argparse.Namespace, transport: httpx.BaseTransport | None = None
) -> int:
    """execution을 읽고 그 갱신 시각을 멱등 키에 넣어 조정한다.

    같은 상태에서 다시 부르면 저장된 응답을 받는다.
    조정 기록으로 execution이 바뀐 뒤에는 새 키라 다시 조회한다.
    """
    target = _ops_target(args, "reconcile")
    if target is None:
        return 2
    base_url, headers = target
    path = f"/ops/executions/{args.execution_id}"
    try:
        with httpx.Client(base_url=base_url, timeout=60.0, transport=transport) as client:
            current = client.get(path, headers=headers)
            if current.status_code != 200:
                return _print_response(current)
            updated_at = current.json()["data"]["updated_at"]
            response = client.post(
                f"{path}/reconcile",
                json={"schema_version": "linemedic.v4", "run_id": args.run_id},
                headers={
                    **headers,
                    "Idempotency-Key": f"reconcile:{args.execution_id}:{updated_at}",
                },
            )
    except httpx.HTTPError as exc:
        print(
            f"reconcile 실패: Control API에 연결하지 못했다({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    return _print_response(response)


RELEASE_CHECKLIST = (  # spec 11 §5 승인 체크리스트
    "사건·run·Issue·work·시작 알림 receipt·PR·검사 candidate가 연결돼 있다.",
    "사람 리뷰 대상 head 이후 무관한 코드 변경이 없다.",
    "GitHub는 `merged=true`이고 최종 merge SHA를 읽었다.",
    "현재 MES image가 승인 요청의 예상값과 같다.",
    "최종 tree와 candidate tree가 같고 final 검사 결과가 있다.",
    "control 실행에 unknown·충돌·보호 실패가 없다.",
    "정해진 합성 데모 환경에만 배포한다.",
)


def _release_checklist(
    args: argparse.Namespace, view: dict[str, Any], pr: dict[str, Any] | None
) -> list[str]:
    incident, work = view["incident"], view.get("work") or {}
    request = (pr or {}).get("request") or {}
    unknown = [e["id"] for e in view.get("executions", []) if e.get("status") == "UNKNOWN"]
    lines = [
        "[G8 배포 승인] 아래를 사람이 직접 확인한 뒤에만 승인한다 (spec 11 §5).",
        f"- run {incident['run_id']} / 사건 {incident['id']} ({incident['status']},"
        f" version {incident['version']})",
        f"- Issue #{work.get('issue_number')} / work {work.get('id')} ({work.get('status')})"
        f" / 시작 알림 {work.get('start_notification_id')}",
        f"- PR #{args.pr_number}: {request.get('head')} → {request.get('base')}",
        f"- 검사한 candidate: {request.get('candidate_sha')}"
        f" / tree {request.get('candidate_tree')}",
        f"- 승인할 최종 merge SHA: {args.merge_sha}",
        f"- 예상 현재 MES image: {args.expected_image_id}",
    ]
    if unknown:
        lines.append(f"- 주의: 결과 불명(UNKNOWN) execution이 있다: {', '.join(unknown)}")
    lines += [f"[ ] {item}" for item in RELEASE_CHECKLIST]
    lines.append(
        "서버가 merged·SHA·head·리뷰·tree·image를 다시 확인한다."
        " 리뷰어는 테스트만 보고 승인하지 않고 diff·근거·허용 파일 범위를 확인한다."
    )
    return lines


def _approve_release(
    args: argparse.Namespace,
    transport: httpx.BaseTransport | None = None,
    confirm: Any = input,
    interactive: bool | None = None,
) -> int:
    """G8: 체크리스트를 보여 주고 사람이 `approve`를 입력해야만 `POST /ops/releases`를 보낸다."""
    target = _ops_target(args, "approve-release")
    if target is None:
        return 2
    base_url, headers = target
    interactive = sys.stdin.isatty() if interactive is None else interactive
    try:
        with httpx.Client(base_url=base_url, timeout=30.0, transport=transport) as client:
            current = client.get(f"/ops/incidents/{args.incident_id}", headers=headers)
            if current.status_code != 200:
                print(json.dumps(current.json(), ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
            view = current.json()["data"]
            prs = [
                e
                for e in view.get("executions", [])
                if e.get("operation") == "CREATE_PR"
                and e.get("status") == "SUCCEEDED"
                and e.get("work_id") == args.work_id
                and (args.proposal_id is None or e.get("proposal_id") == args.proposal_id)
            ]
            if len(prs) != 1:
                print(
                    "approve-release 실패: work의 봇 PR execution을 하나로 정할 수 없다"
                    f"({len(prs)}개). --proposal-id를 확인한다",
                    file=sys.stderr,
                )
                return 1
            pr_view = client.get(f"/ops/executions/{prs[0]['id']}", headers=headers)
            pr = pr_view.json()["data"] if pr_view.status_code == 200 else None
            print("\n".join(_release_checklist(args, view, pr)))
            if not interactive:
                print(
                    "approve-release: 터미널에서 사람이 직접 실행해야 한다(확인 입력 필요)",
                    file=sys.stderr,
                )
                return 2
            if confirm("승인하려면 approve를 입력한다: ").strip() != "approve":
                print("approve-release: 승인하지 않았다(요청 보내지 않음)", file=sys.stderr)
                return 1
            body = {
                "schema_version": "linemedic.v4",
                "run_id": args.run_id,
                "incident_id": args.incident_id,
                "work_id": args.work_id,
                "proposal_id": prs[0]["proposal_id"],
                "pr_number": args.pr_number,
                "approved_merge_sha": args.merge_sha,
                "expected_incident_version": view["incident"]["version"],
                "expected_current_image_id": args.expected_image_id,
                "approval_note": args.note,
            }
            response = client.post(
                "/ops/releases",
                json=body,
                headers={**headers, "Idempotency-Key": f"release:{args.work_id}:{args.merge_sha}"},
            )
    except httpx.HTTPError as exc:
        print(
            f"approve-release 실패: Control API에 연결하지 못했다({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    accepted = response.status_code == 202
    print(
        json.dumps(response.json(), ensure_ascii=False, indent=2),
        file=sys.stdout if accepted else sys.stderr,
    )
    return 0 if accepted else 1


def _issue_bind(args: argparse.Namespace, transport: httpx.BaseTransport | None = None) -> int:
    target = _ops_target(args, "issue-bind")
    if target is None:
        return 2
    base_url, headers = target
    try:
        with httpx.Client(base_url=base_url, timeout=15.0, transport=transport) as client:
            current = client.get(f"/ops/incidents/{args.incident_id}", headers=headers)
            if current.status_code != 200:
                print(json.dumps(current.json(), ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
            incident = current.json()["data"]["incident"]
            version = (
                incident["version"] if args.expected_version is None else args.expected_version
            )
            body = {
                "schema_version": "linemedic.v4",
                "run_id": incident["run_id"],
                "issue_number": args.issue_number,
                "expected_incident_version": version,
                "decision_note": args.note,
            }
            key = (
                f"issue-bind:{args.incident_id}:{args.issue_number}:{version}"  # 같은 명령은 재전송
            )
            response = client.post(
                f"/ops/incidents/{args.incident_id}/issue-binding",
                json=body,
                headers={**headers, "Idempotency-Key": key},
            )
    except httpx.HTTPError as exc:
        print(
            f"issue-bind 실패: Control API에 연결하지 못했다({type(exc).__name__})", file=sys.stderr
        )
        return 2
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0 if response.status_code == 200 else 1


def _start(args: argparse.Namespace) -> int:
    env = process_env(args.env_file)
    try:
        settings = load_settings(args.config, env)
        db_path = args.db or runs.default_db_path(env)
        if not db_path.is_file():
            print(f"start 실패: 제어 DB가 없다: {db_path} (먼저 make run-new)", file=sys.stderr)
            return 2
        return control_main.start(
            settings, dict(env), args.run_id, db_path=db_path, manual_proposal=args.manual_proposal
        )
    except (ConfigError, StoreError, control_main.ControlPlaneError) as exc:
        print(f"start 실패: {exc}", file=sys.stderr)
        return 2


def _stop(args: argparse.Namespace) -> int:
    env = process_env(args.env_file)
    runs_dir = Path(env.get("RUNS_DIR") or "runs").resolve()
    try:
        outcome = control_main.stop(runs_dir, args.run_id)
    except control_main.ControlPlaneError as exc:
        print(f"stop 실패: {exc}", file=sys.stderr)
        return 2
    print(outcome)
    return 1 if outcome == "not_running" else 0


def _detect_once(args: argparse.Namespace) -> int:
    env = process_env(args.env_file)
    db_path = args.db or runs.default_db_path(env)
    if not db_path.is_file():
        print(f"detect-once 실패: 제어 DB가 없다: {db_path} (먼저 make run-new)", file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.config, env)
        clock = SystemClock()
        store = Store(db_path, clock)
        store.migrate()
        manifest = _run_manifest(store, args.run_id)
        if manifest is None:
            print(f"detect-once 실패: 활성 run이 아니다: {args.run_id}", file=sys.stderr)
            return 2
        routing_scope = manifest.get("routing_scope") or f"eval:{args.run_id}"
        detector_settings = detector.settings_for_run(
            settings.config, args.run_id, routing_scope, scenarios.MES_SERVICE
        )
        container = scenarios.resource_names(args.run_id)["container"]
        runs_dir = Path(env.get("RUNS_DIR") or "runs")
        watcher = detector.Detector(
            store, detector_settings, clock, FileLogStore(runs_dir), eval_identifiers()
        )
        summary = detector.run_detect_once(store, watcher, CliDocker(), container)
    except (ConfigError, StoreError, DockerError, ValueError) as exc:
        print(f"detect-once 실패: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": args.run_id, "container": container, **summary}, indent=2))
    return 0


def _active_run_id(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    with Store(db_path, SystemClock()).read() as tx:
        row = runs.active_run(tx)
    return row["id"] if row is not None else None


def _rebuild_case_index(
    args: argparse.Namespace, transport: httpx.BaseTransport | None = None
) -> int:
    """`POST /ops/cases/rebuild-index`(역할 maintenance). run을 생략하면 제어 DB의 활성 run."""
    target = _ops_target(args, "rebuild-case-index")
    if target is None:
        return 2
    base_url, headers = target
    run_id = args.run_id or _active_run_id(
        args.db or runs.default_db_path(process_env(args.env_file))
    )
    if run_id is None:
        print("rebuild-case-index 실패: 활성 run이 없다(먼저 make run-new)", file=sys.stderr)
        return 2
    stamp = SystemClock().utc_now().strftime("%Y%m%dT%H%M%S")
    try:
        with httpx.Client(base_url=base_url, timeout=60.0, transport=transport) as client:
            response = client.post(
                "/ops/cases/rebuild-index",
                json={"schema_version": "linemedic.v4", "run_id": run_id},
                headers={**headers, "Idempotency-Key": f"rebuild-case-index:{run_id}:{stamp}"},
            )
    except httpx.HTTPError as exc:
        print(
            f"rebuild-case-index 실패: Control API에 연결하지 못했다({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    return _print_response(response)


def _selected_notes(args: argparse.Namespace) -> list[str] | None:
    """`--notes`·`--notes-file`로 사람이 고른 note ID. 둘 다 없으면 None."""
    if args.notes is not None:
        items = args.notes.split(",")
    elif args.notes_file is not None:
        text = args.notes_file.read_text(encoding="utf-8")
        items = [line.split("#", 1)[0] for line in text.splitlines()]
    else:
        return None
    return [item.strip() for item in items if item.strip()]


def _memory_snapshot(args: argparse.Namespace) -> int:
    """G9: `--list`로 후보를 보고, 사람이 고른 note ID(`--notes`·`--notes-file`)로만 manifest를
    만들어 쓰고 대상 run의 감사 기록에 남긴다. 고르지 않으면 아무것도 쓰지 않는다(D84 ⑤).
    """
    env = process_env(args.env_file)
    db_path = args.db or runs.default_db_path(env)
    if not db_path.is_file():
        print(f"memory-snapshot 실패: 제어 DB가 없다: {db_path}", file=sys.stderr)
        return 2
    try:
        selected = _selected_notes(args)
    except OSError as exc:
        print(f"memory-snapshot 실패: note 목록 파일을 읽지 못했다({exc})", file=sys.stderr)
        return 2
    if not args.list and not selected:
        print(
            "memory-snapshot 실패: 넣을 노트를 사람이 골라야 한다(G9)."
            " 후보를 LIST=1(--list)로 보고 NOTES=(--notes)·NOTES_FILE=(--notes-file)로 고른다",
            file=sys.stderr,
        )
        return 2
    try:
        settings = load_settings(args.config, env)
        store = Store(db_path, SystemClock())
        store.migrate()
        scope = {
            "run_id": args.run_id,
            "cutoff": args.cutoff,
            "repository_id": settings.config.repository.id,
            "terms": eval_identifiers(),
        }
        with store.tx() as tx:
            if tx.one("SELECT 1 FROM demo_runs WHERE id = ?", (args.run_id,)) is None:
                print(f"memory-snapshot 실패: 없는 run이다: {args.run_id}", file=sys.stderr)
                return 2
            if args.list:
                found = memory_snapshot.candidates(tx, **scope)
            else:
                snapshot = memory_snapshot.build_snapshot(tx, **scope, selected=selected)
                path = memory_snapshot.write_snapshot(snapshot, args.output_dir)
                memory_snapshot.record_snapshot(tx, snapshot, path)
    except memory_snapshot.SnapshotSelectionError as exc:
        print(
            "memory-snapshot 실패: 고른 노트 중 넣을 수 없는 것이 있다(아무것도 쓰지 않음)\n"
            + json.dumps(exc.problems, ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except (ConfigError, StoreError, memory_snapshot.SnapshotError) as exc:
        print(f"memory-snapshot 실패: {exc}", file=sys.stderr)
        return 2
    if args.list:
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "candidates": found,
                    "requires_selection": sorted(memory_snapshot.SELECTION_REQUIRED_ORIGINS),
                    "next": "고른 note ID로: make memory-snapshot RUN_ID=<run> NOTES=<ID,ID>",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    counts = snapshot.manifest["counts"]
    print(
        json.dumps(
            {"snapshot_id": snapshot.snapshot_id, "path": str(path), **counts},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def baseline_port(settings: Any) -> tuple[HttpBaseline | None, list[str]]:
    """기준 브랜치 port. G2(repo·setup credential·BASELINE_COMMIT)와 G10(쓰기 허락)이 모두
    있어야 한다.

    없으면 (None, 빠진 이름 목록). 값은 출력하지 않는다.
    """
    repo = settings.config.repository
    missing = [
        name
        for name, present in (
            ("GITHUB_REPOSITORY", repo.full_name),
            ("GITHUB_REPOSITORY_ID", repo.id),
            ("GITHUB_SETUP_CREDENTIAL", settings.secrets.is_set("GITHUB_SETUP_CREDENTIAL")),
            ("BASELINE_COMMIT", settings.runtime.baseline_commit),
            ("github.write_enabled(G10)", settings.config.github.write_enabled),
        )
        if not present
    ]
    if missing:
        return None, missing
    credential = settings.secrets.get("GITHUB_SETUP_CREDENTIAL")
    port = HttpBaseline(
        repo.id, repo.full_name, credential, base_url=settings.config.github.base_url
    )
    return port, []


def _run_new(args: argparse.Namespace, baseline: Any = None) -> int:
    """`make run-new [CREATE_BASELINE=1]`: DB run·manifest, 요청하면 기준 브랜치(G2·G10)."""
    env = process_env(args.env_file)
    if args.host_manifest is not None and not args.host_manifest.is_file():
        print(f"run-new 실패: host manifest가 없다: {args.host_manifest}", file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.config, env)
        if args.create_baseline and baseline is None:
            baseline, missing = baseline_port(settings)
            if baseline is None:
                print(f"run-new: NOT_CONFIGURED: {', '.join(missing)}", file=sys.stderr)
                return 2
        db_path = args.db or runs.default_db_path(env)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        clock = SystemClock()
        summary = runs.new_run(
            Store(db_path, clock), settings, clock, args.host_manifest, baseline=baseline
        )
    except BaselineError as exc:
        print(f"run-new 실패: 기준 브랜치를 준비하지 못했다({exc.reason}). run을 만들지 않았다",
              file=sys.stderr)  # fmt: skip
        return 1
    except (ConfigError, StoreError, runs.RunError) as exc:
        print(f"run-new 실패: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _local_run(args: argparse.Namespace, command: str) -> tuple[Store, Path] | None:
    """export-run·reset: 제어 DB에 그 run이 있어야 한다. (store, runs_dir)."""
    env = process_env(args.env_file)
    db_path = args.db or runs.default_db_path(env)
    if not db_path.is_file():
        print(f"{command} 실패: 제어 DB가 없다: {db_path}", file=sys.stderr)
        return None
    store = Store(db_path, SystemClock())
    store.migrate()
    with store.read() as tx:
        if tx.one("SELECT 1 FROM demo_runs WHERE id = ?", (args.run_id,)) is None:
            print(f"{command} 실패: 없는 run이다: {args.run_id}", file=sys.stderr)
            return None
    return store, Path(env.get("RUNS_DIR") or db_path.parent)


def _export_run(args: argparse.Namespace) -> int:
    """`make export-run RUN_ID=`: 증거 export만(정지·정리 없음). 이전 export를 덮어쓰지 않는다."""
    local = _local_run(args, "export-run")
    if local is None:
        return 2
    store, runs_dir = local
    with store.read() as tx:
        pending = runs.unresolved(tx, args.run_id)
    root, manifest = run_export.export_run(
        store, args.run_id, runs_dir, clock=SystemClock(), unresolved=pending,
        terms=eval_identifiers(),
    )  # fmt: skip
    print(
        json.dumps(
            {"export": str(root), "files": len(manifest["files"]), **manifest["unresolved"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _reset(args: argparse.Namespace, docker: Any = None) -> int:
    """`make reset RUN_ID=`: 정지 → 미해결 확인 → export → 이 run 라벨 컨테이너·workspace 정리."""
    local = _local_run(args, "reset")
    if local is None:
        return 2
    store, runs_dir = local
    try:
        result = runs.reset(
            store,
            args.run_id,
            runs_dir=runs_dir,
            clock=SystemClock(),
            principal="operator:host-cli",
            docker=docker if docker is not None else CliDocker(),
            terms=eval_identifiers(),
        )
    except runs.RunError as exc:  # 배포·검증 lock 등: 정지·export·정리를 하지 않았다
        print(f"reset 실패: {exc}", file=sys.stderr)
        return 2
    result["unresolved"] = {key: len(items) for key, items in result["unresolved"].items()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["cleanup"] and result["cleanup"]["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return doctor.main(["--config", str(args.config), "--env-file", str(args.env_file)])
    if args.command == "host-manifest":
        text = json.dumps(host_manifest.collect(), ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    if args.command == "scenario-s1":
        env = process_env(args.env_file)
        image = args.image or env.get("MES_BASE_IMAGE_ID") or scenarios.DEFAULT_MES_IMAGE
        runs_dir = Path(env.get("RUNS_DIR") or "runs")
        db_path = args.db or runs.default_db_path(env)
        store = _run_store(db_path, args.run_id)
        if _release_locked(store, args.run_id, "scenario-s1"):
            return 2
        base_sha = env.get("BASELINE_COMMIT") or None
        try:
            result = scenarios.inject_s1(
                args.run_id, runs_dir=runs_dir, image=image, store=store, base_sha=base_sha
            )
        except scenarios.ScenarioError as exc:
            print(f"scenario-s1 실패: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "detect-once":
        return _detect_once(args)
    if args.command == "scenario-s2-lite":
        return _scenario_s2_lite(args)
    if args.command == "issue-sync":
        return _issue_sync(args)
    if args.command == "issue-bind":
        return _issue_bind(args)
    if args.command in ("approve-work", "retry-work", "cancel-work"):
        return _work_command(args)
    if args.command == "notification-reconcile":
        return _notification_reconcile(args)
    if args.command == "reconcile":
        return _execution_reconcile(args)
    if args.command == "approve-release":
        return _approve_release(args)
    if args.command == "start":
        return _start(args)
    if args.command == "stop":
        return _stop(args)
    if args.command == "rebuild-case-index":
        return _rebuild_case_index(args)
    if args.command == "memory-snapshot":
        return _memory_snapshot(args)
    if args.command == "verify-negative":
        env = process_env(args.env_file)
        db_path = args.db or runs.default_db_path(env)
        if not db_path.is_file():
            print(
                f"verify-negative 실패: 제어 DB가 없다: {db_path} (먼저 make run-new)",
                file=sys.stderr,
            )
            return 2
        try:
            settings = load_settings(args.config, env)
            store = Store(db_path, SystemClock())
            store.migrate()
            outcome = harness.run_verify_negative(
                args.run_id,
                runs_dir=Path(env.get("RUNS_DIR") or "runs"),
                store=store,
                mes_image=args.mes_image,
                expected_mes_image_id=env.get("MES_BASE_IMAGE_ID") or None,
                route_id=settings.config.notifications.required_start_route_id,
            )
        except (
            harness.HarnessError,
            DockerError,
            ObserverError,
            ConfigError,
            StoreError,
            sqlite3.Error,
        ) as exc:
            print(f"verify-negative 실패: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        if harness.expected_outcome(outcome):
            return 0
        print("verify-negative: verifier가 S1b를 기대대로 거절하지 않았다", file=sys.stderr)
        return 1
    if args.command == "run-new":
        return _run_new(args)
    if args.command == "export-run":
        return _export_run(args)
    if args.command == "reset":
        return _reset(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
