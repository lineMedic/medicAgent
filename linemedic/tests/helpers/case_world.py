"""사례 기억 시험 세계(W27·W28): 원본 event 행(proposal·execution·verification·알림)을 넣는 도우미와
case builder·snapshot·검색 호출. 노트·색인·snapshot·검색·projection은 제품 코드로 만든다."""

import json

from linemedic.common.ids import new_id
from linemedic.control_plane import audit, deploys
from linemedic.control_plane.memory import search as case_search
from linemedic.control_plane.memory import snapshot as memory_snapshot
from linemedic.control_plane.memory.builder import CaseBuilder
from linemedic.control_plane.memory.search import CaseSearch
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.tests.helpers.api import OTHER_RUN, RUN
from linemedic.tests.helpers.db_rows import (
    REPOSITORY_ID,
    insert_incident,
    insert_issue,
    insert_work,
)

OLD_RUN = OTHER_RUN
OTHER_REPO = 200002
TERMS = tuple(sorted(eval_identifiers()))  # holdout 입력 로트 ID(기대값 아님)
ATTEMPT = "ATT-0000000000C1"
OLD_ATTEMPT = "ATT-0000000000B1"
IMAGE = "sha256:" + "a" * 64
OTHER_IMAGE = "sha256:" + "b" * 64
BASE = "1" * 40
MERGE = "2" * 40
CANDIDATE = "3" * 40
CONTRACT_SHA = "c" * 64
FP = "fp-s1-keyerror-inspector"
T0, T1, T2, T3 = (f"2026-09-26T0{h}:00:00.000000Z" for h in (1, 2, 3, 4))
S1_DETAILS = {
    "signature": {
        "service": "mes-api",
        "error_type": "KeyError: 'inspector_id'",
        "top_frame": "app.defects:summarize",
        "endpoint": "/defects/summary",
    }
}
HYPOTHESIS = (
    "summarize가 모든 record에 inspector_id가 있다고 가정한다. 검사자 미지정 record에서 KeyError"
)
CREATE_PR_ACTION = {
    "type": "create_pr",
    "base_sha": BASE,
    "root_cause_hypothesis": HYPOTHESIS,
    "diff": "diff --git a/app/defects.py b/app/defects.py\n",
    "new_test_path": "tests/repro/test_missing_inspector.py",
}
PASSED_CHECKS = [
    {"check": "B01", "result": "PASS"},
    {
        "check": "PATCH_POLICY",
        "result": "PASS",
        "files": [
            {"path": "app/defects.py", "additions": 3, "deletions": 1},
            {"path": "tests/repro/test_missing_inspector.py", "additions": 12, "deletions": 0},
        ],
    },
    {"check": "R1", "result": "PASS"},
    {"check": "R2", "result": "PASS"},
]
CANDIDATE_RECORD = {
    "base_sha": BASE,
    "candidate_sha": CANDIDATE,
    "candidate_tree": "4" * 40,
    "patch_sha256": "5" * 64,
}
EXPECTED_VALUE, ACTUAL_VALUE = 424242, 737373  # 기대·실제 값은 노트에 남지 않아야 한다


def pass_result(**overrides):
    result = {
        "verdict": "PASS",
        "reason": "all_samples_passed",
        "samples_completed": 4,
        "samples_required": 4,
        "observation_complete": True,
        "failed_assertions": [],
        "target": {"image_id": IMAGE, "container_id": "d" * 64},
        "fixture_sha256": "f" * 64,
    }
    result.update(overrides)
    return result


def fail_result():
    return {
        "verdict": "FAIL",
        "reason": "content_mismatch",
        "samples_completed": 1,
        "samples_required": 4,
        "observation_complete": False,
        "failed_assertions": [
            {
                "case_id": "missing-inspector",
                "assertion": "exact_by_inspector_mapping",
                "field": "by_inspector",
                "expected": EXPECTED_VALUE,
                "actual": ACTUAL_VALUE,
            },
            {
                "case_id": "variant-held-out",
                "assertion": "exact_total_defects",
                "field": "total_defects",
                "expected": EXPECTED_VALUE,
                "actual": ACTUAL_VALUE,
            },
        ],
        "target": {"image_id": IMAGE, "container_id": "d" * 64},
        "fixture_sha256": "f" * 64,
    }


