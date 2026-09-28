"""평가 기대값 로더 (W20, D60·D92).

크기 상한 → `tomllib` → pydantic strict(`extra="forbid"`) 순으로 읽고 모르는 키를 거부한다.
기대값은 에이전트에게 주지 않는다(채점에만 쓴다).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from linemedic.common.config import ConfigError, read_toml, validate_model
from linemedic.control_plane.codes import Category

EXPECTATIONS_PATH = Path(__file__).with_name("scenario_expectations.toml")


class ExpectationError(ValueError):
    """기대값 파일을 쓸 수 없음."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Expectation(_Model):
    group: str = Field(min_length=1, max_length=80)
    target_runs: PositiveInt
    agent_denominator: bool
    category: Category | None = None
    action: Literal["create_pr", "create_work_order_draft", "escalate"] | None = None
    incident_status: str | None = None
    work_status: str | None = None
    verification_verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"] | None = None
    memory_mode: Literal["cold_start", "memory_assisted"] | None = None


class _File(_Model):
    schema_version: Literal["linemedic.v4"]
    suites: dict[str, Expectation]


def load_expectations(path: Path = EXPECTATIONS_PATH) -> dict[str, Expectation]:
    try:
        data = read_toml(path)
        return dict(validate_model(_File, data, "scenario expectations").suites)
    except ConfigError as exc:
        raise ExpectationError(str(exc)) from None
