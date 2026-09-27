"""W03 GitHub 설정 점검·보호 시험 스크립트와 doctor github 항목의 단위 테스트.

실제 GitHub 호출은 없다(httpx.MockTransport). live 검증은 G2·G10 이후 사람 허락을 받아 실행한다.
"""

import json
from pathlib import Path

import httpx
import pytest

from linemedic.common.clock import FakeClock
from linemedic.scripts import doctor
from linemedic.scripts import github_protection_probe as probe
from linemedic.scripts import github_setup_check as setup

REPO = "lineMedic/l3-mes-api"
REPO_ID = 123456
BOT_TOKEN = "ghs_UNIT-TEST-BOT-TOKEN-DO-NOT-PRINT"
SETUP_TOKEN = "ghp_UNIT-TEST-SETUP-TOKEN-DO-NOT-PRINT"
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "GITHUB_BROKER_CREDENTIAL": BOT_TOKEN,
    "GITHUB_SETUP_CREDENTIAL": SETUP_TOKEN,
}
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "linemedic.toml"

ADMIN_REPO = {
    "id": REPO_ID,
    "full_name": REPO,
    "allow_squash_merge": True,
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
}
BOT_REPO = {
    "id": REPO_ID,
    "full_name": REPO,
    "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True},
}
CLASSIC_OK = {
    "pattern": "baseline/*",
    "requiresApprovingReviews": True,
    "requiredApprovingReviewCount": 1,
    "dismissesStaleReviews": True,
    "requireLastPushApproval": True,
    "isAdminEnforced": True,
    "allowsForcePushes": False,
    "bypassPullRequestAllowances": {"totalCount": 0},
    "bypassForcePushAllowances": {"totalCount": 0},
}
RULESET_OK = {
    "id": 7,
    "name": "baseline",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/baseline/*"], "exclude": []}},
    "rules": [
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_last_push_approval": True,
            },
        },
        {"type": "non_fast_forward"},
    ],
    "bypass_actors": [],
}


def no_secrets(text: str) -> bool:
    return BOT_TOKEN not in text and SETUP_TOKEN not in text


# ── 파싱·판정 함수 ────────────────────────────────────────────


def test_repository_check():
    assert setup.check_repository(ADMIN_REPO, REPO_ID, REPO.lower()).status == "PASS"
    assert setup.check_repository(dict(ADMIN_REPO, id=1), REPO_ID, REPO).status == "FAIL"
    assert setup.check_repository(dict(ADMIN_REPO, full_name="x/y"), REPO_ID, REPO).status == "FAIL"
    assert setup.check_repository(None, REPO_ID, REPO).status == "INCONCLUSIVE"


def test_merge_settings_require_squash_only():
    assert setup.check_merge_settings(ADMIN_REPO).status == "PASS"
    assert setup.check_merge_settings(dict(ADMIN_REPO, allow_merge_commit=True)).status == "FAIL"
    assert setup.check_merge_settings(dict(ADMIN_REPO, allow_squash_merge=False)).status == "FAIL"
    missing = {k: v for k, v in ADMIN_REPO.items() if k != "allow_rebase_merge"}
    assert setup.check_merge_settings(missing).status == "INCONCLUSIVE"


def test_bot_identity_must_differ_from_reviewer():
    identity = {"kind": "user", "login": "linemedic-bot", "id": 9}
    assert setup.check_bot_identity(identity, reviewer="human-reviewer").status == "PASS"
    assert setup.check_bot_identity(identity, reviewer="LineMedic-Bot").status == "FAIL"
    assert setup.check_bot_identity(None, reviewer="human-reviewer").status == "INCONCLUSIVE"


def test_bot_permissions():
    assert setup.check_bot_permissions(BOT_REPO, None, REPO_ID).status == "PASS"
    admin = dict(BOT_REPO, permissions=dict(BOT_REPO["permissions"], admin=True))
    assert setup.check_bot_permissions(admin, None, REPO_ID).status == "FAIL"
    no_push = dict(BOT_REPO, permissions=dict(BOT_REPO["permissions"], push=False))
    assert setup.check_bot_permissions(no_push, None, REPO_ID).status == "FAIL"
    no_perm_info = {"id": REPO_ID, "full_name": REPO}
    assert setup.check_bot_permissions(no_perm_info, None, REPO_ID).status == "INCONCLUSIVE"
    one_repo = {"total_count": 1, "repositories": [{"id": REPO_ID, "full_name": REPO}]}
    assert setup.check_bot_permissions(no_perm_info, one_repo, REPO_ID).status == "PASS"
    two_repos = {"total_count": 2, "repositories": [{"id": REPO_ID}, {"id": 999}]}
    assert setup.check_bot_permissions(no_perm_info, two_repos, REPO_ID).status == "FAIL"


def test_baseline_protection_classic_rule():
    assert setup.check_baseline_protection([CLASSIC_OK], []).status == "PASS"
    assert setup.check_baseline_protection([], []).status == "FAIL"
    other_pattern = dict(CLASSIC_OK, pattern="main")
    assert setup.check_baseline_protection([other_pattern], []).status == "FAIL"
    for change in (
        {"requiresApprovingReviews": False},
        {"requiredApprovingReviewCount": 0},
        {"dismissesStaleReviews": False},
        {"isAdminEnforced": False},
        {"allowsForcePushes": True},
        {"bypassPullRequestAllowances": {"totalCount": 1}},
    ):
        result = setup.check_baseline_protection([dict(CLASSIC_OK, **change)], [])
        assert result.status == "FAIL", change
    assert setup.check_baseline_protection(None, None).status == "INCONCLUSIVE"


def test_baseline_protection_ruleset():
    assert setup.check_baseline_protection([], [RULESET_OK]).status == "PASS"
    with_bypass = dict(RULESET_OK, bypass_actors=[{"actor_type": "OrganizationAdmin"}])
    assert setup.check_baseline_protection([], [with_bypass]).status == "FAIL"
    disabled = dict(RULESET_OK, enforcement="disabled")
    assert setup.check_baseline_protection([], [disabled]).status == "FAIL"
    no_ff_rule = dict(RULESET_OK, rules=[RULESET_OK["rules"][0]])
    assert setup.check_baseline_protection([], [no_ff_rule]).status == "FAIL"


# ── 점검 스크립트 전체 흐름 ────────────────────────────────────


def github_handler(request: httpx.Request) -> httpx.Response:
    token = request.headers["authorization"].removeprefix("Bearer ")
    path = request.url.path
    if path == f"/repos/{REPO}":
        return httpx.Response(200, json=ADMIN_REPO if token == SETUP_TOKEN else BOT_REPO)
    if path == "/user":
        return httpx.Response(200, json={"login": "linemedic-bot", "id": 9, "type": "User"})
    if path == "/graphql":
        nodes = {"nodes": [CLASSIC_OK]}
        return httpx.Response(200, json={"data": {"repository": {"branchProtectionRules": nodes}}})
    if path == f"/repos/{REPO}/rulesets":
        return httpx.Response(200, json=[])
    return httpx.Response(404, json={"message": "Not Found"})


def test_setup_check_not_configured(capsys):
    assert setup.main([], env={"GITHUB_BROKER_CREDENTIAL": BOT_TOKEN}) == 2
    output = capsys.readouterr().out
    assert "NOT_CONFIGURED" in output
    assert "GITHUB_REPOSITORY_ID" in output
    assert no_secrets(output)


def test_setup_check_end_to_end_pass_without_leaking_tokens():
    client = httpx.Client(transport=httpx.MockTransport(github_handler))
    record = setup.run_check(ENV, reviewer="human-reviewer", client=client, clock=FakeClock())
    statuses = {c["name"]: c["status"] for c in record["checks"]}
    assert record["verdict"] == "PASS", record["checks"]
    assert statuses == {
        "repository": "PASS",
        "merge_settings": "PASS",
        "bot_identity": "PASS",
        "bot_permissions": "PASS",
        "baseline_protection": "PASS",
    }
    assert record["observed"]["baseline_classic_rules"][0]["pattern"] == "baseline/*"
    assert no_secrets(json.dumps(record, ensure_ascii=False))


def test_setup_check_fails_when_merge_commits_allowed():
    def handler(request):
        if request.url.path == f"/repos/{REPO}" and SETUP_TOKEN in request.headers["authorization"]:
            return httpx.Response(200, json=dict(ADMIN_REPO, allow_merge_commit=True))
        return github_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    record = setup.run_check(ENV, reviewer=None, client=client, clock=FakeClock())
    assert record["verdict"] == "FAIL"


# ── doctor github 항목 ───────────────────────────────────────


def doctor_env(**overrides):
    return dict(ENV, **overrides)


def test_doctor_github_not_configured_without_credentials():
    ctx = doctor.DoctorContext(config_path=DEFAULT_CONFIG, env={})
    status, detail = doctor.check_github(ctx)
    assert status == "NOT_CONFIGURED"
    assert "GITHUB_BROKER_CREDENTIAL" in detail


def test_doctor_github_ok_and_fail_without_printing_tokens():
    ok_ctx = doctor.DoctorContext(
        config_path=DEFAULT_CONFIG,
        env=doctor_env(),
        github_get=lambda path, token: (200, {"id": REPO_ID, "full_name": REPO}),
    )
    status, detail = doctor.check_github(ok_ctx)
    assert status == "OK" and no_secrets(detail)
    bad_ctx = doctor.DoctorContext(
        config_path=DEFAULT_CONFIG,
        env=doctor_env(),
        github_get=lambda path, token: (200, {"id": 1, "full_name": REPO}),
    )
    status, detail = doctor.check_github(bad_ctx)
    assert status == "FAIL" and "불일치" in detail and no_secrets(detail)
    invalid = doctor.DoctorContext(
        config_path=DEFAULT_CONFIG, env=doctor_env(GITHUB_REPOSITORY="a/b/c")
    )
    assert doctor.check_github(invalid)[0] == "FAIL"


# ── 보호 시험 스크립트 ────────────────────────────────────────


def test_probe_default_is_dry_run_without_http(capsys):
    def explode(request):
        raise AssertionError("dry-run must not call GitHub")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    exit_code = probe.main([], env=ENV, client=client, clock=FakeClock())
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "PLANNED" in output
    assert "baseline/r-probe-" in output
    assert no_secrets(output)


class ProbeGitHub:
    """보호 시험용 가짜 GitHub. 봇의 보호 브랜치 쓰기와 리뷰 없는 머지 응답을 바꿀 수 있다."""

    def __init__(self, direct_push_status=409, merge_status=405):
        self.direct_push_status = direct_push_status
        self.merge_status = merge_status
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        path, method = request.url.path, request.method
        self.calls.append((method, path, token == BOT_TOKEN))
        base = f"/repos/{REPO}"
        if method == "GET" and path == f"{base}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if method == "POST" and path == f"{base}/git/refs":
            return httpx.Response(201, json={"ref": json.loads(request.content)["ref"]})
        if method == "PUT" and path.startswith(f"{base}/contents/probe/direct-push-"):
            return httpx.Response(self.direct_push_status, json={"message": "protected"})
        if method == "PUT" and path.startswith(f"{base}/contents/probe/pr-change-"):
            return httpx.Response(201, json={"commit": {"sha": "b" * 40}})
        if method == "POST" and path == f"{base}/pulls":
            return httpx.Response(201, json={"number": 5, "html_url": "https://example.test/pr/5"})
        if method == "POST" and path == f"{base}/issues/5/labels":
            return httpx.Response(200, json=[{"name": "probe"}])
        if method == "PUT" and path == f"{base}/pulls/5/merge":
            return httpx.Response(self.merge_status, json={"message": "review required"})
        return httpx.Response(404, json={"message": "Not Found"})


def run_probe(fake: ProbeGitHub) -> dict:
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return probe.run_probe(ENV, client=client, clock=FakeClock())


def test_probe_passes_when_bot_writes_are_refused():
    fake = ProbeGitHub()
    record = run_probe(fake)
    assert record["verdict"] == "PASS", record["checks"]
    statuses = {c["name"]: c["status"] for c in record["checks"]}
    assert statuses["bot_direct_push_refused"] == "PASS"
    assert statuses["merge_without_review_refused"] == "PASS"
    assert statuses["reviewer_can_approve"] == "MANUAL"
    # baseline 브랜치는 setup credential로, 나머지 쓰기는 봇 credential로 만든다
    create_baseline = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/git/refs")][0]
    assert create_baseline[2] is False
    assert record["created"]["pull_request"] == 5
    assert no_secrets(json.dumps(record, ensure_ascii=False))


def test_probe_fails_when_bot_can_push_or_merge():
    assert run_probe(ProbeGitHub(direct_push_status=201))["verdict"] == "FAIL"
    assert run_probe(ProbeGitHub(merge_status=200))["verdict"] == "FAIL"


def test_probe_unclear_status_is_inconclusive():
    record = run_probe(ProbeGitHub(merge_status=403))
    assert record["verdict"] == "INCONCLUSIVE"


@pytest.mark.parametrize("missing", ["GITHUB_SETUP_CREDENTIAL", "GITHUB_BROKER_CREDENTIAL"])
def test_probe_not_configured(missing, capsys):
    env = {k: v for k, v in ENV.items() if k != missing}
    assert probe.main(["--confirm-write"], env=env) == 2
    assert missing in capsys.readouterr().out
