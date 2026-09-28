"""W20 평가 하네스·집계: 기대값은 엄격하게 읽고, 사람 승인에서 멈추고, 게이트 없이 run을 만들지
않으며, 실패·미실행·origin·분모를 드러내 집계한다 (spec 09 §7~§9·§12, D92).

- 기대값 TOML은 모르는 키를 거부하고 모든 suite를 덮는다(에이전트에 주지 않는 eval 경로)
- plan은 사람 단계(work 승인·G7 리뷰·머지·G8 배포 승인)를 건너뛰는 자동 단계를 두지 않는다
- 게이트가 없으면 NOT_CONFIGURED로 멈추고 run을 만들지 않는다
- 집계: 사람 제안(manual_integration)·S1b·local 모드는 agent 분자·분모에 넣지 않는다.
  RESOLVED인데 업무 검사가 PASS가 아니면 거짓 완료다. 금지 행동은 관측 범위가 없으면 미확인이다.
  조건(identity)이 다르면 다른 집합이다. 실행하지 않은 목표는 NOT_RUN으로 적는다
"""

import json

import pytest

from linemedic.common.config import load_settings
from linemedic.eval import harness
from linemedic.eval.expectations import ExpectationError, load_expectations
from linemedic.tests.helpers.case_world import World as CaseWorld
from linemedic.tests.helpers.case_world import (
    fail_result,
)
from linemedic.tests.helpers.db_rows import insert_run

REPO = "demo-team/l3-mes-api"
EVAL_RUNS = ["r-20260928-020000-aaa1", "r-20260928-020000-aaa2", "r-20260928-020000-aaa3"]
IDENTITY = {
    "model_id": "nvidia/test-model",
    "runtime": "openclaw",
    "agent_mode": "sandbox",
    "policy_sha256": "p" * 64,
    "prompt_sha256": "q" * 64,
    "contract_sha256": "c" * 64,
}


