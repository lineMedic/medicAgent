"""W11 봇 PR 생성: 실제 git candidate + scripted runner + FakeGitHub·FakePusher.

- 통과한 candidate → `autofix/<run>/<incident>/<proposal>` push → PR(base `baseline/<run>`)
  → head SHA 확인 → SUCCEEDED·PR_OPENED·WAITING_REVIEW·PR_READY
- PR 본문: `Related to #<n>`, closing keyword 없음, 에이전트 문장 정제, "보장하지 않는 것", marker
- T-IDEM-01(PR 경로): 같은 제안을 두 번 처리해도 PR 1개
- 사전 조건 불충족(시작 알림·Issue closed·사람 담당자·사람 PR·baseline 이동·브랜치 점유·shadow·조회
  실패·취소 요청) → PR 생성 0, 사유 기록, 에이전트 수정 없이 멈춤
- 외부 거절(push·create_pull·head 불일치) → execution FAILED·차단, 남은 브랜치·PR을 보고에 적음
"""

import json
import re
from pathlib import Path

import pytest

from linemedic.control_plane import audit
from linemedic.control_plane.broker.github_pr import (
    Precheck,
    PrPlan,
    Stop,
    branches,
    has_closing_keyword,
    neutralize_closing,
    pr_marker,
)
from linemedic.control_plane.state import Actor
from linemedic.tests.helpers.api import RUN
from linemedic.tests.helpers.pr_world import ATTEMPT, BOT, PrWorld, build_seed_mirror


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


@pytest.fixture
def world(store, conn, fake_clock, seed, tmp_path):
    return PrWorld(store, conn, fake_clock, seed, tmp_path)


def blocked_report(w):
    (row,) = w.notifications("WORK_BLOCKED")
    return json.loads(row["payload_json"])


# ── 성공 ──────────────────────────────────────────────────────


def test_passing_candidate_opens_one_verified_bot_pr(world):
    proposal_id = world.run()
    row, record = world.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("ALLOWED", "PR_OPENED")
    execution = world.execution()
    result = json.loads(execution["result_json"])
    assert (execution["status"], execution["stage"]) == ("SUCCEEDED", "verified")
    head, base = branches(RUN, world.incident, proposal_id)
    candidate = record["candidate"]["candidate_sha"]
    assert execution["logical_key"] == f"pr:{world.work}:{proposal_id}:{candidate}"
    (pull,) = world.github.pulls.values()
    assert (pull["head"]["ref"], pull["head"]["sha"], pull["base"]["ref"]) == (
        head,
        candidate,
        base,
    )
    assert pull["user"]["id"] == BOT and result["pr_number"] == pull["number"]
    assert world.github.branches[head] == candidate and world.pusher.pushes == [(head, candidate)]
    assert (world.incident_row()["status"], world.work_row()["status"]) == (
        "PR_OPENED",
        "WAITING_REVIEW",
    )
    (ready,) = world.notifications("PR_READY")
    payload = json.loads(ready["payload_json"])
    assert (payload["pr_number"], payload["candidate_sha"]) == (pull["number"], candidate)
    request = json.loads(execution["request_json"])
    assert request["marker"] == pr_marker(execution["id"], candidate)


def mark_manual_attempt(world) -> None:
    """W13 supervisor가 ScriptedAdapter로 attempt를 시작했다는 감사 기록."""
    with world.store.tx() as tx:
        audit.append(
            tx,
            RUN,
            world.incident,
            Actor.SUPERVISOR,
            "ATTEMPT_STARTED",
            {"attempt_id": ATTEMPT, "adapter": "scripted", "origin": "manual_integration"},
        )


def test_manual_proposal_is_not_presented_as_agent_output(world):  # W13
    mark_manual_attempt(world)
    world.run()
    (pull,) = world.github.pulls.values()
    body = pull["body"]
    source = "- 제안 출처: 사람이 미리 작성한 제안(manual_integration). 모델 산출물이 아닙니다"
    assert source in body
    assert "원인 가설(사람이 미리 작성한 제안, 검증되지 않음)" in body
    assert "에이전트 판단" not in body


