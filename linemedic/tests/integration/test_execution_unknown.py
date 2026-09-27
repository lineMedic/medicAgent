"""W11 결과 불명 execution과 운영자 reconcile (T-EXEC-01).

- PR 생성 직후 timeout(부작용 있음) → execution UNKNOWN·incident·work EXECUTION_UNKNOWN,
  두 번째 생성 0회 → reconcile FOUND → PR_OPENED·WAITING_REVIEW·PR_READY
- push 결과 불명: 브랜치가 남았으면 CONFIRMED_ABSENT 기록만,
  PR·브랜치가 모두 없으면(무변경) ESCALATED
- 다른 작성자가 같은 브랜치명·marker로 만든 PR → 채택하지 않음(CONFLICT 기록)
- 조회 오류 → INCONCLUSIVE 기록, 나중 조정은 다시 조회한다
- 재시작 때 INTENDED·RUNNING → UNKNOWN(다시 만들지 않음), 제안은 ALLOWED(EXECUTION_UNKNOWN)
- 운영 API `GET /ops/executions/{id}`·`POST .../reconcile`(run 일치·멱등), CLI `reconcile`
"""

import argparse
import json

import httpx
import pytest

from linemedic import cli
from linemedic.control_plane.broker.github_pr import branches
from linemedic.control_plane.broker.reconcile import ExecutionReconciler
from linemedic.tests.helpers.api import OPERATOR_TOKEN, RUN
from linemedic.tests.helpers.pr_world import PrWorld, build_seed_mirror


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


@pytest.fixture
def world(store, conn, fake_clock, seed, tmp_path):
    return PrWorld(store, conn, fake_clock, seed, tmp_path)


def is_pull_post(request):
    return request.method == "POST" and request.path.endswith("/pulls")


def unknown_after_create(world):
    world.github.fail_next("timeout", after_side_effect=True, when=is_pull_post)
    return world.run()


def assert_unknown(world, proposal_id, stage):
    row, record = world.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("ALLOWED", "EXECUTION_UNKNOWN")
    assert record["checks"][-1]["result"] == "EXECUTION_UNKNOWN"
    execution = world.execution()
    assert (execution["status"], execution["stage"]) == ("UNKNOWN", stage)
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "EXECUTION_UNKNOWN",
        "EXECUTION_UNKNOWN",
    )
    return execution


def test_timeout_after_pr_creation_is_unknown_then_reconcile_found(world):  # T-EXEC-01
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    assert world.pull_posts() == 1 and len(world.github.pulls) == 1  # 실제로는 만들어졌다
    assert world.notifications("PR_READY") == []
    result = world.reconciler.reconcile(execution["id"])
    (pull,) = world.github.pulls.values()
    assert (result["outcome"], result["pr_number"]) == ("FOUND", pull["number"])
    assert world.pull_posts() == 1  # 다시 만들지 않았다
    done = world.execution()
    assert (done["status"], json.loads(done["result_json"])["pr_number"]) == (
        "SUCCEEDED",
        pull["number"],
    )
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "PR_OPENED",
        "WAITING_REVIEW",
    )
    (ready,) = world.notifications("PR_READY")
    assert json.loads(ready["payload_json"])["generation"] == 1
    before = len(world.github.requests)
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "NOT_UNKNOWN"
    assert len(world.github.requests) == before  # 끝난 execution은 외부 조회도 하지 않는다


def test_unknown_push_with_branch_left_is_recorded_not_escalated(world):
    world.pusher.fail_next("UNKNOWN", after_side_effect=True)
    proposal_id = world.run()
    execution = assert_unknown(world, proposal_id, "push")
    head, _ = branches(RUN, world.incident, proposal_id)
    result = world.reconciler.reconcile(execution["id"])
    assert (result["outcome"], result["head_sha"]) == (
        "CONFIRMED_ABSENT",
        world.github.branches[head],
    )
    assert "escalated" not in result
    assert world.incident_row()["status"] == "EXECUTION_UNKNOWN"  # 남은 브랜치를 사람이 판단한다
    assert (
        json.loads(world.execution()["result_json"])["reconcile"]["outcome"] == "CONFIRMED_ABSENT"
    )
    assert world.pull_posts() == 0


