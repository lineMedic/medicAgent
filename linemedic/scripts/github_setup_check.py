"""GitHub 설정 읽기 전용 점검 (W03, N07 준비).

점검 항목
- repository: repo 숫자 ID와 이름이 설정과 일치한다
- merge_settings: squash 머지만 허용한다 (merge commit·rebase 꺼짐)
- bot_identity: 봇 credential의 identity를 확인하고, 리뷰어 계정과 다르다
- bot_permissions: 봇이 관리자 권한이 없고 push는 할 수 있다.
  GitHub App이면 데모 repo 하나에만 설치돼 있다
- baseline_protection: `baseline/*` 보호 규칙(기존 branch protection 또는 ruleset)이 있고
  조건을 만족한다

GitHub에 쓰지 않는다. 토큰은 Authorization 헤더에만 쓰고 출력하지 않는다.
판정은 PASS / FAIL / INCONCLUSIVE / NOT_CONFIGURED, 종료 코드는 0 / 1 / 1 / 2다.
설정은 사람이 한다(G2). 이 스크립트는 규칙을 만들거나 바꾸지 않는다.

실행 (G2 이후):
    python -m linemedic.scripts.github_setup_check --reviewer <리뷰어 계정> \
        --output evidence/github-setup-check.json
"""

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from linemedic.common.clock import Clock, SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_ENV_FILE, process_env

REQUIRED_ENV = (
    "GITHUB_REPOSITORY",
    "GITHUB_REPOSITORY_ID",
    "GITHUB_BROKER_CREDENTIAL",
    "GITHUB_SETUP_CREDENTIAL",
)
API_BASE = "https://api.github.com"
HTTP_TIMEOUT_SECONDS = 15.0
BASELINE_PATTERN = "baseline/*"
BASELINE_REF_PATTERNS = {"refs/heads/baseline/*", "~ALL"}
REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

