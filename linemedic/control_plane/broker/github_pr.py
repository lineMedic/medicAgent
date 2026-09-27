"""봇 PR 생성 (W11, spec 06 §6·§8·§10, spec 15 §7, spec 04 §4·§7·§8, docs/05 ⑤, D81).

R2까지 통과한 candidate를 봇 credential로 `autofix/<run>/<incident>/<proposal>`에 push하고
`baseline/<run>`을 대상으로 PR을 연다. 제안 결정과 차단은 브로커(`intake.py`)가 하고, 이 모듈은
계획·외부 재조회·외부 실행·execution 기록을 맡는다. 외부 호출은 트랜잭션 밖이다.

1. `db_problem`·`plan`: work Issue bound·시작 알림 ACCEPTED, head·base 브랜치 이름, PR 제목·본문
2. `precheck`(트랜잭션 밖): Issue, 열린 PR 목록, baseline 브랜치 SHA, head 브랜치 SHA와 그 PR, 봇 ID
3. `evaluate`: Issue mirror 갱신 → state·승인 snapshot·취소 요청(`recheck_scope`), 사람 담당자·사람
   PR(`check_human_work`), baseline이 base SHA를 가리키는지, head 브랜치·PR을 다른 누가 쓰는지.
   같은 work·candidate의 봇 PR이 이미 있으면 재사용한다
4. `intend`: `executions CREATE_PR INTENDED (logical_key pr:<work>:<proposal>:<candidate_sha>)`
5. `execute`(트랜잭션 밖): push(원격 브랜치가 이미 candidate면 생략, force 없음) → `create_pull` →
   `get_pull`로 head SHA·브랜치·base·봇 작성자 확인
6. `record`: SUCCEEDED면 incident PR_OPENED·work WAITING_REVIEW·PR_READY intent, 불명이면 execution
   UNKNOWN·incident·work EXECUTION_UNKNOWN(다시 만들지 않고 운영자 reconcile), 실패면 FAILED
   (차단은 브로커가 한다). 재시작 때 INTENDED·RUNNING은 `recover`가 UNKNOWN으로 둔다

- PR 제목·본문은 plain text 템플릿이다. `Related to #<n>`을 쓰고 closing keyword를 넣지 않는다.
  에이전트가 쓴 원인 가설은 정제하고 closing keyword를 무력화한다. marker로 조정한다
- 봇 credential로 머지·승인·브랜치 보호 변경을 하지 않는다(이 모듈에 그런 호출이 없다)
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.ids import new_id
from linemedic.common.sanitize import sanitize_text
from linemedic.control_plane import audit
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.state import Actor, coupled_transition
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.supervisor import check_human_work, recheck_scope
from linemedic.integrations.git_push import BranchPusher
from linemedic.integrations.github import (
    Conflict,
    Forbidden,
    GitHubError,
    GitHubPort,
    GitHubResponse,
    NotFound,
    RateLimited,
    Unknown,
    WritePlan,
)

HEAD_PREFIX = "autofix"
BASE_PREFIX = "baseline"
MAX_PULL_PAGES = 10
TITLE_MAX = 200
HYPOTHESIS_MAX = 1500
# closing keyword 뒤에 Issue 참조(#n, owner/repo#n, URL)가 오면 머지로 Issue가 닫힐 수 있다
_CLOSING = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b"
    r"(?=\s*:?\s*(?:[\w.-]+/[\w.-]+)?#\d|\s*:?\s*https?://)"
)
DISCLAIMER = (
    "재현 테스트는 실제 원인이 반드시 코드라는 증거가 아닙니다.",
    "검사는 악성 코드 부재나 모든 업무 동작을 보장하지 않습니다.",
    "머지 전에 사람이 diff와 근거를 검토해야 합니다.",
    "배포 후 별도 업무 계약 검사 전에는 복구 완료가 아닙니다.",
)


def branches(run_id: str, incident_id: str, proposal_id: str) -> tuple[str, str]:
    """(head, base) 브랜치 이름. 서버가 검증한 ID로만 만든다."""
    return f"{HEAD_PREFIX}/{run_id}/{incident_id}/{proposal_id}", f"{BASE_PREFIX}/{run_id}"


def pr_marker(execution_id: str, candidate_sha: str) -> str:
    return f"<!-- linemedic:pr exec={execution_id} candidate={candidate_sha} -->"


def neutralize_closing(text: str) -> str:
    """`fixes #9`처럼 Issue를 닫는 문구의 키워드를 `관련`으로 바꾼다."""
    return _CLOSING.sub("관련", text)


def has_closing_keyword(text: str) -> bool:
    return _CLOSING.search(text) is not None


# ── 계획 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stop:
    """PR을 만들지 않고 멈추는 이유. `code`는 제안 검사 결과, `blocker`는 차단 보고 코드."""

    code: str
    reason: str
    blocker: str


@dataclass(frozen=True)
class PrPlan:
    run_id: str
    incident_id: str
    work_id: str
    generation: int
    proposal_id: str
    idempotency_key: str
    body_sha256: str  # 제안 본문 hash(execution 요청 hash로 쓴다)
    repository_id: int
    repo: str
    issue_number: int
    head: str
    base: str
    base_sha: str
    candidate_sha: str
    candidate_tree: str
    title: str
    lines: tuple[str, ...]  # marker 앞까지의 본문

    def body(self, execution_id: str) -> str:
        return "\n".join([*self.lines, "", pr_marker(execution_id, self.candidate_sha)]) + "\n"

    def request(self, execution_id: str) -> dict[str, Any]:
        return {
            "head": self.head,
            "base": self.base,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "candidate_tree": self.candidate_tree,
            "issue_number": self.issue_number,
            "title": self.title,
            "marker": pr_marker(execution_id, self.candidate_sha),
            "body_sha256": sha256_hex({"body": self.body(execution_id)}),
        }


def _stage(checks: list[dict[str, Any]], name: str) -> str:
    record = next((c for c in reversed(checks) if c.get("check") == name), None)
    if record is None:
        return "기록 없음"
    junit = record.get("junit") or {}
    container = str(record.get("container_id") or "")[:12]
    return (
        f"{record.get('result')} (exit {record.get('exit_code')}, 수집 {junit.get('tests')}개, "
        f"실패 {junit.get('failures')}개, 오류 {junit.get('errors')}개, container {container})"
    )


@dataclass
class Precheck:
    issue: dict[str, Any] | None = None
    open_pulls: list[dict[str, Any]] = field(default_factory=list)
    baseline_sha: str | None = None
    head_sha: str | None = None
    head_pulls: list[dict[str, Any]] = field(default_factory=list)
    bot_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class Execution:
    status: Literal["SUCCEEDED", "UNKNOWN", "FAILED"]
    stage: str
    detail: dict[str, Any] = field(default_factory=dict)
    blocker: str | None = None  # FAILED일 때 차단 보고 코드
    pull: dict[str, Any] | None = None


class PrOpener:
    def __init__(
        self,
        store: Store,
        port: GitHubPort,
        pusher: BranchPusher,
        sync: IssueSync,
        *,
        route_id: str,
    ) -> None:
        self.store = store
        self.port = port
        self.pusher = pusher
        self.sync = sync
        self.route_id = route_id

    @property
    def owner(self) -> str:
        return self.port.full_name.split("/")[0]

    def db_problem(self, tx: Tx, work: Any) -> Stop | None:
        """work가 Issue에 묶였고 같은 generation의 시작 알림이 접수됐는가."""
        if work["issue_number"] is None:
            return Stop("STATE_CONFLICT", "issue_unbound", "VALIDATION_FAILED")
        notice = None
        if work["start_notification_id"]:
            notice = tx.one(
                "SELECT status, event_type, work_id FROM notifications WHERE id = ?",
                (work["start_notification_id"],),
            )
        if (
            notice is None
            or notice["event_type"] != "WORK_STARTING"
            or notice["work_id"] != work["id"]
            or notice["status"] != "ACCEPTED"
        ):
            return Stop("STATE_CONFLICT", "start_notice_unconfirmed", "START_NOTICE_UNCONFIRMED")
        return None

    def plan(
        self,
        row: Any,
        incident: Any,
        work: Any,
        proposal: Any,
        checks: list[dict[str, Any]],
        candidate: dict[str, Any],
        observed: str | None,
        evidence_ids: list[str],
    ) -> PrPlan:
        head, base = branches(row["run_id"], row["incident_id"], row["id"])
        repo = self.port.full_name
        action = proposal.action
        policy = next((c for c in checks if c.get("check") == "PATCH_POLICY"), {})
        changed = ", ".join(
            f"{f['path']} (+{f['additions']}/-{f['deletions']})" for f in policy.get("files", [])
        )
        hypothesis = neutralize_closing(sanitize_text(action.root_cause_hypothesis, repo))
        title = neutralize_closing(
            sanitize_text(f"LineMedic 수정 제안 — {incident['service']} {row['incident_id']}", repo)
        )[:TITLE_MAX]
        issue = work["issue_number"]
        lines = (
            f"## LineMedic 수정 제안 — {row['incident_id']}",
            "",
            f"- 관련 Issue: Related to #{issue}",
            f"- 작업: {work['id']} / generation {work['generation']} / {row['run_id']}",
            f"- 대상: {repo} / {base}",
            f"- 기준 코드: {candidate['base_sha']}",
            f"- 검사한 candidate: {candidate['candidate_sha']} / {candidate['candidate_tree']}",
            f"- 원인 가설(에이전트 판단, 검증되지 않음): {hypothesis[:HYPOTHESIS_MAX]}",
            f"- 근거: {', '.join(evidence_ids) or '없음'}"
            f" — 관찰: {neutralize_closing(sanitize_text(observed or '관찰 요약 없음', repo))}",
            f"- 변경 파일: {changed or '기록 없음'}",
            "",
            "### 브로커가 관찰한 검사",
            f"- 기준 환경·회귀(R0): {_stage(checks, 'R0')}",
            f"- base + 새 테스트(R1): {_stage(checks, 'R1')} — 예상한 실패가 재현됐다",
            f"- candidate 새 테스트·보호 회귀(R2): {_stage(checks, 'R2')}",
            f"- 검사 기록: 제안 {row['id']}, 패치 SHA-256 {candidate['patch_sha256']}",
            "",
            "### 보장하지 않는 것",
            *DISCLAIMER,
        )
        return PrPlan(
            run_id=row["run_id"],
            incident_id=row["incident_id"],
            work_id=work["id"],
            generation=work["generation"],
            proposal_id=row["id"],
            idempotency_key=row["idempotency_key"],
            body_sha256=row["body_sha256"],
            repository_id=work["repository_id"],
            repo=repo,
            issue_number=issue,
            head=head,
            base=base,
            base_sha=candidate["base_sha"],
            candidate_sha=candidate["candidate_sha"],
            candidate_tree=candidate["candidate_tree"],
            title=title,
            lines=lines,
        )

    # 외부 생성 직전 재조회

    def _all_pulls(self, **filters: Any) -> list[dict[str, Any]]:
        pulls: list[dict[str, Any]] = []
        for page in range(1, MAX_PULL_PAGES + 1):
            response = self.port.list_pulls(page=page, **filters)
            pulls.extend(item for item in response.data or [] if isinstance(item, dict))
            if not response.has_next:
                return pulls
        raise Unknown("pull_listing_incomplete", request_sent=True)

    def _branch_sha(self, branch: str) -> str | None:
        try:
            data = self.port.get_branch_head(branch).data
        except NotFound:
            return None
        return (data or {}).get("object", {}).get("sha")

    def precheck(self, plan: PrPlan) -> Precheck:
        found = Precheck()
        try:
            found.issue = self.port.get_issue(plan.issue_number).data
            found.open_pulls = self._all_pulls(state="open")
            found.baseline_sha = self._branch_sha(plan.base)
            found.head_sha = self._branch_sha(plan.head)
            found.head_pulls = self._all_pulls(head=f"{self.owner}:{plan.head}", state="all")
            found.bot_id = self.port.get_identity().data["id"]
        except GitHubError as exc:
            found.error = type(exc).__name__
        return found

    def is_ours(self, pull: dict[str, Any], plan: PrPlan, bot_id: int | None) -> bool:
        head, base = pull.get("head") or {}, pull.get("base") or {}
        return (
            (pull.get("user") or {}).get("id") == bot_id
            and head.get("ref") == plan.head
            and head.get("sha") == plan.candidate_sha
            and base.get("ref") == plan.base
        )

    def evaluate(
        self, tx: Tx, work: Any, plan: PrPlan, found: Precheck
    ) -> Stop | dict[str, Any] | None:
        """멈출 이유(Stop), 재사용할 봇 PR(dict), 또는 None(만들어도 된다)."""
        if found.error is not None or found.issue is None:
            return Stop("PROTECTION_UNAVAILABLE", "lookup_incomplete", "LOOKUP_INCOMPLETE")
        self.sync.upsert_mirror(tx, found.issue)
        fresh = tx.one("SELECT * FROM work_items WHERE id = ?", (work["id"],))
        scope = recheck_scope(tx, fresh)
        if scope == "cancel_requested":
            return Stop("STATE_CONFLICT", scope, "PERMISSION_REQUIRED")
        if scope is not None:
            return Stop("SOURCE_CHANGED", scope, "SOURCE_CHANGED")
        # 우리 head 브랜치의 PR은 아래 브랜치 점유 검사가 따로 본다
        others = [p for p in found.open_pulls if (p.get("head") or {}).get("ref") != plan.head]
        if check_human_work(found.issue, others, found.bot_id) is not None:
            return Stop("STATE_CONFLICT", "human_work_in_progress", "HUMAN_WORK_IN_PROGRESS")
        if found.baseline_sha is None:
            return Stop("SOURCE_CHANGED", "baseline_missing", "SOURCE_CHANGED")
        if found.baseline_sha != plan.base_sha:
            return Stop("SOURCE_CHANGED", "baseline_moved", "SOURCE_CHANGED")
        ours = [p for p in found.head_pulls if self.is_ours(p, plan, found.bot_id)]
        if len(ours) == 1 and len(found.head_pulls) == 1 and found.head_sha == plan.candidate_sha:
            return ours[0]  # 같은 work·candidate의 봇 PR: 새로 만들지 않고 재사용
        if found.head_pulls or found.head_sha not in (None, plan.candidate_sha):
            return Stop("STATE_CONFLICT", "branch_in_use", "HUMAN_WORK_IN_PROGRESS")
        return None

    # 실행 기록

    def intend(self, tx: Tx, plan: PrPlan, *, status: str = "INTENDED") -> str:
        execution_id = new_id("EXE")
        tx.execute(
            "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
            " logical_key, idempotency_key, request_sha256, status, stage, intended_at,"
            " updated_at, request_json, result_json) VALUES (?, ?, ?, ?, ?, 'CREATE_PR', ?, ?, ?,"
            " ?, 'intended', ?, ?, ?, '{}')",
            (
                execution_id,
                plan.run_id,
                plan.incident_id,
                plan.work_id,
                plan.proposal_id,
                f"pr:{plan.work_id}:{plan.proposal_id}:{plan.candidate_sha}",
                plan.idempotency_key,
                plan.body_sha256,
                status,
                tx.now,
                tx.now,
                canonical_dumps(plan.request(execution_id)),
            ),
        )
        return execution_id

    def execute(
        self, plan: PrPlan, execution_id: str, git_dir: Path, head_sha: str | None
    ) -> Execution:
        """push → create_pull → get_pull. 트랜잭션 밖에서 부른다."""
        if head_sha != plan.candidate_sha:
            pushed = self.pusher.push(git_dir, plan.candidate_sha, plan.head)
            if pushed.status == "UNKNOWN":
                return Execution("UNKNOWN", "push", {"observation": pushed.detail})
            if pushed.status == "REJECTED":
                return Execution("FAILED", "push", {"reason": pushed.detail}, "PERMISSION_REQUIRED")
        try:
            created = self.port.create_pull(
                plan.head, plan.base, plan.title, plan.body(execution_id)
            )
        except Unknown as exc:
            if exc.request_sent is False:
                return Execution(
                    "FAILED", "create_pull", {"reason": "not_sent"}, "PERMISSION_REQUIRED"
                )
            return Execution("UNKNOWN", "create_pull", {"observation": exc.observation})
        except (Forbidden, RateLimited) as exc:
            return Execution(
                "FAILED", "create_pull", {"reason": type(exc).__name__}, "PERMISSION_REQUIRED"
            )
        except NotFound:
            return Execution("FAILED", "create_pull", {"reason": "NotFound"}, "SOURCE_CHANGED")
        except Conflict:
            return Execution(
                "FAILED", "create_pull", {"reason": "Conflict"}, "HUMAN_WORK_IN_PROGRESS"
            )
        if isinstance(created, WritePlan):  # 실행 중에 shadow로 바뀌었다
            return Execution(
                "FAILED", "create_pull", {"reason": "write_disabled"}, "PERMISSION_REQUIRED"
            )
        assert isinstance(created, GitHubResponse)
        number = (created.data or {}).get("number")
        if not isinstance(number, int):
            return Execution("UNKNOWN", "create_pull", {"observation": "pull_without_number"})
        return self.verify(plan, number)

    def verify(self, plan: PrPlan, number: int) -> Execution:
        try:
            pull = self.port.get_pull(number).data
            bot_id = self.port.get_identity().data["id"]
        except GitHubError as exc:
            return Execution(
                "UNKNOWN", "verify", {"observation": type(exc).__name__, "pr_number": number}
            )
        if not isinstance(pull, dict) or not self.is_ours(pull, plan, bot_id):
            detail = {"reason": "head_mismatch", "pr_number": number}
            return Execution(
                "FAILED",
                "verify",
                detail,
                "SOURCE_CHANGED",
                pull if isinstance(pull, dict) else None,
            )
        return Execution("SUCCEEDED", "verified", {"pr_number": number}, pull=pull)

    def _pr_ready(self, plan: PrPlan, execution_id: str, pull: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "linemedic.v4",
            "event_type": "PR_READY",
            "run_id": plan.run_id,
            "incident_id": plan.incident_id,
            "work_id": plan.work_id,
            "generation": plan.generation,
            "repository_id": plan.repository_id,
            "issue_number": plan.issue_number,
            "proposal_id": plan.proposal_id,
            "execution_id": execution_id,
            "pr_number": pull["number"],
            "head": plan.head,
            "base": plan.base,
            "candidate_sha": plan.candidate_sha,
        }

    def _write_result(
        self, tx: Tx, execution_id: str, status: str, stage: str, result: dict
    ) -> None:
        row = tx.one("SELECT result_json FROM executions WHERE id = ?", (execution_id,))
        merged = {**json.loads(row["result_json"] or "{}"), **result}
        tx.execute(
            "UPDATE executions SET status = ?, stage = ?, updated_at = ?, result_json = ?"
            " WHERE id = ?",
            (status, stage, tx.now, canonical_dumps(merged), execution_id),
        )

    def record(
        self,
        tx: Tx,
        plan: PrPlan,
        execution_id: str,
        result: Execution,
        incident: Any,
        work: Any,
        *,
        actor: Actor = Actor.BROKER,
        transition: bool = True,
    ) -> None:
        """실행 결과를 쓴다.

        SUCCEEDED·UNKNOWN이면(`transition`일 때) incident·work도 결합 전이한다.
        """
        result_json: dict[str, Any] = {**result.detail}
        if result.pull is not None:
            result_json.update(
                pr_number=result.pull.get("number"),
                html_url=result.pull.get("html_url"),
                head_sha=(result.pull.get("head") or {}).get("sha"),
            )
        status = {"SUCCEEDED": "SUCCEEDED", "UNKNOWN": "UNKNOWN", "FAILED": "FAILED"}[result.status]
        self._write_result(tx, execution_id, status, result.stage, result_json)
        audit.append(
            tx,
            plan.run_id,
            plan.incident_id,
            actor,
            f"PR_EXECUTION_{status}",
            {"execution_id": execution_id, "stage": result.stage, **result_json},
        )
        if not transition or result.status == "FAILED":
            return
        target = (
            ("PR_OPENED", "WAITING_REVIEW") if status == "SUCCEEDED" else ("EXECUTION_UNKNOWN",) * 2
        )
        moved = coupled_transition(
            tx,
            incident_id=incident["id"],
            expected_incident_version=incident["version"],
            incident_to=target[0],
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to=target[1],
            actor=actor,
            details={"execution_id": execution_id, "proposal_id": plan.proposal_id},
        )
        if moved.outbox_event == "PR_READY":
            assert result.pull is not None
            payload = self._pr_ready(plan, execution_id, result.pull)
            outbox.enqueue(tx, work, "PR_READY", payload, self.route_id)

    def recover(self) -> int:
        """재시작 때: 결과를 모르는 INTENDED·RUNNING CREATE_PR을 UNKNOWN으로 둔다.

        다시 만들지 않는다. 운영자가 reconcile로 조회한다.
        """
        recovered = 0
        with self.store.tx() as tx:
            for execution in tx.all(
                "SELECT * FROM executions WHERE operation = 'CREATE_PR'"
                " AND status IN ('INTENDED', 'RUNNING')"
            ):
                incident = tx.one(
                    "SELECT * FROM incidents WHERE id = ?", (execution["incident_id"],)
                )
                work = tx.one("SELECT * FROM work_items WHERE id = ?", (execution["work_id"],))
                self._write_result(
                    tx,
                    execution["id"],
                    "UNKNOWN",
                    execution["stage"],
                    {"observation": "interrupted_before_result"},
                )
                if (
                    incident is not None
                    and work is not None
                    and (
                        incident["status"],
                        work["status"],
                    )
                    == ("VALIDATING", "RUNNING")
                ):
                    coupled_transition(
                        tx,
                        incident_id=incident["id"],
                        expected_incident_version=incident["version"],
                        incident_to="EXECUTION_UNKNOWN",
                        work_id=work["id"],
                        expected_work_version=work["version"],
                        work_to="EXECUTION_UNKNOWN",
                        actor=Actor.BROKER,
                        details={"execution_id": execution["id"], "recovered": True},
                    )
                recovered += 1
        return recovered
