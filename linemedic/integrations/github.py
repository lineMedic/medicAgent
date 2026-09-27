"""GitHub 연동 포트 (W22, spec 15 §2·16 §1·02 §4, D46).

- Control Plane의 모든 GitHub 호출은 `GitHubPort`를 거친다. 대상 repo는 만들 때 config의 등록 repo로
  고정되고 메서드에 repo·URL 인자가 없다. 그래서 등록 repo 밖을 가리키는 요청을 만들 수 없다.
- 번호는 양의 정수로, branch·필터·문자열은 형식과 길이로 검증한다.
  비신뢰 문자열을 URL path에 잇지 않는다(path에는 등록 repo 이름과 검증한 번호, 서버가 만든 뒤
  형식을 검증한 branch 이름만, 나머지는 query·JSON body로 보낸다).
- 쓰기(`create_issue`·`create_issue_comment`·`create_pull`)는 `write_enabled=false`
  (기본, shadow 모드)면 호출하지 않고 `WritePlan`만 돌려준다.
  G10에서 사람이 config `github.write_enabled`를 켠다.
- 오류: `RateLimited`·`Forbidden`·`NotFound`·`Conflict`·`Unknown`.
  `Unknown`은 timeout·연결 끊김·5xx처럼 요청이 반영됐는지 모르는 경우다
  (`request_sent`: 보냈음 True / 안 보냈음 False / 모름 None).
  불명 결과를 '실패이므로 다시 호출'로 다루지 않는다. 재시도 전에 외부 identity를 조회한다.
- credential·Authorization 헤더는 오류 메시지·repr·로그에 넣지 않는다.
- `HttpGitHub`: httpx 동기 client. base URL·API 버전 헤더는 config, credential은 env
  (`GITHUB_BROKER_CREDENTIAL`). API 버전은 N11에서 확정하기 전에는 보내지 않는다.
- `FakeGitHub`: 메모리 상태·페이지네이션·PR 섞인 Issue 목록·장애 주입(`fail_next`).
"""

import dataclasses
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from linemedic.common.clock import Clock, FakeClock
from linemedic.common.config import Settings
from linemedic.common.sanitize import mask_secrets

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "linemedic-control-plane"
MAX_TITLE_CHARS = 256
MAX_BODY_CHARS = 65536  # GitHub Issue·댓글 본문 상한
MAX_LABELS = 10
MAX_LABEL_CHARS = 50
MAX_ERROR_MESSAGE_CHARS = 200

_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_SINCE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_NEXT_LINK = re.compile(r'<[^>]*>\s*;\s*rel="next"')


# ── 응답·오류 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class RateInfo:
    remaining: int | None = None
    reset: int | None = None  # epoch 초 (X-RateLimit-Reset)
    retry_after: int | None = None  # 초 (Retry-After)


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    data: Any  # JSON(dict·list). 304면 None
    etag: str | None = None
    rate: RateInfo = field(default_factory=RateInfo)
    has_next: bool = False
    server_time: str | None = None  # 응답 Date 헤더(UTC `...Z`). polling 경계로 쓴다(W23)


@dataclass(frozen=True)
class WritePlan:
    """`write_enabled=false`에서 쓰기 대신 돌려주는 계획. 실제 요청은 보내지 않았다."""

    method: str
    path: str
    body: dict[str, Any]


class GitHubError(Exception):
    """GitHub 호출 실패. 메시지에 credential·헤더를 넣지 않는다."""

    def __init__(self, status: int | None = None, message: str = "") -> None:
        self.status = status
        self.message = message
        super().__init__(f"{type(self).__name__}(status={status}): {message}")


class RateLimited(GitHubError):
    def __init__(
        self, retry_after: int | None, reset: int | None = None, status: int = 429
    ) -> None:
        self.retry_after = retry_after
        self.reset = reset
        super().__init__(status, f"rate limited (retry_after={retry_after}, reset={reset})")


class Forbidden(GitHubError):
    """401·403: credential·권한 문제."""


class NotFound(GitHubError):
    """404·410."""


class Conflict(GitHubError):
    """409·422: 현재 상태와 충돌하거나(이미 있는 PR 등) 검증 거절."""


