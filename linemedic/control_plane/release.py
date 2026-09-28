"""사람이 승인한 exact SHA 배포와 업무 검증 연결 (W12, spec 08 §1~§3·§9, docs/05 ⑥, D82).

운영자가 `make approve-release`(G8) → `POST /ops/releases`로 PR 번호·최종 merge SHA·지금 MES image
ID를 명시해 승인한다. 머지 감시(polling·webhook)·자동 머지·최신 main 선택은 없다.

1. `approve`: 사전 검사 1~7(외부는 조회만).
   - 같은 논리 작업(`deploy:<work>:<sha>`)의 execution이 있으면 그 상태를 돌려준다
   - 사건 PR_OPENED·기대 version·work WAITING_REVIEW·제안·PR execution이 요청과 일치
   - 등록 repo·PR·base branch가 catalog·run과 일치
   - GitHub `merged=true`·최종 merge SHA, PR head = candidate, 봇이 아닌 리뷰어가 candidate head를
     승인, merge commit tree = candidate tree
   - 지금 MES container image = `expected_current_image_id`
   거부는 `ReleaseRefused`(상태·외부 변경 없음)
2. TX{ DEPLOY INTENDED, incident DEPLOYING·work WAITING_VERIFICATION, run 배포 lock }
3. `run`(트랜잭션 밖, 기본은 백그라운드 thread):
   - 신뢰 mirror에 merge commit 하나만 fetch → 버리는 사본에서 base·repro·final tree
     (final = candidate tree 재확인) → R0·R1·R2 재실행(W10)
   - 신뢰 레시피(`linemedic/runner/mes.Dockerfile`)로 final tree만 빌드
   - 이전 container·image·복원 절차 기록 → 이전 container 제거 → image **ID**로 같은 이름·run
     network·라벨·read-only 데이터로 기동 → `docker inspect`로 container·image·라벨 확인
4. TX{ execution SUCCEEDED, incident VERIFYING, verification RUNNING } → 신뢰 prober로 verifier(W05)
   → `verifier.persist_result`(판정·전이·알림은 verifier 모듈) → lock 해제

실패(spec 08 §3): fetch·재검사·빌드 실패는 이전 container가 그대로인지 확인해 기록하고 이관한다
(FAILED, ESCALATED/BLOCKED, RECOVERY_NOT_VERIFIED `NOT_DEPLOYED`). 이전 container를 지운 뒤 기동
실패는 실제 상태와 사람이 실행할 복원 절차를 기록하고 이관한다(자동 rollback 없음). docker 호출
timeout·기동 뒤 inspect 불일치는 UNKNOWN(EXECUTION_UNKNOWN)이고 운영자가 `make reconcile`로
실제 container·image·라벨만 조회한다. 앱의 `/version` 자기보고는 identity로 쓰지 않는다.
"""

import json
import os
import re
import shlex
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.clock import Clock, to_rfc3339
from linemedic.common.ids import is_valid_entity_id, is_valid_run_id, new_id
from linemedic.common.sanitize import mask_secrets
from linemedic.control_plane import audit, checkpoints
from linemedic.control_plane.attempts import attempt_origin
from linemedic.control_plane.broker.candidate import CandidateError, prepare_release_trees
from linemedic.control_plane.broker.github_pr import branches
from linemedic.control_plane.broker.intake import _block
from linemedic.control_plane.broker.patch_gate import GateOutcome, run_protected_stages
from linemedic.control_plane.broker.patch_policy import BrokerPolicy, load_policy
from linemedic.control_plane.broker.runner import Runner
from linemedic.control_plane.catalog import Catalog, CatalogError
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.observer import ContainerObserver, RecurrenceSignature
from linemedic.control_plane.state import Actor, coupled_transition, transition_incident
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.verifier import (
    DEFAULT_CONTRACT,
    EVAL_DIR,
    PROBER_IMAGE,
    FixtureGuard,
    ProberHttp,
    VerificationResult,
    VerificationRun,
    drive,
    load_contract,
    persist_result,
    prober_options,
    register_running,
    resolve_cases,
    unfinished_result,
    wait_until_healthy,
)
from linemedic.factory_sim.scenarios import (
    HOLDOUT_FIXTURE,
    mes_container_options,
    resource_names,
    write_contract_data,
)
from linemedic.integrations.docker import DockerError, DockerPort
from linemedic.integrations.git_fetch import CommitFetcher, LocalOnlyFetcher
from linemedic.integrations.github import GitHubError, GitHubPort, GitHubResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
MES_DOCKERFILE = REPO_ROOT / "linemedic" / "runner" / "mes.Dockerfile"
LOCK_INTEGRATION = "release"
DEFAULT_ORIGIN = "agent_release"
MAX_REVIEW_PAGES = 10
DOCKER_TIMEOUT_EXIT = 124  # CliDocker가 timeout을 이 코드로 돌려준다
DECISIVE_REVIEWS = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
_SHA = re.compile(r"[0-9a-f]{40}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")


