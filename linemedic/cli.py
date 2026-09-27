"""운영 CLI 진입점: `python -m linemedic.cli <command>` (D48).

카드마다 subcommand를 추가한다. 구현되지 않은 명령을 있다고 안내하지 않는다.
"""

import argparse
import json
import sys
from pathlib import Path

from linemedic import __version__
from linemedic.common.config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_FILE, process_env
from linemedic.factory_sim import scenarios
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
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
