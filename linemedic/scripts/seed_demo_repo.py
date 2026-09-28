"""데모 대상 저장소의 시드 git 이력 생성 (W04, D42).

`l3-mes-api-seed/`의 파일로 버그 base 커밋 하나를 가진 git 저장소를 만든다.
작성자·시각·메시지를 고정하고 사용자 git 설정·hook을 쓰지 않으므로, 같은 시드는 항상 같은
tree hash와 커밋 SHA를 만든다.

원격 push (W03, G2·G10 뒤 사용자 허락): `--push`는 계획만 출력한다(PLANNED, GitHub 호출 없음).
`--push --confirm-write`일 때만 setup credential로 repo를 조회해 숫자 ID가
`GITHUB_REPOSITORY_ID`와 같은지 확인한 뒤, 시드 커밋을 원격 `main`에 push한다. force push는 하지
않는다: 원격 main이 이미 같은 SHA면 그대로 두고(up_to_date), 다른 이력이면 거절로 끝내고 사람에게
넘긴다. 결과 불명(UNKNOWN)이면
다시 push하지 않는다. 판정은 PASS / FAIL / INCONCLUSIVE / NOT_CONFIGURED / PLANNED이고,
종료 코드는 PASS·PLANNED 0, FAIL·INCONCLUSIVE 1, NOT_CONFIGURED 2다.

실행:
    python -m linemedic.scripts.seed_demo_repo --output runs/seed-repo
    python -m linemedic.scripts.seed_demo_repo --push                       # 계획만 출력
    python -m linemedic.scripts.seed_demo_repo --push --confirm-write \
        --record evidence/W03-seed-push.json                              # 허락 후 실제 push
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_ENV_FILE, process_env
from linemedic.integrations.git_push import BranchPusher, GitPusher
from linemedic.scripts.github_setup_check import (
    HTTP_TIMEOUT_SECONDS,
    REPO_FULL_NAME_RE,
    GitHubReader,
    check_repository,
)

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


PUSH_ENV = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_SETUP_CREDENTIAL")
PUSH_BRANCH = "main"
EXIT_CODES = {"PASS": 0, "PLANNED": 0, "FAIL": 1, "INCONCLUSIVE": 1, "NOT_CONFIGURED": 2}


def push_seed(
    env: Mapping[str, str],
    seed: Mapping[str, str],
    *,
    confirm_write: bool,
    client: httpx.Client | None = None,
    pusher: BranchPusher | None = None,
    clock: Clock | None = None,
    api_version: str | None = None,
) -> dict[str, Any]:
    """시드 커밋을 등록 repo의 원격 main에 push한다(force 없음). credential은 기록하지 않는다."""
    clock = clock or SystemClock()
    record: dict[str, Any] = {
        "action": "seed_push",
        "schema_version": "linemedic.v4",
        "started_at": to_rfc3339(clock.utc_now()),
        "repository": env.get("GITHUB_REPOSITORY") or None,
        "repository_id": env.get("GITHUB_REPOSITORY_ID") or None,
        "commit": seed["commit"],
        "tree": seed["tree"],
        "target": f"refs/heads/{PUSH_BRANCH}",
        "credential": "GITHUB_SETUP_CREDENTIAL",
        "push": None,
        "verdict": None,
        "reason": None,
    }
    missing = [name for name in PUSH_ENV if not env.get(name)]
    if missing:
        record.update(verdict="NOT_CONFIGURED", reason="필수 env 미설정", missing_env=missing)
        return record
    full, raw_id = env["GITHUB_REPOSITORY"], env["GITHUB_REPOSITORY_ID"]
    if not REPO_FULL_NAME_RE.fullmatch(full) or not raw_id.isdigit():
        record.update(
            verdict="FAIL", reason="GITHUB_REPOSITORY 또는 GITHUB_REPOSITORY_ID 형식 오류"
        )
        return record
    if not confirm_write:
        record.update(
            verdict="PLANNED",
            reason="GitHub에 쓰지 않았다. G10과 사용자 허락 후 --confirm-write로 실행한다",
            plan=[
                "setup credential로 repo 조회 → 숫자 ID가 다르면 쓰지 않고 FAIL",
                f"시드 커밋 {seed['commit']}을 원격 {PUSH_BRANCH}에 push (force 없음)",
                "원격 main이 다른 이력이면 거절로 끝내고 사람에게 넘긴다",
            ],
        )
        return record

    setup = env["GITHUB_SETUP_CREDENTIAL"]
    owns_client = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        status, body = GitHubReader(client, api_version).get(f"/repos/{full}", setup)
    except httpx.HTTPError as exc:
        record.update(verdict="INCONCLUSIVE", reason=f"repo 조회 실패 ({type(exc).__name__})")
        return record
    finally:
        if owns_client:
            client.close()
    if status != 200:
        reason = f"repo 조회 실패 (HTTP {status}) — 쓰지 않았다"
        record.update(verdict="INCONCLUSIVE", reason=reason)
        return record
    identity = check_repository(body, int(raw_id), full)
    if identity.status != "PASS":
        record.update(verdict=identity.status, reason=f"{identity.detail} — 쓰지 않았다")
        return record

    pusher = pusher or GitPusher(f"https://github.com/{full}.git", setup)
    result = pusher.push(Path(seed["path"]) / ".git", seed["commit"], PUSH_BRANCH)
    record["push"] = {"status": result.status, "detail": result.detail}
    if result.status == "PUSHED":
        record.update(
            verdict="PASS",
            reason=f"원격 {PUSH_BRANCH} = 시드 커밋 ({result.detail})",
            baseline_commit=seed["commit"],
        )
    elif result.status == "REJECTED":
        record.update(
            verdict="FAIL",
            reason=f"원격 {PUSH_BRANCH}가 거절했다(다른 이력 등). force push 하지 않는다"
            " — 사람이 결정",
        )
    else:
        record.update(
            verdict="INCONCLUSIVE",
            reason="push 결과 불명 — 다시 push하지 않고 원격 main을 조회해 확인한다",
        )
    return record


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    pusher: BranchPusher | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="seed_demo_repo")
    default_output = Path(os.environ.get("RUNS_DIR") or "runs") / "seed-repo"
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument(
        "--push", action="store_true", help="시드 커밋을 등록 repo 원격 main에 push (W03)"
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="실제로 push한다 (G10과 사용자 명시 허락 후만). 없으면 계획만 출력",
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--api-version", help="X-GitHub-Api-Version 헤더 (N11 확인 후)")
    parser.add_argument("--record", type=Path, help="push 결과 JSON 저장 경로 (비밀 값 없음)")
    args = parser.parse_args(argv)
    if args.push:
        env = process_env(args.env_file) if env is None else env
        with tempfile.TemporaryDirectory(prefix="linemedic-seed-") as tmp:
            try:
                seed = build_seed_repo(Path(tmp) / "seed-repo")
            except SeedError as exc:
                print(f"시드 저장소 생성 실패: {exc}", file=sys.stderr)
                return 1
            record = push_seed(
                env,
                seed,
                confirm_write=args.confirm_write,
                client=client,
                pusher=pusher,
                api_version=args.api_version,
            )
        text = json.dumps(record, ensure_ascii=False, indent=2)
        if args.record and record["verdict"] not in ("PLANNED", "NOT_CONFIGURED"):
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(text + "\n", encoding="utf-8")
        print(text)
        if record["verdict"] == "PASS":
            print(
                f"원격 main = {record['commit']}. .env의 BASELINE_COMMIT이 이 값인지 확인한다.",
                file=sys.stderr,
            )
        return EXIT_CODES[record["verdict"]]
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
