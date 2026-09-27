"""SQLite 저장소 (W06, D45, spec 04 §7).

- `connect(path)`: foreign_keys ON, WAL, busy_timeout, autocommit(`isolation_level=None`)
- `migrate(conn)`: `migrations/NNNN_*.sql`을 번호 순서로 한 번씩 적용하고 `schema_migrations`에 기록
- `Store.tx()`: `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`.
  시작 때 SQLITE_BUSY면 제한 횟수만 다시 시도하고, 그래도 잠겨 있으면 `StoreBusy`로 멈춘다
- `cas_update(tx, ...)`: `UPDATE ... WHERE id=? AND version=? AND status=?`, 0행이면 `StateConflict`

트랜잭션 객체(`Tx`)는 SQL 실행과 트랜잭션 시각만 가진다. 네트워크 client를 주지 않아
DB 트랜잭션 안에서 모델·GitHub·Docker 응답을 기다리지 않게 한다.
"""

import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock, SystemClock, to_rfc3339

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
BUSY_TIMEOUT_MS = 5000
BUSY_RETRIES = 3
BUSY_BACKOFF_SECONDS = 0.1

# CAS로 상태를 바꿀 수 있는 테이블과, 상태·version 외에 같이 바꿀 수 있는 열.
# 식별 열(id·run_id·routing_scope·repository_id·issue_number·generation 등)은 바꾸지 않는다.
CAS_COLUMNS: dict[str, frozenset[str]] = {
    "incidents": frozenset(
        {
            "category",
            "count",
            "attempt_id",
            "attempt_deadline",
            "submissions",
            "reason_code",
            "last_seen",
            "details_json",
        }
    ),
    "work_items": frozenset(
        {
            "issue_snapshot_sha256",
            "authorization_json",
            "start_notification_id",
            "attempt_id",
            "cancel_requested",
            "reason_code",
            "updated_at",
            "details_json",
        }
    ),
}


class StoreError(RuntimeError):
    """저장소를 쓸 수 없음."""


class StoreBusy(StoreError):
    """SQLITE_BUSY가 제한 횟수 안에 풀리지 않았다. 호출자는 외부 조치 없이 멈춘다."""


class MigrationError(StoreError):
    """migration 파일이나 적용 기록이 올바르지 않음."""


class StateConflict(StoreError):
    """CAS 조건(기대 version·상태)이 현재 행과 다르다. API에서는 409 `STATE_CONFLICT`."""

    def __init__(
        self,
        table: str,
        row_id: str,
        expected_version: int | None,
        expected_status: str | None,
        current_status: str | None,
        current_version: int | None,
    ) -> None:
        super().__init__(f"{table} {row_id}: 기대한 version·상태와 현재 행이 다르다")
        self.table = table
        self.row_id = row_id
        self.expected_version = expected_version
        self.expected_status = expected_status
        self.current_status = current_status
        self.current_version = current_version


class Tx:
    """한 트랜잭션. SQL 실행과 트랜잭션 시각(`now`, UTC RFC3339)만 제공한다."""

    __slots__ = ("_conn", "now")

    def __init__(self, conn: sqlite3.Connection, now: str) -> None:
        self._conn = conn
        self.now = now

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()


