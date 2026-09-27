"""W12 exact SHA 승인 배포.

실제 git(seed mirror·원격·squash merge) + FakeGitHub + FakeDocker·가짜 MES로 확인한다.

- 성공: 승인 → INTENDED(DEPLOYING·WAITING_VERIFICATION·lock) → merge commit 하나만 fetch
  → R0·R1·R2 재검사 → 신뢰 레시피로 final tree 빌드 → image ID로 기동·inspect
  → VERIFYING·RUNNING → verifier PASS → RESOLVED·SUCCEEDED.
  identity chain(base → … → contract hash)이 execution·verification에 채워진다
- T-SOURCE-01: 검사 뒤 PR head가 바뀜 → 거부
- T-SOURCE-02: 최종 tree ≠ candidate tree → 거부·기존 검사 무효 기록
- T-SOURCE-03: unmerged PR·test merge SHA → 거부
- T-AUTH-01: agent token → 403, 외부 변경 없음
- expected image 불일치 → 409 SOURCE_CHANGED. 같은 승인 재전송 → 같은 execution, 배포 1회
- 실패: fetch·재검사·빌드 실패는 이전 container 유지를 확인한 뒤 이관한다.
  이전 container 제거 뒤 기동 실패는 실제 상태·복원 절차를 기록한다(자동 rollback 없음).
  docker timeout·inspect 불일치는 UNKNOWN
- reconcile DEPLOY(FOUND → 새 검증, 없음·충돌 → 이관, 조회 실패 → 기록만), 재시작 복구,
  CLI 체크리스트(G8)
"""

import argparse
import dataclasses
import json
from pathlib import Path

import httpx
import pytest

from linemedic import cli
from linemedic.control_plane import release
from linemedic.control_plane.auth import AgentPrincipal, OperatorPrincipal
from linemedic.control_plane.broker.candidate import CandidateError, prepare_release_trees
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.notifications import templates
from linemedic.factory_sim.scenarios import mes_container_options
from linemedic.integrations.git_fetch import GitFetcher
from linemedic.tests.helpers.api import OPERATOR_TOKEN, OTHER_RUN, RUN
from linemedic.tests.helpers.pr_world import ATTEMPT, BOT, CONFIG, NEW_TEST, build_seed_mirror
from linemedic.tests.helpers.release_world import BASE_IMAGE, REVIEWER, ReleaseWorld, git
from linemedic.tests.helpers.runner import Scripted


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


@pytest.fixture
def world(store, conn, fake_clock, seed, tmp_path):
    return ReleaseWorld(store, conn, fake_clock, seed, tmp_path)


def error(response) -> tuple[int, str, str]:
    body = response.json()["error"]
    return response.status_code, body["code"], body["details"].get("reason")


def payload(row) -> dict:
    return json.loads(row["payload_json"])


def notifications(world, event_type: str) -> list:
    return world.conn.execute(
        "SELECT * FROM notifications WHERE event_type = ? AND work_id = ?",
        (event_type, world.work),
    ).fetchall()


def assert_untouched(world, before_requests: int | None = None) -> None:
    """거부: 상태·lock·execution·docker 변경 없음."""
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "PR_OPENED",
        "WAITING_REVIEW",
    )
    assert world.deploys() == [] and world.lock() is None
    assert not [c for c in world.docker.calls if c[0] in ("build", "run", "stop")]
    assert world.jobs == []


# ── 성공 ──────────────────────────────────────────────────────


