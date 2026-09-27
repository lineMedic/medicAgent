"""W10 통합: 실제 git candidate + 패치 게이트 + 브로커 연결.

컨테이너 대신 로컬 pytest(같은 argv·보호 설정)와 scripted docker를 쓴다.
- candidate: 작업 트리 없이 임시 index로 적용, 서버 commit(작성자·시각 고정),
  repro tree는 새 테스트만, 사용자 git 설정·hook을 쓰지 않음,
  적용 실패는 PATCH_PATH_DENIED, 환경 문제는 CandidateError
- 게이트: 정책 → 기준 base → candidate → runner image → R0 → R1 → R2. 실패하면 거기서 멈춘다
- 브로커: 게이트는 트랜잭션 밖. 통과해도 봇 PR 생성(W11) 전에는 멈추고(가짜 PR 없음),
  에이전트가 고칠 수 있는 실패만 수정 1회를 준다.
  에이전트에게는 검사 이름·코드·사유만 보인다(host 경로·로그 없음)
"""

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from linemedic.common.clock import FakeClock
from linemedic.control_plane.auth import AgentPrincipal
from linemedic.control_plane.broker.candidate import CandidateError, _extract, build_candidate
from linemedic.control_plane.broker.intake import Broker
from linemedic.control_plane.broker.patch_gate import GateRequest, PatchGate
from linemedic.control_plane.broker.patch_policy import PatchDenied, check_diff, load_policy
from linemedic.control_plane.broker.runner import Runner
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.deploys import record_deploy_observed
from linemedic.control_plane.evidence import add_evidence
from linemedic.control_plane.knowledge import KnowledgeBase, load_manual_templates
from linemedic.control_plane.notifications import outbox
from linemedic.integrations.docker import DockerError
from linemedic.scripts.seed_demo_repo import build_seed_repo
from linemedic.tests.helpers.api import ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.db_rows import (
    NOW,
    insert_incident,
    insert_issue,
    insert_run,
    insert_work,
)
from linemedic.tests.helpers.runner import (
    Scripted,
    local_pytest_docker,
    profile,
    scripted_docker,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PATCHES = REPO_ROOT / "linemedic" / "tests" / "fixtures" / "patches"
POLICY = load_policy()
RULES = POLICY.rules
PROPOSAL = "PROP-00000000000A"
RECEIVED = "2026-09-27T00:05:00.000000Z"
NEW_TESTS = {
    "fix_missing_inspector": "tests/repro/test_missing_inspector.py",
    "fix_incomplete": "tests/repro/test_missing_inspector.py",
    "fix_breaks_regression": "tests/repro/test_missing_inspector_fields.py",
    "repro_passes_on_base": "tests/repro/test_summary_with_inspectors.py",
    "repro_import_error": "tests/repro/test_missing_module.py",
}
GIT_ENV = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def patch(name):
    return (PATCHES / f"{name}.patch").read_text(encoding="utf-8")


def git(*args, git_dir):
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", *args], env=GIT_ENV, capture_output=True, check=True
    ).stdout


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    """결정적 시드로 만든 신뢰 mirror와 base SHA(실제 git)."""
    root = tmp_path_factory.mktemp("seed").resolve()
    built = build_seed_repo(root / "seed")
    mirror = root / "mirror" / "l3-mes-api.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(root / "seed"), str(mirror)],
        env=GIT_ENV,
        check=True,
    )
    return mirror, built["commit"]


def candidate_for(seed, workdir, name="fix_missing_inspector", diff=None):
    mirror, base = seed
    diff = diff if diff is not None else patch(name)
    plan = check_diff(diff, NEW_TESTS[name], RULES)
    return build_candidate(
        mirror=mirror,
        workdir=workdir,
        base_sha=base,
        diff=diff,
        plan=plan,
        rules=RULES,
        proposal_id=PROPOSAL,
        committed_at=RECEIVED,
    )


# ── candidate ─────────────────────────────────────────────────


