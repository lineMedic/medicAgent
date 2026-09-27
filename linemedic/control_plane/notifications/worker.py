"""알림 outbox worker (W26, spec 16 §3·§4·§5, docs/05 ④·⑦).

`outbox.enqueue`가 상태 전이와 같은 트랜잭션에 남긴 PENDING 알림을 발송한다.

- `PENDING → SENDING`을 먼저 커밋하고(렌더한 본문 hash도 저장), 트랜잭션 밖에서 adapter를 부른 뒤
  별도 트랜잭션에 결과를 기록한다. route별로 한 번에 하나만 보낸다.
- 결과: `ACCEPTED`(receipt 저장), 보내지 않았음이 확실한 거절은 최대 `retry_max_attempts`회까지
  점증 backoff(Retry-After 우선)로 PENDING에 되돌린다. 그 밖의 거절은 `FAILED`,
  응답이 끊기면 `UNKNOWN`.
  UNKNOWN은 다시 보내지 않고 `reconcile`로 조회한다.
- 재시작 때 `SENDING`은 `UNKNOWN`으로 바꾼다(`recover_sending`).
- 시작 알림(`WORK_STARTING`)이 필수 route에서 ACCEPTED가 되면 같은 트랜잭션에서
  work를 READY로 옮긴다(시작 게이트).
  이미 BLOCKED·CANCELLED인 work는 늦게 온 receipt로 되살리지 않는다.
- 대상은 route catalog와 DB의 work가 정한다. payload의 수신자·URL은 쓰지 않는다. bound Issue가 없는
  알림(Issue 연결 전 차단)은 보낼 곳이 없어 `FAILED(no_bound_issue)`로 미전송을 남긴다.
- shadow 모드(adapter가 보낼 수 없음)에서는 가져가지 않고 PENDING으로 둔다.
- 알림 성공·실패는 incident 상태를 바꾸지 않는다(시작 게이트의 work 전이만 예외).
"""

import json
import threading
from collections import defaultdict
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock, from_rfc3339, to_rfc3339
from linemedic.common.config import LineMedicConfig, NotificationRoute
from linemedic.control_plane import audit, supervisor
from linemedic.control_plane.issue_sync import _gh, _parse
from linemedic.control_plane.notifications.github_comment import (
    Accepted,
    ReconcileResult,
    Rejected,
    UnknownResult,
)
from linemedic.control_plane.notifications.templates import Rendered, notify_marker, render
from linemedic.control_plane.state import Actor
from linemedic.control_plane.store import Store, Tx

BACKOFF_SECONDS = 30
RECONCILE_LOOKBACK = timedelta(minutes=5)


class Adapter(Protocol):
    name: str

    def not_ready(self) -> str | None: ...

    def send(self, issue_number: int, body: str) -> Accepted | Rejected | UnknownResult: ...

    def reconcile(
        self, issue_number: int, *, marker: str, body_sha256: str, since: str | None
    ) -> ReconcileResult: ...


