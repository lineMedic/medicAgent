"""승인한 merge commit을 신뢰 mirror로 가져오기 (W12, spec 08 §2 실행, D82).

- `GitFetcher`: 등록 repo에서 `<sha>` commit 하나만 `refs/linemedic/release/<sha>`로 가져온다.
  branch 최신(main·baseline tip)을 따라가지 않는다. 고정 argv, hook·사용자·시스템 git 설정 없음,
  허용한 protocol만(기본 https). credential은 W11 push와 같은 임시 `GIT_ASKPASS`로만 git에 준다.
- mirror에 그 commit이 이미 있으면 가져오지 않는다(내용 주소라 같은 SHA는 같은 내용이다).
- 결과는 `FETCHED`(mirror에 commit이 있음) / `FAILED`(없음: 배포하지 않고 이관)다.
  fetch는 외부를 바꾸지 않으므로 결과 불명(UNKNOWN)이 없다.
"""

import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from linemedic.common.sanitize import mask_secrets
from linemedic.integrations.git_push import askpass_env, protocol_config

FETCH_TIMEOUT_SECONDS = 120
RELEASE_REF_PREFIX = "refs/linemedic/release"
_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class FetchResult:
    status: Literal["FETCHED", "FAILED"]
    detail: str | None = None


class CommitFetcher(Protocol):
    def fetch(self, mirror: Path, sha: str) -> FetchResult: ...


def has_commit(mirror: Path, sha: str) -> bool:
    """신뢰 mirror에 `sha` commit이 있는가(사용자 설정·hook 없이 조회만)."""
    if not _SHA.fullmatch(sha) or not (mirror / "HEAD").is_file():
        return False
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    try:
        proc = subprocess.run(
            ["git", f"--git-dir={mirror}", "cat-file", "-e", f"{sha}^{{commit}}"],
            env=env,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


class LocalOnlyFetcher:
    """원격을 부르지 않는다. mirror에 이미 있는 commit만 쓴다(테스트·원격 미설정)."""

    def fetch(self, mirror: Path, sha: str) -> FetchResult:
        if has_commit(mirror, sha):
            return FetchResult("FETCHED", "already_present")
        return FetchResult("FAILED", "commit_not_in_mirror")


class GitFetcher:
    def __init__(
        self,
        remote_url: str,
        credential: str | None,
        *,
        protocols: Sequence[str] = ("https",),
        timeout: float = FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self.remote_url = remote_url
        self._credential = credential
        self.protocols = tuple(protocols)
        self.timeout = timeout

    def __repr__(self) -> str:  # credential을 보이지 않는다
        return f"GitFetcher(remote_url={self.remote_url!r}, protocols={self.protocols!r})"

    def fetch(self, mirror: Path, sha: str) -> FetchResult:
        if not _SHA.fullmatch(sha):
            raise ValueError("가져올 commit은 40자 SHA여야 한다")
        if has_commit(mirror, sha):
            return FetchResult("FETCHED", "already_present")
        argv = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.allow=never",
            *protocol_config(self.protocols),
            f"--git-dir={mirror}",
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-write-fetch-head",
            "--quiet",
            "--",
            self.remote_url,
            f"+{sha}:{RELEASE_REF_PREFIX}/{sha}",
        ]
        with tempfile.TemporaryDirectory(prefix="linemedic-fetch-") as tmp:
            try:
                proc = subprocess.run(
                    argv,
                    env=askpass_env(tmp, self._credential),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return FetchResult("FAILED", "timeout")
            except FileNotFoundError:
                return FetchResult("FAILED", "git_missing")
        if proc.returncode != 0:
            last = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
            return FetchResult("FAILED", f"git_exit_{proc.returncode}: {mask_secrets(last)[:200]}")
        if not has_commit(mirror, sha):
            return FetchResult("FAILED", "commit_missing_after_fetch")
        return FetchResult("FETCHED", "fetched")