def test_approved_merge_is_rechecked_built_deployed_by_image_id_and_verified(world):
    merge_sha = world.merge()
    response = world.approve()
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    execution_id = data["execution_id"]
    assert data == {
        "execution_id": execution_id,
        "logical_key": f"deploy:{world.work}:{merge_sha}",
        "status": "INTENDED",
        "stage": "intended",
        "incident_status": "DEPLOYING",
        "work_status": "WAITING_VERIFICATION",
        "reused": False,
    }
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "DEPLOYING",
        "WAITING_VERIFICATION",
    )
    assert world.lock() == execution_id
    assert not [c for c in world.docker.calls if c[0] in ("build", "run", "stop")]

    (summary,) = world.drain()
    assert (summary["verdict"], summary["incident_status"], summary["work_status"]) == (
        "PASS",
        "RESOLVED",
        "SUCCEEDED",
    )
    execution = world.deploy()
    request, result = json.loads(execution["request_json"]), json.loads(execution["result_json"])
    assert (execution["status"], execution["stage"]) == ("SUCCEEDED", "deployed")
    # merge commit 하나만 신뢰 mirror로 가져왔다
    assert git(f"--git-dir={world.mirror}", "rev-parse", f"refs/linemedic/release/{merge_sha}") == (
        merge_sha
    )
    assert result["trees"]["approved_tree"] == world.candidate_tree
    # final tree로 R0·R1·R2를 다시 돌렸다
    stages = [c[1][1] for c in world.docker.calls if c[0] == "run" and c[1][1].startswith("lm-")]
    assert stages == [f"lm-release-{execution_id.lower()}-{s}" for s in ("r0", "r1", "r2")]
    assert [(c["check"], c["result"]) for c in result["checks"]] == [
        ("R0", "PASS"),
        ("R1", "PASS"),
        ("R2", "PASS"),
    ]
    # 신뢰 레시피 + final tree만으로 빌드
    (build,) = [c for c in world.docker.calls if c[0] == "build"]
    root = world.runs_dir / RUN / "releases" / execution_id
    assert (build[1], Path(build[4])) == (
        str(release.MES_DOCKERFILE),
        root / "checkout/trees/final",
    )
    target = result["target"]
    assert target["image_identity"] == "local_image_id" and target["reported_repo_digests"] == []
    # 이전 container를 지우고 image ID로 같은 이름·network·read-only 데이터로 기동했다
    calls = [(c[0], c[1][1] if c[0] == "run" else c[1]) for c in world.docker.calls]
    assert calls.index(("stop", world.mes)) < calls.index(("run", world.mes))
    (mes_run,) = world.mes_runs()
    assert mes_run[2] == target["image_id"] and mes_run[2].startswith("sha256:")
    assert mes_run[1] == [
        *mes_container_options(world.mes, RUN, world.network, root / "mes-data"),
        "--label",
        f"linemedic.incident_id={world.incident}",
        "--label",
        f"linemedic.execution_id={execution_id}",
    ]
    info = world.docker.containers[world.mes]
    assert (info["Id"], info["Image"]) == (result["container_id"], target["image_id"])
    assert info["Config"]["Labels"]["linemedic.execution_id"] == execution_id
    assert not [name for name in world.docker.containers if "prober" in name]  # prober만 정리
    # 검증 기록과 알림
    verification = world.conn.execute("SELECT * FROM verifications").fetchone()
    assert (verification["verdict"], verification["origin"], verification["execution_id"]) == (
        "PASS",
        "agent_release",
        execution_id,
    )
    assert result["verification_id"] == verification["id"]
    assert (world.runs_dir / RUN / "verifications" / f"{verification['id']}.json").is_file()
    assert len(notifications(world, "RECOVERY_VERIFIED")) == 1
    assert world.lock() is None
    events = world.audit_types()
    order = [
        events.index(e) for e in ("RELEASE_APPROVED", "RELEASE_DEPLOYED", "VERIFICATION_RECORDED")
    ]
    assert order == sorted(order)
    # 승인 기록: 리뷰어·최신 변경 승인 설정·이전 runtime·레시피
    assert request["review"]["approvals"][0]["reviewer_id"] == REVIEWER
    assert request["runtime"]["previous"] == {
        "container": world.mes,
        "container_id": world.previous_id,
        "image_id": BASE_IMAGE,
        "network": world.network,
        "data_dir": str(world.s1_data.resolve()),
    }
    assert request["build_recipe"]["path"] == "linemedic/runner/mes.Dockerfile"
    assert request["build_recipe"]["build_recipe_sha256"] is None  # H05
    assert request["approval"]["principal"] == "operator:host-operator"


def test_identity_chain_is_filled_from_base_to_contract_hash(world):
    merge_sha = world.merge()
    execution_id = world.approve().json()["data"]["execution_id"]
    world.drain()
    response = world.api.client.get(f"/ops/executions/{execution_id}", headers=world.api.operator)
    chain = response.json()["data"]["identity_chain"]
    assert chain["base_sha"] == world.base
    assert (chain["candidate_sha"], chain["candidate_tree"]) == (
        world.candidate_sha,
        world.candidate_tree,
    )
    assert (chain["pr_number"], chain["pr_head_sha"]) == (world.pr_number, world.candidate_sha)
    assert (chain["approved_merge_sha"], chain["approved_tree"]) == (
        merge_sha,
        world.candidate_tree,
    )
    assert chain["image_id"] == world.docker.containers[world.mes]["Image"]
    assert chain["container_id"] == world.docker.containers[world.mes]["Id"]
    assert chain["verdict"] == "PASS" and chain["contract_id"] == "defect-summary-v1"
    required = ("patch_sha256", "contract_sha256", "fixture_sha256", "verification_id")
    assert all(chain[key] for key in required)


def test_same_approval_is_idempotent_and_deploys_once(world):
    world.merge()
    body = world.body()
    first = world.approve(key="release-1", body=body)
    replay = world.approve(key="release-1", body=body)
    assert replay.status_code == 202 and replay.content == first.content
    other = world.approve(key="release-2", body=body)  # 다른 키·같은 논리 작업
    assert other.status_code == 202
    assert other.json()["data"]["execution_id"] == first.json()["data"]["execution_id"]
    assert other.json()["data"]["reused"] is True
    world.drain()
    later = world.approve(key="release-3", body=body).json()["data"]  # 배포가 끝난 뒤에도
    assert (later["status"], later["incident_status"]) == ("SUCCEEDED", "RESOLVED")
    assert len(world.deploys()) == 1 and len(world.mes_runs()) == 1
    assert len([c for c in world.docker.calls if c[0] == "build"]) == 1
    changed = world.approve(key="release-1", body={**body, "approval_note": "다른 본문"})
    assert error(changed)[:2] == (409, "IDEMPOTENCY_CONFLICT")


