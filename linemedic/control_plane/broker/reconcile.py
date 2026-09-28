"""결과 불명 execution 조정 (W11, spec 04 §4·§8, spec 06 §6, docs/05 ⑦, D81).

운영자가 `make reconcile RUN_ID= EXECUTION_ID=`로 부른다. 외부 상태를 정확한 identity로 조회만 한다.
새 PR·Issue를 만들거나 다시 push하지 않는다. 자동 bounded 재조회는 H04다.

- CREATE_PR: head 브랜치 PR 목록(끝까지)과 head 브랜치 SHA를 읽는다.
  봇 작성·head 브랜치·candidate SHA·base 브랜치·marker가 모두 맞는 PR이 정확히 1개이고 다른 PR이
  없으면 `FOUND` → SUCCEEDED, incident·work EXECUTION_UNKNOWN → PR_OPENED·WAITING_REVIEW와
  PR_READY intent(reconciler).
  PR이 하나도 없고 브랜치도 없으면(무변경 확인) `CONFIRMED_ABSENT` → ESCALATED·BLOCKED
  (EXTERNAL_RESULT_UNKNOWN, 자동 재생성 없음). PR은 없지만 브랜치가 남았으면 CONFIRMED_ABSENT로
  기록만 한다(남은 브랜치를 사람이 판단).
  맞는 PR이 둘 이상이거나 다른 PR이 브랜치를 쓰면 `CONFLICT`,
  조회가 불완전하거나 오류면 `INCONCLUSIVE`로 기록만 하고 상태는 그대로 둔다
- CREATE_ISSUE: W24 `IssueRouter.reconcile_create_issue`에 맡긴다
- DEPLOY: W12 `ReleaseExecutor.reconcile`에 맡긴다
  (실제 container·image·라벨 조회, 다시 배포하지 않음)
"""

import json
from dataclasses import dataclass
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.control_plane import audit
from linemedic.control_plane.broker.github_pr import Execution, PrOpener, PrPlan
from linemedic.control_plane.broker.intake import _block
from linemedic.control_plane.issue_router import IssueRouter
from linemedic.control_plane.release import ReleaseExecutor
from linemedic.control_plane.state import Actor
from linemedic.control_plane.store import Store
from linemedic.integrations.github import GitHubError

NOT_UNKNOWN = "NOT_UNKNOWN"


