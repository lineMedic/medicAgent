"""등록 repo Issue polling·mirror·checkpoint·snapshot hash (W23, spec 15 §2·§3.3·§5, docs/05 ③).

조회
- 최초 `initial_import`: `state=all` 전 페이지를 mirror에 넣고 활성화 시각을 남긴다.
  backlog는 실행하지 않는다.
- `poll_once`: `since = checkpoint − overlap`, `sort=updated`, `direction=asc`,
  `per_page`·`max_pages`(config). PR 항목·등록 repo 밖 항목은 mirror에 넣지 않는다.
- 페이지마다 mirror를 저장하고, **모든 페이지가 끝난 뒤에만** checkpoint를 서버 시각 경계로 옮긴다.
  cap에 걸려 잔여 페이지가 있으면 실제로 읽은 최대 시각까지만 옮기고 `complete=false`.
- 중간에 실패하면 checkpoint는 그대로다. 다시 읽은 항목은 같은 `poll_event_key`
  (repo·node ID·updated_at·snapshot hash)로 건너뛴다.
- ETag는 같은 경로·query·권한 범위(등록 repo의 봇 credential)에만 쓰고, 304면 그 페이지를 다시
  처리하지 않는다.
- rate limit이면 `Retry-After`(없으면 reset)까지 조회하지 않는다. `full_every`번째 조회는 since 없는
  전체 조회로 delta 누락과 삭제·이전된 Issue를 확인한다.

새 Issue (활성화 뒤 만들어진 open Issue를 처음 볼 때만)
- incident(`GITHUB_ISSUE`, provisional fingerprint `issue:<repo_id>:<number>`, count 0)와
  work `WAITING_APPROVAL`을 만든다(docs/03 §3의 router·issue_sync work 생성).
- router(W24)가 만든 Issue는 router가 CREATE_ISSUE 응답으로 mirror·work를 먼저 기록한다.
  watcher는 같은 `poll_event_key`로 건너뛰거나 snapshot이 같아 새 work를 만들지 않는다.
- 승인된 작성자(숫자 ID)·deny label 없음·사람 작업 없음 → 자동 승인 대상
  (승인 자체는 W25 정책 `auto_approve`가 한다).
- 사람 assignee·다른 사람의 PR → `BLOCKED(HUMAN_WORK_IN_PROGRESS)`와 충돌 보고
  (`WORK_BLOCKED` intent).
- 그 밖(미승인 작성자·deny label·PR 목록 불완전) → `WAITING_APPROVAL`(운영자 승인 대기).

이미 본 Issue
- 본문·라벨·댓글 변화는 mirror만 갱신한다(댓글은 트리거가 아니다).
- 승인 snapshot이 바뀌고 활성 work가 있으면 scope 재검사로 넘긴다(`on_scope_changed`, W25).
- closed·deny label(권한 회수)·삭제면 미시작 work를 router 정책으로 차단하고, 그 밖의 활성 work에는
  `cancel_requested`를 건다. reopened는 자동 재실행하지 않는다.
- incident를 복구 상태로 만들지 않는다(INV-11).

`issue_intake.enabled = false`(G10 전)면 mirror·checkpoint만 갱신하고,
만들 work는 `planned`로만 보고한다.
"""

import dataclasses
import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.clock import Clock, to_rfc3339
from linemedic.common.config import LineMedicConfig
from linemedic.common.ids import new_id
from linemedic.control_plane import audit, checkpoints, supervisor
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.state import TERMINAL_WORK_STATUSES, Actor, coupled_transition
from linemedic.control_plane.store import Store, Tx
from linemedic.integrations.github import GitHubError, GitHubPort, RateLimited

INTEGRATION_ID = "github_issues"
STATE_KEY = "sync"
FINGERPRINT_VERSION = "issue-v1"
FULL_RECONCILE_EVERY = 10
MAX_ETAGS = 50
MAX_BACKOFF_SECONDS = 900
RETRY_AFTER_APPROVAL = "운영자가 새 generation을 승인한 뒤"

