from datetime import UTC, datetime
from pathlib import Path

import pytest

from linemedic.common.clock import FakeClock
from linemedic.control_plane.store import Store


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start=datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC))


@pytest.fixture
def tmp_runs_dir(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    return runs


@pytest.fixture
def store(tmp_path: Path, fake_clock: FakeClock) -> Store:
    """migration을 적용한 빈 제어 DB (파일, WAL)."""
    db = Store(tmp_path / "control.db", fake_clock, backoff_seconds=0, sleep=lambda s: None)
    db.migrate()
    return db


@pytest.fixture
def conn(store: Store):
    """테스트 준비용 autocommit 연결. 제품 전이는 store.tx()로만 한다."""
    connection = store.connect()
    yield connection
    connection.close()
