"""sandbox 실행 port (W15, spec 05 §1·§3, spec 07 §4, spec 12 N03·N04, D88).

sandbox 모드에서 supervisor는 attempt마다 sandbox를 준비하고, 같은 adapter를 그 안에서
돌린 뒤 닫는다. 실제 OpenShell 구현(정책 파일·CLI 문법·보호 확인 방법)은 설치 버전을
확인한 뒤(G5) 이 port 뒤에 둔다.
이 파일에는 port와 설정이 없을 때의 구현, 테스트용 fake만 있다.

- sandbox를 준비하지 못하면 local로 바꿔 돌리지 않는다(supervisor가 attempt를 시작하지 않는다)
- `sandbox_verified`는 host가 정한다: 고정한 정책 파일이 있고 필수 보호가 모두 PASS로 확인됐을 때만
  true다. sandbox 구현이 준 참/거짓 하나를 그대로 믿지 않는다
- effective policy(공급자가 더한 규칙 포함)는 내용 hash 이름으로 `runs/<run>/sandbox/`에
  한 번만 남긴다. 비밀 형태가 보이면 가린 뒤 남기고, 남긴 내용의 hash를 쓴다
"""

import hashlib
import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from linemedic.common.sanitize import mask_secrets

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies" / "openshell"
SANDBOX_DIR = "sandbox"  # runs/<run>/sandbox/
WORKSPACE_PATH = "/sandbox/work"
RULES_PATH = "/agent_rules"

# spec 07 §4 경계. sandbox 구현이 항목마다 PASS·FAIL·NOT_RUN을 알린다
REQUIRED_PROTECTIONS = (
    "egress_allowlist",  # 승인된 추론 경로·Control 도구 API로만 나간다
    "ops_api_denied",  # /ops/* 불가
    "github_denied",  # GitHub 목적지·provider 불가
    "docker_api_denied",  # Docker API·socket 불가
    "writes_confined",  # 작업 사본·임시 공간에만 쓴다
    "rules_read_only",  # /agent_rules에 쓸 수 없다
    "host_secrets_denied",  # host 비밀 파일을 읽을 수 없다
    "non_root",  # root가 아닌 사용자로 실행
    "no_extra_providers",  # 불필요한 provider·credential 연결 없음
    "no_self_approval",  # 에이전트가 정책·보호 예외를 승인하지 못한다
)
POLICY_CHECK = "policy_file"  # 고정한 정책 파일이 없으면 확인하지 못한 것으로 본다


class SandboxUnavailable(RuntimeError):
    """sandbox를 준비할 수 없음. reason은 기록용 짧은 코드다(비밀·경로를 넣지 않는다)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SandboxSession:
    identity: str  # 설치 도구가 준 sandbox 이름·ID
    effective_policy: str  # 적용된 정책 원문(공급자 규칙 포함)
    checks: Mapping[str, str]  # 필수 보호 → PASS / FAIL / NOT_RUN
    version: str | None = None  # sandbox 도구 버전
    tools_base_url: str | None = None  # sandbox 안에서 쓰는 도구 API 주소(없으면 host 설정)
    workspace_path: str = WORKSPACE_PATH
    rules_path: str = RULES_PATH


class SandboxPort(Protocol):
    name: str

    def prepare(
        self, *, run_id: str, attempt_id: str, workspace: Path, rules_dir: Path
    ) -> SandboxSession:
        """workspace를 `/sandbox/work`로, 규칙을 `/agent_rules`(읽기 전용)로 둔 sandbox를 만든다."""
        ...

    def close(self, session: SandboxSession) -> None:
        """그 sandbox 하나만(identity로) 정리한다."""
        ...


class UnconfiguredSandbox:
    """sandbox 구현이 아직 없다(G5 전). 준비를 거절한다."""

    name = "unconfigured"

    def prepare(
        self, *, run_id: str, attempt_id: str, workspace: Path, rules_dir: Path
    ) -> SandboxSession:
        raise SandboxUnavailable("not_configured")

    def close(self, session: SandboxSession) -> None:
        return None


@dataclass
class FakeSandbox:
    """테스트용. 알려 준 보호 확인 결과와 effective policy를 그대로 돌려준다."""

    checks: Mapping[str, str]
    effective_policy: str = "fake effective policy\n"
    tools_base_url: str | None = None
    version: str | None = "fake-0"
    name: str = "fake"
    calls: list[tuple[str, str]] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def prepare(
        self, *, run_id: str, attempt_id: str, workspace: Path, rules_dir: Path
    ) -> SandboxSession:
        identity = f"fake-sbx-{next(self._ids)}"
        self.calls.append(("prepare", identity))
        return SandboxSession(
            identity=identity,
            effective_policy=self.effective_policy,
            checks=dict(self.checks),
            version=self.version,
            tools_base_url=self.tools_base_url,
        )

    def close(self, session: SandboxSession) -> None:
        self.calls.append(("close", session.identity))


def verification(checks: Mapping[str, str], *, policy_sha256: str | None) -> tuple[bool, list[str]]:
    """(sandbox_verified, 확인하지 못한 항목). PASS가 아닌 값·빠진 항목은 확인하지 못한 것이다."""
    unverified = sorted(name for name in REQUIRED_PROTECTIONS if checks.get(name) != "PASS")
    if policy_sha256 is None:
        unverified.insert(0, POLICY_CHECK)
    return not unverified, unverified


def policy_dir_sha256(directory: Path) -> str | None:
    """정책 파일 묶음의 SHA-256(상대 경로 순서, 경로와 바이트를 NUL로 구분). 파일이 없으면 None."""
    if not directory.is_dir():
        return None
    items = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"정책 경로에 symlink가 있다: {path.name}")
        if path.is_file():
            items.append((path.relative_to(directory).as_posix(), path.read_bytes()))
    if not items:
        return None
    digest = hashlib.sha256()
    for rel, data in items:
        digest.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredPolicy:
    sha256: str
    ref: str  # runs_dir 기준 상대 경로
    masked: bool  # 비밀 형태를 가렸는가


def save_effective_policy(runs_dir: Path, run_id: str, text: str) -> StoredPolicy:
    """effective policy를 한 번만 쓴다: `runs/<run>/sandbox/effective-policy-<hash 16자>.txt`."""
    stored = mask_secrets(text)
    sha = sha256_text(stored)
    path = runs_dir / run_id / SANDBOX_DIR / f"effective-policy-{sha[:16]}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(stored)
    except FileExistsError:
        pass  # 같은 내용이 이미 있다
    return StoredPolicy(sha, path.relative_to(runs_dir).as_posix(), stored != text)
