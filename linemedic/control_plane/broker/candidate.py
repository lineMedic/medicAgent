"""candidate 생성 (W10, spec 06 §2·§3).

신뢰 mirror(`RUNS_DIR/mirror/l3-mes-api.git`)에서 제안마다 버리는 bare 사본을 만들고, 작업 트리 없이
임시 index로만 patch를 적용한다. 사용자·에이전트가 보낸 경로에서 git을 실행하지 않는다.

1. `git clone --bare --no-hardlinks --template=` → `<workdir>/repo.git`, 요청 base가 commit인지 확인
2. 임시 index에 base tree → `git apply --check --cached` → `git apply --cached` → `write-tree`
3. 적용 뒤 tree 재확인(`patch_policy.verify_tree`): 변경 경로·mode·blob, 보호 경로 blob 불변,
   바뀐 파일이 정규 UTF-8 텍스트인지
4. 서버 commit(`commit-tree`, 작성자·시각 고정) → `candidate_sha`·`candidate_tree`.
   에이전트가 준 candidate ID는 받지 않는다
5. R1용 repro tree = base + 새 테스트 blob만(업무 파일은 base 그대로)
6. base·repro·candidate tree를 `git archive` → tar 안전 추출(`filter="data"`)로
   `<workdir>/trees/`에 풀어 runner에 read-only로 준다

git 호출은 고정 argv다. 사용자·시스템 설정·hook·credential·prompt 없이 local file protocol만
허용한다. 환경 문제(git 없음·mirror에 base 없음 등)는 `CandidateError`, 패치 문제는 `PatchDenied`다.

`prepare_release_trees`(W12)는 사람이 승인한 merge commit 하나를 같은 방식의 버리는 사본에서 꺼내
base·repro·final tree를 만든다(최신 branch를 고르지 않는다). 배포 재검사와 MES 빌드 context에 쓴다.
"""

import io
import os
import re
import subprocess
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.common.clock import from_rfc3339
from linemedic.control_plane.broker.patch_policy import (
    REGULAR_FILE_MODE,
    PatchDenied,
    PatchPlan,
    PatchRules,
    TreeEntry,
    check_text,
    verify_tree,
)

GIT_TIMEOUT_SECONDS = 60
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
BROKER_NAME = "LineMedic Broker"
BROKER_EMAIL = "broker@linemedic.invalid"
_OBJECT_ID = re.compile(r"[0-9a-f]{40}")
_SAFE_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.file.allow=always",
)


