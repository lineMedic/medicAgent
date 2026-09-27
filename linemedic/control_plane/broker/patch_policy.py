"""브로커 패치 정책 (W10, spec 06 §2, docs/07 §1).

정책 원본은 `linemedic/policies/broker_policy.toml`이다(D80). 두 단계로 검사한다.

1. `check_diff`: 제안의 diff를 git unified diff의 좁은 부분집합으로만 해석한다.
   - 거부: 절대경로·`..`·`.`·빈 단계·역슬래시·NUL·따옴표로 감싼 경로·정규화하면 달라지는 경로·
     `.git` 단계, symlink·submodule·실행 권한(`new file mode` 100644 외)·mode 변경·binary·rename·
     copy·삭제,
     허용 밖 경로, 보호 경로, 기존 테스트 수정, 새 테스트 2개 이상, 파일 수·줄 수 상한 초과,
     제어 문자·양방향 제어 문자, 문법 밖의 줄, header와 맞지 않는 hunk 줄 수
   - 허용: 업무 파일(`app/defects.py`) 수정 1개와 제안이 선언한 새 재현 테스트 1개
2. `verify_tree`: patch를 실제로 적용한 뒤 Git tree를 다시 읽어 변경 경로·mode·blob이 계획과 정확히
   같은지, 보호 경로의 blob이 그대로인지 확인한다. 문자열 prefix 검사만으로 끝내지 않는다.

거부는 모두 `PatchDenied`(검사 코드 `PATCH_PATH_DENIED`, 규칙 이름 `rule`)다.
"""

import functools
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from linemedic.common.config import ConfigError, read_toml, validate_model

POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "broker_policy.toml"
MAX_POLICY_BYTES = 16 * 1024
MAX_PATH_CHARS = 200
REGULAR_FILE_MODE = "100644"
NO_NEWLINE = "\\ No newline at end of file"

_SAFE_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
_HUNK = re.compile(r"@@ -(\d{1,7})(?:,(\d{1,7}))? \+(\d{1,7})(?:,(\d{1,7}))? @@(?: .*)?")
_INDEX = re.compile(r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: ([0-7]{6}))?")
# 탭·개행 외 제어 문자, C1, 줄·문단 구분자, 양방향 제어·보이지 않는 서식 문자(trojan source 방지)
_FORBIDDEN = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)
_REJECTED_HEADERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("old mode ", "new mode "), "mode_change_not_allowed"),
    (("deleted file mode ",), "delete_not_allowed"),
    (
        ("rename from ", "rename to ", "similarity index ", "dissimilarity index "),
        "rename_not_allowed",
    ),
    (("copy from ", "copy to "), "copy_not_allowed"),
    (("Binary files ", "GIT binary patch"), "binary_not_allowed"),
    # 100644 외(symlink 120000·submodule 160000·실행 100755)
    (("new file mode ",), "file_mode_not_allowed"),
)


# ── 정책 파일 ──────────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


RepoPath = Annotated[str, Field(min_length=1, max_length=MAX_PATH_CHARS)]


class PatchRules(_Model):
    allowed_app_file: RepoPath
    allowed_new_test_glob: RepoPath
    max_files: Annotated[int, Field(gt=0)]
    max_changed_lines: Annotated[int, Field(gt=0)]
    protected_globs: Annotated[list[RepoPath], Field(min_length=1)]
    regression_tests: RepoPath


class _PolicyFile(_Model):
    schema_version: Literal["linemedic.v4"]
    patch: PatchRules


@dataclass(frozen=True)
class BrokerPolicy:
    rules: PatchRules
    sha256: str  # 정책 파일 바이트 SHA-256(검사 기록에 남긴다)


def load_policy(path: Path = POLICY_PATH) -> BrokerPolicy:
    rules = validate_model(_PolicyFile, read_toml(path, MAX_POLICY_BYTES), "broker policy").patch
    for value in (rules.allowed_app_file, rules.regression_tests):
        if not is_safe_path(value):
            raise ConfigError(f"broker policy: 안전한 경로가 아니다: {value!r}")
    return BrokerPolicy(rules, hashlib.sha256(path.read_bytes()).hexdigest())


# ── 경로·glob ──────────────────────────────────────────────────


def is_safe_path(path: str) -> bool:
    """repo 안의 정규화된 상대 경로인가.

    허용 문자만 쓰고, 빈 단계(`//`)·앞뒤 `/`·`.`·`..`·`.git` 단계가 없다.
    이 규칙이면 정규화해도 같은 문자열이라 정규화 우회(`app/./x`, `app//x`)가 따로 남지 않는다.
    """
    if len(path) > MAX_PATH_CHARS or not _SAFE_PATH.fullmatch(path):
        return False
    return not any(part in (".", "..") or part.lower() == ".git" for part in path.split("/"))


@functools.cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    """`*`·`?`는 `/`를 넘지 않는다(`fnmatch`와 다르다). `**`만 여러 단계."""
    return _glob_regex(pattern).fullmatch(path) is not None


def is_protected(path: str, rules: PatchRules) -> bool:
    return any(glob_match(path, pattern) for pattern in rules.protected_globs)


