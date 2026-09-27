"""설정 로더 (D52·D60).

처리 순서: TOML 바이트 크기 상한 → stdlib `tomllib` → 날짜·시각 타입 거부 → env 병합
→ pydantic v2 `strict`·`extra="forbid"` 검증.

- 비밀 env는 `Secrets`에 따로 두고 repr에 값을 노출하지 않는다.
- host·run 식별 env(`DEMO_HOST_ID`, `RUNS_DIR` 등)는 `RuntimeEnv`에 두고
  run manifest에 따로 기록한다.
- `config_hash`는 비밀을 제외한 병합 설정(파일 + 설정 키에 대응하는 env)의
  canonical JSON SHA-256이다.
"""

import copy
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from linemedic.common.canonical_json import sha256_hex

MAX_CONFIG_BYTES = 64 * 1024
DEFAULT_CONFIG_PATH = Path("config/linemedic.toml")
DEFAULT_ENV_FILE = Path(".env")

SECRET_ENV_NAMES: tuple[str, ...] = (
    "NVIDIA_API_KEY",
    "GITHUB_BROKER_CREDENTIAL",
    "GITHUB_SETUP_CREDENTIAL",
    "SMTP_CREDENTIAL",
    "LINEMEDIC_OPS_RECIPIENT",
    "CONTROL_AGENT_TOKEN",
    "CONTROL_OPERATOR_TOKEN",
)

# host·run 식별 값: config hash에 넣지 않고 run manifest에 따로 기록한다.
RUNTIME_ENV_FIELDS: dict[str, str] = {
    "LINE_MEDIC_ENV": "line_medic_env",
    "DEMO_HOST_ID": "demo_host_id",
    "BASELINE_COMMIT": "baseline_commit",
    "RUNNER_IMAGE_ID": "runner_image_id",
    "MES_BASE_IMAGE_ID": "mes_base_image_id",
    "RUNS_DIR": "runs_dir",
}

ROUTING_SCOPE_PATTERN = r"^(live|eval:r-\d{8}-\d{6}-[0-9a-f]{4})$"


class ConfigError(ValueError):
    """설정 파일이나 env 값이 규칙을 어김."""


# ── 설정 모델 ──────────────────────────────────────────────────

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
RepoFullName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ServiceConfig(_Model):
    # 업무 계약이 없는 서비스(설비 지표만 있는 vision-inspection)는 contract_id를 생략한다.
    contract_id: str | None = None
    log_source: Literal["stdout_jsonl", "equipment_metrics"]
    line_id: Annotated[str, Field(min_length=1, max_length=32)]
    # 코드 수정 대상 경로. 빈 목록이면 코드 경로가 없는 서비스다(D59). 생략하면 미정.
    code_paths: list[str] | None = None


EquipmentId = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{0,31}$")]


class EquipmentConfig(_Model):
    service: str
    line_id: Annotated[str, Field(min_length=1, max_length=32)]
    kind: Literal["camera"]
    metrics: list[Literal["brightness", "confidence"]]
    manual_ref_ids: list[str]


class RepositoryConfig(_Model):
    id: PositiveInt | None = None
    full_name: RepoFullName | None = None


class AutoStartConfig(_Model):
    mode: Literal["trusted_authors"]
    deny_labels: list[str]
    author_ids: list[PositiveInt] | None = None


class IssueIntakeConfig(_Model):
    enabled: bool
    service_id: str
    routing_scope: Annotated[str, Field(pattern=ROUTING_SCOPE_PATTERN)]
    transport: Literal["polling"]
    poll_interval_seconds: PositiveInt
    overlap_seconds: NonNegativeInt
    initial_import: Literal["observe_only"]
    per_page: PositiveInt
    max_pages: PositiveInt
    closed_issue_policy: Literal["require_operator"]
    start_notification_route_id: str
    auto_start: AutoStartConfig


class NotificationRoute(_Model):
    adapter: Literal["github_comment", "smtp"]
    enabled: bool
    target: Literal["bound_issue"] | None = None
    recipient_config_key: str | None = None


class NotificationsConfig(_Model):
    required_start_route_id: str
    start_wait_seconds: PositiveInt
    retry_max_attempts: PositiveInt
    routes: dict[str, NotificationRoute]


class AgentConfig(_Model):
    max_concurrent: PositiveInt
    deadline_seconds: PositiveInt
    tool_call_budget: PositiveInt
    proposal_revisions: NonNegativeInt
    max_submissions: PositiveInt
    model_transient_retries: NonNegativeInt
    mode: Literal["local", "sandbox"] | None = None
    runtime: str | None = None
    model_id: str | None = None
    model_base_url: str | None = None


class LogsToolConfig(_Model):
    window_minutes: PositiveInt
    max_lines: PositiveInt
    max_bytes: PositiveInt


class MetricsToolConfig(_Model):
    max_minutes: PositiveInt
    max_samples: PositiveInt


class DeploysToolConfig(_Model):
    window_hours: PositiveInt


class ToolsConfig(_Model):
    http_timeout_seconds: PositiveInt
    logs: LogsToolConfig
    metrics: MetricsToolConfig
    deploys: DeploysToolConfig


