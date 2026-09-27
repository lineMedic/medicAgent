"""candidate commit push (W11, spec 06 §6, D81).

- `GitPusher`: 제안마다 만든 disposable bare 사본(W10)에서 `<sha>:refs/heads/<branch>`를 등록 repo에
  push한다. 고정 argv, force·hook 없음, 사용자·시스템 git 설정 없음, 허용한 protocol만(기본 https).
  credential은 명령줄·로그에 넣지 않는다. 임시 `GIT_ASKPASS` 스크립트가 env에서 읽어 git에 준다.
- 결과: `PUSHED`(새 브랜치 또는 이미 같은 SHA), `REJECTED`(원격이 거절: 다른 SHA의 브랜치 등, 반영
  안 됨), `UNKNOWN`(timeout·연결 오류 등 반영 여부를 모름). UNKNOWN은 다시 push하지 않고 조회한다.
- `FakePusher`: 테스트용. FakeGitHub의 `branches`를 바꾸고 장애를 주입한다.
"""

import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from linemedic.common.sanitize import mask_secrets
from linemedic.integrations.github import FakeGitHub, _branch

PUSH_TIMEOUT_SECONDS = 120
_SHA = re.compile(r"[0-9a-f]{40}")
_ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s\\n' "x-access-token" ;;
  *) printf '%s\\n' "$LINEMEDIC_GIT_PASSWORD" ;;
esac
"""


@dataclass(frozen=True)
class PushResult:
    status: Literal["PUSHED", "REJECTED", "UNKNOWN"]
    detail: str | None = None


class BranchPusher(Protocol):
    def push(self, git_dir: Path, sha: str, branch: str) -> PushResult: ...


class GitPusher:
    def __init__(
        self,
        remote_url: str,
        credential: str | None,
        *,
        protocols: Sequence[str] = ("https",),
        timeout: float = PUSH_TIMEOUT_SECONDS,
    ) -> None:
        self.remote_url = remote_url
        self._credential = credential
        self.protocols = tuple(protocols)
        self.timeout = timeout

    def __repr__(self) -> str:  # credential을 보이지 않는다
        return f"GitPusher(remote_url={self.remote_url!r}, protocols={self.protocols!r})"

    def push(self, git_dir: Path, sha: str, branch: str) -> PushResult:
        if not _SHA.fullmatch(sha):
            raise ValueError("push할 commit은 40자 SHA여야 한다")
        refspec = f"{sha}:refs/heads/{_branch(branch)}"
        allowed = [
            arg
            for protocol in self.protocols
            for arg in ("-c", f"protocol.{protocol}.allow=always")
        ]
        argv = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.allow=never",
            *allowed,
            f"--git-dir={git_dir}",
            "push",
            "--porcelain",
            "--no-verify",
            "--",
            self.remote_url,
            refspec,
        ]
        with tempfile.TemporaryDirectory(prefix="linemedic-push-") as tmp:
            askpass = Path(tmp) / "askpass.sh"
            askpass.write_text(_ASKPASS, encoding="utf-8")
            askpass.chmod(stat.S_IRWXU)
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": tmp,
                "LC_ALL": "C",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(askpass),
                "LINEMEDIC_GIT_PASSWORD": self._credential or "",
            }
            try:
                proc = subprocess.run(
                    argv,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return PushResult("UNKNOWN", "timeout")
            except FileNotFoundError:
                return PushResult("REJECTED", "git_missing")  # 실행하지 못했다: 반영 없음
        flags = [line[0] for line in proc.stdout.splitlines() if len(line) > 1 and line[1] == "\t"]
        if proc.returncode == 0 and flags and all(flag in "*=" for flag in flags):
            return PushResult("PUSHED", "up_to_date" if flags == ["="] else "new_branch")
        if "!" in flags:
            return PushResult("REJECTED", "rejected_by_remote")
        detail = mask_secrets(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
        return PushResult("UNKNOWN", f"git_exit_{proc.returncode}: {detail[:200]}")


class FakePusher:
    """테스트용 push. `fail_next`로 거절·결과 불명(반영 뒤/전)을 흉내 낸다."""

    def __init__(self, github: FakeGitHub) -> None:
        self.github = github
        self.pushes: list[tuple[str, str]] = []
        self._next: tuple[str, bool] | None = None

    def fail_next(
        self, status: Literal["REJECTED", "UNKNOWN"], *, after_side_effect: bool = False
    ) -> None:
        self._next = (status, after_side_effect)

    def push(self, git_dir: Path, sha: str, branch: str) -> PushResult:
        _branch(branch)
        self.pushes.append((branch, sha))
        failure, self._next = self._next, None
        if failure is not None and not failure[1]:
            return PushResult(failure[0], "injected")
        current = self.github.branches.get(branch)
        if current is not None and current != sha:
            return PushResult("REJECTED", "rejected_by_remote")  # force push 없음
        self.github.branches[branch] = sha
        if failure is not None:
            return PushResult(failure[0], "injected_after_side_effect")
        return PushResult("PUSHED", "up_to_date" if current == sha else "new_branch")
