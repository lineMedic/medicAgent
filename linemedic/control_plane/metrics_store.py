"""설비 지표 보관 (W08). `query_equipment_metrics`가 읽고 감지기가 이상 규칙을 적용한다.

파일: `<runs_dir>/<run_id>/metrics/<equipment_id>.jsonl`(git 제외 경로).
시계열은 run·설비마다 한 파일이고 `write_series`는 그 run의 시계열을 통째로 바꾼다
(합성 시뮬레이션 전제).
시각은 `...ffffffZ` 고정 형식이다.
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.ids import is_valid_run_id

EQUIPMENT_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")


@dataclass(frozen=True)
class MetricSample:
    ts: str
    equipment_id: str
    brightness: float
    confidence: float
    baseline_brightness: float


class MetricsStore(Protocol):
    def write_series(self, run_id: str, equipment_id: str, samples: list[MetricSample]) -> None: ...

    def read(self, run_id: str, equipment_id: str, start: str, end: str) -> list[MetricSample]: ...


def _check(run_id: str, equipment_id: str) -> None:
    if not is_valid_run_id(run_id):
        raise ValueError(f"run_id 형식이 아니다: {run_id!r}")
    if not EQUIPMENT_RE.fullmatch(equipment_id):
        raise ValueError(f"equipment_id 형식이 아니다: {equipment_id!r}")


class MemoryMetricsStore:
    """테스트용 메모리 보관소."""

    def __init__(self) -> None:
        self.series: dict[tuple[str, str], list[MetricSample]] = {}

    def write_series(self, run_id: str, equipment_id: str, samples: list[MetricSample]) -> None:
        _check(run_id, equipment_id)
        self.series[(run_id, equipment_id)] = list(samples)

    def read(self, run_id: str, equipment_id: str, start: str, end: str) -> list[MetricSample]:
        _check(run_id, equipment_id)
        return [s for s in self.series.get((run_id, equipment_id), []) if start <= s.ts <= end]


class FileMetricsStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def path(self, run_id: str, equipment_id: str) -> Path:
        _check(run_id, equipment_id)
        return self.runs_dir / run_id / "metrics" / f"{equipment_id}.jsonl"

    def write_series(self, run_id: str, equipment_id: str, samples: list[MetricSample]) -> None:
        path = self.path(run_id, equipment_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(canonical_dumps(asdict(sample)) + "\n" for sample in samples)
        path.write_text(text, encoding="utf-8")

    def read(self, run_id: str, equipment_id: str, start: str, end: str) -> list[MetricSample]:
        path = self.path(run_id, equipment_id)
        if not path.exists():
            return []
        samples = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                sample = MetricSample(**json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue
            if start <= sample.ts <= end:
                samples.append(sample)
        return samples
