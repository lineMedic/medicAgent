"""MES 로그 보관 (W07, D72). `search_logs`가 사건 시각 ±30분의 정제본을 읽는다.

- 감지기가 관찰한 줄을 정제(`redaction.clean_text`)한 뒤 run·service별 JSON Lines로 남긴다.
  파일: `<runs_dir>/<run_id>/logs/<service>.jsonl`(git 제외 경로)
- run·service 파일 하나의 상한(32 MiB)을 넘으면 더 쓰지 않고 버린 줄 수만 센다.
- 시각은 `...ffffffZ` 고정 형식이라 문자열 비교로 구간을 거른다.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.ids import is_valid_run_id

MAX_FILE_BYTES = 32 * 1024 * 1024
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class LogRecord:
    observed_at: str
    source: str
    line: str


class LogStore(Protocol):
    def append(self, run_id: str, service: str, record: LogRecord) -> bool: ...

    def read(self, run_id: str, service: str, start: str, end: str) -> list[LogRecord]: ...


def _check(run_id: str, service: str) -> None:
    if not is_valid_run_id(run_id):
        raise ValueError(f"run_id 형식이 아니다: {run_id!r}")
    if not SERVICE_RE.fullmatch(service):
        raise ValueError(f"service 형식이 아니다: {service!r}")


class MemoryLogStore:
    """테스트용 메모리 보관소."""

    def __init__(self, max_records: int = 100_000) -> None:
        self.records: dict[tuple[str, str], list[LogRecord]] = {}
        self.max_records = max_records
        self.dropped = 0

    def append(self, run_id: str, service: str, record: LogRecord) -> bool:
        _check(run_id, service)
        bucket = self.records.setdefault((run_id, service), [])
        if len(bucket) >= self.max_records:
            self.dropped += 1
            return False
        bucket.append(record)
        return True

    def read(self, run_id: str, service: str, start: str, end: str) -> list[LogRecord]:
        _check(run_id, service)
        return [r for r in self.records.get((run_id, service), []) if start <= r.observed_at <= end]


class FileLogStore:
    def __init__(self, runs_dir: Path, max_bytes: int = MAX_FILE_BYTES) -> None:
        self.runs_dir = Path(runs_dir)
        self.max_bytes = max_bytes
        self.dropped = 0

    def path(self, run_id: str, service: str) -> Path:
        _check(run_id, service)
        return self.runs_dir / run_id / "logs" / f"{service}.jsonl"

    def append(self, run_id: str, service: str, record: LogRecord) -> bool:
        path = self.path(run_id, service)
        data = (canonical_dumps(asdict(record)) + "\n").encode("utf-8")
        size = path.stat().st_size if path.exists() else 0
        if size + len(data) > self.max_bytes:
            self.dropped += 1
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data)
        return True

    def _records(self, path: Path) -> Iterator[LogRecord]:
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    item = json.loads(raw)
                    yield LogRecord(item["observed_at"], item["source"], item["line"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue  # 깨진 줄은 건너뛴다(부분 쓰기 등)

    def read(self, run_id: str, service: str, start: str, end: str) -> list[LogRecord]:
        path = self.path(run_id, service)
        if not path.exists():
            return []
        return [r for r in self._records(path) if start <= r.observed_at <= end]