class Unknown(GitHubError):
    """반영 여부를 모르는 결과(timeout·연결 끊김·5xx·해석할 수 없는 성공 응답)."""

    def __init__(self, observation: str, request_sent: bool | None = None) -> None:
        self.observation = observation
        self.request_sent = request_sent
        super().__init__(None, f"{observation} (request_sent={request_sent})")


# ── 입력 검증 ─────────────────────────────────────────────────


def _check_repository(repository_id: Any, full_name: Any) -> None:
    if not isinstance(repository_id, int) or isinstance(repository_id, bool) or repository_id < 1:
        raise ValueError("repository_id는 양의 정수여야 한다")
    parts = full_name.split("/") if isinstance(full_name, str) else []
    if len(parts) != 2 or any(
        not _REPO_PART.fullmatch(part) or part in (".", "..") for part in parts
    ):
        raise ValueError("full_name은 owner/name 형식이어야 한다")


def _number(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Issue·PR 번호는 양의 정수여야 한다")
    return value


def _branch(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not _BRANCH.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ".lock", "."))
    ):
        raise ValueError("branch 이름 형식이 아니다")
    return value


def _head_filter(value: Any) -> str:
    owner, sep, branch = value.partition(":") if isinstance(value, str) else ("", "", "")
    if not sep or not _OWNER.fullmatch(owner):
        raise ValueError("head 필터는 owner:branch 형식이어야 한다")
    _branch(branch)
    return value


