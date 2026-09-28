"""W13·W16 fake E2E: 사람 제안(`manual_integration`)으로 전체 경로를 통과한다.

`make start`와 같은 조립(`build_control_plane`)으로 다음을 돈다.
S1 주입 → 감지 → Issue 생성·binding → 운영자 work 승인 → 시작 댓글 receipt
→ attempt(workspace·token) → ScriptedAdapter 제안 → 브로커 게이트 → 봇 PR
→ (사람) 리뷰·squash 머지 → (사람) exact 배포 승인 → verifier PASS → RESOLVED → 결과 댓글.

- 외부는 FakeGitHub·FakeDocker(runner·MES·prober 흉내)·로컬 git 원격이다.
  루프는 `ControlPlane`의 메서드를 순서대로 부른다
- 사람 개입(work 승인, PR 리뷰·머지, 배포 승인)은 테스트가 운영 API·FakeGitHub로 흉내 낸다
- 확인: 시작 receipt 시각 < attempt 시작 시각, 결합 전이가 docs/03 §3 표와 일치,
  verification·PR 본문의 origin이 manual_integration, 에이전트 성과 집계 제외, token 폐기
- W16: S2-lite(기본·recent-deploy)에서 사람이 쓴 설비 제안이 정비 요청 초안으로 끝난다.
  코드 변경·PR·빌드·배포 0건, 초안 `delivery_status=not_sent`와 `HANDOFF_DRAFTED` 댓글 접수가 따로,
  case note HANDOFF. 실제 에이전트의 도구 선택·분류는 여기서 보지 않는다(live, G3·G4·G5)
"""

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linemedic.agent.adapter import ScriptedAdapter
from linemedic.common.clock import from_rfc3339
from linemedic.common.config import load_settings
from linemedic.control_plane import runs
from linemedic.control_plane.app import create_app
from linemedic.control_plane.auth import TokenRegistry, host_operator
from linemedic.control_plane.broker.patch_gate import PatchGate
from linemedic.control_plane.broker.patch_policy import load_policy
from linemedic.control_plane.broker.runner import Runner
from linemedic.control_plane.deploys import record_deploy_observed
from linemedic.control_plane.main import DEFAULT_MANUAL_PROPOSAL, build_control_plane
from linemedic.control_plane.state import COUPLED_WORK_STATUS
from linemedic.control_plane.verifier import agent_performance_verifications
from linemedic.dashboard import __main__ as dashboard
from linemedic.factory_sim import scenarios
from linemedic.factory_sim.scenarios import mes_container_options, prepare_s1_data, resource_names
from linemedic.integrations.git_fetch import GitFetcher
from linemedic.integrations.git_push import FakePusher
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import OPERATOR_TOKEN, RUN
from linemedic.tests.helpers.db_rows import insert_run
from linemedic.tests.helpers.pr_world import BOT, PASSING, REPO, REPO_ID, build_seed_mirror
from linemedic.tests.helpers.release_world import (
    BASE_IMAGE,
    FIX_MARK,
    REVIEWER,
    REVIEWER_ENV,
    FakeMes,
    git,
)
from linemedic.tests.helpers.runner import profile, scripted_docker

REPO_ROOT = Path(__file__).resolve().parents[3]
OPERATOR = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
S2_MANUAL_PROPOSAL = REPO_ROOT / "linemedic" / "eval" / "manual_proposals" / "s2_lite_manual.json"


def error_line(n: int) -> str:
    return json.dumps(
        {
            "level": "ERROR",
            "service": "mes-api",
            "event": "request_failed",
            "request_id": f"{n:032x}",
            "lot_id": "L3-0927-118",
            "path": "/defects/summary",
            "status": 500,
            "error_type": "KeyError",
            "error_field": "inspector_id",
            "top_frame": "app.defects:summarize",
            "top_frame_line": 7,
        }
    )


