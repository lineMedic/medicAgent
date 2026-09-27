"""데모 대상 저장소의 시드 git 이력 생성 (W04, D42).

`l3-mes-api-seed/`의 파일로 버그 base 커밋 하나를 가진 git 저장소를 만든다.
작성자·시각·메시지를 고정하고 사용자 git 설정·hook을 쓰지 않으므로, 같은 시드는 항상 같은
tree hash와 커밋 SHA를 만든다. 원격 push는 이 스크립트가 하지 않는다(W03, G2 이후 사람 허락).

실행:
    python -m linemedic.scripts.seed_demo_repo --output runs/seed-repo
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = REPO_ROOT / "l3-mes-api-seed"
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store")

SEED_AUTHOR_NAME = "LineMedic Seed"
SEED_AUTHOR_EMAIL = "seed@linemedic.invalid"
SEED_DATE = "2026-09-26T00:00:00+00:00"
SEED_MESSAGE = "l3-mes-api: 불량 집계 API"


class SeedError(RuntimeError):
    """시드 저장소를 만들 수 없음."""


def _git_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        # 사용자·시스템 git 설정(서명·hook·autocrlf 등)을 쓰지 않는다
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": SEED_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": SEED_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": SEED_DATE,
        "GIT_COMMITTER_NAME": SEED_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": SEED_AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": SEED_DATE,
    }
    return env


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise SeedError(f"git {args[0]} 실패: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def build_seed_repo(output: Path, seed_dir: Path = SEED_DIR) -> dict[str, str]:
    if shutil.which("git") is None:
        raise SeedError("git이 없다")
    if output.exists() and any(output.iterdir()):
        raise SeedError(f"출력 디렉터리가 비어 있지 않다: {output} (덮어쓰지 않는다)")
    if not seed_dir.is_dir():
        raise SeedError(f"시드 디렉터리가 없다: {seed_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed_dir, output, ignore=IGNORED, dirs_exist_ok=True)
    _git(["init", "--quiet", "--template=", "-b", "main"], output)
    _git(["config", "core.autocrlf", "false"], output)
    _git(["config", "core.fileMode", "true"], output)
    _git(["add", "--all"], output)
    _git(["commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", SEED_MESSAGE], output)
    return {
        "path": str(output),
        "commit": _git(["rev-parse", "HEAD"], output),
        "tree": _git(["rev-parse", "HEAD^{tree}"], output),
        "branch": "main",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_demo_repo")
    default_output = Path(os.environ.get("RUNS_DIR") or "runs") / "seed-repo"
    parser.add_argument("--output", type=Path, default=default_output)
    args = parser.parse_args(argv)
    try:
        result = build_seed_repo(args.output)
    except SeedError as exc:
        print(f"시드 저장소 생성 실패: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("원격 push는 하지 않았다. G2 이후 사람 허락으로 진행한다(W03).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
