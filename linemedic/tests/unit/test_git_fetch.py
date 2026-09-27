"""W12 merge commit fetch: 실제 git으로 로컬 bare 원격에서 신뢰 mirror로 commit 하나만 가져온다.

- 요청한 SHA 하나(와 그 조상)만 `refs/linemedic/release/<sha>`로 가져온다.
  다른 branch tip은 가져오지 않는다(main 최신 선택 없음). tip이 아닌 commit도 SHA로 가져온다
- mirror에 이미 있으면 원격을 부르지 않는다
- 허용한 protocol만(기본 https). 원격에 없는 commit·없는 원격은 FAILED(배포하지 않고 이관)
- credential은 env와 임시 askpass로만 간다(argv·repr에 없음)
"""

import os
import subprocess

import pytest

from linemedic.integrations import git_fetch
from linemedic.integrations.git_fetch import GitFetcher, LocalOnlyFetcher, has_commit

ENV = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = {
    **ENV,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
TOKEN = "test-fetch-credential-" + "x" * 20  # 테스트 전용 가짜 값


def git(*args, cwd=None, env=ENV):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(work, text: str) -> str:
    (work / "a.txt").write_text(text)
    git("add", "a.txt", cwd=work)
    git("commit", "--quiet", "-m", text, cwd=work, env=IDENTITY)
    return git("rev-parse", "HEAD", cwd=work)


@pytest.fixture
def repos(tmp_path):
    """mirror는 첫 commit만, 원격은 main 두 commit 더 + 다른 branch commit을 가진다."""
    work = tmp_path / "work"
    git("init", "--quiet", "-b", "main", str(work))
    first = commit(work, "1\n")
    mirror = tmp_path / "mirror.git"
    git("clone", "--quiet", "--bare", str(work), str(mirror))
    merged = commit(work, "2\n")  # 승인한 merge commit
    later = commit(work, "3\n")  # 그 뒤 main에 쌓인 commit
    git("checkout", "--quiet", "-b", "other", cwd=work)
    side = commit(work, "side\n")
    remote = tmp_path / "remote.git"
    git("clone", "--quiet", "--bare", str(work), str(remote))
    return {"mirror": mirror, "remote": remote, "first": first, "merged": merged,
            "later": later, "side": side}  # fmt: skip


def test_fetches_exactly_the_approved_commit_not_branch_tips(repos):
    mirror = repos["mirror"]
    assert not has_commit(mirror, repos["merged"])
    result = GitFetcher(str(repos["remote"]), TOKEN, protocols=("file",)).fetch(
        mirror, repos["merged"]
    )
    assert result == git_fetch.FetchResult("FETCHED", "fetched")
    assert has_commit(mirror, repos["merged"])  # branch tip이 아닌 commit도 SHA로
    ref = f"{git_fetch.RELEASE_REF_PREFIX}/{repos['merged']}"
    assert git("--git-dir", str(mirror), "rev-parse", ref) == repos["merged"]
    assert not has_commit(mirror, repos["later"]) and not has_commit(mirror, repos["side"])


def test_present_commit_is_not_fetched_again(repos, tmp_path):
    fetcher = GitFetcher(str(tmp_path / "nowhere.git"), TOKEN, protocols=("file",))
    assert fetcher.fetch(repos["mirror"], repos["first"]) == git_fetch.FetchResult(
        "FETCHED", "already_present"
    )


def test_missing_commit_or_remote_fails_without_touching_the_mirror(repos, tmp_path):
    fetcher = GitFetcher(str(repos["remote"]), TOKEN, protocols=("file",))
    missing = fetcher.fetch(repos["mirror"], "e" * 40)
    assert missing.status == "FAILED" and missing.detail.startswith("git_exit_")
    gone = GitFetcher(str(tmp_path / "nowhere.git"), TOKEN, protocols=("file",))
    assert gone.fetch(repos["mirror"], repos["merged"]).status == "FAILED"
    assert not has_commit(repos["mirror"], repos["merged"])


def test_only_allowed_protocols_are_used(repos):
    result = GitFetcher(str(repos["remote"]), TOKEN).fetch(repos["mirror"], repos["merged"])
    assert result.status == "FAILED" and "git_exit_128" in (result.detail or "")
    assert not has_commit(repos["mirror"], repos["merged"])


def test_invalid_sha_never_reaches_git(repos):
    with pytest.raises(ValueError):
        GitFetcher(str(repos["remote"]), TOKEN, protocols=("file",)).fetch(repos["mirror"], "HEAD")
    assert has_commit(repos["mirror"], "HEAD") is False


def test_credential_goes_only_through_env_and_askpass(repos, monkeypatch):
    captured = {}
    real_run = subprocess.run

    def spy(argv, **kwargs):
        if "fetch" in argv:
            captured["argv"], captured["env"] = argv, kwargs["env"]
        return real_run(argv, **kwargs)

    monkeypatch.setattr(git_fetch.subprocess, "run", spy)
    fetcher = GitFetcher(str(repos["remote"]), TOKEN, protocols=("file",))
    assert fetcher.fetch(repos["mirror"], repos["merged"]).status == "FETCHED"
    assert TOKEN not in " ".join(captured["argv"]) and TOKEN not in repr(fetcher)
    assert captured["env"]["LINEMEDIC_GIT_PASSWORD"] == TOKEN
    assert "credential.helper=" in captured["argv"] and "--no-tags" in captured["argv"]
    assert (
        captured["argv"][-1]
        == f"+{repos['merged']}:{git_fetch.RELEASE_REF_PREFIX}/" + (repos["merged"])
    )


def test_fetch_that_reports_success_without_the_commit_fails(repos, monkeypatch):
    real_run = subprocess.run

    def fake_success(argv, **kwargs):
        if "fetch" in argv:  # 원격이 성공이라고만 답하고 아무것도 주지 않았다
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(git_fetch.subprocess, "run", fake_success)
    fetcher = GitFetcher(str(repos["remote"]), TOKEN, protocols=("file",))
    assert fetcher.fetch(repos["mirror"], repos["merged"]) == git_fetch.FetchResult(
        "FAILED", "commit_missing_after_fetch"
    )


def test_local_only_fetcher_uses_what_the_mirror_has(repos):
    fetcher = LocalOnlyFetcher()
    assert fetcher.fetch(repos["mirror"], repos["first"]).status == "FETCHED"
    assert fetcher.fetch(repos["mirror"], repos["merged"]) == git_fetch.FetchResult(
        "FAILED", "commit_not_in_mirror"
    )
