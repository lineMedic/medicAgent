"""에이전트 규칙 묶음 `/agent_rules`와 workspace 금지 자료 검사
(W14, spec 05 §3·§5·§6, docs/02 §5, D87).

host가 관리하는 prompt·skill·도구 설명을 attempt workspace에 읽기 전용으로 둔다.

- 원본: `linemedic/agent/prompts/system.md`·`tools.md`, `linemedic/agent/skills/*/SKILL.md`
- `prompt_sha256`: 설치한 규칙 파일(경로 순서, 경로와 바이트)을 이은 SHA-256.
  attempt 기록·trace에 남긴다
- `forbidden_findings`: 에이전트가 보는 트리(repo 사본·규칙)에 넣지 않을 자료가 있는지 본다.
  평가 식별자(holdout 입력 로트 ID)·평가 기대값 표지·LineMedic 내부 경로·비밀 형태·`.git`·
  docker socket·symlink. 규칙 파일에는 시나리오 ID(S1 등)도 없어야 한다.
  찾은 위치와 종류만 돌려주고 내용은 돌려주지 않는다(비밀을 다시 퍼뜨리지 않게)
"""

import hashlib
import os
import re
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from linemedic.common.sanitize import mask_secrets

AGENT_DIR = Path(__file__).resolve().parent
RULE_SOURCES: tuple[tuple[str, Path], ...] = (
    ("system.md", AGENT_DIR / "prompts" / "system.md"),
    ("tools.md", AGENT_DIR / "prompts" / "tools.md"),
    ("skills/code-exception/SKILL.md", AGENT_DIR / "skills" / "code-exception" / "SKILL.md"),
    (
        "skills/vision-quality-drop/SKILL.md",
        AGENT_DIR / "skills" / "vision-quality-drop" / "SKILL.md",
    ),
)
SCENARIO_ID_RE = re.compile(
    r"(?<![0-9A-Za-z_-])S(?:1b?|2(?:-lite)?|3(?:-[ABC])?|[4-7])(?![0-9A-Za-z_])"
)
EVAL_MARKERS = ("expected_category", "expected_action", "scenario_name", "scenario_expectations",
                "holdout")  # fmt: skip
INTERNAL_PATHS = ("linemedic/eval", "linemedic/control_plane", "runs/", "eval/")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")
MAX_SCAN_BYTES = 1_000_000  # 이보다 큰 파일은 내용을 보지 않고 크기로 표시한다


@dataclass(frozen=True)
class Finding:
    path: str  # 검사 루트 기준 상대 경로
    kind: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.path}"


def _digest(items: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for rel, data in sorted(items):
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(data + b"\0")
    return digest.hexdigest()


def prompt_sha256(root: Path) -> str:
    """규칙 트리의 SHA-256(상대 경로 순서, 경로와 바이트를 NUL로 구분)."""
    files = [p for p in root.rglob("*") if p.is_file()]
    return _digest((p.relative_to(root).as_posix(), p.read_bytes()) for p in files)


def bundle_sha256() -> str:
    """원본 규칙 묶음의 hash. 설치한 트리의 `prompt_sha256`과 같다(run manifest에 남긴다)."""
    return _digest((rel, source.read_bytes()) for rel, source in RULE_SOURCES)


def install_rules(target: Path) -> str:
    """규칙 파일을 `target`에 복사하고 읽기 전용(파일 0444, 디렉터리 0555)으로 만든다.

    `target`은 없는 새 경로여야 한다. prompt_sha256을 돌려준다.
    """
    target.mkdir(parents=True)
    for rel, source in RULE_SOURCES:
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    digest = prompt_sha256(target)
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(target, 0o555)
    return digest


def _text_findings(rel: str, text: str, terms: tuple[str, ...], rules: bool) -> list[Finding]:
    found = []
    if any(term and term in text for term in terms):
        found.append(Finding(rel, "eval_identifier"))
    lowered = text.lower()
    if any(marker in lowered for marker in EVAL_MARKERS):
        found.append(Finding(rel, "eval_marker"))
    if any(marker in text for marker in INTERNAL_PATHS):
        found.append(Finding(rel, "internal_path"))
    if mask_secrets(text) != text or BEARER_RE.search(text):
        found.append(Finding(rel, "secret_shape"))
    if "docker.sock" in text:
        found.append(Finding(rel, "docker_socket"))
    if rules and SCENARIO_ID_RE.search(text):
        found.append(Finding(rel, "scenario_id"))
    return found


def forbidden_findings(
    root: Path, *, terms: Iterable[str] = (), rules: bool = False
) -> list[Finding]:
    """`root` 아래에서 에이전트에게 주지 않을 자료를 찾는다. `rules`면 시나리오 ID도 본다."""
    terms = tuple(t for t in terms if t)
    found: list[Finding] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in [*dirnames, *filenames]:
            path = base / name
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                found.append(Finding(rel, "symlink"))
            elif name == ".git":
                found.append(Finding(rel, "git_metadata"))
            elif stat.S_ISSOCK(mode) or name == "docker.sock":
                found.append(Finding(rel, "docker_socket"))
        dirnames[:] = [d for d in dirnames if d != ".git" and not (base / d).is_symlink()]
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if path.stat().st_size > MAX_SCAN_BYTES:
                found.append(Finding(rel, "too_large_to_scan"))
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            found.extend(_text_findings(rel, text, terms, rules))
    return found
