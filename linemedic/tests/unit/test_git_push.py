"""W11 candidate push: 실제 git으로 로컬 bare 원격에 push하고, credential이 명령줄에 없는지 본다.

- 새 브랜치 → PUSHED(new_branch), 같은 SHA 다시 → PUSHED(up_to_date),
  다른 SHA → REJECTED(force 없음)
- 허용한 protocol만 쓴다(기본 https만). 허용하지 않은 원격은 git이 거부하고, 반영 여부를 가리지
  않고 UNKNOWN으로 두는 보수적 처리를 확인한다
- credential은 env와 임시 askpass 스크립트로만 git에 간다(argv·repr에 없음)
- FakePusher: FakeGitHub 브랜치를 바꾸고, 거절·결과 불명(반영 전/뒤)을 흉내 낸다
"""

import os
import subprocess
from pathlib import Path

import pytest

from linemedic.integrations import git_push
from linemedic.integrations.git_push import FakePusher, GitPusher
from linemedic.integrations.github import FakeGitHub

ENV = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = {
    **ENV,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
TOKEN = "test-push-credential-" + "x" * 20  # 테스트 전용 가짜 값
BRANCH = "autofix/r-20260927-000000-abcd/INC-0000000000AA/PROP-0000000000AA"


def git(*args, cwd=None, env=ENV):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """두 commit이 있는 로컬 bare 저장소(push 원본)와 빈 bare 원격."""
    work = tmp_path / "work"
    git("init", "--quiet", "-b", "main", str(work))
    (work / "a.txt").write_text("1\n")
    git("add", "a.txt", cwd=work)
    git("commit", "--quiet", "-m", "one", cwd=work, env=IDENTITY)
    first = git("rev-parse", "HEAD", cwd=work)
    (work / "a.txt").write_text("2\n")
    git("commit", "--quiet", "-am", "two", cwd=work, env=IDENTITY)
    second = git("rev-parse", "HEAD", cwd=work)
    source = tmp_path / "source.git"
    git("clone", "--quiet", "--bare", str(work), str(source))
    remote = tmp_path / "remote.git"
    git("init", "--quiet", "--bare", str(remote))
    return source, remote, first, second


def test_push_creates_branch_then_is_idempotent_and_never_forces(repos):
    source, remote, first, second = repos
    pusher = GitPusher(str(remote), TOKEN, protocols=("file",))
    assert pusher.push(source, second, BRANCH) == git_push.PushResult("PUSHED", "new_branch")
    assert git("--git-dir", str(remote), "rev-parse", f"refs/heads/{BRANCH}") == second
    assert pusher.push(source, second, BRANCH) == git_push.PushResult("PUSHED", "up_to_date")
    rejected = pusher.push(source, first, BRANCH)  # 되감기는 force 없이는 거절된다
    assert (rejected.status, rejected.detail) == ("REJECTED", "rejected_by_remote")
    assert git("--git-dir", str(remote), "rev-parse", f"refs/heads/{BRANCH}") == second


def test_only_allowed_protocols_are_used(repos):
    source, remote, _, second = repos
    result = GitPusher(str(remote), TOKEN).push(source, second, BRANCH)  # 기본은 https만
    assert result.status == "UNKNOWN" and "git_exit_128" in (result.detail or "")
    assert (
        subprocess.run(
            [
                "git",
                "--git-dir",
                str(remote),
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{BRANCH}",
            ],
            env=ENV,
            capture_output=True,
        ).returncode
        != 0
    )


def test_credential_goes_only_through_env_and_askpass(repos, monkeypatch):
    source, remote, _, second = repos
    captured = {}
    real_run = subprocess.run

    def spy(argv, **kwargs):
        captured["argv"], captured["env"] = argv, kwargs["env"]
        askpass = Path(kwargs["env"]["GIT_ASKPASS"])
        captured["password"] = real_run(
            [str(askpass), "Password for 'https://github.com': "],
            env=kwargs["env"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        captured["username"] = real_run(
            [str(askpass), "Username for 'https://github.com': "],
            env=kwargs["env"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(git_push.subprocess, "run", spy)
    pusher = GitPusher(str(remote), TOKEN, protocols=("file",))
    assert pusher.push(source, second, BRANCH).status == "PUSHED"
    assert TOKEN not in " ".join(captured["argv"]) and TOKEN not in repr(pusher)
    assert captured["env"]["LINEMEDIC_GIT_PASSWORD"] == TOKEN
    assert (captured["username"], captured["password"]) == ("x-access-token", TOKEN)
    assert "--force" not in captured["argv"] and "--no-verify" in captured["argv"]
    assert captured["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "credential.helper=" in captured["argv"]


@pytest.mark.parametrize(
    ("sha", "branch"), [("HEAD", BRANCH), ("a" * 40, "../x"), ("a" * 40, "a b")]
)
def test_invalid_sha_or_branch_never_reaches_git(repos, sha, branch):
    source, remote, _, _ = repos
    with pytest.raises(ValueError):
        GitPusher(str(remote), TOKEN, protocols=("file",)).push(source, sha, branch)


def test_fake_pusher_updates_branches_and_injects_failures():
    github = FakeGitHub(write_enabled=True)
    pusher = FakePusher(github)
    assert pusher.push(Path("x"), "a" * 40, BRANCH).status == "PUSHED"
    assert github.branches[BRANCH] == "a" * 40
    assert pusher.push(Path("x"), "b" * 40, BRANCH).status == "REJECTED"  # force 없음
    pusher.fail_next("UNKNOWN")
    assert pusher.push(Path("x"), "c" * 40, "autofix/other").status == "UNKNOWN"
    assert "autofix/other" not in github.branches  # 반영 전에 끊겼다
    pusher.fail_next("UNKNOWN", after_side_effect=True)
    assert pusher.push(Path("x"), "c" * 40, "autofix/other").status == "UNKNOWN"
    assert github.branches["autofix/other"] == "c" * 40  # 반영된 뒤 끊겼다