# ── 사전 검사 거부 (상태·외부 변경 없음) ──────────────────────


def test_head_changed_after_check_is_refused(world):  # T-SOURCE-01
    world.merge()
    world.github.pulls[world.pr_number]["head"]["sha"] = "f" * 40  # 리뷰 뒤 새 push
    response = world.approve()
    assert error(response) == (409, "SOURCE_CHANGED", "head_changed")
    assert_untouched(world)
    refused = world.conn.execute(
        "SELECT payload_json FROM audit_events WHERE event_type = 'RELEASE_REFUSED'"
    ).fetchone()
    assert payload(refused)["reason"] == "head_changed"


def test_final_tree_differs_from_candidate_invalidates_checks(world):  # T-SOURCE-02
    tree = world.tree_with_human_change()
    world.merge(tree=tree)
    response = world.approve()
    assert error(response) == (409, "SOURCE_CHANGED", "tree_mismatch")
    details = response.json()["error"]["details"]
    assert (details["approved_tree"], details["candidate_checks_invalidated"]) == (tree, True)
    assert_untouched(world)
    (refused,) = world.conn.execute(
        "SELECT payload_json FROM audit_events WHERE event_type = 'RELEASE_REFUSED'"
    ).fetchall()
    assert payload(refused)["candidate_checks_invalidated"] is True


def test_unmerged_pr_with_test_merge_sha_is_refused(world):  # T-SOURCE-03
    test_merge = world.squash()  # GitHub가 머지 전에 보여 주는 test merge commit
    world.github.pulls[world.pr_number]["merge_commit_sha"] = test_merge
    world.merge_sha = test_merge
    response = world.approve()
    assert error(response) == (409, "STATE_CONFLICT", "pr_not_merged")
    assert_untouched(world)


def test_merge_sha_other_than_final_is_refused(world):  # T-SOURCE-03
    world.merge()
    response = world.approve(approved_merge_sha="a" * 40)
    assert error(response) == (409, "SOURCE_CHANGED", "merge_sha_mismatch")
    assert_untouched(world)


def test_agent_token_cannot_approve_release(world):  # T-AUTH-01
    world.merge()
    requests_before = len(world.github.requests)
    agent = world.api.agent(AgentPrincipal(RUN, world.incident, world.work, ATTEMPT))
    response = world.approve(headers=agent)
    assert error(response)[:2] == (403, "FORBIDDEN_SCOPE")
    assert len(world.github.requests) == requests_before and world.docker.calls == []
    assert_untouched(world)


def test_operator_without_approve_role_is_forbidden(world):
    world.merge()
    token = "limited-operator-token-" + "x" * 20
    world.api.tokens.register_operator(
        token, OperatorPrincipal(operator_id="viewer", roles=frozenset({"read", "reconcile"}))
    )
    response = world.approve(headers={"Authorization": f"Bearer {token}"})
    assert error(response)[:2] == (403, "FORBIDDEN_SCOPE")
    assert world.docker.calls == []
    assert_untouched(world)


def test_expected_image_mismatch_is_source_changed(world):
    world.merge()
    response = world.approve(expected_current_image_id="sha256:" + "2" * 64)
    assert error(response) == (409, "SOURCE_CHANGED", "image_changed")
    assert response.json()["error"]["details"]["current_image_id"] == BASE_IMAGE
    assert_untouched(world)


def test_missing_runtime_or_docker_lookup_failure_refuses(world):
    world.merge()
    world.docker.daemon_down = True
    assert error(world.approve(key="k1")) == (503, "LOOKUP_INCOMPLETE", "docker_lookup_failed")
    world.docker.daemon_down = False
    world.docker.containers.pop(world.mes)
    assert error(world.approve(key="k2")) == (409, "SOURCE_CHANGED", "runtime_missing")
    assert_untouched(world)


def _no_reviews(world):
    world.github.reviews[world.pr_number] = []


def _bot_approval(world):
    world.github.reviews[world.pr_number] = []
    world.github.add_review(world.pr_number, reviewer_id=BOT, commit_id=world.candidate_sha)


def _approval_of_older_head(world):
    world.github.reviews[world.pr_number] = []
    world.github.add_review(world.pr_number, reviewer_id=REVIEWER, commit_id=world.base)


def _changes_requested_later(world):
    world.github.add_review(
        world.pr_number,
        reviewer_id=REVIEWER,
        state="CHANGES_REQUESTED",
        commit_id=world.candidate_sha,
    )


