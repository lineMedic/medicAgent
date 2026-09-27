"""운영 CLI 진입점: `python -m linemedic.cli <command>` (D48).

카드마다 subcommand를 추가한다. 구현되지 않은 명령을 있다고 안내하지 않는다.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from linemedic import __version__
from linemedic.common.clock import SystemClock
from linemedic.common.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    ConfigError,
    load_settings,
    process_env,
)
from linemedic.control_plane import runs
from linemedic.control_plane.observer import ObserverError
from linemedic.control_plane.store import Store, StoreError
from linemedic.factory_sim import scenarios
from linemedic.factory_sim.negative import harness
from linemedic.integrations.docker import DockerError
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
    run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    return parser


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
        try:
            result = scenarios.inject_s1(args.run_id, runs_dir=runs_dir, image=image)
        except scenarios.ScenarioError as exc:
            print(f"scenario-s1 실패: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
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
        env = process_env(args.env_file)
        if args.host_manifest is not None and not args.host_manifest.is_file():
            print(f"run-new 실패: host manifest가 없다: {args.host_manifest}", file=sys.stderr)
            return 2
        try:
            settings = load_settings(args.config, env)
            db_path = args.db or runs.default_db_path(env)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            clock = SystemClock()
            summary = runs.new_run(Store(db_path, clock), settings, clock, args.host_manifest)
        except (ConfigError, StoreError) as exc:
            print(f"run-new 실패: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