# ── 거부·계획 ──────────────────────────────────────────────────


class PatchDenied(Exception):
    code = "PATCH_PATH_DENIED"

    def __init__(self, rule: str, path: str | None = None) -> None:
        super().__init__(rule)
        self.rule = rule
        # 비신뢰 경로는 안전한 모양일 때만 기록에 남긴다
        self.path = path if path is not None and is_safe_path(path) else None

    def detail(self) -> dict[str, str]:
        return {"rule": self.rule, **({"path": self.path} if self.path else {})}


@dataclass(frozen=True)
class FileChange:
    path: str
    kind: Literal["modify", "add"]
    additions: int
    deletions: int


@dataclass(frozen=True)
class PatchPlan:
    files: tuple[FileChange, ...]
    new_test_path: str
    patch_sha256: str  # 제출된 diff 원문(UTF-8)의 SHA-256

    @property
    def changed_lines(self) -> int:
        return sum(f.additions + f.deletions for f in self.files)

    def record(self) -> dict[str, Any]:
        return {
            "patch_sha256": self.patch_sha256,
            "changed_lines": self.changed_lines,
            "files": [
                {"path": f.path, "kind": f.kind, "additions": f.additions, "deletions": f.deletions}
                for f in self.files
            ],
        }


# ── diff 해석 ──────────────────────────────────────────────────


class _Section:
    """파일 하나의 diff. `old`가 None이면 새 파일(`--- /dev/null`)."""

    def __init__(self, header_path: str | None) -> None:
        self.header_path = header_path  # `diff --git` 경로(전통 형식이면 None)
        self.new_file_mode = False
        self.has_paths = False
        self.old: str | None = None
        self.new: str | None = None
        self.additions = 0
        self.deletions = 0
        self.hunks = 0

    @property
    def path(self) -> str:
        assert self.new is not None
        return self.new

    @property
    def kind(self) -> Literal["modify", "add"]:
        return "add" if self.old is None else "modify"


def _path_after(line: str, prefix: str) -> str:
    value = line[len(prefix) :]
    if not is_safe_path(value):
        raise PatchDenied("invalid_path")
    return value


def _git_header(line: str) -> str:
    parts = line.split(" ")
    if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
        raise PatchDenied("invalid_path")
    old, new = _path_after(parts[2], "a/"), _path_after(parts[3], "b/")
    if old != new:
        raise PatchDenied("rename_not_allowed", new)
    return new


def _extended_header(section: _Section, line: str) -> None:
    if line == f"new file mode {REGULAR_FILE_MODE}":
        section.new_file_mode = True
        return
    index = _INDEX.fullmatch(line)
    if index is not None:
        if index.group(1) not in (None, REGULAR_FILE_MODE):
            raise PatchDenied("file_mode_not_allowed", section.header_path)
        return
    for prefixes, rule in _REJECTED_HEADERS:
        if line.startswith(prefixes):
            raise PatchDenied(rule, section.header_path)
    raise PatchDenied("unexpected_line")


def _file_paths(section: _Section, old_line: str, new_line: str | None) -> None:
    if old_line == "--- /dev/null":
        section.old = None
    else:
        if not old_line.startswith("--- a/"):
            raise PatchDenied("invalid_path")
        section.old = _path_after(old_line, "--- a/")
    if new_line is None or not new_line.startswith("+++ "):
        raise PatchDenied("malformed_header")
    if new_line == "+++ /dev/null":
        raise PatchDenied("delete_not_allowed", section.old)
    if not new_line.startswith("+++ b/"):
        raise PatchDenied("invalid_path")
    section.new = _path_after(new_line, "+++ b/")
    section.has_paths = True
    if section.old is not None and section.old != section.new:
        raise PatchDenied("rename_not_allowed", section.new)
    if section.header_path is not None and section.header_path != section.new:
        raise PatchDenied("rename_not_allowed", section.new)
    if section.new_file_mode != (section.old is None) and section.header_path is not None:
        raise PatchDenied("malformed_header", section.new)


def _hunk(section: _Section, lines: list[str], i: int) -> int:
    match = _HUNK.fullmatch(lines[i])
    if match is None:
        raise PatchDenied("malformed_hunk", section.new)
    old_left = int(match.group(2)) if match.group(2) is not None else 1
    new_left = int(match.group(4)) if match.group(4) is not None else 1
    new_file_range = int(match.group(1)) == 0 and old_left == 0  # 새 파일은 `-0,0`
    if old_left + new_left == 0 or (section.old is None and not new_file_range):
        raise PatchDenied("malformed_hunk", section.new)
    i += 1
    previous_content = False
    while old_left > 0 or new_left > 0:
        if i >= len(lines):
            raise PatchDenied("hunk_count_mismatch", section.new)
        line = lines[i]
        tag = line[:1]
        if tag in (" ", ""):  # 빈 줄은 git apply처럼 빈 문맥 줄로 본다
            old_left, new_left = old_left - 1, new_left - 1
        elif tag == "-":
            old_left -= 1
            section.deletions += 1
        elif tag == "+":
            new_left -= 1
            section.additions += 1
        elif line == NO_NEWLINE and previous_content:
            previous_content = False
            i += 1
            continue
        else:
            raise PatchDenied("hunk_count_mismatch", section.new)
        if old_left < 0 or new_left < 0:
            raise PatchDenied("hunk_count_mismatch", section.new)
        previous_content = True
        i += 1
    if i < len(lines) and lines[i] == NO_NEWLINE:
        i += 1
    section.hunks += 1
    return i