class World:
    """원본 event 행을 넣는 도우미와 제품 코드 호출."""

    def __init__(self, store, conn, clock) -> None:
        self.store, self.conn, self.clock = store, conn, clock
        self._issue = 100

    # 원본 행

    def issue(self, repository_id: int = REPOSITORY_ID) -> int:
        self._issue += 1
        return insert_issue(self.conn, self._issue, repository_id)

    def incident(
        self,
        run=OLD_RUN,
        *,
        fingerprint=FP,
        details=None,
        repository_id=REPOSITORY_ID,
        service="mes-api",
        status="RESOLVED",
        attempt_id=OLD_ATTEMPT,
    ) -> str:
        return insert_incident(
            self.conn,
            run,
            status,
            fingerprint=fingerprint,
            repository_id=repository_id,
            service=service,
            attempt_id=attempt_id,
            details_json=json.dumps(S1_DETAILS if details is None else details),
        )

    def work(
        self, run, incident, *, status="SUCCEEDED", attempt_id=OLD_ATTEMPT, repository_id=None
    ) -> str:
        repo = repository_id or REPOSITORY_ID
        return insert_work(
            self.conn,
            run,
            incident,
            self.issue(repo),
            status,
            attempt_id=attempt_id,
            repository_id=repo,
        )

    def started(self, run, incident, attempt, origin) -> None:
        with self.store.tx() as tx:
            audit.append(
                tx,
                run,
                incident,
                "supervisor",
                "ATTEMPT_STARTED",
                {"attempt_id": attempt, "adapter": "scripted", "origin": origin},
            )

    def proposal(
        self,
        run,
        incident,
        work,
        *,
        decision="ALLOWED",
        checks=None,
        action=None,
        attempt=OLD_ATTEMPT,
        reason=None,
        at=T0,
    ) -> str:
        proposal_id = new_id("PROP")
        payload = {
            "schema_version": "linemedic.v4",
            "run_id": run,
            "incident_id": incident,
            "work_id": work,
            "attempt_id": attempt,
            "category": "code_bug",
            "summary": "누락 필드를 별도 분류",
            "evidence_ids": ["EV-0000000000E1"],
            "action": action or CREATE_PR_ACTION,
        }
        record = {
            "checks": PASSED_CHECKS if checks is None else checks,
            "candidate": CANDIDATE_RECORD,
        }
        if reason is not None:
            record["decision_reason"] = reason
        self.conn.execute(
            "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
            " body_sha256, decision, received_at, payload_json, checks_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                run,
                incident,
                work,
                attempt,
                f"key-{proposal_id}",
                "0" * 64,
                decision,
                at,
                json.dumps(payload),
                json.dumps(record),
            ),
        )
        if decision == "REJECTED":
            with self.store.tx() as tx:
                audit.append(
                    tx,
                    run,
                    incident,
                    "broker",
                    "PROPOSAL_REJECTED",
                    {"proposal_id": proposal_id, "code": reason},
                )
        return proposal_id

    def execution(
        self, run, incident, work, proposal, operation, *, status="SUCCEEDED", request=None,
        result=None, at=T1,
    ) -> str:  # fmt: skip
        execution_id = new_id("EXE")
        self.conn.execute(
            "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
            " logical_key, idempotency_key, request_sha256, status, stage, intended_at, updated_at,"
            " request_json, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution_id,
                run,
                incident,
                work,
                proposal,
                operation,
                f"{operation.lower()}:{execution_id}",
                execution_id,
                "0" * 64,
                status,
                "done",
                at,
                at,
                json.dumps(request or {}),
                json.dumps(result or {}),
            ),
        )
        return execution_id

    def verification(
        self, run, incident, execution, verdict, *, origin="agent_release", result=None, at=T2
    ) -> str:
        verification_id = new_id("VER")
        self.conn.execute(
            "INSERT INTO verifications(id, run_id, incident_id, execution_id, origin, verdict,"
            " contract_id, contract_sha256, started_at, ended_at, result_json)"
            " VALUES (?, ?, ?, ?, ?, ?, 'defect-summary-v1', ?, ?, ?, ?)",
            (
                verification_id,
                run,
                incident,
                execution,
                origin,
                verdict,
                CONTRACT_SHA,
                at,
                at,
                json.dumps(result if result is not None else pass_result()),
            ),
        )
        return verification_id

    def blocked(self, run, incident, work, report, *, at=T1) -> str:
        notification_id = new_id("NOT")
        payload = {
            "schema_version": "linemedic.v4",
            "event_type": "WORK_BLOCKED",
            "run_id": run,
            "incident_id": incident,
            "work_id": work,
            **report,
        }
        self.conn.execute(
            "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
            " logical_key, payload_sha256, status, created_at, updated_at, payload_json,"
            " result_json) VALUES (?, ?, ?, ?, 'WORK_BLOCKED', 'github-issue-primary', ?, ?,"
            " 'ACCEPTED', ?, ?, ?, '{}')",
            (
                notification_id,
                run,
                incident,
                work,
                f"blocked:{notification_id}",
                "0" * 64,
                at,
                at,
                json.dumps(payload),
            ),
        )
        return notification_id

    def deploy_observed(self, base_sha=BASE, run=RUN) -> None:
        with self.store.tx() as tx:
            deploys.record_deploy_observed(
                tx,
                run_id=run,
                service="mes-api",
                base_sha=base_sha,
                image_id=IMAGE,
                container="linemedic-mes",
                container_id="e" * 64,
                actor="operator",
            )

    # 묶음

    def pr_opened(self, run=OLD_RUN, **incident_kwargs) -> tuple[str, str, str]:
        incident = self.incident(run, **incident_kwargs)
        work = self.work(run, incident, repository_id=incident_kwargs.get("repository_id"))
        proposal = self.proposal(run, incident, work)
        self.execution(
            run,
            incident,
            work,
            proposal,
            "CREATE_PR",
            result={"pr_number": 51, "head_sha": CANDIDATE},
            at=T1,
        )
        return incident, work, proposal

    def verified(
        self,
        verdict="PASS",
        *,
        result=None,
        deploy_status="SUCCEEDED",
        deploy_image=IMAGE,
        request=None,
        run=OLD_RUN,
        **incident_kwargs,
    ) -> tuple[str, str, str]:
        """봇 PR → (사람) 배포 → 업무 검증 한 벌. (incident, work, verification)."""
        incident, work, proposal = self.pr_opened(run, **incident_kwargs)
        deploy = self.execution(
            run,
            incident,
            work,
            proposal,
            "DEPLOY",
            status=deploy_status,
            request=request
            if request is not None
            else {
                "service": "mes-api",
                "proposal_id": proposal,
                "pr_number": 51,
                "base_sha": BASE,
                "candidate_sha": CANDIDATE,
                "approved_merge_sha": MERGE,
                "approved_tree": "4" * 40,
            },
            result={"target": {"image_id": deploy_image}, "container_id": "d" * 64},
            at=T2,
        )
        verification = self.verification(run, incident, deploy, verdict, result=result, at=T3)
        return incident, work, verification

    def current(self, *, fingerprint=FP, details=None) -> tuple[str, str]:
        """지금 run에서 조사 중인 incident와 RUNNING work."""
        incident = self.incident(
            RUN,
            fingerprint=fingerprint,
            details=details,
            status="INVESTIGATING",
            attempt_id=ATTEMPT,
        )
        work = self.work(RUN, incident, status="RUNNING", attempt_id=ATTEMPT)
        return incident, work

    # 제품 코드

    def build(self, **kwargs) -> list[str]:
        return CaseBuilder(self.store, terms=TERMS, **kwargs).process_pending()

    def note(self, note_id) -> dict:
        row = self.conn.execute("SELECT * FROM case_notes WHERE id = ?", (note_id,)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload_json"])}

    def notes(self) -> list[dict]:
        rows = self.conn.execute("SELECT id FROM case_notes ORDER BY series_id, revision")
        return [self.note(row["id"]) for row in rows]

    def indexed(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT note_id FROM case_search")}

    def snapshot(self, **kwargs) -> memory_snapshot.Snapshot:
        with self.store.tx() as tx:
            return memory_snapshot.build_snapshot(tx, run_id=RUN, terms=TERMS, **kwargs)

    def searcher(self, snapshot=None, *, mode="memory_assisted", **kwargs) -> CaseSearch:
        options = {
            "engine": "sqlite_fts5",
            "contract_id": "defect-summary-v1",
            "contract_sha256": CONTRACT_SHA,
            "terms": TERMS,
            **kwargs,
        }
        return CaseSearch(self.store, mode=mode, snapshot=snapshot, **options)

    def search(self, searcher, incident, work, **kwargs) -> case_search.SearchResult:
        return searcher.search(run_id=RUN, incident_id=incident, work_id=work, **kwargs)

    def retrievals(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM case_retrievals ORDER BY created_at, rowid")
        return [
            {
                **dict(row),
                "query": json.loads(row["query_json"]),
                "results": json.loads(row["results_json"]),
            }
            for row in rows
        ]