@pytest.mark.parametrize(
    "setup", [_no_reviews, _bot_approval, _approval_of_older_head, _changes_requested_later]
)
def test_review_must_be_a_human_approval_of_the_candidate_head(world, setup):
    world.merge()
    setup(world)
    assert error(world.approve()) == (409, "STATE_CONFLICT", "review_not_on_candidate")
    assert_untouched(world)


def test_later_comment_does_not_withdraw_the_approval(world):
    world.merge()
    world.github.add_review(
        world.pr_number, reviewer_id=REVIEWER, state="COMMENTED", commit_id=world.candidate_sha
    )
    assert world.approve().status_code == 202


@pytest.mark.parametrize(
    ("override", "status", "code", "reason"),
    [
        ({"expected_incident_version": 0}, 409, "STATE_CONFLICT", "incident_not_ready"),
        ({"pr_number": 999}, 409, "STATE_CONFLICT", "pr_mismatch"),
        ({"work_id": "WORK-0000000000FF"}, 409, "STATE_CONFLICT", "work_mismatch"),
        ({"proposal_id": "PROP-0000000000FF"}, 409, "STATE_CONFLICT", "proposal_mismatch"),
        ({"run_id": OTHER_RUN}, 404, "RESOURCE_NOT_FOUND", None),
        ({"incident_id": "INC-0000000000FF"}, 404, "RESOURCE_NOT_FOUND", None),
    ],
)
def test_request_must_match_incident_work_proposal_and_pr(world, override, status, code, reason):
    world.merge()
    before = len(world.github.requests)
    assert error(world.approve(**override)) == (status, code, reason)
    assert_untouched(world)
    assert len(world.github.requests) == before and world.docker.calls == []  # 외부 조회 전에 거부


def test_github_lookup_failure_is_retryable_without_changes(world):
    world.merge()
    world.github.fail_next("timeout", when=lambda r: r.path.endswith(f"/pulls/{world.pr_number}"))
    body = world.body()
    assert error(world.approve(body=body)) == (503, "LOOKUP_INCOMPLETE", "github_lookup_failed")
    assert_untouched(world)
    assert world.approve(body=body).status_code == 202  # 같은 키로 다시 시도할 수 있다


def test_release_lock_blocks_another_release_in_the_run(world):
    world.merge()
    with world.store.tx() as tx:
        release._hold(tx, RUN, "EXE-0000000000EE")
    before = len(world.github.requests)
    response = world.approve()
    assert error(response) == (409, "STATE_CONFLICT", "release_locked")
    assert response.json()["error"]["details"]["holder_execution_id"] == "EXE-0000000000EE"
    assert len(world.github.requests) == before and world.docker.calls == []  # 조회 전에 거부


def test_catalog_must_register_the_repository_for_this_service(world):
    world.merge()
    world.executor.catalog = Catalog.from_config(CONFIG)  # 등록 repo 없음(G2 전)
    assert error(world.approve()) == (503, "PROTECTION_UNAVAILABLE", "repository_not_registered")
    assert_untouched(world)


def test_last_push_approval_setting_is_recorded_not_enforced(world):
    world.merge()
    world.github.branch_rules[f"baseline/{RUN}"] = [
        {"type": "pull_request", "parameters": {"require_last_push_approval": True}}
    ]
    assert world.approve().status_code == 202
    review = json.loads(world.deploy()["request_json"])["review"]
    assert (review["require_last_push_approval"], review["rule_observation"]) == (True, "ruleset")


def test_unreadable_rules_do_not_block_but_are_recorded(world):
    world.merge()
    world.github.fail_next("forbidden", when=lambda r: "/rules/branches/" in r.path)
    assert world.approve().status_code == 202
    review = json.loads(world.deploy()["request_json"])["review"]
    assert (review["require_last_push_approval"], review["rule_observation"]) == (
        None,
        "lookup_failed:Forbidden",
    )


def test_ops_releases_without_executor_is_dependency_unavailable(world):
    world.merge()
    state = world.api.client.app.state
    state.ctx = dataclasses.replace(state.ctx, release_executor=None)
    assert error(world.approve())[:2] == (503, "DEPENDENCY_UNAVAILABLE")
    assert_untouched(world)


# ── 배포 실패 처리 ───────────────────────────────────────────


def approved(world) -> str:
    world.merge()
    response = world.approve()
    assert response.status_code == 202, response.text
    return response.json()["data"]["execution_id"]


def assert_failed(world, stage: str, reason: str, blocker: str, environment: str) -> dict:
    execution = world.deploy()
    result = json.loads(execution["result_json"])
    assert (execution["status"], execution["stage"]) == ("FAILED", stage)
    assert result["failure"] == {"stage": stage, "reason": reason, "environment": environment}
    incident, work = world.incident_row(), world.work_row()
    assert (incident["status"], incident["reason_code"]) == ("ESCALATED", blocker)
    assert (work["status"], work["reason_code"]) == ("BLOCKED", blocker)
    (notice,) = notifications(world, "RECOVERY_NOT_VERIFIED")
    sent = payload(notice)
    assert (sent["verdict"], sent["stage"], sent["reason"], sent["environment"]) == (
        "NOT_DEPLOYED",
        stage,
        reason,
        environment,
    )
    assert (sent["contract_id"], sent["blocker_code"]) == ("defect-summary-v1", blocker)
    assert "restore" not in sent  # host 경로가 든 복원 명령은 알림에 넣지 않는다
    assert world.lock() is None and "RELEASE_FAILED" in world.audit_types()
    assert world.conn.execute("SELECT COUNT(*) FROM verifications").fetchone()[0] == 0
    return result