class E2E:
    def __init__(
        self,
        store,
        conn,
        clock,
        seed: tuple[Path, str],
        tmp_path: Path,
        proposal: Path = DEFAULT_MANUAL_PROPOSAL,
    ) -> None:
        seed_mirror, self.base = seed
        self.store, self.conn, self.clock = store, conn, clock
        self.runs_dir = tmp_path / "runs"
        self.mirror = self.runs_dir / "mirror" / "l3-mes-api.git"
        shutil.copytree(seed_mirror, self.mirror)
        settings = load_settings(
            REPO_ROOT / "config" / "linemedic.toml",
            {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)},
        )
        self.settings = settings
        insert_run(conn, RUN)
        manifest = runs.build_manifest(settings, RUN, None)  # make run-new과 같은 모양
        manifest["runtime_env"]["baseline_commit"] = self.base
        conn.execute(
            "UPDATE demo_runs SET config_json = ? WHERE id = ?", (json.dumps(manifest), RUN)
        )
        self.github = FakeGitHub(REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=True)
        self.github.branches[f"baseline/{RUN}"] = self.base  # W03·W19가 준비하는 run 기준 브랜치
        self.remote = tmp_path / "remote.git"
        git("clone", "--quiet", "--bare", str(self.mirror), str(self.remote))

        # FakeDocker: runner 단계는 scripted, MES·prober는 가짜 MES 응답
        self.docker = scripted_docker(dict(PASSING))
        scripted = self.docker.run_handler
        assert scripted is not None
        self.docker.run_handler = lambda name, options, command: (
            scripted(name, options, command) if name.startswith("lm-") else None
        )
        self.mes = FakeMes(self.docker)
        self.docker.exec_handler = self.mes
        real_build = self.docker.build

        def build(context, dockerfile, tag, build_args=None):
            image = real_build(context, dockerfile, tag, build_args)
            if FIX_MARK in (Path(context) / "app" / "defects.py").read_text(encoding="utf-8"):
                self.mes.fixed_images.add(image)
            return image

        self.docker.build = build  # type: ignore[method-assign]
        self.docker.images[BASE_IMAGE] = BASE_IMAGE

        self.tokens = TokenRegistry()
        self.tokens.register_operator(*host_operator(OPERATOR_TOKEN))
        self.issued: list[str] = []
        issue = self.tokens.issue_agent_token

        def spy(principal):
            token = issue(principal)
            self.issued.append(token)
            return token

        self.tokens.issue_agent_token = spy  # type: ignore[method-assign]
        self.jobs: list = []
        holder: dict = {}
        adapter = ScriptedAdapter(
            proposal,
            lambda token: TestClient(holder["app"], headers={"Authorization": f"Bearer {token}"}),
            clock,
            wait=self._pump,
        )
        runner = Runner(self.docker, profile(), clock)
        self.plane = build_control_plane(
            settings,
            RUN,
            store=store,
            clock=clock,
            tokens=self.tokens,
            runs_dir=self.runs_dir,
            docker=self.docker,
            github=self.github,
            pusher=FakePusher(self.github),
            fetcher=GitFetcher(str(self.remote), None, protocols=("file",)),
            adapter=adapter,
            patch_gate=PatchGate(
                policy=load_policy(), runner=runner, mirror=self.mirror, runs_dir=self.runs_dir
            ),
            release_runner=runner,
            dispatch=self.jobs.append,
        )
        holder["app"] = create_app(self.plane.context)
        self.ops = TestClient(holder["app"], raise_server_exceptions=False)

    def _pump(self, seconds: float) -> None:
        """ScriptedAdapter가 결정을 기다리는 동안 broker 루프가 돈다."""
        self.plane.broker_once()
        self.clock.advance(seconds)

    # 사람·하네스 행동

    def inject_s1(self) -> None:
        """`make scenario-s1`: 버그 base MES 기동, 로트 118 오류 3회, 배포 관찰 기록."""
        names = resource_names(RUN)
        self.docker.networks.add(names["network"])
        data = prepare_s1_data(self.runs_dir, RUN)
        self.previous_id = self.docker.run(
            mes_container_options(names["container"], RUN, names["network"], data), BASE_IMAGE
        )
        start = self.clock.utc_now()
        self.docker.log_history[names["container"]] = [
            (start + timedelta(seconds=5 * i)).strftime("%Y-%m-%dT%H:%M:%S.%f")
            + "000Z "
            + error_line(i)
            for i in range(3)
        ]
        with self.store.tx() as tx:
            record_deploy_observed(
                tx,
                run_id=RUN,
                service="mes-api",
                base_sha=self.base,
                image_id=BASE_IMAGE,
                container=names["container"],
                container_id=self.previous_id,
                actor="trusted_harness",
            )
        self.clock.advance(20)

    def inject_s2_lite(self, recent_deploy: bool) -> str:
        """`make scenario-s2-lite [RECENT_DEPLOY=1]`: 카메라 지표를 쓰고 감지기로 한 번 관찰한다."""
        result = scenarios.inject_s2_lite(
            RUN,
            self.runs_dir,
            self.store,
            self.settings.config,
            self.clock,
            recent_deploy=recent_deploy,
            base_sha=self.base,
            mes_image_id=BASE_IMAGE,
        )
        (incident_id,) = result["incidents"]
        self.clock.advance(20)
        return incident_id

    def approve_work(self, work_id: str) -> None:
        work = self.ops.get(f"/ops/work-items/{work_id}", headers=OPERATOR).json()["data"]
        response = self.ops.post(
            f"/ops/work-items/{work_id}/approve",
            json={
                "schema_version": "linemedic.v4",
                "expected_work_version": work["version"],
                "expected_issue_snapshot_sha256": work["issue_snapshot_sha256"],
                "approval_note": "운영자가 Issue 범위를 확인하고 승인",
            },
            headers={**OPERATOR, "Idempotency-Key": f"approve-{work_id}"},
        )
        assert response.status_code == 200, response.text

    def review_and_merge(self, proposal_id: str, pr_number: int) -> str:
        """G7 흉내: 봇이 아닌 리뷰어가 candidate head를 승인하고 squash 머지한다."""
        pull = self.github.pulls[pr_number]
        candidate = pull["head"]["sha"]
        workdir = self.runs_dir / RUN / "checkouts" / proposal_id / "1" / "repo.git"
        git(f"--git-dir={self.remote}", "fetch", "--quiet", str(workdir), candidate)
        tree = git(f"--git-dir={self.remote}", "rev-parse", f"{candidate}^{{tree}}")
        merge_sha = git(
            f"--git-dir={self.remote}",
            "commit-tree",
            tree,
            "-p",
            self.base,
            "-m",
            f"LineMedic 수정 제안 (#{pr_number})",
            env=REVIEWER_ENV,
        )
        self.github.add_review(pr_number, reviewer_id=REVIEWER, commit_id=candidate)
        self.github.merge_pull(pr_number, merge_sha, tree)
        return merge_sha

    def approve_release(self, incident_id, work_id, proposal_id, pr_number, merge_sha):
        """G8 흉내: 운영자가 PR·최종 merge SHA·지금 image를 명시해 승인한다."""
        incident = self.row("incidents", incident_id)
        return self.ops.post(
            "/ops/releases",
            json={
                "schema_version": "linemedic.v4",
                "run_id": RUN,
                "incident_id": incident_id,
                "work_id": work_id,
                "proposal_id": proposal_id,
                "pr_number": pr_number,
                "approved_merge_sha": merge_sha,
                "expected_incident_version": incident["version"],
                "expected_current_image_id": BASE_IMAGE,
                "approval_note": "diff·근거·허용 파일 범위를 확인하고 머지·승인",
            },
            headers={**OPERATOR, "Idempotency-Key": f"release:{work_id}:{merge_sha}"},
        )

    # 조회

    def row(self, table: str, row_id: str):
        return self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()

    def one(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def audit(self, event_type: str) -> list[dict]:
        return [
            {**json.loads(r["payload_json"]), "_actor": r["actor"], "_at": r["created_at"]}
            for r in self.conn.execute(
                "SELECT actor, payload_json, created_at FROM audit_events WHERE event_type = ?"
                " ORDER BY seq",
                (event_type,),
            )
        ]


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    return build_seed_mirror(tmp_path_factory.mktemp("seed").resolve())


@pytest.fixture
def e2e(store, conn, fake_clock, seed, tmp_path):
    return E2E(store, conn, fake_clock, seed, tmp_path)


def test_manual_proposal_goes_through_the_whole_path(e2e):
    plane, clock = e2e.plane, e2e.clock
    assert plane.recover()["attempts_closed"] == []  # 기동 복구: 할 일 없음

    # ① S1 → 감지 → ② Issue 생성·binding → work 승인 대기
    e2e.inject_s1()
    step = plane.step()
    assert len(step["detect"]["created"]) == 1
    (incident_id,) = step["detect"]["created"]
    assert [r["action"] for r in step["route"]] == ["created"]
    work = e2e.one("SELECT * FROM work_items WHERE incident_id = ?", (incident_id,))
    assert work["status"] == "WAITING_APPROVAL"
    issue_number = work["issue_number"]
    assert e2e.github.issues[issue_number]["user"]["id"] == BOT

    # ④ 운영자 승인 → 시작 댓글 receipt → READY
    e2e.approve_work(work["id"])
    clock.advance(2)
    (sent,) = plane.outbox_once()
    assert (sent["event_type"], sent["status"]) == ("WORK_STARTING", "ACCEPTED")
    assert e2e.row("work_items", work["id"])["status"] == "READY"
    clock.advance(3)

    # attempt → ScriptedAdapter 제안 → ⑤ 브로커 게이트 → 봇 PR
    supervised = plane.supervise_once()
    (attempt,) = supervised["attempts"]
    assert (attempt["adapter_status"], attempt["blocked"]) == ("closed", None)
    incident = e2e.row("incidents", incident_id)
    assert (incident["status"], e2e.row("work_items", work["id"])["status"]) == (
        "PR_OPENED",
        "WAITING_REVIEW",
    )
    proposal = e2e.one("SELECT * FROM proposals WHERE incident_id = ?", (incident_id,))
    assert proposal["decision"] == "ALLOWED"
    (pr_number,) = e2e.github.pulls
    body = e2e.github.pulls[pr_number]["body"]
    assert "사람이 미리 작성한 제안(manual_integration)" in body
    assert "에이전트 판단" not in body and f"Related to #{issue_number}" in body
    assert [t for t in e2e.issued if e2e.tokens.resolve(t) is not None] == []  # token 폐기
    clock.advance(2)
    assert [s["event_type"] for s in plane.outbox_once()] == ["PR_READY"]

    # G7(사람): 리뷰·squash 머지 → G8(사람): exact 배포 승인 → ⑥ 배포·검증
    merge_sha = e2e.review_and_merge(proposal["id"], pr_number)
    clock.advance(60)
    response = e2e.approve_release(incident_id, work["id"], proposal["id"], pr_number, merge_sha)
    assert response.status_code == 202, response.text
    (summary,) = [job() for job in e2e.jobs]
    assert (summary["verdict"], summary["incident_status"], summary["work_status"]) == (
        "PASS",
        "RESOLVED",
        "SUCCEEDED",
    )
    clock.advance(2)
    assert [s["event_type"] for s in plane.outbox_once()] == ["RECOVERY_VERIFIED"]

    # 결과 댓글: 시작 → PR 준비 → 복구 확인 순서로 같은 Issue에
    comments = e2e.github.comments[issue_number]
    assert [c["user"]["id"] for c in comments] == [BOT] * 3
    assert "작업 시작 예정" in comments[0]["body"]
    assert f"PR #{pr_number}" in comments[1]["body"]
    assert "업무 복구 확인" in comments[2]["body"]

    # 시작 receipt 시각 < attempt 시작 시각
    (started,) = e2e.audit("ATTEMPT_STARTED")
    assert from_rfc3339(started["start_notice_recorded_at"]) < from_rfc3339(started["_at"])
    assert (started["adapter"], started["origin"]) == ("scripted", "manual_integration")
    (finished,) = e2e.audit("ATTEMPT_FINISHED")
    assert (finished["status"], finished["proposal_ids"]) == ("closed", [proposal["id"]])

    # 결합 전이: incident 도착 상태마다 같은 트랜잭션의 work 도착 상태가 docs/03 §3 표와 같다
    incident_moves = [(m["from"], m["to"]) for m in e2e.audit("INCIDENT_TRANSITION")]
    assert incident_moves == [
        ("NEW", "INVESTIGATING"),
        ("INVESTIGATING", "VALIDATING"),
        ("VALIDATING", "PR_OPENED"),
        ("PR_OPENED", "DEPLOYING"),
        ("DEPLOYING", "VERIFYING"),
        ("VERIFYING", "RESOLVED"),
    ]
    work_moves = [(m["from"], m["to"]) for m in e2e.audit("WORK_TRANSITION")]
    assert work_moves == [
        ("WAITING_APPROVAL", "WAITING_NOTIFICATION"),
        ("WAITING_NOTIFICATION", "READY"),
        ("READY", "RUNNING"),
        ("RUNNING", "WAITING_REVIEW"),
        ("WAITING_REVIEW", "WAITING_VERIFICATION"),
        ("WAITING_VERIFICATION", "SUCCEEDED"),
    ]
    moves = [
        (r["event_type"], json.loads(r["payload_json"]))
        for r in e2e.conn.execute(
            "SELECT event_type, payload_json FROM audit_events"
            " WHERE event_type IN ('INCIDENT_TRANSITION', 'WORK_TRANSITION') ORDER BY seq"
        )
    ]
    work_status = None
    for index, (event, data) in enumerate(moves):
        if event == "WORK_TRANSITION":
            work_status = data["to"]
            continue
        following = moves[index + 1] if index + 1 < len(moves) else None
        after = (
            following[1]["to"] if following and following[0] == "WORK_TRANSITION" else work_status
        )
        if data["to"] in COUPLED_WORK_STATUS:  # 결합 전이: 같은 트랜잭션 뒤 work 상태
            assert after == COUPLED_WORK_STATUS[data["to"]], (data, after)

    # identity chain과 origin
    deploy = e2e.one("SELECT * FROM executions WHERE operation = 'DEPLOY'")
    chain = e2e.ops.get(f"/ops/executions/{deploy['id']}", headers=OPERATOR).json()["data"]
    identity = chain["identity_chain"]
    assert (identity["base_sha"], identity["approved_merge_sha"]) == (e2e.base, merge_sha)
    assert identity["pr_head_sha"] == e2e.github.pulls[pr_number]["head"]["sha"]
    assert identity["verdict"] == "PASS" and identity["image_id"] and identity["container_id"]
    verification = e2e.one("SELECT * FROM verifications")
    assert verification["origin"] == "manual_integration"
    with e2e.store.read() as tx:
        assert agent_performance_verifications(tx, RUN) == []  # 에이전트 성과 집계 제외
    kinds = {
        (r["operation"], r["status"])
        for r in e2e.conn.execute("SELECT operation, status FROM executions")
    }
    assert kinds == {
        ("CREATE_ISSUE", "SUCCEEDED"),
        ("CREATE_PR", "SUCCEEDED"),
        ("DEPLOY", "SUCCEEDED"),
    }

    # 사례 기억(W27): PR 준비(UNVERIFIED) → 업무 검증(VERIFIED_SUCCESS)이 같은 series의 revision
    assert len(plane.cases_once()) == 2 and plane.cases_once() == []
    notes = [
        dict(r)
        for r in e2e.conn.execute(
            "SELECT id, supersedes_id, outcome, phase, origin, publish_status, source_event_key,"
            " payload_json FROM case_notes ORDER BY revision"
        )
    ]
    assert [(n["outcome"], n["phase"], n["origin"]) for n in notes] == [
        ("UNVERIFIED", "review", "manual_integration"),
        ("VERIFIED_SUCCESS", "verification", "manual_integration"),
    ]
    assert notes[1]["supersedes_id"] == notes[0]["id"]
    assert {n["publish_status"] for n in notes} == {"PUBLISHED"}
    assert notes[1]["source_event_key"] == f"verification:{verification['id']}:final"
    success = json.loads(notes[1]["payload_json"])
    assert success["applicability"]["approved_merge_sha"] == merge_sha
    assert success["applicability"]["image_id"] == identity["image_id"]
    assert success["hypothesis_by"] == "사람이 미리 작성한 제안(검증되지 않은 가설)"

    # 대시보드(W18): 같은 DB를 읽기 전용으로 열어 전체 경로를 그대로 보인다
    model = dashboard.load_model(e2e.store.path)
    (card,) = model["works"]
    assert (card["work_status"], card["incident_status"]) == ("SUCCEEDED", "RESOLVED")
    assert card["verification_text"] == "지정한 업무 계약·관찰 범위 통과"
    assert card["start_notice"].startswith("댓글 등록 #")  # github_comment route의 접수
    assert model["run"]["memory_mode"] == "cold_start" and model["run"]["memory_snapshot"] == "N/A"
    assert model["run"]["repository"] == f"{REPO} (ID {REPO_ID})"
    assert all(step["state"] == "done" for step in card["timeline"])
    assert f"PR #{pr_number}" in {s["name"]: s for s in card["timeline"]}["PR"]["detail"]
    assert [c["outcome"] for c in card["cases"]] == ["UNVERIFIED", "VERIFIED_SUCCESS"]
    assert {n["status_text"] for n in card["notifications"]} == {"댓글 등록"}
    assert card["trace"]["status"].startswith("N/A(사람이 미리 작성한 제안")
    assert card["trace"]["tools"][:2] == ["get_incident", "submit_proposal"]  # W14 서버 기록
    assert "<script" not in dashboard.render(model).lower()

    # workspace·context: base 파일만, credential 없음
    root = e2e.runs_dir / RUN / "workspaces" / started["attempt_id"]
    repo = root / "work" / "repo"
    assert (repo / "app" / "defects.py").is_file() and not (repo / ".git").exists()
    assert (root / "agent_rules" / "system.md").is_file()
    trace_ref = json.loads(
        e2e.conn.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = 'ATTEMPT_FINISHED'"
        ).fetchone()[0]
    )["trace"]
    attempt_trace = json.loads((e2e.runs_dir / trace_ref).read_text(encoding="utf-8"))
    tools = [call["tool"] for call in attempt_trace["tool_calls"]["server"]]
    assert tools[:2] == ["get_incident", "submit_proposal"]  # 서버가 받은 도구 순서(W14)
    assert set(tools[2:]) <= {"get_proposal"} and attempt_trace["tool_calls"]["counted"] == 2
    assert attempt_trace["agent_mode"] == "local" and attempt_trace["tokens"]["status"] == "null"
    context = json.loads((root / "context.json").read_text(encoding="utf-8"))
    assert context["base"]["sha"] == e2e.base and context["origin"] == "manual_integration"
    assert all(token not in (root / "context.json").read_text() for token in e2e.issued)