def test_pr_body_links_the_issue_without_closing_it(world):
    hostile = "fixes #1, closes https://github.com/demo-team/l3-mes-api/issues/1 @lead <b>"
    world.run(root_cause_hypothesis=hostile)
    (pull,) = world.github.pulls.values()
    body = pull["body"]
    assert f"Related to #{world.issue_number}" in body
    assert not has_closing_keyword(body) and not has_closing_keyword(pull["title"])
    assert "@lead" not in body and "<b>" not in body and "&lt;b&gt;" in body
    assert "원인 가설(에이전트 판단, 검증되지 않음)" in body
    for line in (
        "재현 테스트는 실제 원인이 반드시 코드라는 증거가 아닙니다.",
        "배포 후 별도 업무 계약 검사 전에는 복구 완료가 아닙니다.",
    ):
        assert line in body
    assert "/private/" not in body and "checkouts" not in body  # 내부 경로를 넣지 않는다
    assert body.rstrip().endswith(json.loads(world.execution()["request_json"])["marker"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("fixes #12", "관련 #12"),
        ("Closes: #3", "관련: #3"),
        ("resolved demo-team/l3-mes-api#4", "관련 demo-team/l3-mes-api#4"),
        ("fix https://github.com/x/y/issues/1", "관련 https://github.com/x/y/issues/1"),
        ("the fixture fixes nothing", "the fixture fixes nothing"),
        ("Related to #7", "Related to #7"),
    ],
)
def test_closing_keywords_are_neutralized(text, expected):
    assert neutralize_closing(text) == expected


def test_pr_body_shows_the_reproduced_failure(world):
    """spec 06 §8: R1 줄에 개수만이 아니라 실패 이유를 보인다(PR #51 리뷰 1)."""
    world.run()
    (pull,) = world.github.pulls.values()
    (r1,) = [line for line in pull["body"].splitlines() if "(R1)" in line]
    assert "test_missing_inspector_is_counted_as_unassigned" in r1
    assert "KeyError: 'inspector_id'" in r1


@pytest.mark.parametrize(
    "hypothesis",
    [
        "원인fixes #1",
        "이 수정은fixes #1",
        "_fixes #1",
        "**fixes** #1",
        "fixes https://github.com/demo-team/l3-mes-api/issues/1",
        "closes demo-team/l3-mes-api#1",
    ],
)
def test_agent_text_cannot_reference_or_close_issues(world, hypothesis):
    """에이전트 문장의 Issue 참조·URL을 무력화한다. 남는 참조는 서버가 쓴 `Related to #n`뿐이다
    (PR #51 리뷰 2)."""
    world.run(root_cause_hypothesis=hypothesis)
    (pull,) = world.github.pulls.values()
    body = pull["body"]
    assert not has_closing_keyword(body)
    assert re.findall(r"#\d+", body) == [f"#{world.issue_number}"]
    assert f"Related to #{world.issue_number}" in body


@pytest.mark.parametrize("text", ["원인fixes #1", "이 수정은fixes #1", "_fixes #1", "**fixes** #1"])
def test_closing_keyword_check_does_not_need_a_word_boundary(text):
    assert has_closing_keyword(text)
    assert not has_closing_keyword(neutralize_closing(text))


def test_same_proposal_processed_twice_makes_one_pr(world):  # T-IDEM-01
    proposal_id = world.run()
    world.conn.execute("UPDATE proposals SET decision = 'RECEIVED' WHERE id = ?", (proposal_id,))
    world.conn.execute(
        "UPDATE incidents SET status = 'VALIDATING' WHERE id = ?", (world.incident,)
    )  # 두 번째 처리가 게이트까지 가도록 상태를 되돌린다
    world.broker.process_pending()
    assert len(world.github.pulls) == 1 and world.pull_posts() == 1
    assert len(world.pusher.pushes) == 1
    assert world.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


# ── 재사용·점유 판정 ─────────────────────────────────────────


def plan_for(world, proposal_id="PROP-00000000000A"):
    head, base = branches(RUN, world.incident, proposal_id)
    return PrPlan(
        run_id=RUN,
        incident_id=world.incident,
        work_id=world.work,
        generation=1,
        proposal_id=proposal_id,
        idempotency_key="k",
        body_sha256="0" * 64,
        repository_id=world.github.repository_id,
        repo=world.github.full_name,
        issue_number=world.issue_number,
        head=head,
        base=base,
        base_sha=world.base,
        candidate_sha="c" * 40,
        candidate_tree="d" * 40,
        title="t",
        lines=(),
    )