def assert_previous_kept(world) -> None:
    info = world.docker.containers[world.mes]
    assert (info["Id"], info["Image"]) == (world.previous_id, BASE_IMAGE)
    assert world.mes_runs() == [] and ("stop", world.mes) not in world.docker.calls


def test_fetch_failure_escalates_after_confirming_environment(world, tmp_path):
    approved(world)
    world.executor.fetcher = GitFetcher(str(tmp_path / "missing.git"), None, protocols=("file",))
    world.drain()
    reason = json.loads(world.deploy()["result_json"])["failure"]["reason"]
    assert reason.startswith("git_exit_128")
    assert_failed(world, "fetch", reason, "LOOKUP_INCOMPLETE", "unchanged")
    assert_previous_kept(world)
    assert not [c for c in world.docker.calls if c[0] in ("build", "run")]
    assert not (world.runs_dir / RUN / "releases").exists()  # checkout도 하지 않았다


def test_recheck_failure_does_not_build(world):
    approved(world)
    world.outcomes["R2"] = Scripted(exit_code=1, junit="r2_regression_failed")
    world.drain()
    result = assert_failed(
        world,
        "recheck",
        "REGRESSION_FAILED:new_test_not_passed",
        "VALIDATION_FAILED",
        "unchanged",
    )
    assert [c["check"] for c in result["checks"]] == ["R0", "R1", "R2"]
    assert_previous_kept(world)
    assert not [c for c in world.docker.calls if c[0] == "build"]


def test_mirror_tree_is_checked_again_not_only_github(world):
    """GitHub가 candidate tree라고 답해도 신뢰 mirror에서 본 merge commit tree가 다르면 멈춘다."""
    human = world.tree_with_human_change()
    world.merge_sha = world.squash(tree=human)  # 원격의 실제 merge commit
    world.github.merge_pull(world.pr_number, world.merge_sha, world.candidate_tree)
    assert world.approve().status_code == 202
    world.drain()
    result = assert_failed(world, "checkout", "tree_mismatch", "SOURCE_CHANGED", "unchanged")
    assert result["approved_tree"] == human
    assert_previous_kept(world)
    assert not [c for c in world.docker.calls if c[0] == "build"]


@pytest.mark.parametrize(
    ("disturb", "environment"),
    [
        (lambda w: w.docker.containers.pop(w.mes), "previous_missing"),
        (lambda w: w.docker.containers[w.mes].update(Image="sha256:" + "5" * 64), "changed"),
        (lambda w: w.docker.containers[w.mes]["State"].update(Running=False), "changed"),
        (lambda w: setattr(w.docker, "daemon_down", True), "unknown"),
    ],
)
def test_failure_records_the_environment_it_actually_saw(world, disturb, environment):
    approved(world)
    world.outcomes["R1"] = Scripted(exit_code=0, junit="r2_all_passed")  # 재현 안 됨
    disturb(world)
    world.drain()
    assert_failed(
        world, "recheck", "REPRO_NOT_FAILING:passed_on_base", "VALIDATION_FAILED", environment
    )


def test_stop_failure_keeps_the_previous_container(world):
    approved(world)
    world.docker.stop_errors[world.mes] = 1
    world.drain()
    assert_failed(world, "stop", "stop_failed", "VALIDATION_FAILED", "unchanged")
    info = world.docker.containers[world.mes]
    assert (info["Id"], info["Image"]) == (world.previous_id, BASE_IMAGE)
    assert world.mes_runs() == []


def test_stop_reported_failure_but_container_gone_continues(world):
    approved(world)
    world.docker.stop_errors[world.mes] = 1
    stop = world.docker.stop

    def remove_then_fail(name):
        result = stop(name)
        world.docker.containers.pop(name, None)  # 오류를 돌려줬지만 실제로는 지워졌다
        return result

    world.docker.stop = remove_then_fail  # type: ignore[method-assign]
    (summary,) = world.drain()
    assert summary["verdict"] == "PASS" and len(world.mes_runs()) == 1


def test_same_error_in_new_container_logs_fails_verification(world):
    approved(world)
    world.fake_mes.log_recurrence = True
    (summary,) = world.drain()
    assert (summary["verdict"], summary["verification_reason"]) == ("FAIL", "error_recurred")
    assert world.incident_row()["reason_code"] == "VERIFICATION_FAILED"