@dataclass
class ExecutionReconciler:
    store: Store
    opener: PrOpener | None = None  # CREATE_PR 조회·기록(W11)
    issue_router: IssueRouter | None = None  # CREATE_ISSUE 조회(W24)
    route_id: str | None = None
    release: ReleaseExecutor | None = None  # DEPLOY 조회(W12)

    def reconcile(self, execution_id: str) -> dict[str, Any]:
        with self.store.read() as tx:
            execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
        if execution is None:
            raise LookupError(execution_id)
        if execution["status"] != "UNKNOWN":
            return {
                "execution_id": execution_id,
                "outcome": NOT_UNKNOWN,
                "status": execution["status"],
            }
        if execution["operation"] == "CREATE_ISSUE":
            if self.issue_router is None:
                raise RuntimeError("issue_router_unavailable")
            return self.issue_router.reconcile_create_issue(execution_id)
        if execution["operation"] == "CREATE_PR":
            if self.opener is None:
                raise RuntimeError("pr_opener_unavailable")
            return self._reconcile_pr(execution)
        if execution["operation"] == "DEPLOY":
            if self.release is None:
                raise RuntimeError("release_executor_unavailable")
            return self.release.reconcile(execution_id)
        return {
            "execution_id": execution_id,
            "outcome": "UNSUPPORTED",
            "operation": execution["operation"],
        }

    def _plan(self, execution: Any, generation: int) -> PrPlan:
        """저장한 요청으로 조정에 필요한 식별 값만 다시 만든다(본문은 marker로만 비교한다)."""
        request = json.loads(execution["request_json"])
        assert self.opener is not None
        return PrPlan(
            run_id=execution["run_id"],
            incident_id=execution["incident_id"],
            work_id=execution["work_id"],
            generation=generation,
            proposal_id=execution["proposal_id"],
            idempotency_key=execution["idempotency_key"],
            body_sha256=execution["request_sha256"],
            repository_id=self.opener.port.repository_id,
            repo=self.opener.port.full_name,
            issue_number=request["issue_number"],
            head=request["head"],
            base=request["base"],
            base_sha=request["base_sha"],
            candidate_sha=request["candidate_sha"],
            candidate_tree=request["candidate_tree"],
            title=request["title"],
            lines=(),
        )

    def _reconcile_pr(self, execution: Any) -> dict[str, Any]:
        assert self.opener is not None
        opener, execution_id = self.opener, execution["id"]
        with self.store.read() as tx:
            work = tx.one("SELECT generation FROM work_items WHERE id = ?", (execution["work_id"],))
        plan = self._plan(execution, work["generation"])
        marker = json.loads(execution["request_json"])["marker"]
        error, pulls, head_sha, bot_id = None, [], None, None
        try:
            bot_id = opener.port.get_identity().data["id"]
            pulls = opener._all_pulls(head=f"{opener.owner}:{plan.head}", state="all")
            head_sha = opener._branch_sha(plan.head)
        except GitHubError as exc:
            error = type(exc).__name__
        matches = [
            p for p in pulls if opener.is_ours(p, plan, bot_id) and marker in (p.get("body") or "")
        ]
        if error is not None:
            outcome = "INCONCLUSIVE"
        elif len(matches) == 1 and len(pulls) == 1:
            outcome = "FOUND"
        elif pulls:
            outcome = "CONFLICT"
        else:
            outcome = "CONFIRMED_ABSENT"
        record = {
            "outcome": outcome,
            "pulls": sorted(p.get("number") for p in pulls if isinstance(p.get("number"), int)),
            "head_sha": head_sha,
            "error": error,
        }
        with self.store.tx() as tx:
            current = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
            if current["status"] != "UNKNOWN":  # 그 사이 다른 조정이 끝냈다
                return {
                    "execution_id": execution_id,
                    "outcome": NOT_UNKNOWN,
                    "status": current["status"],
                }
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (execution["incident_id"],))
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (execution["work_id"],))
            waiting = (incident["status"], work["status"]) == (
                "EXECUTION_UNKNOWN",
                "EXECUTION_UNKNOWN",
            )
            if outcome == "FOUND":
                result = Execution("SUCCEEDED", "reconciled", {"reconciled": True}, pull=matches[0])
                opener.record(
                    tx,
                    plan,
                    execution_id,
                    result,
                    incident,
                    work,
                    actor=Actor.RECONCILER,
                    transition=waiting,
                )
                return {
                    "execution_id": execution_id,
                    **record,
                    "pr_number": matches[0].get("number"),
                }
            stored = json.loads(current["result_json"] or "{}")
            stored["reconcile"] = {**record, "checked_at": tx.now}
            tx.execute(
                "UPDATE executions SET result_json = ?, updated_at = ? WHERE id = ?",
                (canonical_dumps(stored), tx.now, execution_id),
            )
            audit.append(
                tx,
                execution["run_id"],
                execution["incident_id"],
                Actor.RECONCILER,
                "PR_EXECUTION_RECONCILED",
                {"execution_id": execution_id, **record},
            )
            unchanged = outcome == "CONFIRMED_ABSENT" and head_sha is None
            if unchanged and waiting and self.route_id is not None:
                # PR·브랜치 모두 없음(무변경 확인): 멈추고 사람이 새 generation을 판단한다
                _block(
                    tx,
                    self.route_id,
                    incident,
                    work,
                    reason="EXTERNAL_RESULT_UNKNOWN",
                    stage="external_write",
                    reason_detail=(
                        "PR 생성 결과가 불명이었고 조회로 PR·브랜치가 모두 없음을 확인했다."
                        " 자동으로 다시 만들지 않는다"
                    ),
                    evidence_ids=[],
                    details={"execution_id": execution_id, "reconcile": outcome},
                    attempted=[f"reconcile {execution_id} → CONFIRMED_ABSENT"],
                    next_steps=["새 generation 승인 여부를 판단"],
                    actor=Actor.RECONCILER,
                )
                record["escalated"] = True
        return {"execution_id": execution_id, **record}