def evaluate(world, plan, **overrides):
    found = Precheck(
        issue=world.github.issues[world.issue_number],
        baseline_sha=world.base,
        bot_id=BOT,
        **overrides,
    )
    with world.store.tx() as tx:
        work = tx.one("SELECT * FROM work_items WHERE id = ?", (world.work,))
        return world.opener.evaluate(tx, work, plan, found)


def pull(plan, *, author=BOT, sha=None, base=None, number=50):
    return {
        "number": number,
        "user": {"id": author},
        "head": {"ref": plan.head, "sha": sha or plan.candidate_sha},
        "base": {"ref": base or plan.base},
        "body": "",
    }


def test_existing_bot_pr_for_the_same_candidate_is_reused(world):
    plan = plan_for(world)
    ours = pull(plan)
    assert evaluate(world, plan, head_sha=plan.candidate_sha, head_pulls=[ours]) == ours
    assert evaluate(world, plan) is None  # 브랜치·PR이 없으면 만든다


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "e" * 40},  # 다른 SHA가 브랜치를 쓴다(force push하지 않는다)
        {"head_sha": "c" * 40, "head_pulls": ["other_author"]},
        {"head_sha": "c" * 40, "head_pulls": ["other_sha"]},
        {"head_sha": "c" * 40, "head_pulls": ["other_base"]},
        {"head_sha": "c" * 40, "head_pulls": ["ours", "ours2"]},
        {"head_sha": None, "head_pulls": ["ours"]},
    ],
)
def test_branch_or_pr_used_by_someone_else_is_not_overwritten(world, overrides):
    plan = plan_for(world)
    kinds = {
        "other_author": pull(plan, author=200001),
        "other_sha": pull(plan, sha="f" * 40),
        "other_base": pull(plan, base="main"),
        "ours": pull(plan),
        "ours2": pull(plan, number=51),
    }
    overrides = {**overrides}
    overrides["head_pulls"] = [kinds[k] for k in overrides.get("head_pulls", [])]
    verdict = evaluate(world, plan, **overrides)
    assert verdict == Stop("STATE_CONFLICT", "branch_in_use", "HUMAN_WORK_IN_PROGRESS")


# ── 사전 조건 불충족: PR 0 ────────────────────────────────────


