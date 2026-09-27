"""환경 점검 (`make doctor`).

항목마다 `OK / MISSING / FAIL / NOT_CONFIGURED`를 낸다.
필수 항목이 하나라도 OK가 아니면 종료 코드 1이다.
실패를 경고로만 넘기거나 준비 완료로 표시하지 않는다.
비밀 값은 어떤 출력에도 넣지 않는다(이름만 쓴다).
이후 카드가 `register`로 github·model·runtime·openshell·fixtures·runner image 항목을 추가한다.
"""

import argparse
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from linemedic.common.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    ConfigError,
    load_settings,
    process_env,
)
from linemedic.integrations.github import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    GitHubError,
    GitHubPort,
    HttpGitHub,
)
from linemedic.scripts.github_setup_check import REPO_FULL_NAME_RE
from linemedic.scripts.host_manifest import fts5_available, run_version_command

Status = Literal["OK", "MISSING", "FAIL", "NOT_CONFIGURED"]

# spec 11 §2의 변수 중 config 기본값이 없어 반드시 사람이 채워야 하는 것.
# 선택 항목(LINEMEDIC_OPS_RECIPIENT·SMTP_CREDENTIAL·MEMORY_SNAPSHOT_PATH)과
# config 기본값이 있는 덮어쓰기 항목(ISSUE_POLL_SECONDS 등)은 제외한다.
REQUIRED_ENV: tuple[str, ...] = (
    "LINE_MEDIC_ENV",
    "AGENT_MODE",
    "AGENT_RUNTIME",
    "NVIDIA_MODEL_ID",
    "NVIDIA_BASE_URL",
    "NVIDIA_API_KEY",
    "GITHUB_REPOSITORY",
    "GITHUB_REPOSITORY_ID",
    "ISSUE_TRUSTED_AUTHOR_IDS",
    "GITHUB_BROKER_CREDENTIAL",
    "GITHUB_SETUP_CREDENTIAL",
    "DEMO_HOST_ID",
    "CONTROL_AGENT_TOKEN",
    "CONTROL_OPERATOR_TOKEN",
    "BASELINE_COMMIT",
    "RUNNER_IMAGE_ID",
    "MES_BASE_IMAGE_ID",
    "RUNS_DIR",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    required: bool


@dataclass
class DoctorContext:
    config_path: Path = DEFAULT_CONFIG_PATH
    env: Mapping[str, str] = field(default_factory=dict)
    which: Callable[[str], str | None] = shutil.which
    run: Callable[[list[str]], str | None] = run_version_command
    # (repository_id, full_name, credential) → GitHubPort. None이면 config `github` 절로 HttpGitHub
    github_port: Callable[[int, str, str], GitHubPort] | None = None


CheckFn = Callable[[DoctorContext], tuple[Status, str]]
_REGISTRY: list[tuple[str, bool, CheckFn]] = []


def register(name: str, required: bool) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        _REGISTRY.append((name, required, fn))
        return fn

    return decorator


MIN_PYTHON = (3, 12)


@register("python", required=True)
def check_python(ctx: DoctorContext) -> tuple[Status, str]:
    current = tuple(sys.version_info[:3])
    version = ".".join(str(part) for part in current)
    if current[:2] >= MIN_PYTHON:
        return "OK", f"Python {version}"
    return "FAIL", f"Python {version} (3.12 이상 필요)"


@register("sqlite_fts5", required=False)
def check_sqlite_fts5(ctx: DoctorContext) -> tuple[Status, str]:
    if fts5_available():
        return "OK", "SQLite FTS5 사용 가능"
    return "MISSING", "FTS5 없음 → keyword_fallback 사용 (D54)"


@register("docker_cli", required=False)
def check_docker_cli(ctx: DoctorContext) -> tuple[Status, str]:
    if ctx.which("docker") is None:
        return "MISSING", "docker CLI 없음 (docker 카드에서만 필수)"
    return "OK", ctx.run(["docker", "--version"]) or "docker CLI 발견 (버전 확인 실패)"


@register("config", required=True)
def check_config(ctx: DoctorContext) -> tuple[Status, str]:
    try:
        settings = load_settings(ctx.config_path, env=ctx.env)
    except ConfigError as exc:
        return "FAIL", str(exc)
    return "OK", f"{ctx.config_path} 로드, config_hash {settings.config_hash()[:12]}"


@register("env", required=True)
def check_env(ctx: DoctorContext) -> tuple[Status, str]:
    missing = [name for name in REQUIRED_ENV if not ctx.env.get(name)]
    if missing:
        return "NOT_CONFIGURED", "미설정 변수: " + ", ".join(missing)
    return "OK", f"필수 변수 {len(REQUIRED_ENV)}개 설정됨"


GITHUB_DOCTOR_ENV = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_BROKER_CREDENTIAL")


def _default_github_port(ctx: DoctorContext) -> Callable[[int, str, str], GitHubPort]:
    try:
        github = load_settings(ctx.config_path, env=ctx.env).config.github
        base_url, api_version, timeout = github.base_url, github.api_version, github.timeout_seconds
    except ConfigError:  # config 오류는 config 항목이 따로 알린다
        base_url, api_version, timeout = DEFAULT_BASE_URL, None, DEFAULT_TIMEOUT_SECONDS

    def build(repository_id: int, full_name: str, credential: str) -> GitHubPort:
        return HttpGitHub(
            repository_id,
            full_name,
            credential,
            base_url=base_url,
            api_version=api_version,
            timeout_seconds=timeout,
        )

    return build


@register("github", required=True)
def check_github(ctx: DoctorContext) -> tuple[Status, str]:
    """봇 credential로 등록 repo의 숫자 ID가 설정과 같은지, 봇 identity를 확인한다(W03·W22)."""
    missing = [name for name in GITHUB_DOCTOR_ENV if not ctx.env.get(name)]
    if missing:
        return "NOT_CONFIGURED", "미설정 변수: " + ", ".join(missing)
    full_name, expected_id = ctx.env["GITHUB_REPOSITORY"], ctx.env["GITHUB_REPOSITORY_ID"]
    if not REPO_FULL_NAME_RE.fullmatch(full_name) or not expected_id.isdigit():
        return "FAIL", "GITHUB_REPOSITORY(owner/name) 또는 GITHUB_REPOSITORY_ID(숫자) 형식 오류"
    build = ctx.github_port or _default_github_port(ctx)
    try:
        port = build(int(expected_id), full_name, ctx.env["GITHUB_BROKER_CREDENTIAL"])
        repo = port.get_repo().data
        identity = port.get_identity().data
    except ValueError:
        return "FAIL", "GitHub 등록 repo 설정 형식 오류"
    except GitHubError as exc:
        return "FAIL", f"봇 credential로 {full_name}·identity 조회 실패: {type(exc).__name__}"
    if not isinstance(repo, dict) or repo.get("id") != int(expected_id):
        actual = repo.get("id") if isinstance(repo, dict) else None
        return "FAIL", f"repo ID 불일치: 설정 {expected_id}, 실제 {actual}"
    if not isinstance(identity, dict) or not identity.get("login"):
        return "FAIL", "봇 identity(login)를 확인하지 못했다"
    return "OK", (
        f"{repo.get('full_name')} (ID {expected_id}) 조회됨, "
        f"봇 {identity['login']} (ID {identity.get('id')})"
    )


RUNNER_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")


@register("runner_image", required=True)
def check_runner_image(ctx: DoctorContext) -> tuple[Status, str]:
    """`RUNNER_IMAGE_ID`(make runner-image 출력)가 이 host의 image ID와 정확히 같은가(W10)."""
    image_id = ctx.env.get("RUNNER_IMAGE_ID")
    if not image_id:
        return (
            "NOT_CONFIGURED",
            "미설정 변수: RUNNER_IMAGE_ID (make runner-image의 runner_image_id)",
        )
    if not RUNNER_IMAGE_ID_RE.fullmatch(image_id):
        return "FAIL", "RUNNER_IMAGE_ID는 태그가 아니라 sha256:<64 hex> image ID여야 한다"
    if ctx.which("docker") is None:
        return "MISSING", "docker CLI 없음"
    found = ctx.run(["docker", "image", "inspect", "--format", "{{.Id}}", image_id])
    if found is None:
        return "MISSING", f"runner image {image_id[:19]} 없음 (make runner-image)"
    if found != image_id:
        return "FAIL", f"image ID 불일치: 설정 {image_id[:19]}, 실제 {found[:19]}"
    return "OK", f"runner image {image_id[:19]} 확인"


def run_checks(ctx: DoctorContext) -> list[CheckResult]:
    results = []
    for name, required, fn in _REGISTRY:
        try:
            status, detail = fn(ctx)
        except Exception as exc:  # 점검 자체의 오류도 실패로 보인다
            status, detail = "FAIL", f"점검 오류: {type(exc).__name__}"
        results.append(CheckResult(name=name, status=status, detail=detail, required=required))
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.required and r.status != "OK" for r in results) else 0


def render(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        marker = "필수" if r.required else "선택"
        lines.append(f"{r.status:<15} {r.name:<12} [{marker}] {r.detail}")
    bad = [r.name for r in results if r.required and r.status != "OK"]
    if bad:
        lines.append(f"doctor: 필수 항목 {len(bad)}개가 OK 아님 ({', '.join(bad)})")
    else:
        lines.append("doctor: 필수 항목 모두 OK")
    return "\n".join(lines)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linemedic doctor")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args(argv)
    ctx = DoctorContext(
        config_path=args.config,
        env=process_env(args.env_file) if env is None else env,
    )
    results = run_checks(ctx)
    print(render(results))
    return exit_code(results)
