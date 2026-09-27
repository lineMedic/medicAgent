"""GitHub Issue 댓글 알림 어댑터 (W26, spec 16 §1·§4·§5, D46). 공식 SDK API가 아닌 내부 계약이다.

- `send(target, rendered)` → `Accepted(receipt_id=댓글 ID, canonical_ref=댓글 URL, accepted_at)` /
  `Rejected(reason, retry_after, safe_to_retry)` / `UnknownResult(observation)`.
  보내지 않았음이 확실할 때(연결 전 실패·rate limit)만 `safe_to_retry`다. 응답이 끊기면 UNKNOWN이다.
- `reconcile(target, rendered_sha256, marker, since)` → 봇 작성자 + marker + 본문 hash가 모두 맞는
  댓글이 정확히 1개면 `FOUND`, 끝까지 봤는데 없으면 `CONFIRMED_ABSENT`,
  조회가 불완전하면 `INCONCLUSIVE`,
  2개 이상이면 `CONFLICT`. marker만 같은 다른 사용자의 댓글은 채택하지 않는다.
- 대상 repo는 포트(W22)에 고정돼 있고 Issue 번호는 호출자가 DB의 work에서 읽는다.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from linemedic.integrations.github import (
    Conflict,
    Forbidden,
    GitHubError,
    GitHubPort,
    GitHubResponse,
    NotFound,
    RateLimited,
    Unknown,
    WritePlan,
)

MAX_RECONCILE_PAGES = 10


@dataclass(frozen=True)
class Accepted:
    receipt_id: str
    canonical_ref: str | None
    accepted_at: str | None


@dataclass(frozen=True)
class Rejected:
    reason: str
    retry_after: int | None
    safe_to_retry: bool


@dataclass(frozen=True)
class UnknownResult:
    observation: str


@dataclass(frozen=True)
class ReconcileResult:
    outcome: str  # FOUND | CONFIRMED_ABSENT | INCONCLUSIVE | CONFLICT
    receipt: Accepted | None = None
    detail: str | None = None


class GitHubCommentAdapter:
    name = "github_comment"

    def __init__(self, port: GitHubPort) -> None:
        self.port = port
        self._bot_id: int | None = None

    def not_ready(self) -> str | None:
        """보낼 수 없는 이유(shadow 모드). None이면 보낼 수 있다."""
        return None if self.port.write_enabled else "write_disabled"

    def send(self, issue_number: int, body: str) -> Accepted | Rejected | UnknownResult:
        try:
            response = self.port.create_issue_comment(issue_number, body)
        except Unknown as exc:
            if exc.request_sent is False:  # 연결 전 실패: 보내지 않았다
                return Rejected("connect_failed", None, True)
            return UnknownResult(exc.observation)
        except RateLimited as exc:
            return Rejected("rate_limited", exc.retry_after, True)
        except (Forbidden, NotFound, Conflict) as exc:
            return Rejected(type(exc).__name__, None, False)
        if isinstance(response, WritePlan):
            return Rejected("write_disabled", None, False)
        assert isinstance(response, GitHubResponse)
        comment = response.data if isinstance(response.data, dict) else {}
        if "id" not in comment:
            return UnknownResult("comment_without_id")
        return Accepted(str(comment["id"]), comment.get("html_url"), comment.get("created_at"))

    def _bot(self) -> int:
        if self._bot_id is None:
            self._bot_id = self.port.get_identity().data["id"]
        return self._bot_id

    def reconcile(
        self, issue_number: int, *, marker: str, body_sha256: str, since: str | None
    ) -> ReconcileResult:
        matches: list[dict[str, Any]] = []
        try:
            bot_id = self._bot()
            complete = False
            for page in range(1, MAX_RECONCILE_PAGES + 1):
                response = self.port.list_issue_comments(issue_number, since=since, page=page)
                for comment in response.data or []:
                    body = comment.get("body") or ""
                    # 다른 사용자가 marker·본문을 복사해도 채택하지 않는다
                    author = (comment.get("user") or {}).get("id")
                    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                    if author == bot_id and marker in body and digest == body_sha256:
                        matches.append(comment)
                if not response.has_next:
                    complete = True
                    break
        except GitHubError as exc:
            return ReconcileResult("INCONCLUSIVE", detail=type(exc).__name__)
        if len(matches) == 1:
            comment = matches[0]
            receipt = Accepted(
                str(comment["id"]), comment.get("html_url"), comment.get("created_at")
            )
            return ReconcileResult("FOUND", receipt)
        if len(matches) > 1:
            return ReconcileResult("CONFLICT", detail=f"{len(matches)} comments")
        return ReconcileResult("CONFIRMED_ABSENT" if complete else "INCONCLUSIVE")
