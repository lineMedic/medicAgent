from datetime import UTC, datetime
from pathlib import Path

import pytest

from linemedic.common.clock import FakeClock


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start=datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC))


@pytest.fixture
def tmp_runs_dir(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    return runs