def connect(path: Path | str, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=busy_timeout_ms / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise StoreError("SQLite foreign key 검사를 켤 수 없다")
    return conn


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "locked" in str(exc) or "busy" in str(exc)


# ── migration ─────────────────────────────────────────────────


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    files = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME_RE.fullmatch(path.name)
        if not match:
            raise MigrationError(f"migration 파일 이름 형식이 아니다: {path.name}")
        files.append((int(match.group(1)), path))
    versions = [version for version, _ in files]
    if len(set(versions)) != len(versions):
        raise MigrationError("같은 번호의 migration 파일이 둘 이상 있다")
    return files


def split_statements(sql: str) -> list[str]:
    """SQL 파일을 문장 단위로 나눈다. 주석·문자열 안의 `;`는 문장 끝으로 보지 않는다."""
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    leftover = [
        line for line in buffer.splitlines() if line.strip() and not line.lstrip().startswith("--")
    ]
    if leftover:
        raise MigrationError("끝나지 않은 SQL 문이 있다")
    return statements


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(
    conn: sqlite3.Connection, clock: Clock | None = None, directory: Path = MIGRATIONS_DIR
) -> list[int]:
    """적용하지 않은 migration을 번호 순서로 파일마다 한 트랜잭션으로 적용한다."""
    clock = clock or SystemClock()
    files = migration_files(directory)
    applied = applied_versions(conn)
    unknown = applied - {version for version, _ in files}
    if unknown:
        raise MigrationError(f"코드에 없는 migration이 DB에 적용되어 있다: {sorted(unknown)}")
    done = []
    for version, path in files:
        if version in applied:
            continue
        statements = split_statements(path.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        if version in applied_versions(conn):  # 다른 프로세스가 먼저 적용했다
            conn.execute("ROLLBACK")
            continue
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, to_rfc3339(clock.utc_now())),
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        done.append(version)
    return done


# ── 트랜잭션 ─────────────────────────────────────────────────


class Store:
    def __init__(
        self,
        path: Path | str,
        clock: Clock | None = None,
        *,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS,
        busy_retries: int = BUSY_RETRIES,
        backoff_seconds: float = BUSY_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = Path(path)
        self.clock = clock or SystemClock()
        self.busy_timeout_ms = busy_timeout_ms
        self.busy_retries = busy_retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def connect(self) -> sqlite3.Connection:
        return connect(self.path, self.busy_timeout_ms)

    def migrate(self) -> list[int]:
        conn = self.connect()
        try:
            return migrate(conn, self.clock)
        finally:
            conn.close()

    def _begin(self, conn: sqlite3.Connection) -> None:
        for attempt in range(self.busy_retries + 1):
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                if not _is_busy(exc):
                    raise
                if attempt == self.busy_retries:
                    raise StoreBusy(
                        f"SQLite 쓰기 잠금을 {self.busy_retries + 1}회 시도에도 얻지 못했다"
                    ) from None
                self.sleep(self.backoff_seconds * (attempt + 1))

    @contextmanager
    def tx(self) -> Iterator[Tx]:
        """쓰기 트랜잭션. 블록이 예외로 끝나면 ROLLBACK, 정상 종료면 COMMIT한다."""
        conn = self.connect()
        try:
            self._begin(conn)
            try:
                yield Tx(conn, to_rfc3339(self.clock.utc_now()))
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            try:
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                conn.execute("ROLLBACK")
                if _is_busy(exc):
                    raise StoreBusy("COMMIT 중 SQLite 잠금이 풀리지 않았다") from None
                raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[Tx]:
        """읽기 전용 트랜잭션(`query_only`). 한 시점의 일관된 값을 읽는다."""
        conn = self.connect()
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            try:
                yield Tx(conn, to_rfc3339(self.clock.utc_now()))
            finally:
                conn.execute("ROLLBACK")
        finally:
            conn.close()


def cas_update(
    tx: Tx,
    table: str,
    row_id: str,
    expected_version: int,
    expected_status: str,
    new_status: str,
    **fields: Any,
) -> int:
    """상태를 CAS로 바꾸고 version을 1 올린다. 새 version을 돌려준다."""
    allowed = CAS_COLUMNS.get(table)
    if allowed is None:
        raise ValueError(f"CAS 대상 테이블이 아니다: {table}")
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"CAS로 바꿀 수 없는 열: {sorted(unknown)}")
    assignments = ["status = ?", "version = version + 1", *(f"{column} = ?" for column in fields)]
    cursor = tx.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ? AND version = ? AND status = ?",
        [new_status, *fields.values(), row_id, expected_version, expected_status],
    )
    if cursor.rowcount != 1:
        current = tx.one(f"SELECT status, version FROM {table} WHERE id = ?", (row_id,))
        raise StateConflict(
            table,
            row_id,
            expected_version,
            expected_status,
            current["status"] if current else None,
            current["version"] if current else None,
        )
    return expected_version + 1