class ProposalConfig(_Model):
    max_bytes: PositiveInt
    summary_max_chars: PositiveInt
    max_evidence_ids: PositiveInt


class PatchConfig(_Model):
    allowed_app_file: str
    allowed_new_test_glob: str
    max_files: PositiveInt
    max_changed_lines: PositiveInt
    protected_globs: list[str]


class RunnerConfig(_Model):
    network: Literal["none"]
    run_as_non_root: bool
    drop_all_capabilities: bool
    read_only_root: bool
    cpus: PositiveInt
    memory_mib: PositiveInt
    pids: PositiveInt
    stage_timeout_seconds: PositiveInt
    max_log_bytes: PositiveInt


class VerifierConfig(_Model):
    contract_id: str
    sample_offsets_seconds: list[NonNegativeInt]
    observation_end_seconds: PositiveInt


class MemoryConfig(_Model):
    mode: Literal["cold_start", "memory_assisted"]
    search_engine: Literal["sqlite_fts5", "keyword_fallback"]
    top_k: PositiveInt
    snippet_max_chars: PositiveInt
    query_max_tokens: PositiveInt
    snapshot_path: str | None = None


class DetectorConfig(_Model):
    dedupe_window_seconds: PositiveInt
    dedupe_min_occurrences: PositiveInt
    # 설비 지표 이상 규칙(W08): baseline 대비 밝기 하락 비율 이상 또는 신뢰도 미만이 연속 N sample
    metric_brightness_drop_ratio: Annotated[float, Field(gt=0, lt=1)]
    metric_confidence_min: Annotated[float, Field(gt=0, lt=1)]
    metric_consecutive_samples: PositiveInt


class LineMedicConfig(_Model):
    schema_version: Literal["linemedic.v4"]
    services: dict[str, ServiceConfig]
    repository: RepositoryConfig
    issue_intake: IssueIntakeConfig
    notifications: NotificationsConfig
    agent: AgentConfig
    tools: ToolsConfig
    proposal: ProposalConfig
    patch: PatchConfig
    runner: RunnerConfig
    verifier: VerifierConfig
    memory: MemoryConfig
    detector: DetectorConfig
    equipment: dict[EquipmentId, EquipmentConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_cross_references(self) -> "LineMedicConfig":
        for equipment_id, item in self.equipment.items():
            service = self.services.get(item.service)
            if service is None:
                raise ValueError(f"equipment {equipment_id!r}: unknown service {item.service!r}")
            if service.line_id != item.line_id:
                raise ValueError(f"equipment {equipment_id!r}: line differs from its service")
        route_id = self.notifications.required_start_route_id
        route = self.notifications.routes.get(route_id)
        if route is None or not route.enabled:
            raise ValueError(f"required start route {route_id!r} must exist and be enabled")
        if self.issue_intake.start_notification_route_id != route_id:
            raise ValueError("issue_intake.start_notification_route_id must match the start route")
        if self.issue_intake.service_id not in self.services:
            raise ValueError(f"issue_intake.service_id {self.issue_intake.service_id!r} is unknown")
        return self


class RuntimeEnv(_Model):
    line_medic_env: str | None = None
    demo_host_id: str | None = None
    baseline_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None
    runner_image_id: str | None = None
    mes_base_image_id: str | None = None
    runs_dir: str | None = None


class Secrets:
    """비밀 env 값. repr·str에는 설정된 이름만 보이고 값은 보이지 않는다."""

    __slots__ = ("_values",)

    def __init__(self, env: Mapping[str, str]) -> None:
        self._values = {name: env[name] for name in SECRET_ENV_NAMES if env.get(name)}

    def get(self, name: str) -> str | None:
        if name not in SECRET_ENV_NAMES:
            raise KeyError(f"{name} is not a registered secret")
        return self._values.get(name)

    def is_set(self, name: str) -> bool:
        return self.get(name) is not None

    def names_set(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))

    def __repr__(self) -> str:
        return f"Secrets(set={list(self.names_set())})"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    config: LineMedicConfig
    runtime: RuntimeEnv
    secrets: Secrets

    def config_hash(self) -> str:
        return config_hash(self.config)


def config_hash(config: LineMedicConfig) -> str:
    return sha256_hex(config.model_dump(mode="json"))


# ── env 병합 ───────────────────────────────────────────────────


def _parse_bool(raw: str) -> bool:
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ConfigError("expected 'true' or 'false'")


def _parse_int(raw: str) -> int:
    if not re.fullmatch(r"[0-9]+", raw):
        raise ConfigError("expected a non-negative integer")
    return int(raw)


def _parse_int_list(raw: str) -> list[int]:
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise ConfigError("expected comma-separated integers without empty items")
    return [_parse_int(part) for part in parts]


def _parse_str(raw: str) -> str:
    return raw


_EnvRule = tuple[tuple[tuple[str, ...], ...], Callable[[str], Any]]