AutoApprove = Callable[[str], None]
ScopeChanged = Callable[[str], None]


# ── 시각·snapshot ─────────────────────────────────────────────


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _gh(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_sha256(
    repository_id: int, issue: Mapping[str, Any], permission_labels: Iterable[str]
) -> str:
    """승인 snapshot hash (docs/03 §7). updated_at·댓글 수·우리 bot 댓글은 넣지 않는다."""
    names = {label.get("name") for label in issue.get("labels") or [] if isinstance(label, dict)}
    assignees = sorted(
        a["id"] for a in issue.get("assignees") or [] if isinstance(a, dict) and "id" in a
    )
    return sha256_hex(
        {
            "repository_id": repository_id,
            "node_id": issue["node_id"],
            "author_id": (issue.get("user") or {}).get("id"),
            "title": issue.get("title") or "",
            "body": issue.get("body") or "",
            "state": issue["state"],
            "assignees": assignees,
            "labels": sorted(name for name in names if name in set(permission_labels)),
        }
    )


def is_pull_request(item: Mapping[str, Any]) -> bool:
    return "pull_request" in item


def _labels(item: Mapping[str, Any]) -> set[str]:
    return {label.get("name") for label in item.get("labels") or [] if isinstance(label, dict)}


# ── 결과 ──────────────────────────────────────────────────────


@dataclass
class SyncResult:
    mode: str  # initial_import | delta | full | backoff | busy
    pages: int = 0
    seen: int = 0
    mirrored: int = 0
    skipped_pull_requests: int = 0
    skipped_other_repository: int = 0
    new_works: list[str] = field(default_factory=list)
    blocked_works: list[str] = field(default_factory=list)
    cancel_requested: list[str] = field(default_factory=list)
    scope_changed: list[str] = field(default_factory=list)
    planned: list[dict[str, Any]] = field(default_factory=list)  # intake가 꺼져 있을 때 만들 work
    complete: bool | None = None
    checkpoint: str | None = None
    retry_after: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class _Pass:
    """한 번의 조회에서 페이지 사이에 넘기는 값."""

    decide: bool
    activated_at: datetime | None
    bot_id: int | None
    open_pulls: list[dict[str, Any]] | None = None
    pulls_complete: bool = True
    auto_approve: list[str] = field(default_factory=list)
    seen_numbers: set[int] = field(default_factory=set)


# ── 동기화 ────────────────────────────────────────────────────


class IssueSync:
    def __init__(
        self,
        store: Store,
        port: GitHubPort,
        *,
        run_id: str,
        config: LineMedicConfig,
        catalog: Catalog,
        clock: Clock,
        routing_scope: str | None = None,
        auto_approve: AutoApprove | None = None,
        on_scope_changed: ScopeChanged | None = None,
        full_every: int = FULL_RECONCILE_EVERY,
    ) -> None:
        intake = config.issue_intake
        scope = routing_scope or intake.routing_scope
        if scope.startswith("eval:") and scope != f"eval:{run_id}":
            raise ValueError("eval routing_scope가 이 run과 다르다")
        if config.repository.id not in (None, port.repository_id):
            raise ValueError("포트의 repo가 등록 repo와 다르다")
        self.store = store
        self.port = port
        self.run_id = run_id
        self.intake = intake
        self.routing_scope = scope
        self.state_key = f"{STATE_KEY}:{scope}"  # 활성화·checkpoint는 routing scope마다 따로
        self.clock = clock
        self.catalog = catalog
        self.route_id = intake.start_notification_route_id
        self.service = config.repository.service_id
        self.line_id = config.services[self.service].line_id
        self.permission_labels = frozenset(catalog.deny_labels)
        self.auto_approve = auto_approve
        self.on_scope_changed = on_scope_changed
        self.full_every = full_every
        self._lock = threading.Lock()

    # 공개

    def poll_once(self, wait_seconds: float = 0) -> SyncResult:
        """한 번 조회한다. 아직 활성화 전이면 initial import를 한다.

        다른 조회가 진행 중이면 `wait_seconds`까지 기다리고, 그래도 끝나지 않으면 `busy`다.
        """
        acquired = (
            self._lock.acquire(timeout=wait_seconds)
            if wait_seconds > 0
            else self._lock.acquire(blocking=False)
        )
        if not acquired:
            return SyncResult("busy")
        try:
            state = self._state()
            if not state.get("activated_at"):
                return self._pass(state, "initial_import", since=None)
            not_before = state.get("not_before")
            if not_before and self.clock.utc_now() < _parse(not_before):
                wait = int((_parse(not_before) - self.clock.utc_now()).total_seconds()) + 1
                return SyncResult("backoff", checkpoint=state.get("checkpoint"), retry_after=wait)
            full = (state.get("polls", 0) + 1) % self.full_every == 0
            if full:
                return self._pass(state, "full", since=None)
            since = _gh(
                _parse(state["checkpoint"]) - timedelta(seconds=self.intake.overlap_seconds)
            )
            return self._pass(state, "delta", since=since)
        finally:
            self._lock.release()

    def run(self, stop: threading.Event) -> None:
        """poll 루프(W13 `make start`가 thread로 띄운다). `stop`이 설정될 때까지 돈다."""
        while True:
            result = self.poll_once()
            if stop.wait(self.next_wait(result)):
                return

    def next_wait(self, result: SyncResult) -> float:
        base = float(self.intake.poll_interval_seconds)
        if result.retry_after:
            return max(base, float(result.retry_after))
        if result.error:
            failures = self._state().get("consecutive_failures", 1)
            return min(base * 2 ** min(failures, 4), MAX_BACKOFF_SECONDS)
        return base

    # 상태

    def _state(self) -> dict[str, Any]:
        with self.store.read() as tx:
            return checkpoints.get(tx, INTEGRATION_ID, self.state_key) or {}

    def _etag_key(self, since: str | None, page: int) -> str:
        return sha256_hex(
            {
                "path": f"/repos/{self.port.full_name}/issues",
                "principal": f"repo:{self.port.repository_id}",
                "query": {"since": since, "page": page, "per_page": self.intake.per_page},
            }
        )[:32]

    # 조회 한 번

    def _pass(self, state: dict[str, Any], mode: str, since: str | None) -> SyncResult:
        result = SyncResult(mode)
        etags: dict[str, Any] = dict(state.get("etags") or {})
        decide = mode != "initial_import"
        activated = state.get("activated_at")
        ctx = _Pass(
            decide=decide,
            activated_at=_parse(activated) if activated else None,
            bot_id=state.get("bot_id"),
        )
        boundary: str | None = None
        fetched_max: str | None = None
        try:
            if decide and ctx.bot_id is None:
                ctx.bot_id = self.port.get_identity().data["id"]
            result.complete = False
            for page in range(1, self.intake.max_pages + 1):
                key = self._etag_key(since, page)
                cached = etags.get(key)
                response = self.port.list_issues(
                    state="all",
                    since=since,
                    sort="updated",
                    direction="asc",
                    per_page=self.intake.per_page,
                    page=page,
                    etag=cached["etag"] if cached else None,
                )
                boundary = boundary or response.server_time or _gh(self.clock.utc_now())
                result.pages += 1
                if response.status == 304 and cached:
                    has_next = bool(cached["has_next"])
                    ctx.seen_numbers.update(cached["numbers"])
                else:
                    items = [item for item in response.data or [] if isinstance(item, dict)]
                    has_next = response.has_next
                    issues = [item for item in items if not is_pull_request(item)]
                    etags.pop(key, None)
                    if response.etag:
                        etags[key] = {
                            "etag": response.etag,
                            "has_next": has_next,
                            "numbers": [item["number"] for item in issues],
                        }
                    self._maybe_load_pulls(ctx, issues)
                    with self.store.tx() as tx:
                        for item in items:
                            self._observe(tx, item, result, ctx)
                    for item in issues:
                        ctx.seen_numbers.add(item["number"])
                        if fetched_max is None or item["updated_at"] > fetched_max:
                            fetched_max = item["updated_at"]
                    self._run_auto_approve(ctx)
                if not has_next:
                    result.complete = True
                    break
        except RateLimited as exc:
            reset_wait = None
            if exc.reset is not None:
                reset_wait = exc.reset - int(self.clock.utc_now().timestamp())
            wait = max(exc.retry_after or reset_wait or 60, 1)
            result.error, result.retry_after = "RateLimited", wait
            self._save_failure(not_before=self.clock.utc_now() + timedelta(seconds=wait))
            return result
        except GitHubError as exc:
            result.error = type(exc).__name__
            self._save_failure(not_before=None)
            return result
        if mode == "full" and result.complete:
            self._missing_issues(ctx, result)
        result.checkpoint = self._save_success(
            state, mode, bool(result.complete), boundary, fetched_max, etags, ctx.bot_id
        )
        return result

    def _maybe_load_pulls(self, ctx: _Pass, issues: list[dict[str, Any]]) -> None:
        """사람 작업 확인용 open PR 목록. 새 Issue 후보가 있을 때만 트랜잭션 밖에서 한 번 읽는다."""
        if not ctx.decide or ctx.open_pulls is not None or ctx.activated_at is None:
            return
        if not any(
            item.get("state") == "open" and _parse(item["created_at"]) > ctx.activated_at
            for item in issues
        ):
            return
        response = self.port.list_pulls(state="open")
        ctx.open_pulls = [p for p in response.data or [] if isinstance(p, dict)]
        ctx.pulls_complete = not response.has_next

    def _run_auto_approve(self, ctx: _Pass) -> None:
        """자동 승인 정책(W25)은 페이지 트랜잭션이 끝난 뒤 부른다. 실패해도 polling은 이어간다.

        실패한 work는 `WAITING_APPROVAL`에 남고(운영자가 승인할 수 있다), 감사 기록에는
        예외 종류만 남긴다.
        """
        pending, ctx.auto_approve = ctx.auto_approve, []
        if self.auto_approve is None:
            return
        for work_id in pending:
            try:
                self.auto_approve(work_id)
            except Exception as exc:
                with self.store.tx() as tx:
                    payload = {"work_id": work_id, "error": type(exc).__name__}
                    self._audit(tx, None, "WORK_AUTO_APPROVE_FAILED", payload)

    def _save_failure(self, not_before: datetime | None) -> None:
        with self.store.tx() as tx:
            state = checkpoints.get(tx, INTEGRATION_ID, self.state_key) or {}
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            state["last_failure_at"] = tx.now
            if not_before is not None:
                state["not_before"] = to_rfc3339(not_before)
            checkpoints.put(tx, INTEGRATION_ID, self.state_key, state)

    def _save_success(
        self,
        previous: dict[str, Any],
        mode: str,
        complete: bool,
        boundary: str | None,
        fetched_max: str | None,
        etags: dict[str, Any],
        bot_id: int | None,
    ) -> str | None:
        with self.store.tx() as tx:
            state = checkpoints.get(tx, INTEGRATION_ID, self.state_key) or {}
            old = state.get("checkpoint")
            candidate = boundary if complete else (fetched_max if mode == "delta" else None)
            if mode == "initial_import":
                state["activated_at"] = boundary
                candidate = boundary
                state["mirror_complete"] = complete
            elif mode == "full":
                state["mirror_complete"] = complete
            elif not complete:
                state["mirror_complete"] = False
            if candidate and (old is None or _parse(candidate) > _parse(old)):
                state["checkpoint"] = candidate  # 앞으로만 간다
            if mode != "initial_import":
                state["polls"] = previous.get("polls", 0) + 1
            state.update(
                complete=complete,
                fetched_max=fetched_max,
                bot_id=bot_id,
                last_success_at=tx.now,
                consecutive_failures=0,
                etags=dict(list(etags.items())[-MAX_ETAGS:]),
            )
            state.pop("not_before", None)
            checkpoints.put(tx, INTEGRATION_ID, self.state_key, state)
            return state.get("checkpoint")

    # 항목 하나

    def same_repository(self, item: Mapping[str, Any]) -> bool:
        repo_url = item.get("repository_url")
        return repo_url is None or repo_url.endswith(f"/repos/{self.port.full_name}")

    def upsert_mirror(self, tx: Tx, item: dict[str, Any]) -> tuple[Any, Any, bool]:
        """Issue 한 건을 mirror에 넣는다. (이전 행, 지금 행, 바뀌었나)를 돌려준다(router도 쓴다).

        같은 `poll_event_key`면 쓰지 않고, 더 오래된 관찰이 늦게 오면 덮어쓰지 않는다.
        """
        repository_id, number = self.port.repository_id, item["number"]
        snapshot = snapshot_sha256(repository_id, item, self.permission_labels)
        existing = tx.one(
            "SELECT * FROM github_issues WHERE repository_id = ? AND issue_number = ?",
            (repository_id, number),
        )
        if existing is not None and (
            (existing["node_id"], existing["updated_at"], existing["snapshot_sha256"])
            == (item["node_id"], item["updated_at"], snapshot)
            or item["updated_at"] < existing["updated_at"]
        ):
            return existing, existing, False
        tx.execute(
            "INSERT INTO github_issues(repository_id, issue_number, node_id, state, author_id,"
            " created_at, updated_at, snapshot_sha256, payload_json, last_observed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(repository_id, issue_number) DO UPDATE SET node_id = excluded.node_id,"
            " state = excluded.state, author_id = excluded.author_id,"
            " updated_at = excluded.updated_at, snapshot_sha256 = excluded.snapshot_sha256,"
            " payload_json = excluded.payload_json, last_observed_at = excluded.last_observed_at",
            (
                repository_id,
                number,
                item["node_id"],
                item["state"],
                (item.get("user") or {}).get("id") or 0,
                item["created_at"],
                item["updated_at"],
                snapshot,
                canonical_dumps(item),
                tx.now,
            ),
        )
        current = tx.one(
            "SELECT * FROM github_issues WHERE repository_id = ? AND issue_number = ?",
            (repository_id, number),
        )
        return existing, current, True

    def _observe(self, tx: Tx, item: dict[str, Any], result: SyncResult, ctx: _Pass) -> None:
        if is_pull_request(item):
            result.skipped_pull_requests += 1
            return
        if not self.same_repository(item):
            result.skipped_other_repository += 1
            return
        result.seen += 1
        existing, issue, changed = self.upsert_mirror(tx, item)
        if not changed:
            return  # 같은 poll_event_key(overlap 중복) 또는 더 오래된 관찰
        result.mirrored += 1
        if ctx.activated_at is None:
            return  # initial import: 관찰만
        if existing is None:
            self._first_seen(tx, item, issue, result, ctx)
        else:
            self._changed(tx, item, existing, issue, result)

    def _works(self, tx: Tx, number: int) -> list[Any]:
        return tx.all(
            "SELECT * FROM work_items WHERE routing_scope = ? AND repository_id = ?"
            " AND issue_number = ? ORDER BY generation",
            (self.routing_scope, self.port.repository_id, number),
        )

    def _first_seen(
        self, tx: Tx, item: dict[str, Any], issue: Any, result: SyncResult, ctx: _Pass
    ) -> None:
        number = item["number"]
        assert ctx.activated_at is not None
        if item["state"] != "open" or _parse(item["created_at"]) <= ctx.activated_at:
            return  # 활성화 전 backlog·이미 닫힌 Issue: mirror만
        if self._works(
            tx, number
        ):  # 방어용: work는 mirror 행(FK)이 있어야 하므로 보통 도달하지 않는다
            self._audit(tx, None, "ISSUE_WORK_REUSED", {"issue_number": number})
            return
        author_id = (item.get("user") or {}).get("id")
        if author_id == ctx.bot_id:
            self._audit(tx, None, "ISSUE_BOT_WITHOUT_WORK", {"issue_number": number})
            return
        human = supervisor.check_human_work(item, ctx.open_pulls or [], ctx.bot_id)
        trusted = self.catalog.is_trusted_author(author_id)
        denied = bool(_labels(item) & self.permission_labels)
        if human:
            basis = "human_work_in_progress"
        elif trusted and not denied and ctx.pulls_complete:
            basis = "trusted_author"
        elif trusted and denied:
            basis = "deny_label"
        elif trusted:
            basis = "pull_lookup_incomplete"
        else:
            basis = "operator_approval_required"
        if not ctx.decide or not self.intake.enabled:
            result.planned.append({"issue_number": number, "basis": basis})
            return
        incident = self._new_incident(tx, item)
        authorization = {
            "policy": self.intake.auto_start.mode,
            "basis": basis,
            "author_id": author_id,
            "auto_start_eligible": basis == "trusted_author",
        }
        work, created = supervisor.ensure_work(tx, incident, issue, authorization=authorization)
        if not created:
            return
        self._audit(
            tx, incident["id"], "ISSUE_WORK_CREATED", {"work_id": work["id"], "basis": basis}
        )
        if human:
            self._block(
                tx,
                work,
                "HUMAN_WORK_IN_PROGRESS",
                f"GitHub Issue #{number}에 사람 담당자 또는 다른 사람의 PR이 있다",
                missing=["사람 작업과 LineMedic 작업 중 누가 맡을지 결정"],
            )
            result.blocked_works.append(work["id"])
            return
        result.new_works.append(work["id"])
        if basis == "trusted_author":
            ctx.auto_approve.append(work["id"])

    def _new_incident(self, tx: Tx, item: dict[str, Any]) -> Any:
        incident_id = new_id("INC")
        first_seen = to_rfc3339(_parse(item["created_at"]))
        tx.execute(
            "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
            " fingerprint_version, source_kind, status, service, line_id, count, first_seen,"
            " last_seen, details_json) VALUES (?, ?, ?, ?, ?, ?, 'GITHUB_ISSUE', 'NEW', ?, ?, 0,"
            " ?, ?, ?)",
            (
                incident_id,
                self.run_id,
                self.routing_scope,
                self.port.repository_id,
                f"issue:{self.port.repository_id}:{item['number']}",
                FINGERPRINT_VERSION,
                self.service,
                self.line_id,
                first_seen,
                first_seen,
                canonical_dumps(
                    {
                        "issue": {
                            "repository_id": self.port.repository_id,
                            "number": item["number"],
                            "node_id": item["node_id"],
                        },
                        "provisional_fingerprint": True,
                    }
                ),
            ),
        )
        return tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))

    def _changed(
        self, tx: Tx, item: dict[str, Any], existing: Any, issue: Any, result: SyncResult
    ) -> None:
        number = item["number"]
        before = json.loads(existing["payload_json"])
        was_denied = bool(_labels(before) & self.permission_labels)
        denied = bool(_labels(item) & self.permission_labels)
        if existing["state"] == "open" and item["state"] == "closed":
            self._stop(tx, number, "SOURCE_CHANGED", f"GitHub Issue #{number}이 닫혔다", result)
        elif existing["state"] == "closed" and item["state"] == "open":
            self._audit(tx, None, "ISSUE_REOPENED", {"issue_number": number})  # 자동 재실행 없음
        if denied and not was_denied and item["state"] == "open":
            reason = f"GitHub Issue #{number}에 자동 처리 제외 라벨이 붙었다"
            self._stop(tx, number, "PERMISSION_REQUIRED", reason, result)
        if existing["snapshot_sha256"] == issue["snapshot_sha256"]:
            return  # updated_at만 바뀜(우리 댓글 등): 새 attempt 없음
        for work in self._works(tx, number):
            if work["status"] in TERMINAL_WORK_STATUSES:
                continue
            payload = {"work_id": work["id"], "issue_number": number}
            self._audit(tx, work["incident_id"], "ISSUE_SCOPE_CHANGED", payload)
            result.scope_changed.append(work["id"])
            if self.on_scope_changed is not None:
                self.on_scope_changed(work["id"])

    def _missing_issues(self, ctx: _Pass, result: SyncResult) -> None:
        """완전한 전체 조회에서 사라진 open Issue(삭제·이전)는 closed와 같게 처리한다."""
        with self.store.tx() as tx:
            rows = tx.all(
                "SELECT issue_number FROM github_issues WHERE repository_id = ? AND state = 'open'",
                (self.port.repository_id,),
            )
            for row in rows:
                number = row["issue_number"]
                if number in ctx.seen_numbers:
                    continue
                self._audit(tx, None, "ISSUE_MISSING", {"issue_number": number})
                reason = f"GitHub Issue #{number}이 목록에서 사라졌다(삭제·이전 가능)"
                self._stop(tx, number, "SOURCE_CHANGED", reason, result)

    # 차단·취소 요청

    def _stop(self, tx: Tx, number: int, code: str, reason: str, result: SyncResult) -> None:
        for work in self._works(tx, number):
            if work["status"] in TERMINAL_WORK_STATUSES:
                continue
            incident = tx.one("SELECT status FROM incidents WHERE id = ?", (work["incident_id"],))
            if work["status"] == "WAITING_APPROVAL" and incident["status"] == "NEW":
                self._block(tx, work, code, reason, missing=["Issue를 다시 처리할지 운영자 결정"])
                result.blocked_works.append(work["id"])
            elif not work["cancel_requested"]:
                tx.execute(
                    "UPDATE work_items SET cancel_requested = 1, version = version + 1,"
                    " updated_at = ? WHERE id = ? AND cancel_requested = 0",
                    (tx.now, work["id"]),
                )
                payload = {"work_id": work["id"], "code": code}
                self._audit(tx, work["incident_id"], "WORK_CANCEL_REQUESTED", payload)
                result.cancel_requested.append(work["id"])

    def _block(self, tx: Tx, work: Any, code: str, reason: str, *, missing: list[str]) -> None:
        """시작 전 work를 router 정책으로 차단한다(incident NEW→ESCALATED, WORK_BLOCKED intent)."""
        work = tx.one("SELECT * FROM work_items WHERE id = ?", (work["id"],))
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (work["incident_id"],))
        transition = coupled_transition(
            tx,
            incident_id=incident["id"],
            expected_incident_version=incident["version"],
            incident_to="ESCALATED",
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to="BLOCKED",
            actor=Actor.ROUTER,
            reason=code,
            details={"issue_number": work["issue_number"]},
        )
        report = blocker_report(
            blocker_code=code,
            stage="intake",
            incident=incident,
            work=work,
            symptom_impact=reason,
            evidence_ids=[],
            owner_route_id=self.route_id,
            observed_at=tx.now,
            missing_requirements=missing,
            operator_next_step=["Issue와 담당자 상황을 확인한 뒤 작업 여부를 결정"],
            retry_condition=RETRY_AFTER_APPROVAL,
            reason_detail=reason,
        )
        assert transition.outbox_event == "WORK_BLOCKED"
        outbox.enqueue(tx, work, transition.outbox_event, report, self.route_id)

    def _audit(
        self, tx: Tx, incident_id: str | None, event: str, payload: Mapping[str, Any]
    ) -> None:
        audit.append(tx, self.run_id, incident_id, Actor.ISSUE_SYNC, event, payload)