def _choice(value: Any, allowed: Sequence[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name}는 {', '.join(allowed)} 중 하나여야 한다")
    return value


def _bounded_int(value: Any, low: int, high: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name}는 {low}~{high} 정수여야 한다")
    return value


def _since(value: Any) -> str | None:
    if value is not None and (not isinstance(value, str) or not _SINCE.fullmatch(value)):
        raise ValueError("since는 UTC ISO 8601(...Z) 형식이어야 한다")
    return value


def _text(value: Any, name: str, max_chars: int, *, required: bool) -> str:
    if not isinstance(value, str) or len(value) > max_chars or (required and not value.strip()):
        raise ValueError(
            f"{name}는 {'비어 있지 않은 ' if required else ''}{max_chars}자 이하 문자열"
        )
    return value


def _labels(values: Sequence[str]) -> list[str]:
    labels = list(values)
    if len(labels) > MAX_LABELS or any(
        not isinstance(label, str) or not 1 <= len(label) <= MAX_LABEL_CHARS for label in labels
    ):
        raise ValueError(f"label은 {MAX_LABELS}개 이하, 각 1~{MAX_LABEL_CHARS}자")
    return labels


# ── 포트 ──────────────────────────────────────────────────────


class GitHubPort(Protocol):
    repository_id: int
    full_name: str
    write_enabled: bool

    def get_repo(self) -> GitHubResponse: ...

    def get_identity(self) -> GitHubResponse: ...

    def list_issues(
        self,
        *,
        state: str = "all",
        since: str | None = None,
        sort: str = "updated",
        direction: str = "asc",
        per_page: int = 100,
        page: int = 1,
        etag: str | None = None,
    ) -> GitHubResponse: ...

    def get_issue(self, number: int) -> GitHubResponse: ...

    def create_issue(
        self, title: str, body: str, labels: Sequence[str] = ()
    ) -> GitHubResponse | WritePlan: ...

    def list_issue_comments(
        self, number: int, *, since: str | None = None, page: int = 1
    ) -> GitHubResponse: ...

    def create_issue_comment(self, number: int, body: str) -> GitHubResponse | WritePlan: ...

    def list_pulls(
        self, *, head: str | None = None, state: str = "open", page: int = 1
    ) -> GitHubResponse: ...

    def get_pull(self, number: int) -> GitHubResponse: ...

    def get_branch_head(self, branch: str) -> GitHubResponse: ...

    def create_pull(
        self, head: str, base: str, title: str, body: str
    ) -> GitHubResponse | WritePlan: ...


class GitHubBase:
    """경로 조립·입력 검증·쓰기 차단을 한곳에 둔다. 하위 클래스는 `_send`만 구현한다."""

    def __init__(self, repository_id: int, full_name: str, *, write_enabled: bool = False) -> None:
        _check_repository(repository_id, full_name)
        self.repository_id = repository_id
        self.full_name = full_name
        self.write_enabled = write_enabled
        self.write_calls = 0  # 실제로 보낸 쓰기 요청 수

    @property
    def _repo(self) -> str:
        return f"/repos/{self.full_name}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(repository_id={self.repository_id}, "
            f"full_name={self.full_name!r}, write_enabled={self.write_enabled})"
        )

    __str__ = __repr__

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> GitHubResponse:
        raise NotImplementedError

    def _write(self, path: str, body: dict[str, Any]) -> GitHubResponse | WritePlan:
        if not self.write_enabled:
            return WritePlan("POST", path, body)
        self.write_calls += 1
        return self._send("POST", path, body=body)

    # 조회

    def get_repo(self) -> GitHubResponse:
        return self._send("GET", self._repo)

    def get_identity(self) -> GitHubResponse:
        return self._send("GET", "/user")

    def list_issues(
        self,
        *,
        state: str = "all",
        since: str | None = None,
        sort: str = "updated",
        direction: str = "asc",
        per_page: int = 100,
        page: int = 1,
        etag: str | None = None,
    ) -> GitHubResponse:
        params: dict[str, Any] = {"state": _choice(state, ("open", "closed", "all"), "state")}
        if _since(since) is not None:
            params["since"] = since
        params["sort"] = _choice(sort, ("created", "updated", "comments"), "sort")
        params["direction"] = _choice(direction, ("asc", "desc"), "direction")
        params["per_page"] = _bounded_int(per_page, 1, 100, "per_page")
        params["page"] = _bounded_int(page, 1, 10_000, "page")
        headers = {"If-None-Match": etag} if etag else None
        return self._send("GET", f"{self._repo}/issues", params=params, headers=headers)

    def get_issue(self, number: int) -> GitHubResponse:
        return self._send("GET", f"{self._repo}/issues/{_number(number)}")

    def list_issue_comments(
        self, number: int, *, since: str | None = None, page: int = 1
    ) -> GitHubResponse:
        path = f"{self._repo}/issues/{_number(number)}/comments"
        params: dict[str, Any] = {"per_page": 100, "page": _bounded_int(page, 1, 10_000, "page")}
        if _since(since) is not None:
            params["since"] = since
        return self._send("GET", path, params=params)

    def list_pulls(
        self, *, head: str | None = None, state: str = "open", page: int = 1
    ) -> GitHubResponse:
        params: dict[str, Any] = {}
        if head is not None:
            params["head"] = _head_filter(head)
        params["state"] = _choice(state, ("open", "closed", "all"), "state")
        params["per_page"] = 100
        params["page"] = _bounded_int(page, 1, 10_000, "page")
        return self._send("GET", f"{self._repo}/pulls", params=params)

    def get_pull(self, number: int) -> GitHubResponse:
        return self._send("GET", f"{self._repo}/pulls/{_number(number)}")

    def get_branch_head(self, branch: str) -> GitHubResponse:
        """브랜치가 가리키는 commit(`object.sha`). 없으면 NotFound(W11)."""
        return self._send("GET", f"{self._repo}/git/ref/heads/{_branch(branch)}")

    # 쓰기

    def create_issue(
        self, title: str, body: str, labels: Sequence[str] = ()
    ) -> GitHubResponse | WritePlan:
        payload = {
            "title": _text(title, "title", MAX_TITLE_CHARS, required=True),
            "body": _text(body, "body", MAX_BODY_CHARS, required=False),
            "labels": _labels(labels),
        }
        return self._write(f"{self._repo}/issues", payload)

    def create_issue_comment(self, number: int, body: str) -> GitHubResponse | WritePlan:
        path = f"{self._repo}/issues/{_number(number)}/comments"
        return self._write(path, {"body": _text(body, "body", MAX_BODY_CHARS, required=True)})

    def create_pull(
        self, head: str, base: str, title: str, body: str
    ) -> GitHubResponse | WritePlan:
        payload = {
            "head": _branch(head),
            "base": _branch(base),
            "title": _text(title, "title", MAX_TITLE_CHARS, required=True),
            "body": _text(body, "body", MAX_BODY_CHARS, required=False),
        }
        return self._write(f"{self._repo}/pulls", payload)


# ── HTTP ──────────────────────────────────────────────────────


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name, "")
    return int(value) if value.isdigit() else None


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    message = data.get("message", "") if isinstance(data, dict) else ""
    return mask_secrets(str(message))[:MAX_ERROR_MESSAGE_CHARS]