class ReleaseRefused(Exception):
    """사전 검사 거부. 상태·외부 변경 없음. API는 `code`(오류 코드)와 `details`로 응답한다."""

    def __init__(self, code: str, reason: str, **details: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.details = {"reason": reason, **details}


@dataclass(frozen=True)
class ReleaseRequest:
    """docs/04 §4 releases body + 서버가 붙이는 principal·멱등 키."""

    run_id: str
    incident_id: str
    work_id: str
    proposal_id: str
    pr_number: int
    approved_merge_sha: str
    expected_incident_version: int
    expected_current_image_id: str
    approval_note: str
    principal: str
    idempotency_key: str

    @property
    def logical_key(self) -> str:
        return f"deploy:{self.work_id}:{self.approved_merge_sha}"

    def record(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "incident_id": self.incident_id,
            "work_id": self.work_id,
            "proposal_id": self.proposal_id,
            "pr_number": self.pr_number,
            "approved_merge_sha": self.approved_merge_sha,
            "expected_incident_version": self.expected_incident_version,
            "expected_current_image_id": self.expected_current_image_id,
            "approval_note": self.approval_note,
        }

    def problem(self) -> str | None:
        """형식 문제(API가 먼저 막지만 내부 호출도 같은 규칙을 쓴다)."""
        if not is_valid_run_id(self.run_id):
            return "run_id"
        for value, prefix in (
            (self.incident_id, "INC"),
            (self.work_id, "WORK"),
            (self.proposal_id, "PROP"),
        ):
            if not is_valid_entity_id(value, prefix):
                return prefix.lower()
        if not _SHA.fullmatch(self.approved_merge_sha):
            return "approved_merge_sha"
        if not _IMAGE_ID.fullmatch(self.expected_current_image_id):
            return "expected_current_image_id"
        if self.pr_number < 1 or self.expected_incident_version < 0:
            return "number"
        if not self.approval_note.strip():
            return "approval_note"
        return None


# ── run 배포 lock ─────────────────────────────────────────────


def lock_holder(tx: Tx, run_id: str) -> str | None:
    """이 run에서 배포·검증 중인 DEPLOY execution ID(없으면 None).

    reset·장애 주입(W19·W13)도 이 값을 보고 멈춘다.
    """
    state = checkpoints.get(tx, LOCK_INTEGRATION, f"lock:{run_id}") or {}
    holder = state.get("execution_id")
    return holder if isinstance(holder, str) else None


def _hold(tx: Tx, run_id: str, execution_id: str) -> None:
    checkpoints.put(
        tx, LOCK_INTEGRATION, f"lock:{run_id}", {"execution_id": execution_id, "since": tx.now}
    )


def _unhold(tx: Tx, run_id: str, execution_id: str) -> None:
    if lock_holder(tx, run_id) == execution_id:
        checkpoints.put(
            tx,
            LOCK_INTEGRATION,
            f"lock:{run_id}",
            {"execution_id": None, "released": execution_id, "released_at": tx.now},
        )


# ── 도우미 ─────────────────────────────────────────────────────


def _loads(value: str | None) -> dict[str, Any]:
    data = json.loads(value) if value else {}
    return data if isinstance(data, dict) else {}


def _project_commit() -> str | None:
    """빌드 레시피가 들어 있는 LineMedic 저장소 commit(H05 전의 레시피 기록)."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and _SHA.fullmatch(commit) else None


def build_recipe() -> dict[str, Any]:
    """빌드 레시피 기록. spec 08 §1의 `build_recipe_sha256`은 H05다.

    그 전에는 레시피 파일 경로·파일 hash와 LineMedic 저장소 commit을 남긴다.
    """
    return {
        "path": MES_DOCKERFILE.relative_to(REPO_ROOT).as_posix(),
        "file_sha256": sha256_hex({"dockerfile": MES_DOCKERFILE.read_text(encoding="utf-8")}),
        "project_commit": _project_commit(),
        "build_recipe_sha256": None,
    }


def restore_procedure(previous: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """사람이 실행할 복원 절차: 이전 image ID·설정으로 같은 이름의 MES를 다시 띄운다.

    자동으로 실행하지 않는다. 복원한 서비스도 업무 검증을 다시 통과하기 전에는 복구 완료가 아니다.
    """
    name, image, network = (
        previous.get("container"),
        previous.get("image_id"),
        previous.get("network"),
    )
    data_dir = previous.get("data_dir")
    if not (name and image and network and data_dir):
        return {"available": False, "reason": "previous_runtime_unrecorded"}
    commands = [
        ["docker", "rm", "--force", name],
        [
            "docker",
            "run",
            "--detach",
            *mes_container_options(name, run_id, network, Path(data_dir)),
            image,
        ],
    ]
    return {
        "available": True,
        "image_id": image,
        "commands": commands,
        "text": [shlex.join(command) for command in commands],
        "note": "사람이 실행한다. 복원한 서비스도 업무 검증을 다시 통과해야 복구 완료다.",
    }


def recurrence_signature(details: Mapping[str, Any]) -> RecurrenceSignature | None:
    """사건 signature(W07)로 재발 판정 signature를 만든다. `KeyError:field`는 오류 종류만 쓴다."""
    sig = details.get("signature") or {}
    error_type = str(sig.get("error_type") or "").split(":", 1)[0]
    top_frame, endpoint = sig.get("top_frame"), sig.get("endpoint")
    if not (error_type and isinstance(top_frame, str) and isinstance(endpoint, str)):
        return None
    return RecurrenceSignature(error_type=error_type, top_frame=top_frame, path=endpoint)


def identity_problem(
    info: Mapping[str, Any] | None,
    *,
    container_id: str | None,
    image_id: str,
    labels: Mapping[str, str],
) -> str | None:
    """host inspect로 본 container가 이번 배포 대상인가. 아니면 사유."""
    if not info:
        return "target_missing"
    if container_id is not None and info.get("Id") != container_id:
        return "container_id_mismatch"
    if info.get("Image") != image_id:
        return "image_mismatch"
    found = (info.get("Config") or {}).get("Labels") or {}
    if any(found.get(key) != value for key, value in labels.items()):
        return "label_mismatch"
    state = info.get("State") or {}
    if state.get("Running") is not True:
        return "not_running"
    return None


def identity_chain(tx: Tx, execution_id: str) -> dict[str, Any]:
    """spec 08 §1 identity chain을 DEPLOY execution과 연결된 verification에서 모은다."""
    execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
    if execution is None:
        raise LookupError(execution_id)
    request, result = _loads(execution["request_json"]), _loads(execution["result_json"])
    verification = tx.one(
        "SELECT * FROM verifications WHERE execution_id = ? ORDER BY started_at DESC, id DESC",
        (execution_id,),
    )
    stored = _loads(verification["result_json"]) if verification is not None else {}
    target = result.get("target") or {}
    return {
        "base_sha": request.get("base_sha"),
        "patch_sha256": request.get("patch_sha256"),
        "candidate_sha": request.get("candidate_sha"),
        "candidate_tree": request.get("candidate_tree"),
        "pr_number": request.get("pr_number"),
        "pr_head_sha": request.get("pr_head_sha"),
        "approved_merge_sha": request.get("approved_merge_sha"),
        "approved_tree": request.get("approved_tree"),
        "build_recipe": request.get("build_recipe"),
        "image_id": target.get("image_id"),
        "image_identity": target.get("image_identity"),
        "reported_repo_digests": target.get("reported_repo_digests"),
        "container_id": result.get("container_id"),
        "verification_id": verification["id"] if verification is not None else None,
        "verdict": verification["verdict"] if verification is not None else None,
        "contract_id": verification["contract_id"] if verification is not None else None,
        "contract_sha256": verification["contract_sha256"] if verification is not None else None,
        "fixture_sha256": stored.get("fixture_sha256"),
    }


def _background(job: Callable[[], Any]) -> None:
    threading.Thread(target=job, name="linemedic-release", daemon=True).start()


@dataclass(frozen=True)
class Subject:
    """DB에서 읽은 승인 대상(사건·work·제안·봇 PR execution)."""

    run_id: str
    incident_id: str
    work_id: str
    proposal_id: str
    service: str
    repository_id: int
    pr_execution_id: str
    pr_number: int
    head: str
    base: str
    base_sha: str
    candidate_sha: str
    candidate_tree: str
    patch_sha256: str | None
    new_test_path: str
    attempt_id: str


@dataclass
class _Job:
    execution_id: str
    run_id: str
    incident_id: str
    work_id: str
    request: dict[str, Any]
    intended_at: str
    phase: str = "prepare"  # prepare → stopped(이전 container 제거 뒤) → verifying
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def previous(self) -> dict[str, Any]:
        return self.request["runtime"]["previous"]


class ReleaseExecutor:
    def __init__(
        self,
        store: Store,
        *,
        port: GitHubPort,
        catalog: Catalog,
        docker: DockerPort,
        runner: Runner,
        mirror: Path,
        runs_dir: Path,
        clock: Clock,
        route_id: str,
        fetcher: CommitFetcher | None = None,
        dispatch: Callable[[Callable[[], Any]], None] | None = None,
        policy: BrokerPolicy | None = None,
        contract_path: Path = DEFAULT_CONTRACT,
        eval_dir: Path = EVAL_DIR,
        prober_image: str = PROBER_IMAGE,
        origin: str = DEFAULT_ORIGIN,
    ) -> None:
        self.store = store
        self.port = port
        self.catalog = catalog
        self.docker = docker
        self.runner = runner
        self.mirror = mirror
        self.runs_dir = runs_dir
        self.clock = clock
        self.route_id = route_id
        self.fetcher = fetcher or LocalOnlyFetcher()
        self.dispatch = dispatch or _background
        self.policy = policy or load_policy()
        self.contract_path = contract_path
        self.eval_dir = eval_dir
        self.prober_image = prober_image
        self.origin = origin

    # ── 1. 사전 검사 ──────────────────────────────────────────

    def approve(self, request: ReleaseRequest) -> dict[str, Any]:
        """사전 검사 1~7 뒤 INTENDED를 기록하고 배포를 dispatch한다. 거부는 `ReleaseRefused`."""
        problem = request.problem()
        if problem is not None:
            raise ReleaseRefused("INVALID_REQUEST", f"invalid_{problem}")
        with self.store.read() as tx:  # 7. 같은 논리 작업의 execution이 있으면 그 상태
            existing = tx.one(
                "SELECT * FROM executions WHERE logical_key = ?", (request.logical_key,)
            )
            if existing is not None:
                return self._existing(tx, existing, request)
            subject = self._subject(tx, request)
        try:
            self._catalog_check(subject)
            observed = self._github_checks(subject, request)
            previous = self._runtime_check(subject, request)
        except ReleaseRefused as exc:
            self._refused(subject, request, exc)
            raise
        return self._intend(subject, request, observed, previous)

    def _existing(self, tx: Tx, execution: Any, request: ReleaseRequest) -> dict[str, Any]:
        if (
            execution["operation"] != "DEPLOY"
            or execution["run_id"] != request.run_id
            or execution["incident_id"] != request.incident_id
        ):
            raise ReleaseRefused("STATE_CONFLICT", "logical_key_conflict")
        incident = tx.one("SELECT status FROM incidents WHERE id = ?", (request.incident_id,))
        work = tx.one("SELECT status FROM work_items WHERE id = ?", (execution["work_id"],))
        return {
            "execution_id": execution["id"],
            "logical_key": execution["logical_key"],
            "status": execution["status"],
            "stage": execution["stage"],
            "incident_status": incident["status"] if incident is not None else None,
            "work_status": work["status"] if work is not None else None,
            "reused": True,
        }

    def _subject(self, tx: Tx, request: ReleaseRequest) -> Subject:
        """1. operator 요청의 사건·work·제안·봇 PR이 DB와 맞고 사건이 PR_OPENED인가."""
        incident = tx.one("SELECT * FROM incidents WHERE id = ?", (request.incident_id,))
        if incident is None or incident["run_id"] != request.run_id:
            raise ReleaseRefused("RESOURCE_NOT_FOUND", "incident_not_found")
        run = tx.one("SELECT active FROM demo_runs WHERE id = ?", (request.run_id,))
        if run is None or run["active"] != 1:
            raise ReleaseRefused("STATE_CONFLICT", "run_inactive")
        if (
            incident["version"] != request.expected_incident_version
            or incident["status"] != "PR_OPENED"
        ):
            raise ReleaseRefused(
                "STATE_CONFLICT",
                "incident_not_ready",
                current_status=incident["status"],
                current_version=incident["version"],
            )
        work = tx.one(
            "SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?",
            (request.run_id, request.incident_id),
        )
        if work is None or work["id"] != request.work_id:
            raise ReleaseRefused("STATE_CONFLICT", "work_mismatch")
        if work["status"] != "WAITING_REVIEW":
            raise ReleaseRefused(
                "STATE_CONFLICT", "work_not_waiting_review", work_status=work["status"]
            )
        proposal = tx.one("SELECT * FROM proposals WHERE id = ?", (request.proposal_id,))
        if (
            proposal is None
            or (proposal["run_id"], proposal["incident_id"], proposal["work_id"])
            != (request.run_id, request.incident_id, request.work_id)
            or proposal["decision"] != "ALLOWED"
        ):
            raise ReleaseRefused("STATE_CONFLICT", "proposal_mismatch")
        pr_rows = tx.all(
            "SELECT * FROM executions WHERE operation = 'CREATE_PR' AND work_id = ?"
            " AND proposal_id = ? AND status = 'SUCCEEDED'",
            (request.work_id, request.proposal_id),
        )
        if len(pr_rows) != 1:
            raise ReleaseRefused("STATE_CONFLICT", "pr_execution_missing")
        pr_request, pr_result = (
            _loads(pr_rows[0]["request_json"]),
            _loads(pr_rows[0]["result_json"]),
        )
        if pr_result.get("pr_number") != request.pr_number:
            raise ReleaseRefused("STATE_CONFLICT", "pr_mismatch")
        candidate = _loads(proposal["checks_json"]).get("candidate") or {}
        if (candidate.get("candidate_sha"), candidate.get("candidate_tree")) != (
            pr_request.get("candidate_sha"),
            pr_request.get("candidate_tree"),
        ):
            raise ReleaseRefused("STATE_CONFLICT", "candidate_mismatch")
        holder = lock_holder(tx, request.run_id)
        if holder is not None:
            raise ReleaseRefused("STATE_CONFLICT", "release_locked", holder_execution_id=holder)
        action = _loads(proposal["payload_json"]).get("action") or {}
        return Subject(
            run_id=request.run_id,
            incident_id=request.incident_id,
            work_id=request.work_id,
            proposal_id=request.proposal_id,
            service=incident["service"],
            repository_id=work["repository_id"],
            pr_execution_id=pr_rows[0]["id"],
            pr_number=request.pr_number,
            head=pr_request["head"],
            base=pr_request["base"],
            base_sha=pr_request["base_sha"],
            candidate_sha=pr_request["candidate_sha"],
            candidate_tree=pr_request["candidate_tree"],
            patch_sha256=candidate.get("patch_sha256"),
            new_test_path=action["new_test_path"],
            attempt_id=proposal["attempt_id"],
        )

    def _catalog_check(self, subject: Subject) -> None:
        """2. repo·PR·base branch가 server catalog·이 run과 일치한다."""
        try:
            entry = self.catalog.require_repository(subject.repository_id)
        except CatalogError:
            raise ReleaseRefused("PROTECTION_UNAVAILABLE", "repository_not_registered") from None
        if (entry.id, entry.full_name, entry.service_id) != (
            self.port.repository_id,
            self.port.full_name,
            subject.service,
        ):
            raise ReleaseRefused("STATE_CONFLICT", "catalog_mismatch")
        if (subject.head, subject.base) != branches(
            subject.run_id, subject.incident_id, subject.proposal_id
        ):
            raise ReleaseRefused("STATE_CONFLICT", "branch_mismatch")

    def _read(self, call: Callable[..., GitHubResponse], *args: Any, **kwargs: Any) -> Any:
        try:
            return call(*args, **kwargs)
        except GitHubError as exc:
            raise ReleaseRefused(
                "LOOKUP_INCOMPLETE", "github_lookup_failed", error=type(exc).__name__
            ) from None

    def _approvals(self, number: int, bot_id: int, candidate_sha: str) -> list[dict[str, Any]]:
        """봇이 아닌 리뷰어별 마지막 결정 리뷰가 candidate head의 APPROVED인 것."""
        reviews: list[dict[str, Any]] = []
        for page in range(1, MAX_REVIEW_PAGES + 1):
            response = self._read(self.port.list_pull_reviews, number, page=page)
            reviews.extend(r for r in response.data or [] if isinstance(r, dict))
            if not response.has_next:
                break
        else:
            raise ReleaseRefused("LOOKUP_INCOMPLETE", "review_listing_incomplete")
        latest: dict[int, dict[str, Any]] = {}
        for review in reviews:  # 오래된 것부터
            reviewer = (review.get("user") or {}).get("id")
            if isinstance(reviewer, int) and reviewer != bot_id:
                if review.get("state") in DECISIVE_REVIEWS:
                    latest[reviewer] = review
        return [
            {
                "reviewer_id": reviewer,
                "review_id": review.get("id"),
                "commit_id": review.get("commit_id"),
                "submitted_at": review.get("submitted_at"),
            }
            for reviewer, review in sorted(latest.items())
            if review.get("state") == "APPROVED" and review.get("commit_id") == candidate_sha
        ]

    def _last_push_rule(self, base: str) -> tuple[bool | None, str]:
        """최신 변경 승인(require_last_push_approval)이 실제로 걸려 있는가. 기록만 한다."""
        try:
            rules = self.port.get_branch_rules(base).data or []
        except GitHubError as exc:
            return None, f"lookup_failed:{type(exc).__name__}"
        values = [
            (rule.get("parameters") or {}).get("require_last_push_approval")
            for rule in rules
            if isinstance(rule, dict) and rule.get("type") == "pull_request"
        ]
        if not values:
            return None, "no_ruleset_pull_request_rule"
        return any(value is True for value in values), "ruleset"

    def _github_checks(self, subject: Subject, request: ReleaseRequest) -> dict[str, Any]:
        """3~5. GitHub에서 merged·최종 merge SHA·PR head·리뷰·merge tree를 조회만 한다."""
        pull = self._read(self.port.get_pull, request.pr_number).data
        if not isinstance(pull, dict):
            raise ReleaseRefused("LOOKUP_INCOMPLETE", "pull_unreadable")
        if pull.get("merged") is not True:  # 머지 전 merge_commit_sha(test merge)는 쓰지 않는다
            raise ReleaseRefused("STATE_CONFLICT", "pr_not_merged", pr_state=pull.get("state"))
        if pull.get("merge_commit_sha") != request.approved_merge_sha:
            raise ReleaseRefused(
                "SOURCE_CHANGED",
                "merge_sha_mismatch",
                merge_commit_sha=pull.get("merge_commit_sha"),
            )
        bot_id = (self._read(self.port.get_identity).data or {}).get("id")
        head, base = pull.get("head") or {}, pull.get("base") or {}
        if (pull.get("number"), (pull.get("user") or {}).get("id"), head.get("ref")) != (
            request.pr_number,
            bot_id,
            subject.head,
        ) or base.get("ref") != subject.base:
            raise ReleaseRefused("SOURCE_CHANGED", "pr_identity_mismatch")
        if head.get("sha") != subject.candidate_sha:
            raise ReleaseRefused("SOURCE_CHANGED", "head_changed", pr_head_sha=head.get("sha"))
        approvals = self._approvals(request.pr_number, bot_id, subject.candidate_sha)
        if not approvals:
            raise ReleaseRefused("STATE_CONFLICT", "review_not_on_candidate")
        commit = self._read(self.port.get_commit, request.approved_merge_sha).data or {}
        tree = (commit.get("tree") or {}).get("sha")
        if commit.get("sha") != request.approved_merge_sha:
            raise ReleaseRefused("LOOKUP_INCOMPLETE", "commit_unreadable")
        if tree != subject.candidate_tree:
            raise ReleaseRefused(
                "SOURCE_CHANGED",
                "tree_mismatch",
                approved_tree=tree,
                candidate_checks_invalidated=True,
            )
        last_push, observation = self._last_push_rule(subject.base)
        return {
            "bot_id": bot_id,
            "pr_head_sha": head.get("sha"),
            "approved_tree": tree,
            "merged_at": pull.get("merged_at"),
            "approvals": approvals,
            "require_last_push_approval": last_push,
            "rule_observation": observation,
        }

    def _runtime_check(self, subject: Subject, request: ReleaseRequest) -> dict[str, Any]:
        """6. 지금 이 run의 MES container image가 요청의 예상값과 같은가(host inspect)."""
        names = resource_names(subject.run_id)
        name, network = names["container"], names["network"]
        exists = self.docker.container_exists(name)
        if exists is None:
            raise ReleaseRefused("LOOKUP_INCOMPLETE", "docker_lookup_failed")
        info = self.docker.inspect(name) if exists else None
        if not info:
            raise ReleaseRefused("SOURCE_CHANGED", "runtime_missing")
        if info.get("Image") != request.expected_current_image_id:
            raise ReleaseRefused(
                "SOURCE_CHANGED", "image_changed", current_image_id=info.get("Image")
            )
        if (info.get("HostConfig") or {}).get("NetworkMode") != network:
            raise ReleaseRefused("SOURCE_CHANGED", "runtime_network_changed")
        data_dir = next(
            (m.get("Source") for m in info.get("Mounts") or [] if m.get("Destination") == "/data"),
            None,
        )
        return {
            "container": name,
            "container_id": info.get("Id"),
            "image_id": info.get("Image"),
            "network": network,
            "data_dir": data_dir,
        }

    def _refused(self, subject: Subject, request: ReleaseRequest, exc: ReleaseRefused) -> None:
        """외부 관찰 뒤 거부를 감사 기록에 남긴다(상태 전이 없음)."""
        with self.store.tx() as tx:
            audit.append(
                tx,
                subject.run_id,
                subject.incident_id,
                Actor.RELEASE_EXECUTOR,
                "RELEASE_REFUSED",
                {
                    "code": exc.code,
                    **exc.details,
                    "pr_number": request.pr_number,
                    "approved_merge_sha": request.approved_merge_sha,
                    "requested_by": request.principal,
                },
            )

    # ── 2. INTENDED ───────────────────────────────────────────

    def _intend(
        self,
        subject: Subject,
        request: ReleaseRequest,
        observed: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        execution_id = new_id("EXE")
        with self.store.tx() as tx:
            existing = tx.one(
                "SELECT * FROM executions WHERE logical_key = ?", (request.logical_key,)
            )
            if existing is not None:  # 그 사이 같은 승인이 먼저 기록됐다
                return self._existing(tx, existing, request)
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (subject.incident_id,))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (subject.work_id,))
            if (incident["status"], incident["version"]) != (
                "PR_OPENED",
                request.expected_incident_version,
            ) or work["status"] != "WAITING_REVIEW":
                raise ReleaseRefused(
                    "STATE_CONFLICT",
                    "incident_not_ready",
                    current_status=incident["status"],
                    current_version=incident["version"],
                )
            holder = lock_holder(tx, subject.run_id)
            if holder is not None:
                raise ReleaseRefused("STATE_CONFLICT", "release_locked", holder_execution_id=holder)
            record = {
                "service": subject.service,
                "repository": self.port.full_name,
                "repository_id": self.port.repository_id,
                "proposal_id": subject.proposal_id,
                "pr_execution_id": subject.pr_execution_id,
                "pr_number": subject.pr_number,
                "head": subject.head,
                "base": subject.base,
                "base_sha": subject.base_sha,
                "patch_sha256": subject.patch_sha256,
                "candidate_sha": subject.candidate_sha,
                "candidate_tree": subject.candidate_tree,
                "new_test_path": subject.new_test_path,
                "pr_head_sha": observed["pr_head_sha"],
                "approved_merge_sha": request.approved_merge_sha,
                "approved_tree": observed["approved_tree"],
                "merged_at": observed["merged_at"],
                "review": {
                    "approvals": observed["approvals"],
                    "require_last_push_approval": observed["require_last_push_approval"],
                    "rule_observation": observed["rule_observation"],
                },
                "expected_current_image_id": request.expected_current_image_id,
                "runtime": {"previous": previous},
                "build_recipe": build_recipe(),
                # 사람 제안(W13 manual_integration)은 attempt 기록의 origin을 따른다
                "origin": attempt_origin(
                    tx, subject.run_id, subject.incident_id, subject.attempt_id, self.origin
                ),
                "approval": {
                    "principal": request.principal,
                    "note": request.approval_note,
                    "approved_at": tx.now,
                },
            }
            tx.execute(
                "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
                " logical_key, idempotency_key, request_sha256, status, stage, intended_at,"
                " updated_at, request_json, result_json) VALUES (?, ?, ?, ?, ?, 'DEPLOY', ?, ?, ?,"
                " 'INTENDED', 'intended', ?, ?, ?, '{}')",
                (
                    execution_id,
                    subject.run_id,
                    subject.incident_id,
                    subject.work_id,
                    subject.proposal_id,
                    request.logical_key,
                    request.idempotency_key,
                    sha256_hex(request.record()),
                    tx.now,
                    tx.now,
                    canonical_dumps(record),
                ),
            )
            details = {
                "execution_id": execution_id,
                "pr_number": subject.pr_number,
                "approved_merge_sha": request.approved_merge_sha,
                "requested_by": request.principal,
            }
            moved = coupled_transition(
                tx,
                incident_id=subject.incident_id,
                expected_incident_version=incident["version"],
                incident_to="DEPLOYING",
                work_id=subject.work_id,
                expected_work_version=work["version"],
                work_to="WAITING_VERIFICATION",
                actor=Actor.RELEASE_EXECUTOR,
                details=details,
            )
            assert moved.outbox_event is None
            _hold(tx, subject.run_id, execution_id)
            audit.append(
                tx,
                subject.run_id,
                subject.incident_id,
                Actor.RELEASE_EXECUTOR,
                "RELEASE_APPROVED",
                {**details, "note": request.approval_note, "logical_key": request.logical_key},
            )
        self.dispatch(lambda: self.run(execution_id))
        return {
            "execution_id": execution_id,
            "logical_key": request.logical_key,
            "status": "INTENDED",
            "stage": "intended",
            "incident_status": "DEPLOYING",
            "work_status": "WAITING_VERIFICATION",
            "reused": False,
        }

    # ── 3. 배포 ───────────────────────────────────────────────

    def _write(self, tx: Tx, execution_id: str, status: str, stage: str, result: dict) -> None:
        row = tx.one("SELECT result_json FROM executions WHERE id = ?", (execution_id,))
        merged = {**_loads(row["result_json"]), **result}
        tx.execute(
            "UPDATE executions SET status = ?, stage = ?, updated_at = ?, result_json = ?"
            " WHERE id = ?",
            (status, stage, tx.now, canonical_dumps(merged), execution_id),
        )

    def _stage(self, job: _Job, stage: str, result: dict[str, Any]) -> None:
        job.result.update(result)
        with self.store.tx() as tx:
            self._write(tx, job.execution_id, "RUNNING", stage, result)

    def run(self, execution_id: str) -> dict[str, Any]:
        """INTENDED 배포 하나를 끝까지 진행한다(트랜잭션 밖 외부 호출). 요약을 돌려준다."""
        with self.store.tx() as tx:
            execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
            if (
                execution is None
                or execution["operation"] != "DEPLOY"
                or execution["status"] != "INTENDED"
            ):
                status = execution["status"] if execution is not None else None
                return {"execution_id": execution_id, "outcome": "NOT_INTENDED", "status": status}
            self._write(tx, execution_id, "RUNNING", "fetch", {"started_at": tx.now})
        job = _Job(
            execution_id=execution_id,
            run_id=execution["run_id"],
            incident_id=execution["incident_id"],
            work_id=execution["work_id"],
            request=_loads(execution["request_json"]),
            intended_at=execution["intended_at"],
        )
        try:
            return self._deploy(job)
        except Exception as exc:  # noqa: BLE001 — 어떤 오류도 DEPLOYING·lock을 남기지 않게 닫는다
            detail = f"unexpected_error:{type(exc).__name__}"
            if job.phase == "prepare":
                return self._fail(job, "release_error", detail, "VALIDATION_FAILED")
            if job.phase == "stopped":
                return self._unknown(job, "release_error", detail)
            raise

    def _deploy(self, job: _Job) -> dict[str, Any]:
        req = job.request
        fetched = self.fetcher.fetch(self.mirror, req["approved_merge_sha"])
        if fetched.status != "FETCHED":
            return self._fail(job, "fetch", fetched.detail or "fetch_failed", "LOOKUP_INCOMPLETE")
        self._stage(job, "checkout", {"fetch": fetched.detail})

        root = self.runs_dir / job.run_id / "releases" / job.execution_id
        try:
            trees = prepare_release_trees(
                mirror=self.mirror,
                workdir=root / "checkout",
                base_sha=req["base_sha"],
                merge_sha=req["approved_merge_sha"],
                new_test_path=req["new_test_path"],
            )
        except CandidateError as exc:
            return self._fail(job, "checkout", exc.reason, "VALIDATION_FAILED")
        if trees.final_tree != req["candidate_tree"]:  # 신뢰 mirror에서 다시 본 tree
            return self._fail(
                job,
                "checkout",
                "tree_mismatch",
                "SOURCE_CHANGED",
                extra={"approved_tree": trees.final_tree},
            )
        self._stage(job, "recheck", {"trees": trees.record()})

        image_problem = self.runner.image_problem()
        if image_problem is not None:
            return self._fail(job, "recheck", image_problem, "VALIDATION_FAILED")
        outcome = GateOutcome()
        passed = run_protected_stages(
            self.runner,
            outcome,
            trees=trees.trees,
            workdir=root,
            regression=self.policy.rules.regression_tests,
            new_test=req["new_test_path"],
            name=lambda stage: f"lm-release-{job.execution_id.lower()}-{stage.lower()}",
            labels={
                "linemedic.role": "runner",
                "linemedic.run": job.run_id,
                "linemedic.execution": job.execution_id,
            },
        )
        if not passed:
            return self._fail(
                job,
                "recheck",
                f"{outcome.code}:{outcome.reason}",
                "VALIDATION_FAILED",
                extra={"checks": outcome.checks},
            )
        self._stage(job, "build", {"checks": outcome.checks})

        tag = f"linemedic-mes:release-{job.run_id}-{req['approved_merge_sha'][:12]}"
        try:  # 신뢰 레시피 + final tree만(repo의 Dockerfile·hook을 쓰지 않는다)
            image_id = self.docker.build(trees.trees["final"], MES_DOCKERFILE, tag)
        except DockerError as exc:
            return self._fail(
                job,
                "build",
                "build_failed",
                "VALIDATION_FAILED",
                extra={"detail": mask_secrets(str(exc))[-300:]},
            )
        inspected = self.docker.image_inspect(image_id) or {}
        if inspected.get("Id") != image_id:
            return self._fail(job, "build", "built_image_missing", "VALIDATION_FAILED")
        previous = job.previous
        name, network = previous["container"], previous["network"]
        data_dir = root / "mes-data"
        data_files = write_contract_data(data_dir)
        reported = [d for d in inspected.get("RepoDigests") or [] if isinstance(d, str)]
        target = {
            "container": name,
            "network": network,
            "image_id": image_id,
            "image_tag": tag,
            # 로컬에서 빌드했고 registry에 올리지 않았다: 실행 대상 identity는 local image ID다.
            # containerd 이미지 저장소는 로컬 빌드에도 digest를 보고하지만 registry digest가 아니다
            "image_identity": "local_image_id",
            "reported_repo_digests": reported,
            "data_dir": str(data_dir.resolve()),
            "data_files": [str(path.resolve()) for path in data_files],
        }

        current = self._observe(name)  # 승인 때 본 container 그대로인가(다른 변경 덮어쓰기 금지)
        if current["exists"] is None:
            return self._fail(job, "deploy", "docker_lookup_failed", "LOOKUP_INCOMPLETE")
        if (current.get("container_id"), current.get("image_id")) != (
            previous["container_id"],
            previous["image_id"],
        ):
            return self._fail(
                job,
                "deploy",
                "runtime_changed",
                "SOURCE_CHANGED",
                environment="changed",
                extra={"observed": current},
            )
        restore = restore_procedure(previous, job.run_id)
        self._stage(job, "deploy", {"target": target, "restore": restore})

        stopped = self.docker.stop(name)
        if stopped.returncode != 0:
            after = self._observe(name)
            if after["exists"] is None:
                return self._unknown(job, "stop", "stop_result_unknown")
            if after["exists"]:
                if after.get("container_id") == previous["container_id"]:
                    return self._fail(
                        job, "stop", "stop_failed", "VALIDATION_FAILED", environment="unchanged"
                    )
                return self._unknown(job, "stop", "stop_result_unknown")
        job.phase = "stopped"

        labels = {
            "linemedic.run_id": job.run_id,
            "linemedic.role": "mes",
            "linemedic.incident_id": job.incident_id,
            "linemedic.execution_id": job.execution_id,
        }
        options = [
            *mes_container_options(name, job.run_id, network, data_dir),
            "--label",
            f"linemedic.incident_id={job.incident_id}",
            "--label",
            f"linemedic.execution_id={job.execution_id}",
        ]
        try:
            container_id = self.docker.run(options, image_id)  # image ID로 기동(태그 아님)
        except DockerError as exc:
            if exc.returncode == DOCKER_TIMEOUT_EXIT:
                return self._unknown(job, "start", "start_timeout")
            return self._fail(
                job,
                "start",
                "start_failed",
                "VALIDATION_FAILED",
                environment="previous_removed",
                extra={"detail": mask_secrets(str(exc))[-300:], "observed": self._observe(name)},
            )
        info = self.docker.inspect(name)
        problem = identity_problem(
            info, container_id=container_id, image_id=image_id, labels=labels
        )
        if problem is not None:
            return self._unknown(job, "inspect", problem)

        verification_id = new_id("VER")
        contract, contract_sha256 = load_contract(self.contract_path)
        with self.store.tx() as tx:
            self._write(
                tx,
                job.execution_id,
                "SUCCEEDED",
                "deployed",
                {
                    "container_id": container_id,
                    "deployed_at": tx.now,
                    "verification_id": verification_id,
                },
            )
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (job.incident_id,))
            transition_incident(
                tx,
                job.incident_id,
                incident["version"],
                "VERIFYING",
                Actor.RELEASE_EXECUTOR,
                details={"execution_id": job.execution_id, "verification_id": verification_id},
            )
            register_running(
                tx,
                verification_id=verification_id,
                run_id=job.run_id,
                incident_id=job.incident_id,
                execution_id=job.execution_id,
                origin=req.get("origin", self.origin),
                contract_id=contract.contract_id,
                contract_sha256=contract_sha256,
                target={**target, "container_id": container_id},
            )
            audit.append(
                tx,
                job.run_id,
                job.incident_id,
                Actor.RELEASE_EXECUTOR,
                "RELEASE_DEPLOYED",
                {
                    "execution_id": job.execution_id,
                    "image_id": image_id,
                    "container_id": container_id,
                    "verification_id": verification_id,
                },
            )
        job.phase = "verifying"
        return self._verify(job.execution_id, verification_id)

    def _observe(self, name: str) -> dict[str, Any]:
        exists = self.docker.container_exists(name)
        info = self.docker.inspect(name) if exists else None
        if exists and not info:
            exists = None  # 있다고 했는데 읽지 못했다
        return {
            "exists": exists,
            "container_id": (info or {}).get("Id"),
            "image_id": (info or {}).get("Image"),
            "running": ((info or {}).get("State") or {}).get("Running"),
        }

    def _environment(self, job: _Job) -> str:
        """배포 전 실패일 때 이전 container가 그대로인지 확인한 결과."""
        previous = job.previous
        current = self._observe(previous["container"])
        if current["exists"] is None:
            return "unknown"
        if not current["exists"]:
            return "previous_missing"
        same = (current["container_id"], current["image_id"]) == (
            previous["container_id"],
            previous["image_id"],
        )
        return "unchanged" if same and current["running"] else "changed"

    def _fail(
        self,
        job: _Job,
        stage: str,
        reason: str,
        blocker: str,
        *,
        environment: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """새 배포가 실행 대상이 되지 못했다: FAILED, ESCALATED/BLOCKED, RECOVERY_NOT_VERIFIED."""
        environment = environment or self._environment(job)
        result = {"failure": {"stage": stage, "reason": reason, "environment": environment}}
        result.update(extra or {})
        try:  # 계약 파일을 읽지 못해도 실패 기록·이관은 남긴다
            contract_id = load_contract(self.contract_path)[0].contract_id
        except Exception:  # noqa: BLE001
            contract_id = self.contract_path.stem
        with self.store.tx() as tx:
            self._write(tx, job.execution_id, "FAILED", stage, result)
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (job.incident_id,))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (job.work_id,))
            details = {"execution_id": job.execution_id, "stage": stage, "reason": reason}
            moved = coupled_transition(
                tx,
                incident_id=job.incident_id,
                expected_incident_version=incident["version"],
                incident_to="ESCALATED",
                work_id=job.work_id,
                expected_work_version=work["version"],
                work_to="BLOCKED",
                actor=Actor.RELEASE_EXECUTOR,
                reason=blocker,
                details=details,
            )
            assert moved.outbox_event == "RECOVERY_NOT_VERIFIED"
            payload = {
                "schema_version": "linemedic.v4",
                "event_type": moved.outbox_event,
                "run_id": job.run_id,
                "incident_id": job.incident_id,
                "work_id": job.work_id,
                "generation": work["generation"],
                "repository_id": work["repository_id"],
                "issue_number": work["issue_number"],
                "execution_id": job.execution_id,
                "verdict": "NOT_DEPLOYED",
                "reason": reason,
                "stage": stage,
                "environment": environment,
                "blocker_code": blocker,
                "contract_id": contract_id,
                "samples_completed": 0,
                "observation_complete": False,
                "started_at": job.intended_at,
                "ended_at": tx.now,
            }
            outbox.enqueue(tx, work, moved.outbox_event, payload, self.route_id)
            _unhold(tx, job.run_id, job.execution_id)
            audit.append(
                tx,
                job.run_id,
                job.incident_id,
                Actor.RELEASE_EXECUTOR,
                "RELEASE_FAILED",
                {**details, "environment": environment, "blocker_code": blocker},
            )
        return {
            "execution_id": job.execution_id,
            "status": "FAILED",
            "stage": stage,
            "reason": reason,
            "environment": environment,
        }

    def _unknown(self, job: _Job, stage: str, observation: str) -> dict[str, Any]:
        """배포 결과를 모른다: UNKNOWN·EXECUTION_UNKNOWN. 다시 배포하지 않고 운영자가 조정한다.

        lock은 유지한다(실제 대상이 확인되기 전 다른 배포·주입·reset 금지).
        """
        restore = restore_procedure(job.previous, job.run_id)
        with self.store.tx() as tx:
            self._write(
                tx,
                job.execution_id,
                "UNKNOWN",
                stage,
                {"observation": observation, "restore": restore},
            )
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (job.incident_id,))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (job.work_id,))
            coupled_transition(
                tx,
                incident_id=job.incident_id,
                expected_incident_version=incident["version"],
                incident_to="EXECUTION_UNKNOWN",
                work_id=job.work_id,
                expected_work_version=work["version"],
                work_to="EXECUTION_UNKNOWN",
                actor=Actor.RELEASE_EXECUTOR,
                details={"execution_id": job.execution_id, "observation": observation},
            )
            audit.append(
                tx,
                job.run_id,
                job.incident_id,
                Actor.RELEASE_EXECUTOR,
                "RELEASE_UNKNOWN",
                {"execution_id": job.execution_id, "stage": stage, "observation": observation},
            )
        return {
            "execution_id": job.execution_id,
            "status": "UNKNOWN",
            "stage": stage,
            "observation": observation,
        }

    # ── 4. 업무 검증 ──────────────────────────────────────────

    def _verify(self, execution_id: str, verification_id: str) -> dict[str, Any]:
        """배포한 container를 신뢰 prober로 검증하고 verifier 주체로 저장한다. 끝나면 lock 해제."""
        with self.store.read() as tx:
            execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
            running = tx.one("SELECT * FROM verifications WHERE id = ?", (verification_id,))
            incident = tx.one(
                "SELECT details_json FROM incidents WHERE id = ?", (execution["incident_id"],)
            )
        run_id = execution["run_id"]
        result_record = _loads(execution["result_json"])
        target = {
            **(result_record.get("target") or {}),
            "container_id": result_record.get("container_id"),
        }
        name, network = target["container"], target["network"]
        prober = f"linemedic-prober-{run_id}-{execution_id.lower()}"
        observer: ContainerObserver | None = None
        run: VerificationRun | None = None
        interrupted: BaseException | None = None
        samples_required = 4
        result: VerificationResult
        try:
            contract, contract_sha256 = load_contract(self.contract_path)
            samples_required = contract.observation.samples
            cases = resolve_cases(contract, self.eval_dir)
            guard = FixtureGuard(
                [self.contract_path, HOLDOUT_FIXTURE, *map(Path, target.get("data_files", []))]
            )
            self.docker.run(
                prober_options(prober, run_id, network), self.prober_image, ["sleep", "infinity"]
            )
            http = ProberHttp(self.docker, prober, f"http://{name}:8000")
            healthy = wait_until_healthy(http, self.clock, self.clock.sleep)
            observer = ContainerObserver(
                self.docker, name, recurrence_signature(_loads(incident["details_json"]))
            )
            identity = observer.start(since=to_rfc3339(self.clock.utc_now()))
            run = VerificationRun(
                contract=contract,
                contract_sha256=contract_sha256,
                cases=cases,
                http=http,
                observer=observer,
                clock=self.clock,
                origin=running["origin"],
                target={**target, **identity, "execution_id": execution_id},
                fixture_guard=guard,
                verification_id=verification_id,
            )
            deployed = (target["container_id"], target["image_id"])
            if not healthy:
                result = run.abort("mes_not_healthy")
            elif (identity["container_id"], identity["image_id"]) != deployed:
                result = run.abort("identity_changed_before_t0")
            else:
                result = drive(run)
        except Exception as exc:  # noqa: BLE001 — 판정 전 오류는 INCONCLUSIVE로 닫는다
            detail = f"verification_setup_failed:{type(exc).__name__}"
            result = (
                run.abort(detail)
                if run is not None
                else self._unfinished(running, samples_required, detail)
            )
        except BaseException as exc:  # Ctrl-C 등: 기록한 뒤 다시 올린다
            interrupted = exc
            detail = f"interrupted:{type(exc).__name__}"
            result = (
                run.abort(detail)
                if run is not None
                else self._unfinished(running, samples_required, detail)
            )
        finally:
            if observer is not None:
                observer.stop()
            self.docker.stop(prober)
        summary = self._persist(execution, result)
        if interrupted is not None:
            raise interrupted
        return summary

    def _unfinished(self, running: Any, samples_required: int, detail: str) -> VerificationResult:
        return unfinished_result(
            running,
            samples_required=samples_required,
            ended_at=to_rfc3339(self.clock.utc_now()),
            detail=detail,
        )

    def _persist(self, execution: Any, result: VerificationResult) -> dict[str, Any]:
        run_id, incident_id = execution["run_id"], execution["incident_id"]
        with self.store.tx() as tx:
            incident = tx.one("SELECT version FROM incidents WHERE id = ?", (incident_id,))
            persisted = persist_result(
                tx,
                result,
                run_id=run_id,
                incident_id=incident_id,
                expected_incident_version=incident["version"],
                route_id=self.route_id,
                execution_id=execution["id"],
            )
            _unhold(tx, run_id, execution["id"])
        out_dir = self.runs_dir / run_id / "verifications"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{result.verification_id}.json").write_text(
            json.dumps(persisted.result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "execution_id": execution["id"],
            "status": "SUCCEEDED",
            "stage": "verified",
            "verification_id": result.verification_id,
            "verdict": result.verdict,
            "verification_reason": result.reason,
            "incident_status": persisted.incident_status,
            "work_status": persisted.work_status,
        }

    # ── 조정·재시작 ───────────────────────────────────────────

    def reconcile(self, execution_id: str) -> dict[str, Any]:
        """UNKNOWN DEPLOY를 실제 container·image·라벨 조회로만 조정한다. 다시 배포하지 않는다.

        - FOUND: 이번 image ID·execution 라벨의 container가 실행 중 → SUCCEEDED,
          EXECUTION_UNKNOWN → VERIFYING·WAITING_VERIFICATION(reconciler), 새 검증 RUNNING → verifier
        - CONFIRMED_ABSENT: container가 없거나 승인 때의 이전 container 그대로 → ESCALATED·BLOCKED
          (EXTERNAL_RESULT_UNKNOWN, 자동 재배포 없음)
        - CONFLICT: 이번 대상도 이전 container도 아닌 것이 실행 중
          → ESCALATED·BLOCKED(SOURCE_CHANGED)
        - INCONCLUSIVE: docker 조회 실패 → 기록만(lock 유지)
        """
        with self.store.read() as tx:
            execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
        if execution is None:
            raise LookupError(execution_id)
        if execution["status"] != "UNKNOWN":
            return {
                "execution_id": execution_id,
                "outcome": "NOT_UNKNOWN",
                "status": execution["status"],
            }
        request, stored = _loads(execution["request_json"]), _loads(execution["result_json"])
        previous = request["runtime"]["previous"]
        target = stored.get("target") or {}
        name = previous["container"]
        observed = self._observe(name)
        labels = {
            "linemedic.run_id": execution["run_id"],
            "linemedic.incident_id": execution["incident_id"],
            "linemedic.execution_id": execution_id,
        }
        info = self.docker.inspect(name) if observed["exists"] else None
        if observed["exists"] is None:
            outcome = "INCONCLUSIVE"
        elif not observed["exists"]:
            outcome = "CONFIRMED_ABSENT"
        elif (
            target.get("image_id")
            and identity_problem(
                info, container_id=None, image_id=target["image_id"], labels=labels
            )
            is None
        ):
            outcome = "FOUND"
        elif (observed["container_id"], observed["image_id"]) == (
            previous["container_id"],
            previous["image_id"],
        ):
            outcome = "CONFIRMED_ABSENT"  # 이번 배포는 없고 이전 container가 그대로다
        else:
            outcome = "CONFLICT"
        record = {"outcome": outcome, "container": name, **observed}
        verification_id = None
        with self.store.tx() as tx:
            current = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
            if current["status"] != "UNKNOWN":  # 그 사이 다른 조정이 끝냈다
                return {
                    "execution_id": execution_id,
                    "outcome": "NOT_UNKNOWN",
                    "status": current["status"],
                }
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (execution["incident_id"],))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (execution["work_id"],))
            waiting = (incident["status"], work["status"]) == ("EXECUTION_UNKNOWN",) * 2
            reconcile = {**record, "checked_at": tx.now}
            if outcome == "FOUND":
                verification_id = new_id("VER") if waiting else None
                self._write(
                    tx,
                    execution_id,
                    "SUCCEEDED",
                    "reconciled",
                    {
                        "container_id": observed["container_id"],
                        "reconcile": reconcile,
                        "verification_id": verification_id,
                    },
                )
                if waiting:
                    coupled_transition(
                        tx,
                        incident_id=incident["id"],
                        expected_incident_version=incident["version"],
                        incident_to="VERIFYING",
                        work_id=work["id"],
                        expected_work_version=work["version"],
                        work_to="WAITING_VERIFICATION",
                        actor=Actor.RECONCILER,
                        details={"execution_id": execution_id, "verification_id": verification_id},
                    )
                    contract, contract_sha256 = load_contract(self.contract_path)
                    register_running(
                        tx,
                        verification_id=verification_id,
                        run_id=execution["run_id"],
                        incident_id=execution["incident_id"],
                        execution_id=execution_id,
                        origin=request.get("origin", self.origin),
                        contract_id=contract.contract_id,
                        contract_sha256=contract_sha256,
                        target={**target, "container_id": observed["container_id"]},
                    )
            else:
                self._write(tx, execution_id, "UNKNOWN", current["stage"], {"reconcile": reconcile})
            audit.append(
                tx,
                execution["run_id"],
                execution["incident_id"],
                Actor.RECONCILER,
                "DEPLOY_EXECUTION_RECONCILED",
                {"execution_id": execution_id, **record},
            )
            if outcome in ("CONFIRMED_ABSENT", "CONFLICT") and waiting:
                absent = outcome == "CONFIRMED_ABSENT"
                _block(
                    tx,
                    self.route_id,
                    incident,
                    work,
                    reason="EXTERNAL_RESULT_UNKNOWN" if absent else "SOURCE_CHANGED",
                    stage="external_write",
                    reason_detail=(
                        "배포 결과가 불명이었고 조회로 이번 배포 대상이 실행 중이 아님을 확인했다."
                        " 자동으로 다시 배포하지 않는다"
                        if absent
                        else "배포 결과가 불명이었고 이번 배포 대상도 이전 container도 아닌 것이"
                        " 실행 중이다. 덮어쓰지 않는다"
                    ),
                    evidence_ids=[],
                    details={"execution_id": execution_id, "reconcile": outcome},
                    attempted=[f"reconcile {execution_id} → {outcome}"],
                    next_steps=[
                        "실제 MES 상태를 확인하고 execution 기록의 복원 절차 실행 여부를 판단"
                    ],
                    actor=Actor.RECONCILER,
                    side_effect_state="OBSERVED" if observed["exists"] else "NONE",
                    side_effect_identities=[f"container {name} {observed['container_id']}"]
                    if observed["exists"]
                    else [],
                )
                _unhold(tx, execution["run_id"], execution_id)
                record["escalated"] = True
        if verification_id is not None:
            self.dispatch(lambda: self._verify(execution_id, verification_id))
            record["verification_id"] = verification_id
        return {"execution_id": execution_id, **record}

    def recover(self) -> dict[str, list[str]]:
        """재시작 때 결과를 모르는 배포·검증을 닫는다.

        INTENDED·RUNNING DEPLOY는 UNKNOWN, 끝나지 않은 검증은 INCONCLUSIVE다.

        다시 배포하지 않는다. 검증을 이어 PASS로 추정하지 않는다(docs/03 §10).
        """
        unknown: list[str] = []
        with self.store.tx() as tx:
            for execution in tx.all(
                "SELECT * FROM executions WHERE operation = 'DEPLOY'"
                " AND status IN ('INTENDED', 'RUNNING')"
            ):
                self._write(
                    tx,
                    execution["id"],
                    "UNKNOWN",
                    execution["stage"],
                    {"observation": "interrupted_before_result"},
                )
                incident = tx.one(
                    "SELECT * FROM incidents WHERE id = ?", (execution["incident_id"],)
                )
                work = tx.one("SELECT * FROM work_items WHERE id = ?", (execution["work_id"],))
                if (incident["status"], work["status"]) == ("DEPLOYING", "WAITING_VERIFICATION"):
                    coupled_transition(
                        tx,
                        incident_id=incident["id"],
                        expected_incident_version=incident["version"],
                        incident_to="EXECUTION_UNKNOWN",
                        work_id=work["id"],
                        expected_work_version=work["version"],
                        work_to="EXECUTION_UNKNOWN",
                        actor=Actor.RELEASE_EXECUTOR,
                        details={"execution_id": execution["id"], "recovered": True},
                    )
                audit.append(
                    tx,
                    execution["run_id"],
                    execution["incident_id"],
                    Actor.RELEASE_EXECUTOR,
                    "RELEASE_UNKNOWN",
                    {"execution_id": execution["id"], "observation": "interrupted_before_result"},
                )
                unknown.append(execution["id"])
        with self.store.read() as tx:
            running = tx.all(
                "SELECT v.*, i.status AS incident_status FROM verifications v"
                " JOIN incidents i ON i.id = v.incident_id WHERE v.verdict = 'RUNNING'"
            )
            executions = {
                row["id"]: row
                for row in tx.all("SELECT * FROM executions WHERE operation = 'DEPLOY'")
            }
        try:
            samples_required = load_contract(self.contract_path)[0].observation.samples
        except Exception:  # noqa: BLE001 — 계약을 못 읽어도 닫아야 한다
            samples_required = 4
        closed: list[str] = []
        for row in running:
            execution = executions.get(row["execution_id"])
            if execution is None or row["incident_status"] != "VERIFYING":
                continue
            self._persist(
                execution, self._unfinished(row, samples_required, "interrupted_by_restart")
            )
            closed.append(row["id"])
        return {"unknown": unknown, "inconclusive": closed}