def test_step_is_quiet_without_incidents_and_features_are_reported(e2e):
    plane = e2e.plane
    assert plane.features["github"].startswith("on") and plane.features["release"] == "on"
    assert plane.features["agent"].startswith("on: scripted")
    assert plane.features["memory"].startswith("on: cold_start")
    step = plane.step()
    assert step["route"] == [] and step["outbox"] == [] and step["broker"] == []
    assert step["cases"] == []
    assert step["supervisor"] == {
        "start_notices_expired": [],
        "attempts_closed": [],
        "attempts": [],
    }


@pytest.mark.parametrize("recent_deploy", [False, True], ids=["default", "recent_deploy"])
def test_s2_lite_manual_proposal_ends_as_an_unsent_work_order_draft(
    store, conn, fake_clock, seed, tmp_path, recent_deploy
):
    e2e = E2E(store, conn, fake_clock, seed, tmp_path, proposal=S2_MANUAL_PROPOSAL)
    plane, clock = e2e.plane, e2e.clock

    # S2-lite → 설비 사건 → Issue 생성·binding → 승인 → 시작 댓글 receipt
    incident_id = e2e.inject_s2_lite(recent_deploy)
    assert e2e.row("incidents", incident_id)["service"] == "vision-inspection"
    step = plane.step()
    assert [r["action"] for r in step["route"]] == ["created"]
    work = e2e.one("SELECT * FROM work_items WHERE incident_id = ?", (incident_id,))
    issue_number = work["issue_number"]
    e2e.approve_work(work["id"])
    clock.advance(2)
    (sent,) = plane.outbox_once()
    assert (sent["event_type"], sent["status"]) == ("WORK_STARTING", "ACCEPTED")
    clock.advance(3)

    # attempt → 사람 제안(equipment + create_work_order_draft) → 브로커 → 초안
    (attempt,) = plane.supervise_once()["attempts"]
    assert (attempt["adapter_status"], attempt["blocked"]) == ("closed", None)
    assert (
        e2e.row("incidents", incident_id)["status"],
        e2e.row("work_items", work["id"])["status"],
    ) == (
        "WORK_ORDER_DRAFTED",
        "HANDED_OFF",
    )  # 복구(RESOLVED)가 아니다
    proposal = e2e.one("SELECT * FROM proposals WHERE incident_id = ?", (incident_id,))
    submitted = json.loads(proposal["payload_json"])
    assert proposal["decision"] == "ALLOWED"
    assert (submitted["category"], submitted["action"]["type"]) == (
        "equipment",
        "create_work_order_draft",
    )
    (draft_row,) = e2e.conn.execute(
        "SELECT * FROM executions WHERE operation = 'DRAFT_WORK_ORDER'"
    ).fetchall()
    draft = json.loads(draft_row["result_json"])
    evidence = {
        r["id"]
        for r in e2e.conn.execute("SELECT id FROM evidence WHERE incident_id = ?", (incident_id,))
    }
    assert draft["equipment_id"] == "L3-CAM-2"
    assert draft["evidence_ids"] and set(draft["evidence_ids"]) <= evidence  # 관찰 지표 근거
    assert draft["probable_cause_is_hypothesis"] is True and draft["open_questions"]
    assert draft["manual_ref_id"] == "MANUAL-L3-VISION-4.2"  # 승인된 매뉴얼 참조
    assert (draft["review_required"], draft["delivery_status"]) == (True, "not_sent")

    # 코드 변경·PR·빌드·배포 0건
    assert e2e.github.pulls == {} and e2e.jobs == []
    assert not [c for c in e2e.docker.calls if c[0] in ("build", "run")]
    kinds = {
        (r["operation"], r["status"])
        for r in e2e.conn.execute("SELECT operation, status FROM executions")
    }
    assert kinds == {("CREATE_ISSUE", "SUCCEEDED"), ("DRAFT_WORK_ORDER", "SUCCEEDED")}

    # HANDOFF_DRAFTED 댓글 접수와 초안 미전송은 따로 남는다
    clock.advance(2)
    (handoff,) = plane.outbox_once()
    assert (handoff["event_type"], handoff["status"]) == ("HANDOFF_DRAFTED", "ACCEPTED")
    body = e2e.github.comments[issue_number][-1]["body"]
    assert "정비 요청 초안" in body and "정비 완료" not in body
    again = e2e.row("executions", draft_row["id"])
    assert json.loads(again["result_json"])["delivery_status"] == "not_sent"

    # 사례 기억: HANDOFF(실제 정비·복구 아님)
    assert len(plane.cases_once()) == 1
    (note,) = e2e.conn.execute("SELECT outcome, phase, origin FROM case_notes").fetchall()
    assert (note["outcome"], note["phase"], note["origin"]) == (
        "HANDOFF",
        "handoff",
        "manual_integration",
    )

    # recent-deploy 변형: MES 배포 기록이 보여도 host 경로는 같다
    observed = [d for d in e2e.audit("DEPLOY_OBSERVED") if d.get("service") == "mes-api"]
    assert len(observed) == (1 if recent_deploy else 0)

    # 대시보드: 정비 요청 초안으로 보이고 금지 표현이 없다
    model = dashboard.load_model(e2e.store.path)
    (card,) = model["works"]
    assert (card["work_status"], card["incident_status"]) == ("HANDED_OFF", "WORK_ORDER_DRAFTED")
    html = dashboard.render(model)
    assert "정비 요청 초안·담당자 확인 필요" in html and "정비 완료" not in html