def test_recurrence_signature_uses_the_error_kind():
    details = {
        "signature": {
            "error_type": "KeyError:inspector_id",
            "top_frame": "app.defects:summarize",
            "endpoint": "/defects/summary",
        }
    }
    assert release.recurrence_signature(details) == release.RecurrenceSignature(
        error_type="KeyError", top_frame="app.defects:summarize", path="/defects/summary"
    )
    assert release.recurrence_signature({"signature": {"error_type": "KeyError"}}) is None
    assert release.recurrence_signature({}) is None


def test_build_failure_keeps_the_previous_container(world):
    approved(world)
    world.docker.build_error = "pip install 실패"
    world.drain()
    assert_failed(world, "build", "build_failed", "VALIDATION_FAILED", "unchanged")
    assert_previous_kept(world)
    (notice,) = notifications(world, "RECOVERY_NOT_VERIFIED")
    rendered = templates.render(
        "RECOVERY_NOT_VERIFIED",
        payload(notice),
        repo=world.github.full_name,
        issue_number=world.pr.issue_number,
        notification_id=notice["id"],
        payload_sha256=notice["payload_sha256"],
    )
    assert "배포 안 됨" in rendered.title
    assert "이전 MES 컨테이너가 그대로 실행 중" in rendered.body
    assert "NOT_DEPLOYED" not in rendered.body


def test_runtime_changed_by_someone_else_is_not_overwritten(world):
    approved(world)
    world.docker.containers[world.mes]["Id"] = "e" * 64  # 다른 운영자가 다시 띄웠다
    world.drain()
    assert_failed(world, "deploy", "runtime_changed", "SOURCE_CHANGED", "changed")
    assert world.mes_runs() == [] and ("stop", world.mes) not in world.docker.calls


def test_start_failure_after_stop_records_state_and_restore_procedure(world):
    approved(world)
    world.docker.run_errors[world.mes] = 125
    world.drain()
    result = assert_failed(world, "start", "start_failed", "VALIDATION_FAILED", "previous_removed")
    assert result["observed"]["exists"] is False  # 실제 상태: MES가 없다
    restore = result["restore"]
    assert restore["available"] is True and restore["image_id"] == BASE_IMAGE
    assert restore["commands"][1] == [
        "docker",
        "run",
        "--detach",
        *mes_container_options(world.mes, RUN, world.network, world.s1_data),
        BASE_IMAGE,
    ]
    assert len(world.mes_runs()) == 1  # 자동 rollback·재시도 없음
    assert world.mes not in world.docker.containers


def test_start_timeout_is_unknown_and_not_retried(world):
    execution_id = approved(world)
    world.docker.run_errors[world.mes] = 124
    world.drain()
    execution = world.deploy()
    result = json.loads(execution["result_json"])
    assert (execution["status"], execution["stage"], result["observation"]) == (
        "UNKNOWN",
        "start",
        "start_timeout",
    )
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "EXECUTION_UNKNOWN",
        "EXECUTION_UNKNOWN",
    )
    assert world.lock() == execution_id  # 실제 대상 확인 전 다른 배포 금지
    assert result["restore"]["available"] is True
    assert len(world.mes_runs()) == 1 and notifications(world, "RECOVERY_NOT_VERIFIED") == []


def test_inspect_mismatch_after_start_is_unknown(world):
    approved(world)
    handler = world.docker.run_handler

    def swapped(name, options, command):
        handler(name, options, command)
        if name == world.mes:
            world.docker.containers[name]["Image"] = "sha256:" + "9" * 64

    world.docker.run_handler = swapped
    world.drain()
    execution = world.deploy()
    assert (execution["status"], json.loads(execution["result_json"])["observation"]) == (
        "UNKNOWN",
        "image_mismatch",
    )


def test_unexpected_error_before_deploy_escalates(world, monkeypatch):
    approved(world)

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(world.executor.runner, "image_problem", broken)
    world.drain()
    assert_failed(
        world, "release_error", "unexpected_error:RuntimeError", "VALIDATION_FAILED", "unchanged"
    )


def test_verification_fail_escalates_without_rollback(world):
    approved(world)
    world.fake_mes.wrong_answers = True
    (summary,) = world.drain()
    assert (summary["verdict"], summary["verification_reason"]) == ("FAIL", "content_mismatch")
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "ESCALATED",
        "BLOCKED",
    )
    (notice,) = notifications(world, "RECOVERY_NOT_VERIFIED")
    assert payload(notice)["verdict"] == "FAIL"
    assert world.docker.containers[world.mes]["Image"] != BASE_IMAGE  # 자동 rollback 없음
    assert len(world.mes_runs()) == 1 and world.lock() is None


def test_run_only_starts_intended_executions(world):
    execution_id = approved(world)
    world.drain()
    again = world.executor.run(execution_id)
    assert (again["outcome"], again["status"]) == ("NOT_INTENDED", "SUCCEEDED")
    assert len(world.mes_runs()) == 1


# ── reconcile DEPLOY ─────────────────────────────────────────