def _parse(diff: str) -> list[_Section]:
    if _FORBIDDEN.search(diff):
        raise PatchDenied("control_character")
    lines = diff.split("\n")
    if lines[-1] == "":
        lines.pop()
    sections: list[_Section] = []
    current: _Section | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current = _Section(_git_header(line))
            sections.append(current)
            i += 1
            while i < len(lines) and not lines[i].startswith(("--- ", "diff --git ")):
                _extended_header(current, lines[i])
                i += 1
            continue
        if line.startswith("--- "):
            if current is None or current.has_paths:  # 전통 형식: `diff --git` 없이 시작
                current = _Section(None)
                sections.append(current)
            _file_paths(current, line, lines[i + 1] if i + 1 < len(lines) else None)
            i += 2
            if i >= len(lines) or not lines[i].startswith("@@ "):
                raise PatchDenied("no_hunks", current.new)
            while i < len(lines) and lines[i].startswith("@@ "):
                i = _hunk(current, lines, i)
            continue
        if line.startswith("Binary files "):
            raise PatchDenied("binary_not_allowed")
        if current is not None and line[:1] in ("+", "-", " "):  # header보다 줄이 많은 hunk
            raise PatchDenied("hunk_count_mismatch", current.new)
        raise PatchDenied("unexpected_line")
    for section in sections:
        if not section.has_paths:  # 빈 새 파일·mode만 바뀐 파일 등
            raise PatchDenied("no_hunks", section.header_path)
    return sections


def check_diff(diff: str, new_test_path: str, rules: PatchRules) -> PatchPlan:
    """diff를 해석해 정책을 확인한다. 통과하면 적용 계획, 아니면 `PatchDenied`."""
    sections = _parse(diff)
    if not sections:
        raise PatchDenied("empty_diff")
    if len(sections) > rules.max_files:
        raise PatchDenied("too_many_files")
    if sum(s.additions + s.deletions for s in sections) > rules.max_changed_lines:
        raise PatchDenied("too_many_lines")
    seen: set[str] = set()
    files: list[FileChange] = []
    for section in sections:
        path, kind = section.path, section.kind
        if path in seen:
            raise PatchDenied("duplicate_file", path)
        seen.add(path)
        if is_protected(path, rules):
            raise PatchDenied("protected_path", path)
        if path == rules.allowed_app_file:
            if kind != "modify":
                raise PatchDenied("app_file_must_be_modified", path)
        elif glob_match(path, rules.allowed_new_test_glob):
            if kind != "add":
                raise PatchDenied("existing_test_modified", path)
        elif kind == "modify" and path.startswith("tests/"):
            raise PatchDenied("existing_test_modified", path)
        else:
            raise PatchDenied("path_not_allowed", path)
        files.append(FileChange(path, kind, section.additions, section.deletions))
    new_tests = [f.path for f in files if f.kind == "add"]
    if len(new_tests) > 1:
        raise PatchDenied("too_many_new_tests")
    if new_tests != [new_test_path]:
        raise PatchDenied("new_test_path_mismatch")
    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return PatchPlan(tuple(files), new_test_path, digest)


# ── 적용 뒤 tree 재확인 ────────────────────────────────────────

TreeEntry = tuple[str, str, str]  # (mode, type, object id)


def verify_tree(
    base: Mapping[str, TreeEntry],
    candidate: Mapping[str, TreeEntry],
    plan: PatchPlan,
    rules: PatchRules,
) -> None:
    """적용한 tree의 실제 변경이 계획과 같은지. 보호 경로 blob 불변도 따로 확인한다."""
    paths = set(base) | set(candidate)
    for path in sorted(paths):
        if is_protected(path, rules) and base.get(path) != candidate.get(path):
            raise PatchDenied("protected_changed", path)
    changed = {path for path in paths if base.get(path) != candidate.get(path)}
    if changed != {f.path for f in plan.files}:
        raise PatchDenied("tree_mismatch")
    for change in plan.files:
        before, after = base.get(change.path), candidate.get(change.path)
        if after is None or after[:2] != (REGULAR_FILE_MODE, "blob"):
            raise PatchDenied("tree_mismatch", change.path)
        if (change.kind == "add") != (before is None):
            raise PatchDenied("tree_mismatch", change.path)
        if before is not None and before[:2] != (REGULAR_FILE_MODE, "blob"):
            raise PatchDenied("tree_mismatch", change.path)


def check_text(content: bytes, path: str) -> None:
    """적용 뒤 바뀐 파일이 정규 UTF-8 텍스트인가(NUL·제어 문자·CR 없음)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchDenied("not_text", path) from None
    if _FORBIDDEN.search(text):
        raise PatchDenied("not_text", path)