def test_confirmed_no_change_escalates_without_recreating(world):
    world.pusher.fail_next("UNKNOWN")  # 반영 전에 끊겼다
    proposal_id = world.run()
    execution = assert_unknown(world, proposal_id, "push")
    result = world.reconciler.reconcile(execution["id"])
    assert (result["outcome"], result["head_sha"], result["escalated"]) == (
        "CONFIRMED_ABSENT",
        None,
        True,
    )
    assert (world.incident_row()["status"], world.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    (blocked,) = world.notifications("WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert (report["blocker_code"], report["stage"]) == (
        "EXTERNAL_RESULT_UNKNOWN",
        "external_write",
    )
    assert world.pull_posts() == 0 and len(world.pusher.pushes) == 1
    assert world.execution()["status"] == "UNKNOWN"  # 결과는 여전히 기록일 뿐 새 실행이 아니다


def test_other_authors_pr_with_same_branch_and_marker_is_not_adopted(world):
    world.github.fail_next("timeout", when=is_pull_post)  # 보내지 못하고 끊겼다(부작용 없음)
    proposal_id = world.run()
    execution = assert_unknown(world, proposal_id, "create_pull")
    request = json.loads(execution["request_json"])
    head, base = branches(RUN, world.incident, proposal_id)
    world.github.add_pull(
        title="copy",
        body=f"같은 marker {request['marker']}",
        author_id=200001,
        head=head,
        head_sha=request["candidate_sha"],
        base=base,
    )
    result = world.reconciler.reconcile(execution["id"])
    assert result["outcome"] == "CONFLICT"
    assert (
        world.execution()["status"] == "UNKNOWN"
        and world.incident_row()["status"] == "EXECUTION_UNKNOWN"
    )


def test_lookup_error_is_inconclusive_and_a_later_reconcile_rechecks(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    world.github.fail_next(
        "server_error", when=lambda r: r.path.endswith("/pulls") and r.method == "GET"
    )
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "INCONCLUSIVE"
    assert world.execution()["status"] == "UNKNOWN"
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "FOUND"


def test_restart_turns_interrupted_execution_into_unknown_without_retrying(world):
    world.opener.execute = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("died"))
    proposal_id = world.submit()
    assert world.broker.process_pending(keep_going=True) == []
    assert world.execution()["status"] == "INTENDED"
    assert world.broker.recover_checking() == 0
    execution = assert_unknown(world, proposal_id, "intended")
    assert json.loads(execution["result_json"])["observation"] == "interrupted_before_result"
    _, record = world.proposal(proposal_id)
    assert record["checks"][-1]["reason"] == "interrupted_before_result"
    assert world.pull_posts() == 0 and world.pusher.pushes == []


def test_reconcile_dispatches_by_operation(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")

    class Router:
        def reconcile_create_issue(self, execution_id):
            return {"execution_id": execution_id, "outcome": "ROUTED"}

    world.conn.execute(
        "UPDATE executions SET operation = 'CREATE_ISSUE' WHERE id = ?", (execution["id"],)
    )
    routed = ExecutionReconciler(world.store, opener=world.opener, issue_router=Router())
    assert routed.reconcile(execution["id"])["outcome"] == "ROUTED"
    with pytest.raises(RuntimeError):
        ExecutionReconciler(world.store).reconcile(execution["id"])
    world.conn.execute(
        "UPDATE executions SET operation = 'DEPLOY' WHERE id = ?", (execution["id"],)
    )
    assert routed.reconcile(execution["id"])["outcome"] == "UNSUPPORTED"
    with pytest.raises(LookupError):
        routed.reconcile("EXE-000000000000")


# ── 운영 API·CLI ──────────────────────────────────────────────


def reconcile_post(world, execution_id, key="rec-1", run_id=RUN):
    return world.api.client.post(
        f"/ops/executions/{execution_id}/reconcile",
        json={"schema_version": "linemedic.v4", "run_id": run_id},
        headers={**world.api.operator, "Idempotency-Key": key},
    )


def test_ops_execution_read_and_reconcile(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    read = world.api.client.get(f"/ops/executions/{execution['id']}", headers=world.api.operator)
    data = read.json()["data"]
    assert (read.status_code, data["status"], data["operation"]) == (200, "UNKNOWN", "CREATE_PR")
    assert data["request"]["head"] == branches(RUN, world.incident, proposal_id)[0]
    assert (
        world.api.client.get(
            "/ops/executions/EXE-000000000000", headers=world.api.operator
        ).status_code
        == 404
    )
    assert (
        reconcile_post(world, execution["id"], run_id="r-20260101-000000-0000").status_code == 404
    )
    first = reconcile_post(world, execution["id"])
    assert (first.status_code, first.json()["data"]["outcome"]) == (200, "FOUND")
    replay = reconcile_post(world, execution["id"])
    assert replay.json()["data"] == first.json()["data"]  # 같은 키는 저장된 응답
    other = reconcile_post(world, execution["id"], key="rec-2")
    assert other.json()["data"]["outcome"] == "NOT_UNKNOWN"


def test_ops_reconcile_without_reconciler_is_dependency_unavailable(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    world.reconciler.opener = None  # GitHub 포트가 없는 기동
    response = reconcile_post(world, execution["id"])
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "DEPENDENCY_UNAVAILABLE",
    )
    world.reconciler.opener = world.opener
    retried = reconcile_post(world, execution["id"])  # 같은 키로 다시 시도할 수 있다
    assert retried.json()["data"]["outcome"] == "FOUND"


def test_cli_reconcile_reads_then_posts_with_a_state_keyed_idempotency_key(
    world, tmp_path, monkeypatch
):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == f"Bearer {OPERATOR_TOKEN}"
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"updated_at": "2026-09-27T00:01:00.000000Z"}})
        return httpx.Response(200, json={"data": {"outcome": "FOUND"}})

    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args = argparse.Namespace(
        run_id=RUN,
        execution_id="EXE-0000000000AA",
        config=cli.DEFAULT_CONFIG_PATH,
        env_file=tmp_path / "none.env",
    )
    assert cli._execution_reconcile(args, transport=httpx.MockTransport(handler)) == 0
    get, post = seen
    assert get.url.path == "/ops/executions/EXE-0000000000AA"
    assert post.url.path == "/ops/executions/EXE-0000000000AA/reconcile"
    assert (
        post.headers["idempotency-key"] == "reconcile:EXE-0000000000AA:2026-09-27T00:01:00.000000Z"
    )
    assert json.loads(post.content) == {"schema_version": "linemedic.v4", "run_id": RUN}


