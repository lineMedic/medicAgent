"""주입 가능한 시계 (D51).

시간 판정 로직은 이 모듈의 Clock을 주입받아 쓴다. 저장 형식은 `YYYY-MM-DDTHH:MM:SS.ffffffZ`다.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

_DEFAULT_FAKE_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class Clock(Protocol):
    def utc_now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """실제 시스템 시계."""

    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    """테스트용 시계. `advance()`나 `sleep()`으로만 시간이 흐른다."""

    def __init__(self, start: datetime | None = None, monotonic_start: float = 0.0) -> None:
        start = start or _DEFAULT_FAKE_START
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start.astimezone(UTC)
        self._monotonic = monotonic_start

    def utc_now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("FakeClock cannot move backwards")
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


def to_rfc3339(value: datetime) -> str:
    """timezone-aware datetime을 UTC 마이크로초 `...Z` 문자열로 바꾼다."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime is not allowed; use a timezone-aware UTC datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


RFC3339_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def from_rfc3339(value: str) -> datetime:
    """`to_rfc3339` 형식(`...ffffffZ`)만 받아 UTC datetime으로 바꾼다. 다른 형식은 ValueError."""
    return datetime.strptime(value, RFC3339_FORMAT).replace(tzinfo=UTC)