def test_candidate_is_a_server_commit_of_exactly_the_planned_change(seed, tmp_path):
    first = candidate_for(seed, tmp_path / "1")
    repo = tmp_path / "1" / "repo.git"
    changed = git(
        "diff-tree",
        "-r",
        "--no-renames",
        "--name-status",
        first.base_tree,
        first.candidate_tree,
        git_dir=repo,
    )
    assert changed.decode().split() == [
        "M",
        "app/defects.py",
        "A",
        NEW_TESTS["fix_missing_inspector"],
    ]
    commit = git("cat-file", "-p", first.candidate_sha, git_dir=repo).decode()
    assert f"tree {first.candidate_tree}" in commit and f"parent {first.base_sha}" in commit
    assert "author LineMedic Broker <broker@linemedic.invalid>" in commit
    assert f"LineMedic candidate {PROPOSAL}" in commit
    again = candidate_for(seed, tmp_path / "2")
    assert (again.candidate_sha, again.candidate_tree) == (
        first.candidate_sha,
        first.candidate_tree,
    )
    assert not (repo / "hooks").exists() or not any((repo / "hooks").iterdir())


def test_trees_are_exported_for_each_stage(seed, tmp_path):
    built = candidate_for(seed, tmp_path / "w")
    new_test = NEW_TESTS["fix_missing_inspector"]
    base_app = (built.trees["base"] / "app" / "defects.py").read_text()
    assert 'row["inspector_id"]' in base_app
    assert (built.trees["repro"] / "app" / "defects.py").read_text() == base_app  # 업무 파일은 base
    assert (built.trees["repro"] / new_test).is_file()
    assert not (built.trees["base"] / new_test).exists()
    assert "미지정" in (built.trees["candidate"] / "app" / "defects.py").read_text()
    assert not (built.trees["candidate"] / ".git").exists()
    assert (
        built.record()["patch_sha256"]
        == check_diff(patch("fix_missing_inspector"), new_test, RULES).patch_sha256
    )


def test_patch_that_does_not_apply_is_denied(seed, tmp_path):
    diff = patch("fix_missing_inspector").replace(
        "    by_inspector: dict", "    by_inspectors: dict"
    )
    with pytest.raises(PatchDenied) as info:
        candidate_for(seed, tmp_path / "w", diff=diff)
    assert (info.value.code, info.value.rule) == ("PATCH_PATH_DENIED", "apply_failed")


def test_applied_tree_is_rechecked_against_the_plan(seed, tmp_path):
    """git apply가 한 일이 정책이 읽은 계획과 다르면 거부한다(문자열 검사만 믿지 않는다)."""
    mirror, base = seed
    plan = check_diff(patch("fix_missing_inspector"), NEW_TESTS["fix_missing_inspector"], RULES)
    with pytest.raises(PatchDenied) as info:
        build_candidate(
            mirror=mirror,
            workdir=tmp_path / "w",
            base_sha=base,
            diff=patch("repro_passes_on_base"),  # 다른 새 테스트 경로를 만드는 diff
            plan=plan,
            rules=RULES,
            proposal_id=PROPOSAL,
            committed_at=RECEIVED,
        )
    assert info.value.rule == "tree_mismatch"


def test_changed_file_must_still_be_plain_text_after_apply(seed, tmp_path):
    """hunk 밖(base)의 CR까지 포함해, 바뀐 파일 전체가 정규 UTF-8 텍스트여야 한다."""
    mirror, _ = seed
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "--quiet", str(mirror), str(work)], env=GIT_ENV, check=True)
    defects = work / "app" / "defects.py"
    defects.write_bytes(defects.read_bytes() + b"# windows line\r\n")
    identity = {
        **GIT_ENV,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(
        ["git", "-C", str(work), "commit", "--quiet", "-am", "cr"], env=identity, check=True
    )
    base = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cr_mirror = tmp_path / "cr.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(work), str(cr_mirror)], env=GIT_ENV, check=True
    )
    diff = patch("fix_missing_inspector")
    plan = check_diff(diff, NEW_TESTS["fix_missing_inspector"], RULES)
    with pytest.raises(PatchDenied) as info:
        build_candidate(
            mirror=cr_mirror,
            workdir=tmp_path / "w",
            base_sha=base,
            diff=diff,
            plan=plan,
            rules=RULES,
            proposal_id=PROPOSAL,
            committed_at=RECEIVED,
        )
    assert (info.value.rule, info.value.path) == ("not_text", "app/defects.py")