def test_timeout_while_verifying_is_unknown_then_found(world):
    world.github.fail_next("timeout", when=lambda r: r.method == "GET" and "/pulls/" in r.path)
    proposal_id = world.run()
    execution = assert_unknown(world, proposal_id, "verify")
    assert json.loads(execution["result_json"])["pr_number"] == next(iter(world.github.pulls))
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "FOUND"
    assert world.pull_posts() == 1


def test_bot_pr_without_our_marker_is_not_adopted(world):
    world.github.fail_next("timeout", when=is_pull_post)
    proposal_id = world.run()
    execution = assert_unknown(world, proposal_id, "create_pull")
    request = json.loads(execution["request_json"])
    head, base = branches(RUN, world.incident, proposal_id)
    world.github.add_pull(
        title="marker 없음",
        body="다른 실행이 만든 봇 PR",
        author_id=world.github.identity["id"],
        head=head,
        head_sha=request["candidate_sha"],
        base=base,
    )
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "CONFLICT"


def test_our_pr_plus_another_pr_on_the_branch_is_a_conflict(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    head, _ = branches(RUN, world.incident, proposal_id)
    world.github.pulls[next(iter(world.github.pulls))]["state"] = "closed"  # 하나는 닫혔고
    world.github.add_pull(title="다른 PR", author_id=200001, head=head)  # 하나는 사람이 열었다
    result = world.reconciler.reconcile(execution["id"])
    assert result["outcome"] == "CONFLICT" and len(result["pulls"]) == 2


def test_found_without_the_expected_state_records_but_does_not_transition(world):
    proposal_id = unknown_after_create(world)
    execution = assert_unknown(world, proposal_id, "create_pull")
    world.conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (world.incident,))
    assert world.reconciler.reconcile(execution["id"])["outcome"] == "FOUND"
    assert world.execution()["status"] == "SUCCEEDED"
    assert world.incident_row()["status"] == "ESCALATED"
    assert world.notifications("PR_READY") == []