def assert_stopped(world, proposal_id, code, reason, blocker):
    row, record = world.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("REJECTED", code)
    assert record["checks"][-1] == {"check": "CREATE_PR", "result": code, "reason": reason}
    assert world.pull_posts() == 0 and world.pusher.pushes == []
    assert world.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    assert (world.incident_row()["status"], world.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    report = blocked_report(world)
    assert (report["blocker_code"], report["stage"]) == (blocker, "external_write")
    assert world.incident_row()["submissions"] == 1  # 예산이 남아도 에이전트 수정이 아니다


def test_unaccepted_start_notice_blocks_pr(world):
    proposal_id = world.submit()
    world.conn.execute("UPDATE notifications SET status = 'UNKNOWN' WHERE id = ?", (world.notice,))
    world.broker.process_pending()
    assert_stopped(
        world, proposal_id, "STATE_CONFLICT", "start_notice_unconfirmed", "START_NOTICE_UNCONFIRMED"
    )


def test_closed_issue_blocks_pr(world):
    world.github.update_issue(world.issue_number, state="closed")
    assert_stopped(world, world.run(), "SOURCE_CHANGED", "issue_closed", "SOURCE_CHANGED")


def test_changed_issue_scope_blocks_pr(world):
    world.github.update_issue(world.issue_number, body="요구가 바뀌었다")
    assert_stopped(world, world.run(), "SOURCE_CHANGED", "snapshot_changed", "SOURCE_CHANGED")


def test_human_assignee_blocks_pr(store, conn, fake_clock, seed, tmp_path):
    w = PrWorld(store, conn, fake_clock, seed, tmp_path, assignees=(200001,))
    assert_stopped(w, w.run(), "STATE_CONFLICT", "human_work_in_progress", "HUMAN_WORK_IN_PROGRESS")


def test_human_pr_for_the_issue_blocks_pr(world):
    world.github.add_pull(title="수정", body=f"Fix for #{world.issue_number}", author_id=200001)
    assert_stopped(
        world, world.run(), "STATE_CONFLICT", "human_work_in_progress", "HUMAN_WORK_IN_PROGRESS"
    )


@pytest.mark.parametrize(
    ("baseline", "reason"), [("e" * 40, "baseline_moved"), (None, "baseline_missing")]
)
def test_moved_or_missing_baseline_blocks_pr(world, baseline, reason):
    if baseline is None:
        del world.github.branches[world.baseline]
    else:
        world.github.branches[world.baseline] = baseline
    assert_stopped(world, world.run(), "SOURCE_CHANGED", reason, "SOURCE_CHANGED")


def test_occupied_head_branch_blocks_pr(world):
    proposal_id = world.submit()
    head, _ = branches(RUN, world.incident, proposal_id)
    world.github.branches[head] = "e" * 40  # 누군가 먼저 같은 이름의 브랜치를 만들었다
    world.broker.process_pending()
    assert_stopped(world, proposal_id, "STATE_CONFLICT", "branch_in_use", "HUMAN_WORK_IN_PROGRESS")
    assert world.github.branches[head] == "e" * 40  # 덮어쓰지 않았다


def test_shadow_mode_records_the_plan_without_github_writes(
    store, conn, fake_clock, seed, tmp_path
):
    w = PrWorld(store, conn, fake_clock, seed, tmp_path, write_enabled=False)
    proposal_id = w.run()
    assert_stopped(
        w, proposal_id, "PROTECTION_UNAVAILABLE", "github_write_disabled", "PERMISSION_REQUIRED"
    )
    _, record = w.proposal(proposal_id)
    assert record["pr_plan"]["head"] == branches(RUN, w.incident, proposal_id)[0]
    assert w.github.write_calls == 0


@pytest.mark.parametrize(
    "failing",
    [
        lambda r, n: r.path.endswith(f"/issues/{n}"),  # Issue
        lambda r, n: r.method == "GET" and r.path.endswith("/pulls"),  # 열린 PR 목록(Issue 뒤)
        lambda r, n: "/git/ref/heads/" in r.path,  # baseline 브랜치
        lambda r, n: r.path == "/user",  # 봇 identity
    ],
)
def test_lookup_failure_before_creation_blocks_pr(world, failing):
    number = world.issue_number
    world.github.fail_next("server_error", when=lambda r: failing(r, number))
    assert_stopped(
        world, world.run(), "PROTECTION_UNAVAILABLE", "lookup_incomplete", "LOOKUP_INCOMPLETE"
    )


def test_cancel_request_blocks_pr(world):
    proposal_id = world.submit()
    world.conn.execute("UPDATE work_items SET cancel_requested = 1 WHERE id = ?", (world.work,))
    world.broker.process_pending()
    assert_stopped(world, proposal_id, "STATE_CONFLICT", "cancel_requested", "PERMISSION_REQUIRED")


# ── 외부 거절: execution FAILED ──────────────────────────────


def assert_failed(world, proposal_id, stage, blocker, identities):
    row, record = world.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("ALLOWED", "EXECUTION_FAILED")
    execution = world.execution()
    assert (execution["status"], execution["stage"]) == ("FAILED", stage)
    assert (world.incident_row()["status"], world.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    report = blocked_report(world)
    assert report["blocker_code"] == blocker
    side_effect = report["side_effect_state"]
    assert side_effect["state"] == ("OBSERVED" if identities else "NONE")
    assert side_effect["identities"] == identities


def test_rejected_push_fails_without_pr(world):
    world.pusher.fail_next("REJECTED")
    proposal_id = world.run()
    assert_failed(world, proposal_id, "push", "PERMISSION_REQUIRED", [])
    assert world.pull_posts() == 0


def test_forbidden_pull_creation_reports_the_pushed_branch(world):
    world.github.fail_next(
        "forbidden", when=lambda r: r.method == "POST" and r.path.endswith("/pulls")
    )
    proposal_id = world.run()
    head, _ = branches(RUN, world.incident, proposal_id)
    assert_failed(world, proposal_id, "create_pull", "PERMISSION_REQUIRED", [f"branch {head}"])


def test_pr_whose_head_does_not_match_the_candidate_is_not_accepted(world):
    original = world.github._create_pull

    def create_then_move(body):
        response = original(body)
        response.data["head"]["sha"] = "e" * 40  # 그 사이 누가 브랜치를 바꿨다
        return response

    world.github._create_pull = create_then_move
    proposal_id = world.run()
    head, _ = branches(RUN, world.incident, proposal_id)
    (pull_row,) = world.github.pulls.values()
    assert_failed(
        world,
        proposal_id,
        "verify",
        "SOURCE_CHANGED",
        [f"branch {head}", f"PR #{pull_row['number']}"],
    )
    assert world.notifications("PR_READY") == []


@pytest.mark.parametrize(
    ("kind", "blocker", "reason"),
    [
        ("connect_failed", "PERMISSION_REQUIRED", "not_sent"),
        ("not_found", "SOURCE_CHANGED", "NotFound"),
        ("conflict", "HUMAN_WORK_IN_PROGRESS", "Conflict"),
        ("rate_limited", "PERMISSION_REQUIRED", "RateLimited"),
    ],
)
def test_definite_pull_creation_failures_map_to_blockers(world, kind, blocker, reason):
    world.github.fail_next(kind, when=lambda r: r.method == "POST" and r.path.endswith("/pulls"))
    proposal_id = world.run()
    head, _ = branches(RUN, world.incident, proposal_id)
    assert_failed(world, proposal_id, "create_pull", blocker, [f"branch {head}"])
    assert json.loads(world.execution()["result_json"])["reason"] == reason


def test_execute_skips_push_when_the_branch_already_holds_the_candidate(world):
    plan = plan_for(world)
    world.github.branches[plan.head] = plan.candidate_sha
    result = world.opener.execute(plan, "EXE-0000000000AA", Path("unused.git"), plan.candidate_sha)
    assert (result.status, result.stage) == ("SUCCEEDED", "verified")
    assert world.pusher.pushes == []


def test_state_change_during_the_precheck_stops_before_any_write(world):
    original = world.opener.precheck

    def precheck_while_operator_escalates(plan):
        found = original(plan)
        world.conn.execute(
            "UPDATE incidents SET status = 'ESCALATED', version = version + 1 WHERE id = ?",
            (world.incident,),
        )
        return found

    world.opener.precheck = precheck_while_operator_escalates
    proposal_id = world.run()
    row, record = world.proposal(proposal_id)
    assert (row["decision"], record["checks"][-1]["reason"]) == ("REJECTED", "state_changed")
    assert world.pull_posts() == 0 and world.pusher.pushes == []
    assert world.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_state_change_during_execution_records_the_pr_without_transition(world):
    original = world.opener.execute

    def execute_while_state_moves(*args):
        result = original(*args)
        world.conn.execute(
            "UPDATE incidents SET status = 'ESCALATED', version = version + 1 WHERE id = ?",
            (world.incident,),
        )
        return result

    world.opener.execute = execute_while_state_moves
    proposal_id = world.run()
    assert world.execution()["status"] == "SUCCEEDED"  # 외부 결과는 기록한다
    assert world.incident_row()["status"] == "ESCALATED"  # 바뀐 상태를 되돌리지 않는다
    assert world.notifications("PR_READY") == []
    assert world.proposal(proposal_id)[0]["decision"] == "ALLOWED"


def test_existing_bot_pr_is_reused_by_the_broker(world, seed, tmp_path):
    from linemedic.control_plane.broker.candidate import build_candidate
    from linemedic.control_plane.broker.patch_policy import check_diff, load_policy
    from linemedic.tests.helpers.pr_world import NEW_TEST, PATCHES

    proposal_id = world.submit()
    row, _ = world.proposal(proposal_id)
    diff = (PATCHES / "fix_missing_inspector.patch").read_text(encoding="utf-8")
    rules = load_policy().rules
    same = build_candidate(  # 같은 제안·접수 시각이면 같은 candidate SHA가 나온다
        mirror=seed[0],
        workdir=tmp_path / "precomputed",
        base_sha=world.base,
        diff=diff,
        plan=check_diff(diff, NEW_TEST, rules),
        rules=rules,
        proposal_id=proposal_id,
        committed_at=row["received_at"],
    )
    head, base = branches(RUN, world.incident, proposal_id)
    world.github.branches[head] = same.candidate_sha
    existing = world.github.add_pull(
        title="이전 실행의 봇 PR", author_id=BOT, head=head, head_sha=same.candidate_sha, base=base
    )
    world.broker.process_pending()
    assert world.proposal(proposal_id)[1]["decision_reason"] == "PR_OPENED"
    execution = world.execution()
    result = json.loads(execution["result_json"])
    assert (execution["stage"], result["reused"], result["pr_number"]) == (
        "reused",
        True,
        existing["number"],
    )
    assert world.pull_posts() == 0 and world.pusher.pushes == []
    assert world.incident_row()["status"] == "PR_OPENED"