def _server_time(headers: httpx.Headers) -> str | None:
    try:
        moment = parsedate_to_datetime(headers.get("date", ""))
    except (TypeError, ValueError, IndexError):
        return None
    if moment is None or moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_response(response: httpx.Response) -> GitHubResponse:
    headers = response.headers
    rate = RateInfo(
        remaining=_int_header(headers, "x-ratelimit-remaining"),
        reset=_int_header(headers, "x-ratelimit-reset"),
        retry_after=_int_header(headers, "retry-after"),
    )
    etag = headers.get("etag")
    status = response.status_code
    server_time = _server_time(headers)
    if status == 304:
        return GitHubResponse(304, None, etag, rate, server_time=server_time)
    if 200 <= status < 300:
        try:
            data = response.json() if response.content else None
        except ValueError:
            raise Unknown("invalid_json_response", request_sent=True) from None
        has_next = bool(_NEXT_LINK.search(headers.get("link", "")))
        return GitHubResponse(status, data, etag, rate, has_next, server_time)
    message = _error_message(response)
    limited = rate.remaining == 0 or rate.retry_after is not None or "rate limit" in message.lower()
    if status == 429 or (status == 403 and limited):
        raise RateLimited(rate.retry_after, rate.reset, status)
    if status in (401, 403):
        raise Forbidden(status, message)
    if status in (404, 410):
        raise NotFound(status, message)
    if status in (409, 422):
        raise Conflict(status, message)
    raise Unknown(f"http_{status}", request_sent=True)  # 5xx 등: 반영됐는지 모른다