ENV_OVERRIDES: dict[str, _EnvRule] = {
    "ISSUE_INTAKE_ENABLED": ((("issue_intake", "enabled"),), _parse_bool),
    "ISSUE_POLL_SECONDS": ((("issue_intake", "poll_interval_seconds"),), _parse_int),
    "ISSUE_TRUSTED_AUTHOR_IDS": ((("issue_intake", "auto_start", "author_ids"),), _parse_int_list),
    "ROUTING_SCOPE": ((("issue_intake", "routing_scope"),), _parse_str),
    "NOTIFICATION_ROUTE_ID": (
        (
            ("notifications", "required_start_route_id"),
            ("issue_intake", "start_notification_route_id"),
        ),
        _parse_str,
    ),
    "GITHUB_REPOSITORY": ((("repository", "full_name"),), _parse_str),
    "GITHUB_REPOSITORY_ID": ((("repository", "id"),), _parse_int),
    "AGENT_MODE": ((("agent", "mode"),), _parse_str),
    "AGENT_RUNTIME": ((("agent", "runtime"),), _parse_str),
    "NVIDIA_MODEL_ID": ((("agent", "model_id"),), _parse_str),
    "NVIDIA_BASE_URL": ((("agent", "model_base_url"),), _parse_str),
    "MEMORY_MODE": ((("memory", "mode"),), _parse_str),
    "MEMORY_SNAPSHOT_PATH": ((("memory", "snapshot_path"),), _parse_str),
    "CASE_SEARCH_ENGINE": ((("memory", "search_engine"),), _parse_str),
}


def _set_path(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = target
    for key in path[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise ConfigError(f"cannot apply env override under non-table key {key!r}")
        node = child
    node[path[-1]] = value


def _apply_env_overrides(raw: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    merged = copy.deepcopy(raw)
    for name, (paths, parse) in ENV_OVERRIDES.items():
        value = env.get(name)
        if not value:  # 빈 값은 설정되지 않은 것으로 본다 (.env.example 형식)
            continue
        try:
            parsed = parse(value)
        except ConfigError as exc:
            raise ConfigError(f"invalid env {name}: {exc}") from None
        for path in paths:
            _set_path(merged, path, parsed)
    return merged


# ── 파일 읽기 ──────────────────────────────────────────────────


def _reject_date_time(value: Any, path: tuple[str, ...]) -> None:
    if isinstance(value, date | time):  # datetime은 date의 하위 클래스다
        where = ".".join(path) or "<root>"
        raise ConfigError(
            f"TOML date/time types are not allowed at {where}; use an RFC3339 '...Z' string"
        )
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_date_time(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_date_time(child, (*path, str(index)))


def _read_toml(path: Path, max_bytes: int = MAX_CONFIG_BYTES) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    if size > max_bytes:
        raise ConfigError(f"config file size {size} exceeds {max_bytes} bytes")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ConfigError(f"config file size {len(data)} exceeds {max_bytes} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("config file is not valid UTF-8") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from None
    _reject_date_time(raw, ())
    return raw


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_input=False, include_url=False):
        where = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def _validate(model: type[BaseModel], data: dict[str, Any], label: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid {label}: {_format_validation_error(exc)}") from None


def read_toml(path: Path, max_bytes: int = MAX_CONFIG_BYTES) -> dict[str, Any]:
    """TOML 파일을 크기 상한·UTF-8·날짜/시각 타입 거부 규칙으로 읽는다(D60).

    설정 외의 계약·정책 파일 로더도 같은 규칙으로 읽기 위해 공개한다.
    """
    return _read_toml(path, max_bytes)


def validate_model(model: type[BaseModel], data: dict[str, Any], label: str) -> Any:
    """pydantic 모델 검증 실패를 입력 값 없이 요약한 `ConfigError`로 바꾼다."""
    return _validate(model, data, label)


def read_env_file(path: Path) -> dict[str, str]:
    """`.env` 형식(`NAME=value`)을 읽는다. 없으면 빈 dict. 값은 어디에도 출력하지 않는다."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match:
            continue
        name, rest = match.group(1), match.group(2).strip()
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
            value = rest[1:-1]
        elif rest.startswith("#"):
            value = ""
        else:
            comment = rest.find(" #")
            value = rest[:comment].rstrip() if comment != -1 else rest
        values[name] = value
    return values


def process_env(env_file: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """`.env` 파일 위에 프로세스 환경 변수를 덮어쓴 값."""
    return {**read_env_file(env_file), **os.environ}


# ── 공개 진입점 ────────────────────────────────────────────────


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LineMedicConfig:
    """env 없이 설정 파일만 검증한다."""
    return _validate(LineMedicConfig, _read_toml(path), "config")


def load_settings(
    path: Path = DEFAULT_CONFIG_PATH,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """설정 파일과 env를 병합해 검증한다. `env`가 없으면 `.env` + 프로세스 환경을 쓴다."""
    env = process_env() if env is None else env
    merged = _apply_env_overrides(_read_toml(path), env)
    config = _validate(LineMedicConfig, merged, "config")
    runtime_values = {
        field: env[name] for name, field in RUNTIME_ENV_FIELDS.items() if env.get(name)
    }
    runtime = _validate(RuntimeEnv, runtime_values, "runtime env")
    return Settings(config=config, runtime=runtime, secrets=Secrets(env))