def unknown_start(world) -> str:
    execution_id = approved(world)
    world.docker.run_errors[world.mes] = 124
    world.drain()
    return execution_id


def reconcile(world, execution_id: str, key: str = "rc-1"):
    return world.api.client.post(
        f"/ops/executions/{execution_id}/reconcile",
        json={"schema_version": "linemedic.v4", "run_id": RUN},
        headers={**world.api.operator, "Idempotency-Key": key},
    )


def test_reconcile_found_starts_a_new_verification(world):
    execution_id = unknown_start(world)
    (failed_run,) = world.mes_runs()
    world.docker.run(failed_run[1], failed_run[2])  # timeout 뒤 실제로는 떠 있었다
    response = reconcile(world, execution_id)
    data = response.json()["data"]
    assert data["outcome"] == "FOUND" and data["verification_id"]
    execution = world.deploy()
    assert (execution["status"], execution["stage"]) == ("SUCCEEDED", "reconciled")
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "VERIFYING",
        "WAITING_VERIFICATION",
    )
    (summary,) = world.drain()
    assert (summary["verdict"], summary["incident_status"]) == ("PASS", "RESOLVED")
    assert world.lock() is None and len(world.mes_runs()) == 2  # 조정은 기동하지 않았다


def test_target_replaced_before_t0_is_inconclusive(world):
    execution_id = unknown_start(world)
    (failed_run,) = world.mes_runs()
    world.docker.run(failed_run[1], failed_run[2])
    assert reconcile(world, execution_id).json()["data"]["outcome"] == "FOUND"
    world.docker.containers[world.mes]["Id"] = "d" * 64  # 조정과 검증 시작 사이에 바뀌었다
    (summary,) = world.drain()
    assert (summary["verdict"], summary["verification_reason"]) == (
        "INCONCLUSIVE",
        "verifier_error",
    )
    stored = json.loads(world.conn.execute("SELECT result_json FROM verifications").fetchone()[0])
    assert stored["detail"] == "identity_changed_before_t0" and stored["samples_completed"] == 0
    assert [u for u in world.fake_mes.requests if "/defects/" in u] == []  # 표본 요청 없음
    assert world.incident_row()["status"] == "ESCALATED"


def test_reconcile_absent_escalates_and_releases_the_lock(world):
    execution_id = unknown_start(world)
    data = reconcile(world, execution_id).json()["data"]
    assert (data["outcome"], data["escalated"]) == ("CONFIRMED_ABSENT", True)
    incident, work = world.incident_row(), world.work_row()
    assert (incident["status"], work["status"]) == ("ESCALATED", "BLOCKED")
    (blocked,) = notifications(world, "WORK_BLOCKED")
    assert payload(blocked)["blocker_code"] == "EXTERNAL_RESULT_UNKNOWN"
    assert world.lock() is None and len(world.mes_runs()) == 1
    assert world.deploy()["status"] == "UNKNOWN"  # 조정 기록만 남긴다


def test_reconcile_conflict_escalates_without_overwriting(world):
    execution_id = unknown_start(world)
    world.docker.run(["--name", world.mes, "--network", world.network], "sha256:" + "7" * 64)
    data = reconcile(world, execution_id).json()["data"]
    assert (data["outcome"], data["escalated"]) == ("CONFLICT", True)
    (blocked,) = notifications(world, "WORK_BLOCKED")
    assert payload(blocked)["blocker_code"] == "SOURCE_CHANGED"
    assert world.docker.containers[world.mes]["Image"] == "sha256:" + "7" * 64


def test_reconcile_lookup_failure_only_records(world):
    execution_id = unknown_start(world)
    world.docker.daemon_down = True
    data = reconcile(world, execution_id).json()["data"]
    assert data["outcome"] == "INCONCLUSIVE" and "escalated" not in data
    assert world.incident_row()["status"] == "EXECUTION_UNKNOWN"
    assert world.lock() == execution_id
    stored = json.loads(world.deploy()["result_json"])
    assert stored["reconcile"]["outcome"] == "INCONCLUSIVE"


def test_reconcile_without_release_executor_is_dependency_unavailable(world):
    execution_id = unknown_start(world)
    world.reconciler.release = None
    assert error(reconcile(world, execution_id))[:2] == (503, "DEPENDENCY_UNAVAILABLE")


# ── 재시작 ────────────────────────────────────────────────────


def test_restart_marks_intended_deploy_unknown_and_reconcile_sees_no_change(world):
    execution_id = approved(world)
    recovered = world.executor.recover()
    assert recovered == {"unknown": [execution_id], "inconclusive": []}
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "EXECUTION_UNKNOWN",
        "EXECUTION_UNKNOWN",
    )
    assert world.drain()[0]["outcome"] == "NOT_INTENDED"  # 늦게 도는 job도 배포하지 않는다
    data = world.executor.reconcile(execution_id)
    assert (data["outcome"], data["escalated"]) == ("CONFIRMED_ABSENT", True)  # 이전 그대로
    assert world.mes_runs() == [] and world.lock() is None