class HttpGitHub(GitHubBase):
    def __init__(
        self,
        repository_id: int,
        full_name: str,
        credential: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str | None = None,
        write_enabled: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(repository_id, full_name, write_enabled=write_enabled)
        if not credential:
            raise ValueError("GitHub credential이 없다")
        if not base_url.startswith("https://"):
            raise ValueError("base_url은 https여야 한다")
        self.api_version = api_version
        self._credential = credential
        self._client = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> GitHubResponse:
        request_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._credential}",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        }
        if self.api_version:  # N11에서 확정하기 전에는 보내지 않는다(D46)
            request_headers["X-GitHub-Api-Version"] = self.api_version
        try:
            response = self._client.request(
                method, path, params=params, json=body, headers=request_headers
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise Unknown("connect_failed", request_sent=False) from None
        except httpx.ReadTimeout:
            raise Unknown("timeout", request_sent=True) from None
        except httpx.HTTPError:  # 쓰기 중 timeout·연결 끊김: 일부만 갔을 수 있다
            raise Unknown("transport_error", request_sent=None) from None
        return _to_response(response)


class GitHubNotConfigured(Exception):
    """등록 repo·credential이 없어 포트를 만들 수 없다(G2 전). 값은 메시지에 넣지 않는다."""


def github_from_settings(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> HttpGitHub:
    """설정의 등록 repo·`github` 절·env credential로만 포트를 만든다."""
    repo, gh = settings.config.repository, settings.config.github
    missing = [
        name
        for name, value in (
            ("GITHUB_REPOSITORY_ID", repo.id),
            ("GITHUB_REPOSITORY", repo.full_name),
        )
        if value is None
    ]
    credential = settings.secrets.get("GITHUB_BROKER_CREDENTIAL")
    if not credential:
        missing.append("GITHUB_BROKER_CREDENTIAL")
    if missing or repo.id is None or repo.full_name is None or credential is None:
        raise GitHubNotConfigured("미설정: " + ", ".join(missing))
    return HttpGitHub(
        repo.id,
        repo.full_name,
        credential,
        base_url=gh.base_url,
        api_version=gh.api_version,
        write_enabled=gh.write_enabled,
        timeout_seconds=gh.timeout_seconds,
        transport=transport,
    )


# ── Fake ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeRequest:
    method: str
    path: str
    params: Mapping[str, Any]
    body: Mapping[str, Any] | None
    headers: Mapping[str, str] = field(default_factory=dict)


_FAILURES: dict[str, Callable[[], GitHubError]] = {
    "timeout": lambda: Unknown("timeout", request_sent=None),
    "disconnect": lambda: Unknown("connection_closed", request_sent=None),
    "server_error": lambda: Unknown("http_502", request_sent=True),
    "connect_failed": lambda: Unknown("connect_failed", request_sent=False),
    "forbidden": lambda: Forbidden(403, "Resource not accessible by integration"),
    "rate_limited": lambda: RateLimited(60, None, 403),
    "not_found": lambda: NotFound(404, "Not Found"),
    "conflict": lambda: Conflict(422, "Validation Failed"),
}


@dataclass
class _Failure:
    kind: str
    after_side_effect: bool
    when: Callable[[FakeRequest], bool] | None


def _gh_time(clock: Clock) -> str:
    return clock.utc_now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeGitHub(GitHubBase):
    """테스트용 메모리 GitHub. 요청은 `requests`에 남고 경로는 실제 client와 같은 코드로 만든다."""

    def __init__(
        self,
        repository_id: int = 100001,
        full_name: str = "demo-team/l3-mes-api",
        *,
        write_enabled: bool = False,
        clock: Clock | None = None,
        bot_login: str = "linemedic-bot",
        bot_id: int = 900001,
        actual_repository_id: int | None = None,
    ) -> None:
        super().__init__(repository_id, full_name, write_enabled=write_enabled)
        self.clock = clock or FakeClock()
        self.identity = {"login": bot_login, "id": bot_id, "type": "User"}
        self.actual_repository_id = actual_repository_id or repository_id
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.pulls: dict[int, dict[str, Any]] = {}
        self.branches: dict[str, str] = {}  # branch → commit SHA (push·준비로만 바뀐다)
        self.requests: list[FakeRequest] = []
        self._failures: list[_Failure] = []
        self._next_number = 1
        self._next_id = 5_000_001

    # 테스트 준비

    def add_issue(
        self,
        *,
        title: str = "Issue",
        body: str = "",
        author_id: int = 200001,
        author_login: str = "reporter",
        state: str = "open",
        labels: Sequence[str] = (),
        assignees: Sequence[int] = (),
        is_pull: bool = False,
    ) -> dict[str, Any]:
        """외부 사람이 만든 Issue(또는 PR 항목)를 넣는다. 요청 기록에 남지 않는다."""
        user = {"login": author_login, "id": author_id, "type": "User"}
        return self._new_issue(title, body, user, list(labels), state, list(assignees), is_pull)

    def update_issue(self, number: int, **changes: Any) -> dict[str, Any]:
        """사람이 Issue를 고친 것처럼 필드를 바꾸고 updated_at을 지금으로 올린다.

        `labels`는 이름 목록, `assignees`는 user ID 목록으로 받는다.
        """
        issue = self.issues[number]
        for key, value in changes.items():
            if key == "labels":
                issue["labels"] = [{"name": name} for name in value]
            elif key == "assignees":
                issue["assignees"] = [{"login": f"user{uid}", "id": uid} for uid in value]
            elif key in ("title", "body", "state"):
                issue[key] = value
            else:
                raise ValueError(f"바꿀 수 없는 필드: {key}")
        now = _gh_time(self.clock)
        issue["updated_at"] = now
        issue["closed_at"] = now if issue["state"] == "closed" else None
        return issue

    def remove_issue(self, number: int) -> None:
        """삭제·이전으로 목록에서 사라진 Issue."""
        self.issues.pop(number)

    def fail_next(
        self,
        kind: str,
        *,
        after_side_effect: bool = False,
        when: Callable[[FakeRequest], bool] | None = None,
    ) -> None:
        """다음(또는 `when`에 맞는 첫) 요청을 실패시킨다.

        `after_side_effect`면 요청을 반영한 뒤 실패한다(생성 뒤 timeout 등).
        """
        if kind not in _FAILURES:
            raise ValueError(f"알 수 없는 장애 종류: {kind}")
        if kind == "connect_failed" and after_side_effect:
            raise ValueError("보내지 않은 요청은 부작용이 없다")
        self._failures.append(_Failure(kind, after_side_effect, when))

    # 내부

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _new_issue(
        self,
        title: str,
        body: str,
        user: dict[str, Any],
        labels: list[str],
        state: str = "open",
        assignees: list[int] | None = None,
        is_pull: bool = False,
    ) -> dict[str, Any]:
        number, now = self._next_number, _gh_time(self.clock)
        self._next_number += 1
        issue_id = self._id()
        html_url = f"https://github.com/{self.full_name}/{'pull' if is_pull else 'issues'}/{number}"
        issue: dict[str, Any] = {
            "id": issue_id,
            "node_id": f"I_fake{issue_id}",
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "user": user,
            "labels": [{"name": label} for label in labels],
            "assignees": [{"login": f"user{uid}", "id": uid} for uid in assignees or []],
            "created_at": now,
            "updated_at": now,
            "closed_at": now if state == "closed" else None,
            "html_url": html_url,
            "repository_url": f"https://api.github.com/repos/{self.full_name}",
        }
        if is_pull:
            issue["pull_request"] = {"html_url": html_url}
        self.issues[number] = issue
        return issue

    def _take_failure(self, request: FakeRequest) -> _Failure | None:
        for index, failure in enumerate(self._failures):
            if failure.when is None or failure.when(request):
                return self._failures.pop(index)
        return None

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> GitHubResponse:
        request = FakeRequest(
            method, path, dict(params or {}), dict(body) if body else None, dict(headers or {})
        )
        self.requests.append(request)
        failure = self._take_failure(request)
        if failure is not None and not failure.after_side_effect:
            raise _FAILURES[failure.kind]()
        response = self._route(request, dict(headers or {}))
        if failure is not None:
            raise _FAILURES[failure.kind]()
        return dataclasses.replace(response, server_time=_gh_time(self.clock))

    def _route(self, request: FakeRequest, headers: dict[str, str]) -> GitHubResponse:
        if request.path == "/user" and request.method == "GET":
            return GitHubResponse(200, dict(self.identity))
        prefix = f"/repos/{self.full_name}"
        if not request.path.startswith(prefix):
            raise NotFound(404, "Not Found")  # 등록 repo 밖(포트 코드로는 만들 수 없다)
        rest = request.path[len(prefix) :]
        if request.method == "GET" and rest.startswith("/git/ref/heads/"):
            return self._get_branch_head(rest[len("/git/ref/heads/") :])
        key = (request.method, re.sub(r"/\d+", "/{n}", rest))
        number = int(match.group(1)) if (match := re.search(r"/(\d+)", rest)) else 0
        handlers: dict[tuple[str, str], Callable[[], GitHubResponse]] = {
            ("GET", ""): self._get_repo,
            ("GET", "/issues"): lambda: self._list_issues(request.params, headers),
            ("POST", "/issues"): lambda: self._create_issue(request.body or {}),
            ("GET", "/issues/{n}"): lambda: self._get_issue(number),
            ("GET", "/issues/{n}/comments"): lambda: self._list_comments(number, request.params),
            ("POST", "/issues/{n}/comments"): lambda: self._create_comment(
                number, request.body or {}
            ),
            ("GET", "/pulls"): lambda: self._list_pulls(request.params),
            ("POST", "/pulls"): lambda: self._create_pull(request.body or {}),
            ("GET", "/pulls/{n}"): lambda: self._get_pull(number),
        }
        handler = handlers.get(key)
        if handler is None:
            raise NotFound(404, "Not Found")
        return handler()

    def _get_repo(self) -> GitHubResponse:
        repo = {"id": self.actual_repository_id, "full_name": self.full_name, "private": True}
        return GitHubResponse(200, repo)

    def _list_issues(self, params: Mapping[str, Any], headers: dict[str, str]) -> GitHubResponse:
        items = list(self.issues.values())
        if params["state"] != "all":
            items = [item for item in items if item["state"] == params["state"]]
        if "since" in params:
            since = _parse_time(params["since"])
            items = [item for item in items if _parse_time(item["updated_at"]) >= since]
        field_name = {"created": "created_at", "updated": "updated_at"}.get(params["sort"])
        items.sort(key=lambda item: (item.get(field_name or "created_at"), item["number"]))
        if params["direction"] == "desc":
            items.reverse()
        per_page, page = params["per_page"], params["page"]
        chunk = items[(page - 1) * per_page : page * per_page]
        digest = hashlib.sha256(json.dumps(chunk, sort_keys=True).encode()).hexdigest()[:16]
        etag = f'W/"{digest}"'
        if headers.get("If-None-Match") == etag:
            return GitHubResponse(304, None, etag)
        return GitHubResponse(200, chunk, etag, has_next=page * per_page < len(items))

    def _create_issue(self, body: Mapping[str, Any]) -> GitHubResponse:
        issue = self._new_issue(body["title"], body["body"], dict(self.identity), body["labels"])
        return GitHubResponse(201, issue)

    def _get_issue(self, number: int) -> GitHubResponse:
        if number not in self.issues:
            raise NotFound(404, "Not Found")
        return GitHubResponse(200, self.issues[number])

    def _list_comments(self, number: int, params: Mapping[str, Any]) -> GitHubResponse:
        if number not in self.issues:
            raise NotFound(404, "Not Found")
        comments = self.comments.get(number, [])
        if "since" in params:
            since = _parse_time(params["since"])
            comments = [c for c in comments if _parse_time(c["updated_at"]) >= since]
        page = params["page"]
        chunk = comments[(page - 1) * 100 : page * 100]
        return GitHubResponse(200, chunk, has_next=page * 100 < len(comments))

    def _create_comment(self, number: int, body: Mapping[str, Any]) -> GitHubResponse:
        if number not in self.issues:
            raise NotFound(404, "Not Found")
        comment_id, now = self._id(), _gh_time(self.clock)
        comment = {
            "id": comment_id,
            "node_id": f"IC_fake{comment_id}",
            "body": body["body"],
            "user": dict(self.identity),
            "created_at": now,
            "updated_at": now,
            "html_url": f"https://github.com/{self.full_name}/issues/{number}#issuecomment-{comment_id}",
        }
        self.comments.setdefault(number, []).append(comment)
        self.issues[number]["updated_at"] = now
        return GitHubResponse(201, comment)

    def _list_pulls(self, params: Mapping[str, Any]) -> GitHubResponse:
        pulls = list(self.pulls.values())
        if params["state"] != "all":
            pulls = [p for p in pulls if p["state"] == params["state"]]
        if "head" in params:
            pulls = [p for p in pulls if p["head"]["label"] == params["head"]]
        per_page, page = params["per_page"], params["page"]
        chunk = pulls[(page - 1) * per_page : page * per_page]
        return GitHubResponse(200, chunk, has_next=page * per_page < len(pulls))

    def add_pull(
        self,
        *,
        title: str,
        body: str = "",
        author_id: int = 200001,
        head: str = "feature",
        head_sha: str = "0" * 40,
        base: str = "main",
    ) -> dict[str, Any]:
        """사람(또는 `author_id`)이 연 PR(테스트 준비용). Issue 번호 공간을 같이 쓴다."""
        user = {"login": f"user{author_id}", "id": author_id, "type": "User"}
        issue = self._new_issue(title, body, user, [], is_pull=True)
        owner = self.full_name.split("/")[0]
        pull = {
            **issue,
            "head": {"ref": head, "label": f"{owner}:{head}", "sha": head_sha},
            "base": {"ref": base},
        }
        pull.pop("pull_request")
        pull["merged"] = False
        self.pulls[issue["number"]] = pull
        return pull

    def _create_pull(self, body: Mapping[str, Any]) -> GitHubResponse:
        owner = self.full_name.split("/")[0]
        label = f"{owner}:{body['head']}"
        if any(p["head"]["label"] == label and p["state"] == "open" for p in self.pulls.values()):
            raise Conflict(422, "A pull request already exists")
        if body["head"] not in self.branches or body["base"] not in self.branches:
            raise Conflict(422, "Validation Failed")  # head·base 브랜치가 없다
        issue = self._new_issue(body["title"], body["body"], dict(self.identity), [], is_pull=True)
        pull = {
            **issue,
            "head": {"ref": body["head"], "label": label, "sha": self.branches[body["head"]]},
            "base": {"ref": body["base"], "sha": self.branches[body["base"]]},
            "merged": False,
        }
        pull.pop("pull_request")
        self.pulls[issue["number"]] = pull
        return GitHubResponse(201, pull)

    def _get_pull(self, number: int) -> GitHubResponse:
        if number not in self.pulls:
            raise NotFound(404, "Not Found")
        return GitHubResponse(200, self.pulls[number])

    def _get_branch_head(self, branch: str) -> GitHubResponse:
        if branch not in self.branches:
            raise NotFound(404, "Not Found")
        ref = {
            "ref": f"refs/heads/{branch}",
            "object": {"sha": self.branches[branch], "type": "commit"},
        }
        return GitHubResponse(200, ref)