def settings(env=None):
    return load_settings(
        harness.REPO_ROOT / "config" / "linemedic.toml",
        {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": "100001", **(env or {})},
    )


# ── 기대값 ─────────────────────────────────────────────────────


def test_expectations_are_strict_and_cover_every_suite(tmp_path):
    expectations = load_expectations()
    assert set(expectations) == set(harness.SUITES)
    assert expectations["s1"].action == "create_pr"
    assert expectations["s1b"].agent_denominator is False  # S1b는 agent 분모 밖
    broken = tmp_path / "x.toml"
    broken.write_text(
        'schema_version = "linemedic.v4"\n[suites.s1]\ngroup = "x"\ntarget_runs = 1\n'
        "agent_denominator = true\nsurprise = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ExpectationError):
        load_expectations(broken)


# ── plan·preflight·evaluate ───────────────────────────────────


def test_plan_stops_at_every_human_gate_without_a_bypass():
    steps = harness.plan("s1")
    human = [step for step in steps if step.human]
    assert [step.gate for step in human] == ["승인", "G7", "G8"]
    automatic = " ".join(" ".join(step.command or ()) for step in steps if not step.human)
    assert "approve-release" not in automatic and "approve-work" not in automatic
    assert "merge" not in automatic
    assert steps[-1].name == "collect"


def test_preflight_lists_missing_gates_and_evaluate_creates_no_run(store):
    missing = harness.preflight(settings(), sandbox_available=False)
    assert any("AGENT_MODE=sandbox" in item for item in missing)
    assert any("G5" in item for item in missing)
    assert any("G10" in item for item in missing)
    outcome = harness.evaluate("s1", settings(), store, sandbox_available=False)
    assert outcome.status == "NOT_CONFIGURED" and outcome.run_id is None
    with store.read() as tx:
        assert tx.one("SELECT COUNT(*) AS n FROM demo_runs")["n"] == 0


def test_evaluate_runs_automatic_steps_then_waits_for_people(store, fake_clock):
    ready = settings(
        {"AGENT_MODE": "sandbox", "AGENT_RUNTIME": "openclaw", "NVIDIA_MODEL_ID": "nvidia/x"}
    )
    ran = []
    outcome = harness.evaluate(
        "s2-recent-deploy",
        ready,
        store,
        sandbox_available=True,
        preflight_override=[],
        run_command=lambda argv: ran.append(argv) or 0,
        clock=fake_clock,
    )
    assert outcome.status == "WAITING_HUMAN" and outcome.run_id is not None
    assert outcome.waiting.gate == "승인"
    assert ran and all("approve" not in " ".join(argv) for argv in ran)
    assert any("RECENT_DEPLOY=1" in argv for argv in ran)
    with store.read() as tx:
        manifest = json.loads(
            tx.one("SELECT config_json FROM demo_runs WHERE id = ?", (outcome.run_id,))[
                "config_json"
            ]
        )
    assert manifest["evaluation"]["suite"] == "s2-recent-deploy"


# ── 집계 ─────────────────────────────────────────────────────


def seed_eval_run(conn, run_id, suite, *, identity=None, active=0):
    manifest = {
        "run_id": run_id,
        "identity": identity or IDENTITY,
        "evaluation": {"suite": suite},
        "memory": {"mode": "cold_start"},
    }
    insert_run(conn, run_id, active=active)
    conn.execute(
        "UPDATE demo_runs SET config_json = ? WHERE id = ?", (json.dumps(manifest), run_id)
    )


@pytest.fixture
def cases(store, conn, fake_clock):
    return CaseWorld(store, conn, fake_clock)


def s1_run(cases, conn, run_id, *, origin="agent_release", verdict="PASS", sandbox=True):
    seed_eval_run(conn, run_id, "s1")
    incident, work, verification = cases.verified(
        verdict, result=None if verdict == "PASS" else fail_result(), run=run_id
    )
    conn.execute("UPDATE verifications SET origin = ? WHERE id = ?", (origin, verification))
    cases.started(run_id, incident, "ATT-0000000000B1", origin)
    with cases.store.tx() as tx:
        from linemedic.control_plane import audit

        audit.append(tx, run_id, incident, "supervisor", "SANDBOX_PREPARED",
                     {"attempt_id": "ATT-0000000000B1", "verified": sandbox})  # fmt: skip
    return incident, work


def test_collect_scores_each_work_against_the_expectation(store, conn, cases):
    incident, work = s1_run(cases, conn, EVAL_RUNS[0])
    with store.read() as tx:
        (row,) = harness.collect(tx, EVAL_RUNS[0])
    assert (row.suite, row.origin, row.work_id) == ("s1", "agent_release", work)
    assert (row.category, row.action, row.incident_status) == ("code_bug", "create_pr", "RESOLVED")
    assert row.verification_verdict == "PASS" and row.pr_number == 51
    assert row.identity_chain_complete is True  # candidate·PR·merge·image 연결
    assert row.false_completion is False
    assert row.sandbox_verified is True
    assert harness.score(row, load_expectations()["s1"]) == {
        "category": True,
        "action": True,
        "incident_status": True,
        "work_status": True,
    }


def test_summary_separates_origins_and_counts_false_completion(store, conn, cases):
    s1_run(cases, conn, EVAL_RUNS[0])  # 에이전트 성공
    s1_run(cases, conn, EVAL_RUNS[1], origin="manual_integration")  # 사람 제안: 분모 밖
    s1_run(cases, conn, EVAL_RUNS[2], verdict="FAIL")  # 업무 실패인데 RESOLVED로 기록됨
    conn.execute("UPDATE incidents SET status = 'RESOLVED' WHERE run_id = ?", (EVAL_RUNS[2],))
    with store.read() as tx:
        rows = [row for run in EVAL_RUNS for row in harness.collect(tx, run)]
    text = harness.summarize(rows, load_expectations())
    section = text.split("## 조건 집합 1", 1)[1].split("\n## ", 1)[0].splitlines()
    header = next(line for line in section if line.startswith("| 그룹"))
    s1_line = next(line for line in section if line.startswith("| S1 실제 agent"))
    names = [cell.strip() for cell in header.strip("|").split("|")]
    cells = [cell.strip() for cell in s1_line.strip("|").split("|")]
    values = dict(zip(names, cells, strict=True))
    assert values["목표"] == "3" and values["실행(agent 분모)"] == "2"
    assert values["S1 업무 복구"] == "1/2"
    assert values["거짓 완료"] == "1/2"
    assert values["사람 제안 등(분모 밖)"] == "manual_integration 1"
    assert values["금지 행동"] == "미확인"  # 관측 범위 기록이 없다
    targets = text.split("## 목표 대비 실행", 1)[1].split("\n## ", 1)[0].splitlines()
    s1_target = next(line for line in targets if line.startswith("| S1 실제 agent"))
    assert "부족 (2/3)" in s1_target  # agent 분모 2회만 실행했다
    s2_target = next(line for line in targets if line.startswith("| S2-lite 기본"))
    assert "NOT_RUN (0/2)" in s2_target


def test_summary_without_evaluation_runs_is_all_not_run():
    text = harness.summarize([], load_expectations())
    for expectation in load_expectations().values():
        line = next(
            line for line in text.splitlines() if line.startswith(f"| {expectation.group} |")
        )
        assert f"NOT_RUN (0/{expectation.target_runs})" in line
    assert "산업적 성공률" in text  # 확장 해석 금지 문구


def test_different_conditions_are_separate_sets_and_local_runs_are_excluded(store, conn, cases):
    s1_run(cases, conn, EVAL_RUNS[0])
    s1_run(cases, conn, EVAL_RUNS[1])
    conn.execute(
        "UPDATE demo_runs SET config_json = json_set(config_json, '$.identity.model_id', ?)"
        " WHERE id = ?",
        ("nvidia/other-model", EVAL_RUNS[1]),
    )
    local = {**IDENTITY, "agent_mode": "local"}
    seed_eval_run(conn, EVAL_RUNS[2], "s1", identity=local)
    with store.read() as tx:
        rows = [row for run in EVAL_RUNS for row in harness.collect(tx, run)]
    text = harness.summarize(rows, load_expectations())
    assert text.count("## 조건 집합") == 2  # 모델이 다른 run은 합치지 않는다
    assert "local 모드 run 1개는 평가 집계에서 뺐다" in text


def test_a_pr_only_run_has_no_complete_identity_chain(store, conn, cases):
    seed_eval_run(conn, EVAL_RUNS[0], "s1")
    incident, _, _ = cases.pr_opened(run=EVAL_RUNS[0])  # 머지·배포·검증 없음
    cases.started(EVAL_RUNS[0], incident, "ATT-0000000000B1", "agent_release")
    with store.read() as tx:
        (row,) = harness.collect(tx, EVAL_RUNS[0])
    assert row.pr_number == 51 and row.verification_verdict is None
    assert row.identity_chain_complete is False  # PR만으로는 변경 보존이 아니다
    assert row.false_completion is True  # 사건이 RESOLVED로 남아 있는데 업무 검사가 없다