class CandidateError(RuntimeError):
    """검사 환경 문제(mirror·git). 에이전트가 고칠 수 없다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _GitFailed(RuntimeError):
    def __init__(self, command: str, returncode: int) -> None:
        super().__init__(f"git {command} 실패({returncode})")
        self.command = command


def _git_env(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        **(extra or {}),
    }


def _git(
    args: list[str],
    *,
    home: Path,
    git_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    argv = ["git", *_SAFE_CONFIG, *([f"--git-dir={git_dir}"] if git_dir else []), *args]
    try:
        proc = subprocess.run(
            argv,
            env=_git_env(home, env),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise CandidateError("git_missing") from None
    except subprocess.TimeoutExpired:
        raise CandidateError("git_timeout") from None
    if proc.returncode != 0:
        raise _GitFailed(args[0], proc.returncode)
    return proc.stdout


def _ls_tree(git_dir: Path, home: Path, tree: str) -> dict[str, TreeEntry]:
    """`git ls-tree -r -z`: 경로 → (mode, type, object)."""
    entries: dict[str, TreeEntry] = {}
    out = _git(["ls-tree", "-r", "-z", "--full-tree", tree], home=home, git_dir=git_dir)
    for item in out.split(b"\0"):
        if not item:
            continue
        meta, _, path = item.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, oid)
    return entries


def _extract(archive: bytes, dest: Path) -> None:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise CandidateError("archive_too_large")
    dest.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            tar.extractall(dest, filter="data")  # 절대경로·밖을 가리키는 link·특수 파일 거부
    except (tarfile.TarError, OSError):  # FilterError도 TarError다
        raise CandidateError("archive_unsafe") from None


def _clone(mirror: Path, workdir: Path) -> tuple[Path, Path]:
    """`workdir`(없는 새 경로)에 mirror의 bare 사본을 만든다. (repo.git, git HOME)."""
    if not (mirror / "HEAD").is_file():
        raise CandidateError("mirror_missing")
    workdir.mkdir(parents=True)
    home = workdir / "home"
    home.mkdir()
    repo = workdir / "repo.git"
    try:
        _git(
            [
                "clone",
                "--bare",
                "--no-hardlinks",
                "--template=",
                "--quiet",
                "--",
                str(mirror),
                str(repo),
            ],
            home=home,
        )
    except _GitFailed:
        raise CandidateError("clone_failed") from None
    return repo, home


def _has_commit(repo: Path, home: Path, sha: str) -> bool:
    try:
        _git(["cat-file", "-e", f"{sha}^{{commit}}"], home=home, git_dir=repo)
    except _GitFailed:
        return False
    return True


def _tree_of(repo: Path, home: Path, sha: str) -> str:
    return _git(["rev-parse", f"{sha}^{{tree}}"], home=home, git_dir=repo).decode().strip()


def _repro_tree(
    repo: Path, home: Path, workdir: Path, base_sha: str, new_test_blob: str, new_test_path: str
) -> str:
    """R1용 tree: base에 새 테스트 blob 하나만 더한다(업무 파일은 base 그대로)."""
    index = {"GIT_INDEX_FILE": str(workdir / "repro.index")}
    _git(["read-tree", base_sha], home=home, git_dir=repo, env=index)
    _git(
        [
            "update-index",
            "--add",
            "--cacheinfo",
            f"{REGULAR_FILE_MODE},{new_test_blob},{new_test_path}",
        ],
        home=home,
        git_dir=repo,
        env=index,
    )
    return _git(["write-tree"], home=home, git_dir=repo, env=index).decode().strip()


def _export(repo: Path, home: Path, workdir: Path, trees: Mapping[str, str]) -> dict[str, Path]:
    extracted = {}
    for name, tree in trees.items():
        archive = _git(["archive", "--format=tar", tree], home=home, git_dir=repo)
        extracted[name] = workdir / "trees" / name
        _extract(archive, extracted[name])
    return extracted


@dataclass(frozen=True)
class Candidate:
    base_sha: str
    base_tree: str
    candidate_sha: str
    candidate_tree: str
    repro_tree: str
    patch_sha256: str
    workdir: Path
    trees: Mapping[str, Path] = field(default_factory=dict)  # base·repro·candidate 추출 경로

    def record(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "base_tree": self.base_tree,
            "candidate_sha": self.candidate_sha,
            "candidate_tree": self.candidate_tree,
            "repro_tree": self.repro_tree,
            "patch_sha256": self.patch_sha256,
        }


def build_candidate(
    *,
    mirror: Path,
    workdir: Path,
    base_sha: str,
    diff: str,
    plan: PatchPlan,
    rules: PatchRules,
    proposal_id: str,
    committed_at: str,
) -> Candidate:
    """`workdir`(비어 있는 새 경로)에 candidate를 만든다.

    `committed_at`은 RFC3339 제안 접수 시각이다(candidate commit 시각으로 고정).
    """
    if not _OBJECT_ID.fullmatch(base_sha):
        raise CandidateError("invalid_base_sha")
    repo, home = _clone(mirror, workdir)
    if not _has_commit(repo, home, base_sha):
        raise CandidateError("base_not_in_mirror")
    try:
        return _build(repo, home, workdir, base_sha, diff, plan, rules, proposal_id, committed_at)
    except _GitFailed as exc:
        raise CandidateError(f"git_{exc.command}_failed") from None


def _build(
    repo: Path,
    home: Path,
    workdir: Path,
    base_sha: str,
    diff: str,
    plan: PatchPlan,
    rules: PatchRules,
    proposal_id: str,
    committed_at: str,
) -> Candidate:
    base_tree = _tree_of(repo, home, base_sha)
    patch = workdir / "proposal.patch"
    patch.write_bytes(diff.encode("utf-8") + (b"" if diff.endswith("\n") else b"\n"))
    index = {"GIT_INDEX_FILE": str(workdir / "candidate.index")}
    _git(["read-tree", base_sha], home=home, git_dir=repo, env=index)
    try:
        _git(["apply", "--check", "--cached", str(patch)], home=home, git_dir=repo, env=index)
    except _GitFailed:
        raise PatchDenied("apply_failed") from None
    _git(["apply", "--cached", str(patch)], home=home, git_dir=repo, env=index)
    candidate_tree = _git(["write-tree"], home=home, git_dir=repo, env=index).decode().strip()

    before = _ls_tree(repo, home, base_tree)
    after = _ls_tree(repo, home, candidate_tree)
    verify_tree(before, after, plan, rules)
    for change in plan.files:
        blob = _git(["cat-file", "blob", after[change.path][2]], home=home, git_dir=repo)
        check_text(blob, change.path)

    when = f"@{int(from_rfc3339(committed_at).timestamp())} +0000"
    identity = {
        "GIT_AUTHOR_NAME": BROKER_NAME,
        "GIT_AUTHOR_EMAIL": BROKER_EMAIL,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_NAME": BROKER_NAME,
        "GIT_COMMITTER_EMAIL": BROKER_EMAIL,
        "GIT_COMMITTER_DATE": when,
    }
    message = (
        f"LineMedic candidate {proposal_id}\n\nbase {base_sha}\npatch sha256 {plan.patch_sha256}\n"
    )
    candidate_sha = (
        _git(
            ["commit-tree", candidate_tree, "-p", base_sha, "-m", message],
            home=home,
            git_dir=repo,
            env=identity,
        )
        .decode()
        .strip()
    )

    new_test_blob = after[plan.new_test_path][2]
    repro_tree = _repro_tree(repo, home, workdir, base_sha, new_test_blob, plan.new_test_path)
    trees = _export(
        repo, home, workdir, {"base": base_tree, "repro": repro_tree, "candidate": candidate_tree}
    )
    return Candidate(
        base_sha=base_sha,
        base_tree=base_tree,
        candidate_sha=candidate_sha,
        candidate_tree=candidate_tree,
        repro_tree=repro_tree,
        patch_sha256=plan.patch_sha256,
        workdir=workdir,
        trees=trees,
    )


# ── 배포 재검사용 tree (W12) ─────────────────────────────────


@dataclass(frozen=True)
class ReleaseTrees:
    base_sha: str
    base_tree: str
    merge_sha: str
    final_tree: str
    repro_tree: str
    workdir: Path
    trees: Mapping[str, Path] = field(default_factory=dict)  # base·repro·final 추출 경로

    def record(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "base_tree": self.base_tree,
            "approved_merge_sha": self.merge_sha,
            "approved_tree": self.final_tree,
            "repro_tree": self.repro_tree,
        }


def prepare_release_trees(
    *, mirror: Path, workdir: Path, base_sha: str, merge_sha: str, new_test_path: str
) -> ReleaseTrees:
    """승인한 `merge_sha` commit 하나를 버리는 bare 사본에서 꺼낸다.

    - branch·main의 최신 상태를 보지 않는다. mirror에 그 commit이 없으면 `merge_not_in_mirror`
    - final = merge commit의 tree, repro = base + final tree의 새 테스트 blob
    - base·repro·final을 안전 추출해 R0·R1·R2와 MES 빌드 context로 쓴다(repo의 hook·설정 없음)
    """
    if not _OBJECT_ID.fullmatch(base_sha) or not _OBJECT_ID.fullmatch(merge_sha):
        raise CandidateError("invalid_sha")
    repo, home = _clone(mirror, workdir)
    if not _has_commit(repo, home, merge_sha):
        raise CandidateError("merge_not_in_mirror")
    if not _has_commit(repo, home, base_sha):
        raise CandidateError("base_not_in_mirror")
    try:
        base_tree = _tree_of(repo, home, base_sha)
        final_tree = _tree_of(repo, home, merge_sha)
        entry = _ls_tree(repo, home, final_tree).get(new_test_path)
        if entry is None or entry[:2] != (REGULAR_FILE_MODE, "blob"):
            raise CandidateError("new_test_missing")
        repro_tree = _repro_tree(repo, home, workdir, base_sha, entry[2], new_test_path)
        trees = _export(
            repo, home, workdir, {"base": base_tree, "repro": repro_tree, "final": final_tree}
        )
    except _GitFailed as exc:
        raise CandidateError(f"git_{exc.command}_failed") from None
    return ReleaseTrees(
        base_sha=base_sha,
        base_tree=base_tree,
        merge_sha=merge_sha,
        final_tree=final_tree,
        repro_tree=repro_tree,
        workdir=workdir,
        trees=trees,
    )
