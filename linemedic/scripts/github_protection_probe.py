"""GitHub 보호 규칙 쓰기 시험 (W03, N07).

기본은 실행 계획만 출력한다(PLANNED, GitHub에 쓰지 않음). `--confirm-write`를 줄 때만 실제로 쓴다.
G10(쓰기 활성화)과 사용자의 명시적 허락을 받은 뒤에만 `--confirm-write`로 실행한다.

시험 순서
1. setup credential로 main에서 `baseline/r-probe-<UTC>` 브랜치를 만든다.
2. 봇 credential로 그 브랜치에 파일을 직접 쓴다 → 거절돼야 한다.
3. 봇 credential로 `probe/r-probe-<UTC>` 브랜치와 변경 커밋을 만들고
   baseline 브랜치로 PR을 연다(`probe` 라벨).
4. 봇 credential로 리뷰 없이 squash 머지를 시도한다 → 거절돼야 한다.
5. 리뷰어가 봇 PR을 승인할 수 있는지는 사람이 확인한다(MANUAL).

만든 브랜치와 PR은 삭제하지 않고 `probe` 라벨을 붙여 남긴다. 토큰은 출력하지 않는다.
판정은 PASS / FAIL / INCONCLUSIVE / NOT_CONFIGURED / PLANNED이고,
종료 코드는 PASS·PLANNED 0, FAIL·INCONCLUSIVE 1, NOT_CONFIGURED 2다.

실행:
    python -m linemedic.scripts.github_protection_probe                  # 계획만 출력
    python -m linemedic.scripts.github_protection_probe --confirm-write \
        --output evidence/github-protection-probe.json                   # 허락 후 실제 시험
"""

import argparse
import base64
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_ENV_FILE, process_env
from linemedic.scripts.github_setup_check import (
    HTTP_TIMEOUT_SECONDS,
    REPO_FULL_NAME_RE,
    REQUIRED_ENV,
    CheckResult,
    GitHubReader,
    missing_env,
)

REFUSED_DIRECT_PUSH = {403, 405, 409, 422}
REFUSED_MERGE = {405}
PROBE_LABEL = "probe"


class ProbeError(Exception):
    def __init__(self, verdict: str, reason: str) -> None:
        super().__init__(reason)
        self.verdict = verdict
        self.reason = reason


def probe_names(clock: Clock) -> dict[str, str]:
    stamp = clock.utc_now().strftime("%Y%m%d-%H%M%S")
    run = f"r-probe-{stamp}"  # `/`를 넣지 않는다: baseline/* 패턴의 `*`는 `/`와 일치하지 않는다
    return {
        "run": run,
        "baseline_branch": f"baseline/{run}",
        "head_branch": f"probe/{run}",
        "direct_push_path": f"probe/direct-push-{stamp}.txt",
        "pr_change_path": f"probe/pr-change-{stamp}.txt",
    }


def plan(names: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "step": "1",
            "credential": "GITHUB_SETUP_CREDENTIAL",
            "action": f"main에서 {names['baseline_branch']} 브랜치 생성",
        },
        {
            "step": "2",
            "credential": "GITHUB_BROKER_CREDENTIAL",
            "action": (
                f"{names['baseline_branch']}에 {names['direct_push_path']} 직접 쓰기 → 거절 기대"
            ),
        },
        {
            "step": "3",
            "credential": "GITHUB_BROKER_CREDENTIAL",
            "action": f"{names['head_branch']} 생성·변경 커밋 → {names['baseline_branch']}로 PR "
            f"(라벨 {PROBE_LABEL})",
        },
        {
            "step": "4",
            "credential": "GITHUB_BROKER_CREDENTIAL",
            "action": "리뷰 없이 squash 머지 시도 → 거절 기대",
        },
        {
            "step": "5",
            "credential": "(사람)",
            "action": "리뷰어가 probe PR을 승인할 수 있는지 확인",
        },
    ]