def test_environment_problems_are_candidate_errors(seed, tmp_path):
    mirror, base = seed
    diff = patch("fix_missing_inspector")
    plan = check_diff(diff, NEW_TESTS["fix_missing_inspector"], RULES)
    cases = [
        (tmp_path / "no-mirror.git", base, "mirror_missing"),
        (mirror, "f" * 40, "base_not_in_mirror"),
        (mirror, "HEAD", "invalid_base_sha"),
    ]
    for index, (source, sha, reason) in enumerate(cases):
        with pytest.raises(CandidateError) as info:
            build_candidate(
                mirror=source,
                workdir=tmp_path / f"w{index}",
                base_sha=sha,
                diff=diff,
                plan=plan,
                rules=RULES,
                proposal_id=PROPOSAL,
                committed_at=RECEIVED,
            )
        assert info.value.reason == reason


def test_hostile_user_git_configuration_is_ignored(seed, tmp_path, monkeypatch):
    hostile = tmp_path / "gitconfig"
    hostile.write_text(
        "[user]\n\tname = Someone Else\n[commit]\n\tgpgsign = true\n"
        "[core]\n\thooksPath = /tmp/evil\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("HOME", str(tmp_path))
    built = candidate_for(seed, tmp_path / "w")
    commit = git(
        "cat-file", "-p", built.candidate_sha, git_dir=tmp_path / "w" / "repo.git"
    ).decode()
    assert "Someone Else" not in commit and "gpgsig" not in commit


def test_unsafe_archive_members_are_refused(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        info = tarfile.TarInfo("../escape.py")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(CandidateError) as error:
        _extract(data.getvalue(), tmp_path / "out")
    assert error.value.reason == "archive_unsafe"
    assert not (tmp_path / "escape.py").exists()


# ── 게이트 ────────────────────────────────────────────────────


def gate(seed, runs_dir, docker=None):
    mirror, _ = seed
    docker = docker if docker is not None else local_pytest_docker()
    return PatchGate(
        policy=POLICY,
        runner=Runner(docker, profile(), FakeClock()),
        mirror=mirror,
        runs_dir=runs_dir,
    )


def request(seed, name="fix_missing_inspector", **overrides):
    _, base = seed
    values = {
        "run_id": RUN,
        "proposal_id": PROPOSAL,
        "base_sha": base,
        "allowed_base": base,
        "deploy_base": base,
        "diff": patch(name),
        "new_test_path": NEW_TESTS[name],
        "received_at": RECEIVED,
    }
    return GateRequest(**{**values, **overrides})


def results_of(outcome):
    return [(c["check"], c["result"]) for c in outcome.checks]


def test_correct_fix_passes_r0_r1_r2_on_the_real_trees(seed, tmp_path):
    outcome = gate(seed, tmp_path).check(request(seed))
    assert outcome.passed and outcome.code is None
    assert results_of(outcome) == [
        ("PATCH_POLICY", "PASS"),
        ("BASE", "PASS"),
        ("CANDIDATE", "PASS"),
        ("R0", "PASS"),
        ("R1", "PASS"),
        ("R2", "PASS"),
    ]
    r1 = outcome.checks[4]
    assert (r1["exit_code"], r1["junit"]["failures"]) == (1, 1)  # base에서 KeyError로 실패
    r2 = outcome.checks[5]
    assert (r2["exit_code"], r2["junit"]["tests"]) == (0, 5)
    assert outcome.checks[0]["policy_sha256"] == POLICY.sha256
    assert outcome.candidate["candidate_sha"] == outcome.checks[2]["candidate_sha"]
    workdir = tmp_path / RUN / "checkouts" / PROPOSAL / "1"
    assert (workdir / "logs" / "R2.log").is_file() and r2["log"]["path"] == str(
        workdir / "logs" / "R2.log"
    )


@pytest.mark.parametrize(
    ("name", "check", "code", "reason"),
    [
        ("repro_passes_on_base", "R1", "REPRO_NOT_FAILING", "passed_on_base"),  # T-REPRO-01
        ("repro_import_error", "R1", "REPRO_NOT_FAILING", "pytest_exit_2"),  # T-REPRO-02
        ("fix_breaks_regression", "R2", "REGRESSION_FAILED", "regression_not_passed"),  # T-REPRO-03
        ("fix_incomplete", "R2", "REGRESSION_FAILED", "new_test_not_passed"),  # T-REPRO-03
    ],
)
def test_real_trees_that_do_not_reproduce_or_regress_are_rejected(
    seed, tmp_path, name, check, code, reason
):
    outcome = gate(seed, tmp_path).check(request(seed, name))
    assert (outcome.passed, outcome.code, outcome.reason, outcome.revisable) == (
        False,
        code,
        reason,
        True,
    )
    assert outcome.checks[-1]["check"] == check
    if check == "R1":
        assert "R2" not in [c["check"] for c in outcome.checks]


def test_policy_denial_stops_before_git_and_docker(seed, tmp_path):
    docker = scripted_docker({})
    diff = patch("fix_missing_inspector").replace(
        "app/defects.py", "tests/regression/test_summary_regression.py"
    )
    outcome = gate(seed, tmp_path, docker).check(request(seed, diff=diff))
    assert (outcome.code, outcome.reason) == ("PATCH_PATH_DENIED", "protected_path")
    assert outcome.checks == [
        {
            "check": "PATCH_POLICY",
            "result": "PATCH_PATH_DENIED",
            "reason": "protected_path",
            "path": "tests/regression/test_summary_regression.py",
        }
    ]
    assert docker.calls == [] and not (tmp_path / RUN).exists()


@pytest.mark.parametrize(
    ("overrides", "code", "reason", "revisable"),
    [
        ({"allowed_base": None}, "PROTECTION_UNAVAILABLE", "run_baseline_unconfigured", False),
        ({"deploy_base": None}, "PROTECTION_UNAVAILABLE", "deploy_base_unknown", False),
        ({"deploy_base": "e" * 40}, "SOURCE_CHANGED", "deploy_base_differs", False),
        ({"base_sha": "e" * 40}, "SOURCE_CHANGED", "proposal_base_mismatch", True),
    ],
)
def test_base_must_match_run_baseline_and_observed_deploy(
    seed, tmp_path, overrides, code, reason, revisable
):
    docker = scripted_docker({})
    outcome = gate(seed, tmp_path, docker).check(request(seed, **overrides))
    assert (outcome.code, outcome.reason, outcome.revisable) == (code, reason, revisable)
    assert outcome.checks[-1]["check"] == "BASE" and docker.calls == []


def test_missing_runner_image_is_protection_unavailable(seed, tmp_path):
    docker = scripted_docker({})
    docker.images.clear()
    outcome = gate(seed, tmp_path, docker).check(request(seed))
    assert (outcome.code, outcome.reason, outcome.revisable) == (
        "PROTECTION_UNAVAILABLE",
        "runner_image_missing",
        False,
    )
    assert not any(call[0] == "run" for call in docker.calls)


def test_broken_base_environment_stops_at_r0(seed, tmp_path):
    docker = scripted_docker({"R0": Scripted(exit_code=1, junit="r0_regression_passed")})
    outcome = gate(seed, tmp_path, docker).check(request(seed))
    assert (outcome.code, outcome.reason, outcome.revisable) == (
        "PROTECTION_UNAVAILABLE",
        "r0_pytest_exit_1",
        False,
    )


def test_timeout_and_oom_in_r1_are_not_reproduction(seed, tmp_path):
    r0 = Scripted(exit_code=0, junit="r0_regression_passed")
    for index, (r1, reason) in enumerate(
        [(Scripted(exit_code=None), "timeout"), (Scripted(exit_code=137, oom=True), "oom")]
    ):
        docker = scripted_docker({"R0": r0, "R1": r1})
        outcome = gate(seed, tmp_path / str(index), docker).check(request(seed))
        assert (outcome.code, outcome.reason, outcome.revisable) == (
            "REPRO_NOT_FAILING",
            reason,
            True,
        )


def test_rechecking_uses_a_new_numbered_directory(seed, tmp_path):
    docker = scripted_docker({"R0": Scripted(exit_code=1)})
    checker = gate(seed, tmp_path, docker)
    checker.check(request(seed))
    checker.check(request(seed))
    root = tmp_path / RUN / "checkouts" / PROPOSAL
    assert sorted(p.name for p in root.iterdir()) == ["1", "2"]


# ── 브로커 연결 ───────────────────────────────────────────────

ATTEMPT = "ATT-00000000000A"
DEADLINE = "2026-09-27T00:20:00.000000Z"


class World:
    """승인된 mes-api work가 RUNNING이고 시작 알림이 ACCEPTED인 조사 중 사건 + 실제 seed mirror."""

    def __init__(self, store, conn, seed, runs_dir, docker=None, baseline=True):
        _, self.base = seed
        insert_run(conn, RUN)
        manifest = {"runtime_env": {"baseline_commit": self.base}} if baseline else {}
        conn.execute(
            "UPDATE demo_runs SET config_json = ? WHERE id = ?", (json.dumps(manifest), RUN)
        )
        insert_issue(conn, 9)
        self.store, self.conn = store, conn
        self.incident = insert_incident(
            conn,
            RUN,
            "INVESTIGATING",
            attempt_id=ATTEMPT,
            service="mes-api",
            attempt_deadline=DEADLINE,
            details_json=json.dumps(
                {"signature": {"endpoint": "/defects/summary", "error_type": "KeyError:x"}}
            ),
        )
        self.work = insert_work(
            conn,
            RUN,
            self.incident,
            9,
            "RUNNING",
            attempt_id=ATTEMPT,
            issue_snapshot_sha256="0" * 64,
        )
        with store.tx() as tx:
            work = tx.one("SELECT * FROM work_items WHERE id = ?", (self.work,))
            notice = outbox.enqueue(
                tx, work, "WORK_STARTING", {"event_type": "WORK_STARTING"}, ROUTE_ID
            )
            self.evidence = add_evidence(
                tx,
                run_id=RUN,
                incident_id=self.incident,
                kind="log_error",
                observed_at=NOW,
                source_identity="test:1",
                payload={"i": 1},
            )
            record_deploy_observed(
                tx,
                run_id=RUN,
                service="mes-api",
                base_sha=self.base,
                image_id="sha256:" + "1" * 64,
                container="mes",
                container_id="c" * 64,
                actor="test",
            )
        conn.execute(
            "UPDATE notifications SET status = 'ACCEPTED', accepted_at = ? WHERE id = ?",
            (NOW, notice),
        )
        conn.execute(
            "UPDATE work_items SET start_notification_id = ? WHERE id = ?", (notice, self.work)
        )
        catalog = Catalog.from_config(load_config())
        knowledge = KnowledgeBase()
        self.api = make_api(store, conn, catalog=catalog, knowledge=knowledge)
        self.docker = docker if docker is not None else local_pytest_docker()
        self.broker = Broker(
            store,
            catalog,
            load_manual_templates(knowledge),
            ROUTE_ID,
            patch_gate=gate(seed, runs_dir, self.docker),
        )
        self.principal = AgentPrincipal(RUN, self.incident, self.work, ATTEMPT)
        self.headers = self.api.agent(self.principal)

    def submit(self, name="fix_missing_inspector", key="pr-1", **action):
        body = {
            "schema_version": "linemedic.v4",
            "run_id": RUN,
            "incident_id": self.incident,
            "work_id": self.work,
            "attempt_id": ATTEMPT,
            "category": "code_bug",
            "summary": "inspector_id가 없는 record에서 KeyError가 난다",
            "evidence_ids": [self.evidence],
            "action": {
                "type": "create_pr",
                "base_sha": self.base,
                "root_cause_hypothesis": "필수라고 가정한 필드를 직접 조회한다",
                "diff": patch(name),
                "new_test_path": NEW_TESTS[name],
                **action,
            },
        }
        response = self.api.client.post(
            "/tools/proposals", json=body, headers={**self.headers, "Idempotency-Key": key}
        )
        assert response.status_code == 202, response.text
        return response.json()["data"]["proposal_id"]

    def proposal(self, proposal_id):
        row = self.conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return row, json.loads(row["checks_json"])

    def status(self, proposal_id):
        return self.api.client.get(f"/tools/proposals/{proposal_id}", headers=self.headers).json()[
            "data"
        ]

    def incident_row(self):
        return self.conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (self.incident,)
        ).fetchone()

    def work_row(self):
        return self.conn.execute("SELECT * FROM work_items WHERE id = ?", (self.work,)).fetchone()


def load_config():
    from linemedic.common.config import load_settings

    return load_settings(REPO_ROOT / "config" / "linemedic.toml", {}).config


def test_passing_candidate_is_recorded_but_stops_without_a_fake_pr(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)
    proposal_id = w.submit()
    assert w.broker.process_pending() == [proposal_id]
    row, record = w.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("REJECTED", "PROTECTION_UNAVAILABLE")
    assert [c["check"] for c in record["checks"]][-7:] == [
        "PATCH_POLICY",
        "BASE",
        "CANDIDATE",
        "R0",
        "R1",
        "R2",
        "CREATE_PR",
    ]
    assert (
        record["candidate"]["candidate_sha"]
        and record["checks"][-1]["reason"] == "pr_creation_unavailable"
    )
    assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0  # PR 시도 없음
    assert (w.incident_row()["status"], w.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    (blocked,) = conn.execute(
        "SELECT payload_json FROM notifications WHERE event_type = 'WORK_BLOCKED'"
    ).fetchall()
    report = json.loads(blocked[0])
    assert (
        report["blocker_code"] == "VALIDATION_FAILED"
        and "봇 PR 생성 경로" in report["reason_detail"]
    )


def test_fixable_failure_gives_one_revision_then_escalates(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)
    first = w.submit("repro_passes_on_base", key="pr-1")
    w.broker.process_pending()
    row, record = w.proposal(first)
    assert (row["decision"], record["decision_reason"]) == ("REJECTED", "REPRO_NOT_FAILING")
    assert w.incident_row()["status"] == "INVESTIGATING"  # 수정 1회
    status = w.status(first)
    assert status["revision_allowed"]
    assert status["checks"][-1] == {
        "check": "R1",
        "result": "REPRO_NOT_FAILING",
        "reason": "passed_on_base",
        "evidence_id": None,
    }
    assert [c["check"] for c in status["checks"]][-4:] == ["BASE", "CANDIDATE", "R0", "R1"]
    assert str(tmp_path) not in json.dumps(
        status
    )  # host 경로·container·로그는 에이전트에게 보이지 않는다
    second = w.submit("fix_breaks_regression", key="pr-2")
    w.broker.process_pending()
    assert w.proposal(second)[1]["decision_reason"] == "REGRESSION_FAILED"
    assert (w.incident_row()["status"], w.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0  # T-REPRO-03: PR 없음


def test_junit_failure_messages_stay_out_of_the_agent_view(store, conn, seed, tmp_path):
    """실패 요약은 host 기록·PR 본문용이다. 에이전트 조회에는 check·result·reason만 보인다."""
    w = World(store, conn, seed, tmp_path)
    proposal_id = w.submit("fix_breaks_regression")
    w.broker.process_pending()
    _, record = w.proposal(proposal_id)
    (r2,) = [c for c in record["checks"] if c["check"] == "R2"]
    messages = [c["message"] for c in r2["junit"]["cases"] if c.get("message")]
    assert messages  # 실패한 보호 회귀 case의 요약이 host 기록에 남는다
    status = json.dumps(w.status(proposal_id), ensure_ascii=False)
    assert all(message not in status for message in messages)


def test_unfixable_environment_problem_escalates_without_using_the_revision(
    store, conn, seed, tmp_path
):
    w = World(store, conn, seed, tmp_path, baseline=False)
    proposal_id = w.submit()
    w.broker.process_pending()
    _, record = w.proposal(proposal_id)
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert record["checks"][-1]["reason"] == "run_baseline_unconfigured"
    assert (w.incident_row()["status"], w.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    assert w.incident_row()["submissions"] == 1  # 예산이 남아도 멈춘다
    (blocked,) = conn.execute(
        "SELECT payload_json FROM notifications WHERE event_type = 'WORK_BLOCKED'"
    ).fetchall()
    report = json.loads(blocked[0])
    assert "검사 환경" in report["operator_next_step"][0]


def test_changed_deploy_base_escalates_as_source_changed(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)
    with store.tx() as tx:
        record_deploy_observed(
            tx,
            run_id=RUN,
            service="mes-api",
            base_sha="e" * 40,
            image_id=None,
            container="mes",
            container_id=None,
            actor="test",
        )
    proposal_id = w.submit()
    w.broker.process_pending()
    assert w.proposal(proposal_id)[1]["decision_reason"] == "SOURCE_CHANGED"
    (blocked,) = conn.execute(
        "SELECT payload_json FROM notifications WHERE event_type = 'WORK_BLOCKED'"
    ).fetchall()
    assert json.loads(blocked[0])["blocker_code"] == "SOURCE_CHANGED"


def test_state_change_during_the_gate_records_the_result_without_transition(
    store, conn, seed, tmp_path
):
    w = World(store, conn, seed, tmp_path)
    original = w.broker.patch_gate.check

    def check_while_operator_escalates(request):
        outcome = original(request)
        conn.execute(
            "UPDATE incidents SET status = 'ESCALATED', version = version + 1 WHERE id = ?",
            (w.incident,),
        )
        return outcome

    w.broker.patch_gate.check = check_while_operator_escalates
    proposal_id = w.submit("repro_passes_on_base")
    w.broker.process_pending()
    row, record = w.proposal(proposal_id)
    assert (row["decision"], record["decision_reason"]) == ("REJECTED", "REPRO_NOT_FAILING")
    assert w.incident_row()["status"] == "ESCALATED"  # 게이트 결과로 되돌리지 않는다
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE event_type = 'WORK_BLOCKED'"
        ).fetchone()[0]
        == 0
    )


def test_proposal_decided_elsewhere_during_the_gate_is_left_alone(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)
    original = w.broker.patch_gate.check

    def decided_elsewhere(request):
        outcome = original(request)
        conn.execute(
            "UPDATE proposals SET decision = 'REJECTED' WHERE id = ?", (request.proposal_id,)
        )
        return outcome

    w.broker.patch_gate.check = decided_elsewhere
    proposal_id = w.submit("repro_passes_on_base")
    w.broker.process_pending()
    row, record = w.proposal(proposal_id)
    assert row["decision"] == "REJECTED" and "decision_reason" not in record  # 덮어쓰지 않는다
    assert w.incident_row()["status"] == "VALIDATING"  # 게이트 결과로 전이하지 않는다
    rejected = conn.execute(
        "SELECT COUNT(*) FROM audit_events WHERE event_type = 'PROPOSAL_REJECTED'"
    ).fetchone()[0]
    assert rejected == 0


def test_gate_crash_leaves_checking_and_restart_rechecks_in_a_new_directory(
    store, conn, seed, tmp_path
):
    w = World(store, conn, seed, tmp_path)
    original = w.broker.patch_gate.check
    calls = []

    def crash_once(request):
        calls.append(request.proposal_id)
        if len(calls) == 1:
            original(request)
            raise RuntimeError("process died")
        return original(request)

    w.broker.patch_gate.check = crash_once
    proposal_id = w.submit("repro_passes_on_base")
    assert w.broker.process_pending(keep_going=True) == []
    row, record = w.proposal(proposal_id)
    assert row["decision"] == "CHECKING" and [c["check"] for c in record["checks"]][-1] == "B06"
    assert w.broker.recover_checking() == 1
    w.broker.process_pending()
    assert w.proposal(proposal_id)[1]["decision_reason"] == "REPRO_NOT_FAILING"
    root = tmp_path / RUN / "checkouts" / proposal_id
    assert sorted(p.name for p in root.iterdir()) == ["1", "2"]


def test_docker_error_after_run_escalates_instead_of_holding_the_running_slot(
    store, conn, seed, tmp_path
):
    """PR #50 리뷰 재현: `docker wait`가 No such container여도 work가 RUNNING에 남지 않는다."""
    docker = local_pytest_docker()

    def vanished(name, timeout):
        raise DockerError(f"docker wait 실패: No such container: {name}", 1)

    docker.wait = vanished
    w = World(store, conn, seed, tmp_path, docker=docker)
    proposal_id = w.submit()
    w.broker.process_pending()
    row, record = w.proposal(proposal_id)
    assert row["decision"] != "CHECKING"
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert (record["checks"][-1]["check"], record["checks"][-1]["reason"]) == (
        "R0",
        "r0_docker_wait_failed",
    )
    assert (w.incident_row()["status"], w.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    assert not docker.containers  # 컨테이너는 finally에서 지워졌다
    blocked = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE event_type = 'WORK_BLOCKED'"
    ).fetchone()[0]
    assert blocked == 1


def test_unexpected_gate_exception_is_protection_unavailable(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)

    def broken():
        raise OSError("disk full")

    w.broker.patch_gate.runner.image_problem = broken
    proposal_id = w.submit()
    w.broker.process_pending()
    _, record = w.proposal(proposal_id)
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert record["checks"][-1]["reason"] == "unexpected_error:OSError"
    assert (w.incident_row()["status"], w.work_row()["status"]) == ("ESCALATED", "BLOCKED")
    assert w.incident_row()["submissions"] == 1  # 수정 예산과 관계없이 멈춘다


def test_broker_without_a_gate_still_refuses_create_pr(store, conn, seed, tmp_path):
    w = World(store, conn, seed, tmp_path)
    w.broker.patch_gate = None
    proposal_id = w.submit()
    w.broker.process_pending()
    _, record = w.proposal(proposal_id)
    assert record["decision_reason"] == "PROTECTION_UNAVAILABLE"
    assert record["checks"][-1] == {
        "check": "PATCH_GATE",
        "result": "PROTECTION_UNAVAILABLE",
        "reason": "patch_gate_unavailable",
    }
    assert w.docker.calls == []