CLASSIC_RULES_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    branchProtectionRules(first: 50) {
      nodes {
        pattern
        requiresApprovingReviews
        requiredApprovingReviewCount
        dismissesStaleReviews
        requireLastPushApproval
        isAdminEnforced
        allowsForcePushes
        allowsDeletions
        restrictsPushes
        bypassPullRequestAllowances(first: 20) { totalCount }
        bypassForcePushAllowances(first: 20) { totalCount }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # PASS / FAIL / INCONCLUSIVE
    detail: str


# ── HTTP ───────────────────────────────────────────────────────


class GitHubReader:
    """GitHub REST·GraphQL 최소 클라이언트. 호출마다 credential을 명시한다."""

    def __init__(self, client: httpx.Client, api_version: str | None = None) -> None:
        self.client = client
        self.api_version = api_version

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        if self.api_version:  # N11에서 확인하기 전에는 보내지 않는다(D46)
            headers["X-GitHub-Api-Version"] = self.api_version
        return headers

    def request(
        self, method: str, path: str, token: str, body: Any | None = None
    ) -> tuple[int, Any]:
        response = self.client.request(
            method, API_BASE + path, headers=self._headers(token), json=body
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload

    def get(self, path: str, token: str) -> tuple[int, Any]:
        return self.request("GET", path, token)

    def graphql(self, query: str, variables: dict[str, Any], token: str) -> tuple[int, Any]:
        return self.request("POST", "/graphql", token, {"query": query, "variables": variables})


# ── 판정 함수 (응답 dict만 받는 순수 함수) ────────────────────


def check_repository(repo: Any, expected_id: int, expected_full_name: str) -> CheckResult:
    if not isinstance(repo, dict):
        return CheckResult("repository", "INCONCLUSIVE", "repo 조회 실패")
    if repo.get("id") != expected_id:
        return CheckResult(
            "repository", "FAIL", f"repo ID 불일치: 설정 {expected_id}, 실제 {repo.get('id')}"
        )
    actual_name = str(repo.get("full_name", ""))
    if actual_name.casefold() != expected_full_name.casefold():
        return CheckResult(
            "repository", "FAIL", f"repo 이름 불일치: 설정 {expected_full_name}, 실제 {actual_name}"
        )
    return CheckResult("repository", "PASS", f"{actual_name} (ID {expected_id}) 일치")


def check_merge_settings(repo: Any) -> CheckResult:
    keys = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge")
    if not isinstance(repo, dict) or not all(isinstance(repo.get(key), bool) for key in keys):
        return CheckResult(
            "merge_settings",
            "INCONCLUSIVE",
            "머지 설정 필드가 응답에 없음 (관리자 권한 credential로 조회 필요)",
        )
    values = ", ".join(f"{key}={repo[key]}" for key in keys)
    if (
        repo["allow_squash_merge"]
        and not repo["allow_merge_commit"]
        and not repo["allow_rebase_merge"]
    ):
        return CheckResult("merge_settings", "PASS", f"squash만 허용 ({values})")
    return CheckResult("merge_settings", "FAIL", f"squash만 허용해야 함 ({values})")


def check_bot_identity(identity: Any, reviewer: str | None) -> CheckResult:
    if not isinstance(identity, dict):
        return CheckResult(
            "bot_identity", "INCONCLUSIVE", "봇 credential의 identity를 확인하지 못함"
        )
    if identity.get("kind") == "app_installation":
        return CheckResult("bot_identity", "PASS", "GitHub App 설치 토큰")
    login = str(identity.get("login", ""))
    if reviewer and login.casefold() == reviewer.casefold():
        return CheckResult(
            "bot_identity",
            "FAIL",
            f"봇 credential({login})이 리뷰어 계정과 같음 — PR 작성자는 자기 PR을 승인할 수 없다",
        )
    note = "" if reviewer else " (리뷰어 계정 미지정 — --reviewer로 비교 가능)"
    return CheckResult(
        "bot_identity", "PASS", f"봇 identity: {login} ({identity.get('type')}){note}"
    )


def check_bot_permissions(bot_repo: Any, installation_repos: Any, expected_id: int) -> CheckResult:
    if isinstance(installation_repos, dict):
        repos = installation_repos.get("repositories") or []
        total = installation_repos.get("total_count", len(repos))
        if total == 1 and repos and repos[0].get("id") == expected_id:
            return CheckResult("bot_permissions", "PASS", "GitHub App이 데모 repo 하나에만 설치됨")
        return CheckResult(
            "bot_permissions",
            "FAIL",
            f"GitHub App 설치 범위가 데모 repo 하나가 아님 (repo {total}개)",
        )
    permissions = bot_repo.get("permissions") if isinstance(bot_repo, dict) else None
    if not isinstance(permissions, dict):
        return CheckResult(
            "bot_permissions",
            "INCONCLUSIVE",
            "권한 정보가 응답에 없음 — GitHub 설정 화면에서 사람이 확인해야 함",
        )
    if permissions.get("admin"):
        return CheckResult(
            "bot_permissions", "FAIL", "봇이 관리자 권한을 가짐 — 보호 규칙을 바꿀 수 있다"
        )
    if not permissions.get("push"):
        return CheckResult(
            "bot_permissions", "FAIL", "봇에 push 권한이 없어 PR 브랜치를 만들 수 없다"
        )
    return CheckResult(
        "bot_permissions",
        "PASS",
        "관리자 아님, push 가능 (다른 repo 접근 여부는 이 응답으로 확인할 수 없음)",
    )


def _count(value: Any) -> int:
    return int(value.get("totalCount", 0)) if isinstance(value, dict) else 0


def _classic_problems(rule: dict[str, Any]) -> list[str]:
    problems = []
    if rule.get("requiresApprovingReviews") is not True:
        problems.append("PR 리뷰 필수가 아님")
    if not isinstance(rule.get("requiredApprovingReviewCount"), int) or (
        rule["requiredApprovingReviewCount"] < 1
    ):
        problems.append("필수 승인 수가 1 미만")
    if rule.get("dismissesStaleReviews") is not True:
        problems.append("새 push 후 기존 승인이 유지됨 (stale approval 폐기 꺼짐)")
    if rule.get("isAdminEnforced") is not True:
        problems.append("관리자가 규칙을 우회할 수 있음")
    if rule.get("allowsForcePushes") is True:
        problems.append("force push 허용")
    if _count(rule.get("bypassPullRequestAllowances")) > 0:
        problems.append(f"PR 우회 허용 대상 {_count(rule.get('bypassPullRequestAllowances'))}개")
    if _count(rule.get("bypassForcePushAllowances")) > 0:
        problems.append(
            f"force push 우회 허용 대상 {_count(rule.get('bypassForcePushAllowances'))}개"
        )
    return problems


def _ruleset_targets_baseline(ruleset: dict[str, Any]) -> bool:
    if ruleset.get("target", "branch") != "branch":
        return False
    ref_name = (ruleset.get("conditions") or {}).get("ref_name") or {}
    return bool(BASELINE_REF_PATTERNS & set(ref_name.get("include") or []))


def _ruleset_problems(ruleset: dict[str, Any]) -> list[str]:
    problems = []
    if ruleset.get("enforcement") != "active":
        problems.append(f"enforcement={ruleset.get('enforcement')}")
    bypass = ruleset.get("bypass_actors")
    if bypass is None:
        problems.append("bypass_actors 정보 없음 (관리자 권한 credential로 조회 필요)")
    elif bypass:
        problems.append(f"우회 허용 대상 {len(bypass)}개")
    rules = ruleset.get("rules") or []
    pull_request = next((r for r in rules if r.get("type") == "pull_request"), None)
    if pull_request is None:
        problems.append("pull_request 규칙 없음")
    else:
        params = pull_request.get("parameters") or {}
        if not isinstance(params.get("required_approving_review_count"), int) or (
            params["required_approving_review_count"] < 1
        ):
            problems.append("필수 승인 수가 1 미만")
        if params.get("dismiss_stale_reviews_on_push") is not True:
            problems.append("stale approval 폐기 꺼짐")
    if not any(r.get("type") == "non_fast_forward" for r in rules):
        problems.append("force push 차단 규칙(non_fast_forward) 없음")
    return problems


def check_baseline_protection(classic_rules: Any, rulesets: Any) -> CheckResult:
    if classic_rules is None and rulesets is None:
        return CheckResult("baseline_protection", "INCONCLUSIVE", "보호 규칙 조회 실패")
    reasons = []
    for rule in classic_rules or []:
        if rule.get("pattern") != BASELINE_PATTERN:
            continue
        problems = _classic_problems(rule)
        if not problems:
            return CheckResult(
                "baseline_protection", "PASS", f"branch protection '{BASELINE_PATTERN}' 조건 충족"
            )
        reasons.append("branch protection: " + ", ".join(problems))
    for ruleset in rulesets or []:
        if not _ruleset_targets_baseline(ruleset):
            continue
        problems = _ruleset_problems(ruleset)
        if not problems:
            return CheckResult(
                "baseline_protection", "PASS", f"ruleset '{ruleset.get('name')}' 조건 충족"
            )
        reasons.append(f"ruleset '{ruleset.get('name')}': " + ", ".join(problems))
    if reasons:
        return CheckResult("baseline_protection", "FAIL", "; ".join(reasons))
    if classic_rules is None or rulesets is None:
        return CheckResult(
            "baseline_protection", "INCONCLUSIVE", "일부 조회 실패, baseline/* 규칙을 찾지 못함"
        )
    return CheckResult("baseline_protection", "FAIL", f"'{BASELINE_PATTERN}' 보호 규칙 없음")


def overall_verdict(checks: list[CheckResult]) -> str:
    statuses = {check.status for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses == {"PASS"}:
        return "PASS"
    return "INCONCLUSIVE"


# ── 실제 조회 ──────────────────────────────────────────────────


def missing_env(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV if not env.get(name)]


def _ok(status: int, body: Any, kind: type) -> Any:
    return body if status == 200 and isinstance(body, kind) else None


def _classic_nodes(status: int, body: Any) -> list[dict[str, Any]] | None:
    if status != 200 or not isinstance(body, dict) or body.get("errors"):
        return None
    try:
        nodes = body["data"]["repository"]["branchProtectionRules"]["nodes"]
    except (KeyError, TypeError):
        return None
    return nodes if isinstance(nodes, list) else None


def _ruleset_summary(ruleset: dict[str, Any]) -> dict[str, Any]:
    bypass = ruleset.get("bypass_actors")
    return {
        "id": ruleset.get("id"),
        "name": ruleset.get("name"),
        "enforcement": ruleset.get("enforcement"),
        "include": ((ruleset.get("conditions") or {}).get("ref_name") or {}).get("include"),
        "rules": [r.get("type") for r in ruleset.get("rules") or []],
        "bypass_actor_count": None if bypass is None else len(bypass),
    }


def run_check(
    env: Mapping[str, str],
    reviewer: str | None = None,
    client: httpx.Client | None = None,
    clock: Clock | None = None,
    api_version: str | None = None,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    record: dict[str, Any] = {
        "check": "github_setup",
        "schema_version": "linemedic.v4",
        "checked_at": to_rfc3339(clock.utc_now()),
        "repository": env.get("GITHUB_REPOSITORY") or None,
        "repository_id": env.get("GITHUB_REPOSITORY_ID") or None,
        "verdict": None,
        "checks": [],
        "observed": {},
        "http": [],
    }
    missing = missing_env(env)
    if missing:
        record.update(verdict="NOT_CONFIGURED", missing_env=missing)
        return record
    full_name, raw_id = env["GITHUB_REPOSITORY"], env["GITHUB_REPOSITORY_ID"]
    if not REPO_FULL_NAME_RE.fullmatch(full_name) or not raw_id.isdigit():
        failed = CheckResult(
            "repository", "FAIL", "GITHUB_REPOSITORY 또는 GITHUB_REPOSITORY_ID 형식 오류"
        )
        record.update(verdict="FAIL", checks=[asdict(failed)])
        return record
    expected_id = int(raw_id)
    owner, name = full_name.split("/")
    bot_token, setup_token = env["GITHUB_BROKER_CREDENTIAL"], env["GITHUB_SETUP_CREDENTIAL"]

    owns_client = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    reader = GitHubReader(client, api_version)

    def call(label: str, fn, *args) -> tuple[int, Any]:
        status, body = fn(*args)
        record["http"].append({"call": label, "status": status})
        return status, body

    try:
        admin_repo = _ok(
            *call("repo (setup)", reader.get, f"/repos/{full_name}", setup_token), dict
        )
        bot_repo = _ok(*call("repo (bot)", reader.get, f"/repos/{full_name}", bot_token), dict)

        identity, installation_repos = None, None
        status, body = call("user (bot)", reader.get, "/user", bot_token)
        if status == 200 and isinstance(body, dict):
            identity = {
                "kind": "user",
                "login": body.get("login"),
                "id": body.get("id"),
                "type": body.get("type"),
            }
        elif status in (401, 403):
            status, body = call(
                "installation repositories (bot)",
                reader.get,
                "/installation/repositories",
                bot_token,
            )
            if status == 200 and isinstance(body, dict):
                identity, installation_repos = {"kind": "app_installation"}, body

        classic = _classic_nodes(
            *call(
                "branchProtectionRules (setup)",
                reader.graphql,
                CLASSIC_RULES_QUERY,
                {"owner": owner, "name": name},
                setup_token,
            )
        )
        rulesets = None
        status, body = call(
            "rulesets (setup)", reader.get, f"/repos/{full_name}/rulesets", setup_token
        )
        if status == 200 and isinstance(body, list):
            rulesets = []
            for item in body:
                detail_status, detail = call(
                    f"ruleset {item.get('id')} (setup)",
                    reader.get,
                    f"/repos/{full_name}/rulesets/{item.get('id')}",
                    setup_token,
                )
                rulesets.append(
                    detail if detail_status == 200 and isinstance(detail, dict) else item
                )
    except httpx.HTTPError as exc:
        record.update(verdict="INCONCLUSIVE", error=f"HTTP_ERROR: {type(exc).__name__}")
        return record
    finally:
        if owns_client:
            client.close()

    checks = [
        check_repository(admin_repo or bot_repo, expected_id, full_name),
        check_merge_settings(admin_repo),
        check_bot_identity(identity, reviewer),
        check_bot_permissions(bot_repo, installation_repos, expected_id),
        check_baseline_protection(classic, rulesets),
    ]
    merge_keys = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge")
    record["observed"] = {
        "merge_settings": {k: admin_repo.get(k) for k in merge_keys} if admin_repo else None,
        "bot_identity": identity,
        "bot_permissions": bot_repo.get("permissions") if bot_repo else None,
        "installation_repository_count": (
            installation_repos.get("total_count") if installation_repos else None
        ),
        "baseline_classic_rules": [
            r for r in classic or [] if r.get("pattern") == BASELINE_PATTERN
        ],
        "rulesets": [_ruleset_summary(r) for r in rulesets or []],
        "reviewer": reviewer,
    }
    record["checks"] = [asdict(check) for check in checks]
    record["verdict"] = overall_verdict(checks)
    return record


def exit_code_for(verdict: str | None) -> int:
    return {"PASS": 0, "NOT_CONFIGURED": 2}.get(verdict or "", 1)


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    clock: Clock | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="github_setup_check")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--reviewer", help="봇이 아닌 리뷰어 계정 (봇 identity와 비교)")
    parser.add_argument("--api-version", help="X-GitHub-Api-Version 헤더 (N11 확인 후)")
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로 (비밀 값 없음)")
    args = parser.parse_args(argv)
    env = process_env(args.env_file) if env is None else env
    record = run_check(
        env, reviewer=args.reviewer, client=client, clock=clock, api_version=args.api_version
    )
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"GitHub 설정 점검: {record['verdict']}", file=sys.stderr)
    return exit_code_for(record["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