def _content(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _expect(status: int, expected: set[int], label: str) -> None:
    if status not in expected:
        raise ProbeError("INCONCLUSIVE", f"{label} 실패 (HTTP {status})")


def _refusal_check(name: str, status: int, refused: set[int], action: str) -> CheckResult:
    if 200 <= status < 300:
        return CheckResult(
            name, "FAIL", f"{action}이 허용됨 (HTTP {status}) — 보호가 적용되지 않았다"
        )
    if status in refused:
        return CheckResult(name, "PASS", f"{action}이 거절됨 (HTTP {status})")
    return CheckResult(name, "INCONCLUSIVE", f"{action} 결과를 판단할 수 없음 (HTTP {status})")


def run_probe(
    env: Mapping[str, str],
    client: httpx.Client | None = None,
    clock: Clock | None = None,
    api_version: str | None = None,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    names = probe_names(clock)
    record: dict[str, Any] = {
        "probe": "github_protection",
        "schema_version": "linemedic.v4",
        "started_at": to_rfc3339(clock.utc_now()),
        "repository": env.get("GITHUB_REPOSITORY") or None,
        "names": names,
        "created": {},
        "checks": [],
        "verdict": None,
        "reason": None,
    }
    missing = missing_env(env)
    if missing:
        record.update(verdict="NOT_CONFIGURED", reason="필수 env 미설정", missing_env=missing)
        return record
    full = env["GITHUB_REPOSITORY"]
    if not REPO_FULL_NAME_RE.fullmatch(full):
        record.update(verdict="FAIL", reason="GITHUB_REPOSITORY 형식 오류")
        return record
    bot, setup = env["GITHUB_BROKER_CREDENTIAL"], env["GITHUB_SETUP_CREDENTIAL"]
    base = f"/repos/{full}"

    owns_client = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    github = GitHubReader(client, api_version)
    checks: list[CheckResult] = []
    try:
        status, body = github.get(f"{base}/git/ref/heads/main", setup)
        _expect(status, {200}, "main ref 조회")
        sha = (body or {}).get("object", {}).get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            raise ProbeError("INCONCLUSIVE", "main ref SHA를 읽지 못함")

        status, _ = github.request(
            "POST",
            f"{base}/git/refs",
            setup,
            {"ref": f"refs/heads/{names['baseline_branch']}", "sha": sha},
        )
        _expect(status, {201}, "baseline 브랜치 생성")
        record["created"]["baseline_branch"] = names["baseline_branch"]

        status, _ = github.request(
            "PUT",
            f"{base}/contents/{names['direct_push_path']}",
            bot,
            {
                "message": f"[probe] 보호 브랜치 직접 쓰기 시험 {names['run']}",
                "content": _content("LineMedic protection probe: direct write\n"),
                "branch": names["baseline_branch"],
            },
        )
        checks.append(
            _refusal_check(
                "bot_direct_push_refused", status, REFUSED_DIRECT_PUSH, "봇의 baseline 직접 쓰기"
            )
        )

        status, _ = github.request(
            "POST",
            f"{base}/git/refs",
            bot,
            {"ref": f"refs/heads/{names['head_branch']}", "sha": sha},
        )
        _expect(status, {201}, "봇 probe 브랜치 생성")
        record["created"]["head_branch"] = names["head_branch"]
        status, _ = github.request(
            "PUT",
            f"{base}/contents/{names['pr_change_path']}",
            bot,
            {
                "message": f"[probe] PR 변경 {names['run']}",
                "content": _content("LineMedic protection probe: pull request change\n"),
                "branch": names["head_branch"],
            },
        )
        _expect(status, {200, 201}, "봇 probe 커밋 생성")

        status, body = github.request(
            "POST",
            f"{base}/pulls",
            bot,
            {
                "title": f"[probe] 보호 규칙 시험 {names['run']}",
                "head": names["head_branch"],
                "base": names["baseline_branch"],
                "body": "LineMedic W03 보호 규칙 시험용 PR이다. 머지하지 않는다.",
            },
        )
        _expect(status, {201}, "봇 PR 생성")
        number = (body or {}).get("number")
        if not isinstance(number, int):
            raise ProbeError("INCONCLUSIVE", "PR 번호를 읽지 못함")
        record["created"]["pull_request"] = number
        record["created"]["pull_request_url"] = (body or {}).get("html_url")
        label_status, _ = github.request(
            "POST", f"{base}/issues/{number}/labels", bot, {"labels": [PROBE_LABEL]}
        )
        record["created"]["label_status"] = label_status

        status, _ = github.request(
            "PUT", f"{base}/pulls/{number}/merge", bot, {"merge_method": "squash"}
        )
        checks.append(
            _refusal_check("merge_without_review_refused", status, REFUSED_MERGE, "리뷰 없는 머지")
        )
        checks.append(
            CheckResult(
                "reviewer_can_approve",
                "MANUAL",
                f"봇이 아닌 리뷰어가 PR #{number}을 승인할 수 있는지 사람이 확인한다",
            )
        )
    except ProbeError as exc:
        record.update(verdict=exc.verdict, reason=exc.reason)
    except httpx.HTTPError as exc:
        record.update(verdict="INCONCLUSIVE", reason=f"HTTP_ERROR: {type(exc).__name__}")
    finally:
        if owns_client:
            client.close()

    record["checks"] = [asdict(check) for check in checks]
    if record["verdict"] is None:
        automated = {check.status for check in checks if check.status != "MANUAL"}
        if "FAIL" in automated:
            record["verdict"] = "FAIL"
        elif automated == {"PASS"}:
            record["verdict"] = "PASS"
        else:
            record["verdict"] = "INCONCLUSIVE"
        record["reason"] = "; ".join(check.detail for check in checks if check.status != "MANUAL")
    return record


def exit_code_for(verdict: str | None) -> int:
    return {"PASS": 0, "PLANNED": 0, "NOT_CONFIGURED": 2}.get(verdict or "", 1)


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    clock: Clock | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="github_protection_probe")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="실제로 GitHub에 쓴다 (G10과 사용자 명시 허락 후만)",
    )
    parser.add_argument("--api-version", help="X-GitHub-Api-Version 헤더 (N11 확인 후)")
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로 (비밀 값 없음)")
    args = parser.parse_args(argv)
    env = process_env(args.env_file) if env is None else env

    missing = missing_env(env)
    if missing:
        record: dict[str, Any] = {
            "probe": "github_protection",
            "verdict": "NOT_CONFIGURED",
            "missing_env": missing,
            "required_env": list(REQUIRED_ENV),
        }
    elif not args.confirm_write:
        names = probe_names(clock or SystemClock())
        record = {
            "probe": "github_protection",
            "verdict": "PLANNED",
            "repository": env.get("GITHUB_REPOSITORY"),
            "names": names,
            "plan": plan(names),
            "note": "GitHub에 쓰지 않았다. G10과 사용자 허락 후 --confirm-write로 실행한다",
        }
    else:
        record = run_probe(env, client=client, clock=clock, api_version=args.api_version)

    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output and record["verdict"] not in ("PLANNED", "NOT_CONFIGURED"):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"GitHub 보호 시험: {record['verdict']}", file=sys.stderr)
    return exit_code_for(record["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