def test_restart_during_verification_closes_it_inconclusive(world, monkeypatch):
    execution_id = approved(world)
    monkeypatch.setattr(world.executor, "_verify", lambda exe, ver: {"stopped": True})
    world.drain()  # 검증 시작 직후 프로세스가 죽은 상태
    running = world.conn.execute("SELECT * FROM verifications").fetchone()
    assert running["verdict"] == "RUNNING" and world.incident_row()["status"] == "VERIFYING"
    assert world.lock() == execution_id
    monkeypatch.undo()
    recovered = world.executor.recover()
    assert recovered == {"unknown": [], "inconclusive": [running["id"]]}
    closed = world.conn.execute("SELECT * FROM verifications").fetchone()
    result = json.loads(closed["result_json"])
    assert (closed["verdict"], result["reason"], result["detail"]) == (
        "INCONCLUSIVE",
        "verifier_error",
        "interrupted_by_restart",
    )
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "ESCALATED",
        "BLOCKED",
    )
    assert world.lock() is None


# ── 재검사 tree ───────────────────────────────────────────────


def test_release_trees_come_from_the_exact_merge_commit(world, tmp_path):
    merge_sha = world.merge()
    git(f"--git-dir={world.mirror}", "fetch", "--quiet", str(world.remote), merge_sha)
    trees = prepare_release_trees(
        mirror=world.mirror,
        workdir=tmp_path / "rt",
        base_sha=world.base,
        merge_sha=merge_sha,
        new_test_path=NEW_TEST,
    )
    assert trees.final_tree == world.candidate_tree
    assert (trees.trees["repro"] / NEW_TEST).is_file()
    assert (trees.trees["final"] / "app" / "defects.py").read_text().count("미지정") == 1
    assert "미지정" not in (trees.trees["repro"] / "app" / "defects.py").read_text()
    with pytest.raises(CandidateError, match="merge_not_in_mirror"):
        prepare_release_trees(
            mirror=world.mirror,
            workdir=tmp_path / "missing",
            base_sha=world.base,
            merge_sha="b" * 40,
            new_test_path=NEW_TEST,
        )
    with pytest.raises(CandidateError, match="new_test_missing"):
        prepare_release_trees(
            mirror=world.mirror,
            workdir=tmp_path / "no-test",
            base_sha=world.base,
            merge_sha=merge_sha,
            new_test_path="tests/repro/test_other.py",
        )


# ── CLI (G8) ─────────────────────────────────────────────────


def cli_args(world, tmp_path) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=RUN,
        incident_id=world.incident,
        work_id=world.work,
        pr_number=world.pr_number,
        merge_sha=world.merge_sha,
        expected_image_id=BASE_IMAGE,
        proposal_id=None,
        note="G8 승인",
        config=cli.DEFAULT_CONFIG_PATH,
        env_file=tmp_path / "none.env",
    )


def forward(world, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response = world.api.client.request(
            request.method,
            request.url.path,
            headers={k: v for k, v in request.headers.items() if k != "host"},
            content=request.content,
        )
        return httpx.Response(response.status_code, content=response.content)

    return httpx.MockTransport(handler)


def test_cli_shows_checklist_and_posts_only_after_typed_approve(
    world, tmp_path, monkeypatch, capsys
):
    world.merge()
    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args, seen = cli_args(world, tmp_path), []
    transport = forward(world, seen)
    assert cli._approve_release(args, transport, confirm=lambda _: "yes", interactive=True) == 1
    out = capsys.readouterr().out
    assert all(f"[ ] {item}" in out for item in cli.RELEASE_CHECKLIST)
    assert world.candidate_sha in out and world.merge_sha in out
    assert (
        cli._approve_release(args, transport, confirm=lambda _: "approve", interactive=False) == 2
    )
    assert not [r for r in seen if r.method == "POST"] and world.deploys() == []

    assert cli._approve_release(args, transport, confirm=lambda _: "approve", interactive=True) == 0
    (post,) = [r for r in seen if r.method == "POST"]
    assert post.url.path == "/ops/releases"
    assert post.headers["idempotency-key"] == f"release:{world.work}:{world.merge_sha}"
    body = json.loads(post.content)
    assert (body["proposal_id"], body["expected_incident_version"]) == (
        world.proposal_id,
        world.incident_row()["version"] - 1,
    )
    assert world.deploy()["status"] == "INTENDED"


def test_cli_refuses_when_the_bot_pr_is_ambiguous(world, tmp_path, monkeypatch, capsys):
    world.merge()
    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args = cli_args(world, tmp_path)
    args.proposal_id = "PROP-0000000000FF"
    seen: list = []
    code = cli._approve_release(
        args, forward(world, seen), confirm=lambda _: "approve", interactive=True
    )
    assert code == 1 and not [r for r in seen if r.method == "POST"]
    assert "봇 PR execution" in capsys.readouterr().err