def _receipt_time(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    try:
        return to_rfc3339(_parse(value))
    except ValueError:
        return fallback


class OutboxWorker:
    def __init__(
        self,
        store: Store,
        *,
        adapters: Mapping[str, Adapter],
        config: LineMedicConfig,
        repo: str,
        clock: Clock,
        backoff_seconds: int = BACKOFF_SECONDS,
    ) -> None:
        self.store = store
        self.adapters = dict(adapters)
        self.routes: Mapping[str, NotificationRoute] = dict(config.notifications.routes)
        self.required_route = config.notifications.required_start_route_id
        self.max_attempts = config.notifications.retry_max_attempts
        self.repo = repo
        self.clock = clock
        self.backoff_seconds = backoff_seconds
        self._route_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    # 공개

    def recover_sending(self) -> int:
        """재시작 때: 보냈는지 모르는 SENDING을 UNKNOWN으로 둔다(다시 보내지 않고 조회한다)."""
        with self.store.tx() as tx:
            return tx.execute(
                "UPDATE notifications SET status = 'UNKNOWN', updated_at = ?"
                " WHERE status = 'SENDING'",
                (tx.now,),
            ).rowcount

    def process_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        done = []
        for _ in range(limit):
            claimed = self._claim()
            if claimed is None:
                break
            done.append(self._deliver(claimed))
        return done

    def reconcile(self, notification_id: str) -> dict[str, Any]:
        """UNKNOWN 알림을 외부 조회로만 조정한다. FOUND면 ACCEPTED, 아니면 기록만 한다."""
        with self.store.read() as tx:
            row = tx.one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
            issue_number = self._issue_number(tx, row) if row is not None else None
        if row is None:
            raise LookupError(notification_id)
        if row["status"] != "UNKNOWN":
            return {
                "notification_id": notification_id,
                "outcome": "NOT_UNKNOWN",
                "status": row["status"],
            }
        route = self.routes.get(row["route_id"])
        adapter = self.adapters.get(route.adapter) if route is not None else None
        stored = json.loads(row["result_json"] or "{}")
        if adapter is None or issue_number is None or not stored.get("rendered_sha256"):
            result = ReconcileResult("INCONCLUSIVE", detail="missing_target_or_rendered_hash")
        else:
            since = _gh(from_rfc3339(row["created_at"]) - RECONCILE_LOOKBACK)
            result = adapter.reconcile(
                issue_number,
                marker=notify_marker(row["id"], row["payload_sha256"]),
                body_sha256=stored["rendered_sha256"],
                since=since,
            )
        with self.store.tx() as tx:
            current = tx.one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
            if result.outcome == "FOUND" and current["status"] == "UNKNOWN":
                assert result.receipt is not None
                gate = self._accept(tx, current, result.receipt)
                return {"notification_id": notification_id, "outcome": "FOUND", "start_gate": gate}
            record = {**json.loads(current["result_json"] or "{}")}
            record["reconcile"] = {
                "outcome": result.outcome,
                "detail": result.detail,
                "checked_at": tx.now,
            }
            tx.execute(
                "UPDATE notifications SET result_json = ?, updated_at = ? WHERE id = ?",
                (canonical_dumps(record), tx.now, notification_id),
            )
        return {"notification_id": notification_id, "outcome": result.outcome}

    # 가져가기·보내기·기록

    def _issue_number(self, tx: Tx, row: Any) -> int | None:
        if row["work_id"] is None:
            return None
        work = tx.one("SELECT issue_number FROM work_items WHERE id = ?", (row["work_id"],))
        return work["issue_number"] if work is not None else None

    def _claim(self) -> dict[str, Any] | None:
        with self.store.tx() as tx:
            rows = tx.all(
                "SELECT * FROM notifications WHERE status = 'PENDING'"
                " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
                " ORDER BY created_at, rowid LIMIT 50",
                (tx.now,),
            )
            for row in rows:
                route = self.routes.get(row["route_id"])
                adapter = self.adapters.get(route.adapter) if route is not None else None
                if route is None or not route.enabled or adapter is None:
                    self._finish(tx, row, "FAILED", {"error": "route_unavailable"})
                    continue
                if adapter.not_ready() is not None:
                    continue  # shadow: 보내지 않고 PENDING으로 둔다
                issue_number = self._issue_number(tx, row)
                if issue_number is None:
                    self._finish(tx, row, "FAILED", {"error": "no_bound_issue"})  # 미전송 표시
                    continue
                rendered = render(
                    row["event_type"],
                    json.loads(row["payload_json"]),
                    repo=self.repo,
                    issue_number=issue_number,
                    notification_id=row["id"],
                    payload_sha256=row["payload_sha256"],
                )
                record = {
                    **json.loads(row["result_json"] or "{}"),
                    "rendered_sha256": rendered.body_sha256,
                    "issue_number": issue_number,
                }
                tx.execute(
                    "UPDATE notifications SET status = 'SENDING',"
                    " attempt_count = attempt_count + 1, updated_at = ?, result_json = ?"
                    " WHERE id = ? AND status = 'PENDING'",
                    (tx.now, canonical_dumps(record), row["id"]),
                )
                return {
                    "id": row["id"],
                    "route_id": row["route_id"],
                    "adapter": adapter,
                    "rendered": rendered,
                    "issue_number": issue_number,
                }
        return None

    def _deliver(self, claimed: dict[str, Any]) -> dict[str, Any]:
        adapter: Adapter = claimed["adapter"]
        rendered: Rendered = claimed["rendered"]
        with self._route_locks[claimed["route_id"]]:  # route별 직렬화
            result = adapter.send(claimed["issue_number"], rendered.body)
        with self.store.tx() as tx:
            row = tx.one("SELECT * FROM notifications WHERE id = ?", (claimed["id"],))
            summary: dict[str, Any] = {
                "notification_id": row["id"],
                "event_type": row["event_type"],
            }
            if isinstance(result, Accepted):
                summary.update(status="ACCEPTED", start_gate=self._accept(tx, row, result))
            elif isinstance(result, Rejected):
                retry = result.safe_to_retry and row["attempt_count"] < self.max_attempts
                error = {"error": result.reason, "retry_after": result.retry_after}
                if retry:
                    delay = max(
                        result.retry_after or 0, self.backoff_seconds * row["attempt_count"]
                    )
                    next_at = to_rfc3339(self.clock.utc_now() + timedelta(seconds=delay))
                    self._finish(tx, row, "PENDING", error, next_attempt_at=next_at)
                    summary.update(status="PENDING", next_attempt_at=next_at)
                else:
                    self._finish(tx, row, "FAILED", error)
                    summary.update(status="FAILED", error=result.reason)
            else:
                self._finish(tx, row, "UNKNOWN", {"observation": result.observation})
                summary.update(status="UNKNOWN")
        return summary

    def _finish(
        self,
        tx: Tx,
        row: Any,
        status: str,
        detail: Mapping[str, Any],
        *,
        next_attempt_at: str | None = None,
    ) -> None:
        record = {**json.loads(row["result_json"] or "{}"), "last": dict(detail)}
        tx.execute(
            "UPDATE notifications SET status = ?, next_attempt_at = ?, updated_at = ?,"
            " result_json = ? WHERE id = ?",
            (status, next_attempt_at, tx.now, canonical_dumps(record), row["id"]),
        )
        if status == "FAILED":
            audit.append(
                tx,
                row["run_id"],
                row["incident_id"],
                Actor.NOTIFIER,
                "NOTIFICATION_FAILED",
                {"notification_id": row["id"], "event_type": row["event_type"], **detail},
            )

    def _accept(self, tx: Tx, row: Any, receipt: Accepted) -> str | None:
        """receipt를 저장한다. 필수 route의 시작 알림이면 시작 게이트를 연다.

        `accepted_at`은 provider가 준 접수 시각이고, `recorded_at`은 우리가 receipt를 저장한
        시각이다. 순서 기록(receipt ≤ attempt 시작)은 두 시계의 차이에 흔들리지 않게
        `recorded_at`으로 한다.
        """
        accepted_at = _receipt_time(receipt.accepted_at, tx.now)
        record = {
            **json.loads(row["result_json"] or "{}"),
            "canonical_ref": receipt.canonical_ref,
            "recorded_at": tx.now,
        }
        tx.execute(
            "UPDATE notifications SET status = 'ACCEPTED', receipt_id = ?, accepted_at = ?,"
            " next_attempt_at = NULL, updated_at = ?, result_json = ? WHERE id = ?",
            (receipt.receipt_id, accepted_at, tx.now, canonical_dumps(record), row["id"]),
        )
        audit.append(
            tx,
            row["run_id"],
            row["incident_id"],
            Actor.NOTIFIER,
            "NOTIFICATION_ACCEPTED",
            {
                "notification_id": row["id"],
                "event_type": row["event_type"],
                "receipt_id": receipt.receipt_id,
            },
        )
        if row["event_type"] == "WORK_STARTING" and row["route_id"] == self.required_route:
            current = tx.one("SELECT * FROM notifications WHERE id = ?", (row["id"],))
            return supervisor.on_start_notice_accepted(tx, current)
        return None
