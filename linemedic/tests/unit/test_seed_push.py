"""W03 시드 push(`seed_demo_repo --push`)의 단위 테스트.

GitHub 호출은 httpx.MockTransport, 원격은 로컬 bare 저장소(file protocol)다. 실제 push는
G2·G10과 사용자 허락 뒤 `--confirm-write`로 한다.
"""

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from linemedic.integrations.git_push import GitPusher, PushResult
from linemedic.scripts import seed_demo_repo

REPO = "lineMedic/l3-mes-api"
REPO_ID = 123456
SETUP_TOKEN = "ghp_UNIT-TEST-SETUP-TOKEN-DO-NOT-PRINT"
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "GITHUB_SETUP_CREDENTIAL": SETUP_TOKEN,
}
REPO_BODY = {"id": REPO_ID, "full_name": REPO}


def _repo_client(repo: dict | None = None, status: int = 200, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(status, json=repo if repo is not None else REPO_BODY)

    return httpx.Client(transport=httpx.MockTransport(handler))


class RecordingPusher:
    def __init__(self, result: PushResult | None = None) -> None:
        self.calls: list[tuple[Path, str, str]] = []
        self.result = result or PushResult("PUSHED", "new_branch")

    def push(self, git_dir: Path, sha: str, branch: str) -> PushResult:
        self.calls.append((git_dir, sha, branch))
        return self.result


def _git(args: list[str], cwd: Path) -> str:
    return seed_demo_repo._git(args, cwd)


def _bare(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", "--bare", "--template=", str(path)], path.parent)
    return path


def _remote_main(remote: Path) -> str | None:
    proc = subprocess.run(
        ["git", f"--git-dir={remote}", "rev-parse", "--verify", "--quiet", "refs/heads/main"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None


@pytest.fixture
def seed(tmp_path):
    return seed_demo_repo.build_seed_repo(tmp_path / "seed")


def test_default_is_plan_without_http_or_push(seed):
    pusher = RecordingPusher()

    def explode(request):
        raise AssertionError("계획 모드에서 GitHub를 부르면 안 된다")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    record = seed_demo_repo.push_seed(ENV, seed, confirm_write=False, client=client, pusher=pusher)
    assert record["verdict"] == "PLANNED"
    assert pusher.calls == []
    assert SETUP_TOKEN not in json.dumps(record)


@pytest.mark.parametrize("missing", seed_demo_repo.PUSH_ENV)
def test_not_configured_never_pushes(seed, missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    pusher = RecordingPusher()
    record = seed_demo_repo.push_seed(env, seed, confirm_write=True, pusher=pusher)
    assert record["verdict"] == "NOT_CONFIGURED"
    assert record["missing_env"] == [missing]
    assert pusher.calls == []


def test_non_numeric_repo_id_fails_without_http(seed):
    pusher = RecordingPusher()
    record = seed_demo_repo.push_seed(
        {**ENV, "GITHUB_REPOSITORY_ID": "l3-mes-api"}, seed, confirm_write=True, pusher=pusher
    )
    assert record["verdict"] == "FAIL"
    assert pusher.calls == []


def test_checks_repo_id_with_setup_credential_before_push(seed):
    calls: list = []
    pusher = RecordingPusher()
    record = seed_demo_repo.push_seed(
        ENV, seed, confirm_write=True, client=_repo_client(calls=calls), pusher=pusher
    )
    assert calls == [("GET", f"/repos/{REPO}", f"Bearer {SETUP_TOKEN}")]
    assert pusher.calls == [(Path(seed["path"]) / ".git", seed["commit"], "main")]
    assert record["verdict"] == "PASS"
    assert record["baseline_commit"] == seed["commit"]
    assert len(record["baseline_commit"]) == 40


@pytest.mark.parametrize(
    "repo,status,verdict",
    [
        ({**REPO_BODY, "id": REPO_ID + 1}, 200, "FAIL"),
        ({**REPO_BODY, "full_name": "other/l3-mes-api"}, 200, "FAIL"),
        ({"message": "Not Found"}, 404, "INCONCLUSIVE"),
    ],
)
def test_wrong_or_unreadable_repo_never_pushes(seed, repo, status, verdict):
    pusher = RecordingPusher()
    record = seed_demo_repo.push_seed(
        ENV, seed, confirm_write=True, client=_repo_client(repo, status), pusher=pusher
    )
    assert record["verdict"] == verdict
    assert pusher.calls == []
    assert "baseline_commit" not in record


def test_repo_lookup_error_is_inconclusive_without_push(seed):
    def boom(request):
        raise httpx.ConnectError("down")

    pusher = RecordingPusher()
    record = seed_demo_repo.push_seed(
        ENV,
        seed,
        confirm_write=True,
        client=httpx.Client(transport=httpx.MockTransport(boom)),
        pusher=pusher,
    )
    assert record["verdict"] == "INCONCLUSIVE"
    assert pusher.calls == []


def test_push_to_empty_remote_then_up_to_date(seed, tmp_path):
    remote = _bare(tmp_path / "remote.git")
    pusher = GitPusher(str(remote), SETUP_TOKEN, protocols=("file",))

    first = seed_demo_repo.push_seed(
        ENV, seed, confirm_write=True, client=_repo_client(), pusher=pusher
    )
    assert first["verdict"] == "PASS"
    assert first["push"] == {"status": "PUSHED", "detail": "new_branch"}
    assert _remote_main(remote) == seed["commit"]

    again = seed_demo_repo.push_seed(
        ENV, seed, confirm_write=True, client=_repo_client(), pusher=pusher
    )
    assert again["verdict"] == "PASS"
    assert again["push"] == {"status": "PUSHED", "detail": "up_to_date"}
    assert SETUP_TOKEN not in json.dumps(first) + json.dumps(again)


def test_refuses_to_overwrite_other_remote_history(seed, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("다른 이력\n", encoding="utf-8")
    _git(["init", "--quiet", "--template=", "-b", "main"], other)
    _git(["add", "--all"], other)
    _git(["commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", "x"], other)
    existing = _git(["rev-parse", "HEAD"], other)
    remote = _bare(tmp_path / "remote.git")
    _git(["push", "--quiet", str(remote), "main:main"], other)

    record = seed_demo_repo.push_seed(
        ENV,
        seed,
        confirm_write=True,
        client=_repo_client(),
        pusher=GitPusher(str(remote), SETUP_TOKEN, protocols=("file",)),
    )
    assert record["verdict"] == "FAIL"
    assert record["push"]["status"] == "REJECTED"
    assert _remote_main(remote) == existing  # force push 없음
    assert "baseline_commit" not in record


def test_unknown_push_result_is_inconclusive_and_not_retried(seed):
    pusher = RecordingPusher(PushResult("UNKNOWN", "timeout"))
    record = seed_demo_repo.push_seed(
        ENV, seed, confirm_write=True, client=_repo_client(), pusher=pusher
    )
    assert record["verdict"] == "INCONCLUSIVE"
    assert len(pusher.calls) == 1
    assert "baseline_commit" not in record


def test_cli_records_result_without_secret(tmp_path, capsys):
    out = tmp_path / "evidence" / "seed.json"
    code = seed_demo_repo.main(
        ["--push", "--confirm-write", "--record", str(out)],
        env=ENV,
        client=_repo_client(),
        pusher=RecordingPusher(),
    )
    assert code == 0
    saved = out.read_text(encoding="utf-8")
    assert json.loads(saved)["verdict"] == "PASS"
    assert SETUP_TOKEN not in saved + capsys.readouterr().out


def test_cli_plan_and_not_configured_write_no_record(tmp_path):
    out = tmp_path / "seed.json"
    assert seed_demo_repo.main(["--push", "--record", str(out)], env=ENV) == 0
    assert seed_demo_repo.main(["--push", "--confirm-write", "--record", str(out)], env={}) == 2
    assert not out.exists()


def test_cli_rejected_push_exit_code(tmp_path):
    code = seed_demo_repo.main(
        ["--push", "--confirm-write"],
        env=ENV,
        client=_repo_client(),
        pusher=RecordingPusher(PushResult("REJECTED", "rejected_by_remote")),
    )
    assert code == 1
