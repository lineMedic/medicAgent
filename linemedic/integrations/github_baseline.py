"""run 기준 브랜치 준비 (W19, spec 11 §3·§7, docs/05 ⑨, D86).

`make run-new CREATE_BASELINE=1`이 setup credential(`GITHUB_SETUP_CREDENTIAL`)로 등록 repo에
`baseline/<run_id>` 브랜치를 `BASELINE_COMMIT`에 만든다. G2(repo·credential)와 G10(쓰기 허락)
뒤에만 쓴다.

- 쓰기 전에 repo를 조회해 숫자 ID가 등록 repo와 같은지 확인한다. 다르면 아무것도 쓰지 않는다
- 브랜치가 이미 같은 SHA에 있으면 그대로 둔다(`EXISTS`). 다른 SHA를 가리키면 옮기지 않고 멈춘다
  (force push·브랜치 이동·삭제 없음)
- credential은 헤더에만 두고 출력·기록하지 않는다
"""

import re
from typing import Protocol

import httpx

from linemedic.common.ids import is_valid_run_id

API_VERSION = "2022-11-28"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BaselineError(RuntimeError):
    """기준 브랜치를 준비하지 못했다. `reason`은 기록해도 되는 짧은 코드다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BaselinePort(Protocol):
    def ensure_branch(self, branch: str, sha: str) -> str:
        """`CREATED` 또는 `EXISTS`. 준비하지 못하면 BaselineError."""
        ...


def baseline_branch(run_id: str) -> str:
    if not is_valid_run_id(run_id):  # `/`가 들어가면 baseline/* 보호 패턴과 맞지 않는다
        raise BaselineError("invalid_run_id")
    return f"baseline/{run_id}"


def _check(branch: str, sha: str) -> None:
    if not branch.startswith("baseline/") or not is_valid_run_id(branch.split("/", 1)[1]):
        raise BaselineError("invalid_branch")
    if not SHA_RE.fullmatch(sha):
        raise BaselineError("invalid_sha")


class HttpBaseline:
    def __init__(
        self,
        repository_id: int,
        full_name: str,
        credential: str,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.repository_id = repository_id
        self.full_name = full_name
        self._credential = credential
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = timeout

    def __repr__(self) -> str:  # credential을 보이지 않는다
        return f"HttpBaseline({self.repository_id}, {self.full_name!r})"

    def ensure_branch(self, branch: str, sha: str) -> str:
        _check(branch, sha)
        headers = {
            "Authorization": f"Bearer {self._credential}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        repo = f"/repos/{self.full_name}"
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                found = client.get(repo)
                if found.status_code != 200:
                    raise BaselineError(f"repo_lookup_{found.status_code}")
                if found.json().get("id") != self.repository_id:
                    raise BaselineError("repo_id_mismatch")  # 등록 repo가 아니면 쓰지 않는다
                existing = self._head(client, f"{repo}/git/ref/heads/{branch}")
                if existing is not None:
                    return _same(existing, sha)
                created = client.post(
                    f"{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}
                )
                if created.status_code == 201:
                    return "CREATED"
                if created.status_code == 422:  # 그사이 누가 만들었다
                    again = self._head(client, f"{repo}/git/ref/heads/{branch}")
                    if again is not None:
                        return _same(again, sha)
                raise BaselineError(f"create_{created.status_code}")
        except httpx.HTTPError as exc:
            raise BaselineError(f"transport_{type(exc).__name__}") from None

    @staticmethod
    def _head(client: httpx.Client, path: str) -> str | None:
        response = client.get(path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise BaselineError(f"ref_lookup_{response.status_code}")
        return str((response.json().get("object") or {}).get("sha") or "")


def _same(existing: str, sha: str) -> str:
    if existing != sha:
        raise BaselineError("branch_points_elsewhere")  # 옮기지 않는다
    return "EXISTS"


class FakeBaseline:
    """메모리 기준 브랜치(테스트용). `branches`에 이미 있는 브랜치를 넣어 둘 수 있다."""

    def __init__(self) -> None:
        self.branches: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def ensure_branch(self, branch: str, sha: str) -> str:
        _check(branch, sha)
        self.calls.append((branch, sha))
        if branch in self.branches:
            return _same(self.branches[branch], sha)
        self.branches[branch] = sha
        return "CREATED"
