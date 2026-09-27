"""W24 통합 테스트: 로그 incident → 기존 Issue 연결 / 신규 Issue 생성 (FakeGitHub + 실제 SQLite).

- T-ISS-01: 기존 binding → 같은 번호 재사용·새 Issue 0개.
  완전 조회·후보 없음 → Issue 1개·binding·work 1개
- T-ISS-02: 제목만 비슷한 후보 2개 → AMBIGUOUS(생성 0).
  페이지 조회 실패 → LOOKUP_INCOMPLETE(생성 0). PR 항목은 후보가 아님
- T-ISS-03: 생성 뒤 timeout → UNKNOWN, 두 번째 POST 0회, reconcile로 1개 채택.
  다른 작성자의 같은 marker는 채택하지 않음
- 로그 본문의 `#번호`·HTML marker로 연결되지 않음, shadow 모드는 만들 Issue 계획만
"""

import json
from pathlib import Path

import httpx
import pytest

from linemedic.common.config import load_settings
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.detector import (
    Detector,
    problem_fingerprint,
    settings_for_run,
    signature,
)
from linemedic.control_plane.issue_router import IssueRouter, marker, parse_form
from linemedic.control_plane.issue_sync import IssueSync
from linemedic.control_plane.log_store import MemoryLogStore
from linemedic.integrations.github import FakeGitHub
from linemedic.tests.helpers.api import OPERATOR_TOKEN, RUN, make_api
from linemedic.tests.helpers.db_rows import count, insert_run

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "linemedic.toml"
REPO, REPO_ID = "demo-team/l3-mes-api", 100001
TRUSTED, STRANGER, BOT = 200001, 300001, 900001
SCOPE = f"eval:{RUN}"
ENV = {
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REPOSITORY_ID": str(REPO_ID),
    "ISSUE_TRUSTED_AUTHOR_IDS": str(TRUSTED),
    "ISSUE_INTAKE_ENABLED": "true",
}


def error_line(n: int = 0, **changes) -> str:
    event = {
        "ts": f"2026-09-27T01:00:{n % 60:02d}.000000Z",
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
    }
    event.update(changes)
    return json.dumps(event)


def log_fingerprint(**changes) -> str:
    sig = signature(json.loads(error_line(**changes)), "mes-api")
    return f"fp-v1:{problem_fingerprint(sig)}"


def form_body(service: str, fingerprint: str) -> str:
    return (
        f"### 서비스\n\n{service}\n\n### 오류 signature\n\n{fingerprint}\n\n### 현상\n\n요약 실패\n"
    )


class World:
    def __init__(self, store, conn, clock, *, write_enabled=True, per_page=10, max_pages=3):
        insert_run(conn, RUN)
        self.store, self.conn, self.clock = store, conn, clock
        config = load_settings(CONFIG_PATH, ENV).config
        intake = config.issue_intake.model_copy(
            update={"per_page": per_page, "max_pages": max_pages}
        )
        self.config = config.model_copy(update={"issue_intake": intake})
        self.catalog = Catalog.from_config(self.config)
        self.github = FakeGitHub(
            REPO_ID, REPO, clock=clock, bot_id=BOT, write_enabled=write_enabled
        )
        self.sync = IssueSync(
            store,
            self.github,
            run_id=RUN,
            config=self.config,
            catalog=self.catalog,
            clock=clock,
            routing_scope=SCOPE,
        )
        self.router = IssueRouter(
            store, self.github, self.sync, catalog=self.catalog, clock=clock, wait_seconds=0
        )
        self.results = []
        self.detector = Detector(
            store,
            settings_for_run(self.config, RUN, SCOPE, "mes-api"),
            clock,
            MemoryLogStore(),
            on_new_incident=lambda incident_id: self.results.append(self.router.route(incident_id)),
        )

    def activate(self):
        assert self.sync.poll_once().mode == "initial_import"
        self.clock.advance(61)

    def detect(self, lines=None, **changes) -> str:
        """같은 오류 3번 → 사건 NEW → hook으로 router.route."""
        lines = lines or [error_line(i, **changes) for i in range(3)]
        outcome = None
        for line in lines:
            outcome = self.detector.observe_line(line, "container:mes")
        assert outcome is not None and outcome.action == "created"
        return outcome.incident_id

    def incident(self, incident_id):
        return self.conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()

    def works(self):
        return self.conn.execute("SELECT * FROM work_items ORDER BY rowid").fetchall()

    def bindings(self):
        return self.conn.execute("SELECT * FROM issue_bindings").fetchall()

    def executions(self):
        return self.conn.execute("SELECT * FROM executions").fetchall()

    def posts(self):
        return [r for r in self.github.requests if r.method == "POST"]

    def bind_row(self, number, basis="OPERATOR", fingerprint=None):
        fp = (fingerprint or log_fingerprint()).split(":", 1)[1]
        self.conn.execute(
            "INSERT INTO issue_bindings(routing_scope, repository_id, fingerprint_version,"
            " problem_fingerprint, issue_number, basis, decision_json, created_at)"
            " VALUES (?, ?, 'fp-v1', ?, ?, ?, '{}', 't')",
            (SCOPE, REPO_ID, fp, number, basis),
        )


@pytest.fixture
def world(store, conn, fake_clock):
    return World(store, conn, fake_clock)


# ── T-ISS-01 ──────────────────────────────────────────────────


def test_complete_lookup_without_candidates_creates_one_issue_binding_and_work(world):
    world.activate()
    incident_id = world.detect()
    (result,) = world.results
    assert (result.lookup, result.action) == ("NO_MATCH_IN_SCOPE", "created")
    (issue,) = world.github.issues.values()
    assert issue["user"]["id"] == BOT and issue["labels"] == [{"name": "linemedic"}]
    (execution,) = world.executions()
    assert (execution["operation"], execution["status"]) == ("CREATE_ISSUE", "SUCCEEDED")
    fp = log_fingerprint()
    assert execution["logical_key"] == f"issue:{SCOPE}:{REPO_ID}:{fp.split(':', 1)[1]}"
    assert marker(SCOPE, fp, execution["id"]) in issue["body"]
    (binding,) = world.bindings()
    assert (binding["issue_number"], binding["basis"]) == (issue["number"], "CREATED")
    (work,) = world.works()
    assert (work["status"], work["issue_number"], work["incident_id"]) == (
        "WAITING_APPROVAL",
        issue["number"],
        incident_id,
    )
    assert world.incident(incident_id)["status"] == "NEW"
    assert len(world.posts()) == 1


def test_issue_text_is_sanitized_template_without_raw_log(world):
    world.activate()
    world.detect(path="/defects/@admin-team/summary", lot_id="L3-SECRET-LOT")
    (issue,) = world.github.issues.values()
    text = issue["title"] + issue["body"]
    assert "원인: 미확정" in issue["body"]
    assert "L3-SECRET-LOT" not in text and "request_failed" not in text  # raw 로그 없음
    assert "@admin-team" not in text  # 멘션 무력화
    assert "http" not in text.replace("https://github.com", "")
    assert len(issue["title"]) <= 120


def test_existing_binding_reuses_same_issue_without_new_issue(world):
    existing = world.github.add_issue(title="요약 오류", author_id=STRANGER)
    world.activate()
    world.bind_row(existing["number"])
    incident_id = world.detect()
    (result,) = world.results
    assert (result.lookup, result.action, result.issue_number) == (
        "EXISTING_BINDING",
        "linked",
        existing["number"],
    )
    assert world.posts() == [] and len(world.github.issues) == 1  # 새 Issue 0개
    assert any(r.path.endswith(f"/issues/{existing['number']}") for r in world.github.requests)
    (work,) = world.works()
    assert (work["issue_number"], work["incident_id"]) == (existing["number"], incident_id)


def test_bound_issue_closed_waits_for_operator(world):
    existing = world.github.add_issue(title="요약 오류", author_id=STRANGER, state="closed")
    world.activate()
    world.bind_row(existing["number"])
    incident_id = world.detect()
    assert world.results[0].action == "escalated"
    assert world.incident(incident_id)["reason_code"] == "PERMISSION_REQUIRED"
    assert world.posts() == [] and world.works() == []


def test_bound_issue_missing_is_lookup_incomplete(world):
    world.activate()
    # 예전에 본 Issue #42(지금은 삭제·이전됨). binding FK 때문에 mirror 행이 있다
    world.conn.execute(
        "INSERT INTO github_issues(repository_id, issue_number, node_id, state, author_id,"
        " created_at, updated_at, snapshot_sha256, payload_json, last_observed_at)"
        " VALUES (?, 42, 'I_gone', 'open', 1, 't', 't', 'x', '{}', 't')",
        (REPO_ID,),
    )
    world.bind_row(42)
    incident_id = world.detect()
    assert world.results[0].detail["reason"] == "bound_issue_not_found"
    assert world.incident(incident_id)["reason_code"] == "LOOKUP_INCOMPLETE"


# ── T-ISS-02 ──────────────────────────────────────────────────


def test_title_only_similar_candidates_are_ambiguous_without_creation(world):
    first = world.github.add_issue(title="mes-api KeyError 발생", author_id=STRANGER)
    second = world.github.add_issue(title="mes-api에서 KeyError 반복", author_id=TRUSTED)
    world.activate()
    incident_id = world.detect()
    (result,) = world.results
    assert (result.lookup, result.action) == ("AMBIGUOUS", "escalated")
    assert result.detail["candidates"] == [first["number"], second["number"]]
    incident = world.incident(incident_id)
    assert (incident["status"], incident["reason_code"]) == ("ESCALATED", "AMBIGUOUS")
    (blocked,) = world.conn.execute("SELECT * FROM notifications").fetchall()
    assert blocked["logical_key"].startswith(f"notify:intake:{RUN}:{incident_id}:WORK_BLOCKED")
    report = json.loads(blocked["payload_json"])
    assert (report["work_id"], report["issue_number"], report["stage"]) == (None, None, "intake")
    assert len(report["evidence_ids"]) == 3
    assert world.posts() == [] and world.works() == []


def test_single_similar_candidate_is_also_ambiguous(world):
    world.github.add_issue(title="KeyError on /defects/summary", author_id=STRANGER)
    world.activate()
    world.detect()
    assert world.results[0].lookup == "AMBIGUOUS" and world.posts() == []


def test_page_failure_during_refresh_is_lookup_incomplete(world):
    world.activate()
    world.github.fail_next("forbidden", when=lambda r: r.path.endswith("/issues"))
    incident_id = world.detect()
    assert world.results[0].lookup == "LOOKUP_INCOMPLETE"
    assert world.incident(incident_id)["reason_code"] == "LOOKUP_INCOMPLETE"
    assert world.posts() == []


def test_incomplete_mirror_is_not_treated_as_none(store, conn, fake_clock):
    w = World(store, conn, fake_clock, per_page=10, max_pages=1)
    for i in range(15):
        w.github.add_issue(title=f"무관한 Issue {i}", author_id=STRANGER)
    w.sync.poll_once()  # 1페이지 cap → complete=false
    fake_clock.advance(61)
    w.detect()
    assert w.results[0].lookup == "LOOKUP_INCOMPLETE" and w.posts() == []


def test_pull_request_items_are_not_candidates(world):
    world.github.add_pull(title="mes-api KeyError 수정", author_id=STRANGER)
    world.activate()
    world.detect()
    assert (world.results[0].lookup, world.results[0].action) == ("NO_MATCH_IN_SCOPE", "created")


# ── 승인 form·scope ───────────────────────────────────────────


def test_structured_form_from_trusted_author_is_reused(world):
    world.activate()
    reported = world.github.add_issue(
        title="보고", body=form_body("mes-api", log_fingerprint()), author_id=TRUSTED
    )
    world.sync.poll_once()  # W23: 승인된 작성자의 새 Issue → Issue 기반 work
    issue_work = world.works()[0]
    world.clock.advance(61)
    incident_id = world.detect()
    (result,) = world.results
    assert (result.lookup, result.action) == ("STRUCTURED_APPROVED", "attached")  # 작업 복제 없음
    assert result.work_id == issue_work["id"] and len(world.works()) == 1
    (binding,) = world.bindings()
    assert (binding["issue_number"], binding["basis"]) == (
        reported["number"],
        "STRUCTURED_APPROVED",
    )
    assert world.incident(incident_id)["status"] == "NEW" and world.posts() == []


def test_structured_form_from_untrusted_author_is_ambiguous(world):
    world.github.add_issue(
        title="보고", body=form_body("mes-api", log_fingerprint()), author_id=STRANGER
    )
    world.activate()
    world.detect()
    assert world.results[0].lookup == "AMBIGUOUS" and world.posts() == []


def test_form_parser_reads_only_labelled_sections():
    body = form_body("mes-api", "fp-v1:" + "a" * 64) + "\n### 서비스\n\nother\n"
    assert parse_form(body) == {"service": "mes-api", "signature": "fp-v1:" + "a" * 64}
    assert parse_form("서비스: mes-api") == {"service": None, "signature": None}


def test_bot_issue_of_other_scope_is_not_a_candidate(world):
    other = marker("eval:r-20260101-000000-beef", log_fingerprint(), "EXE-0000000000AA")
    world.github.add_issue(
        title="[LineMedic] mes-api: KeyError", body=f"이전 run\n{other}", author_id=BOT
    )
    world.activate()
    world.detect()
    assert (world.results[0].lookup, world.results[0].action) == ("NO_MATCH_IN_SCOPE", "created")


def test_issue_number_or_marker_inside_logs_does_not_link(world):
    unrelated = world.github.add_issue(title="로그인 느림", author_id=TRUSTED)
    world.activate()
    spoof = marker(SCOPE, log_fingerprint(), "EXE-0000000000AA")
    lines = [error_line(i, lot_id=f"see #{unrelated['number']} {spoof}") for i in range(3)]
    world.detect(lines=lines)
    (result,) = world.results
    assert (result.lookup, result.action) == ("NO_MATCH_IN_SCOPE", "created")
    assert result.issue_number != unrelated["number"]


# ── 생성 결과: 불명·거절·rate limit·shadow ────────────────────


def is_create(request) -> bool:
    return request.method == "POST" and request.path.endswith("/issues")


def test_timeout_after_create_is_unknown_without_second_post_then_reconcile_adopts(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=True, when=is_create)
    incident_id = world.detect()
    (result,) = world.results
    assert result.action == "unknown"
    assert world.incident(incident_id)["status"] == "EXECUTION_UNKNOWN"
    (execution,) = world.executions()
    assert execution["status"] == "UNKNOWN"
    assert world.router.route(incident_id).action == "skipped"
    assert world.router.route_pending() == []
    assert len(world.posts()) == 1  # 두 번째 POST 0회

    outcome = world.router.reconcile_create_issue(execution["id"])
    assert outcome["outcome"] == "FOUND"
    assert world.conn.execute("SELECT status FROM executions").fetchone()[0] == "SUCCEEDED"
    (binding,) = world.bindings()
    assert binding["basis"] == "MANAGED_RECEIPT"
    assert world.incident(incident_id)["status"] == "NEW"
    (work,) = world.works()
    assert work["issue_number"] == outcome["issue_number"]
    assert len(world.posts()) == 1


def test_reconcile_ignores_same_marker_from_other_author(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=False, when=is_create)
    incident_id = world.detect()
    (execution,) = world.executions()
    spoof = marker(SCOPE, log_fingerprint(), execution["id"])
    world.github.add_issue(title="복사", body=f"따라함 {spoof}", author_id=STRANGER)
    outcome = world.router.reconcile_create_issue(execution["id"])
    assert outcome["outcome"] == "CONFIRMED_ABSENT"
    assert world.incident(incident_id)["status"] == "EXECUTION_UNKNOWN"  # 운영자 판단 대기
    assert world.bindings() == [] and len(world.posts()) == 1  # 다시 POST하지 않는다
    stored = json.loads(world.conn.execute("SELECT result_json FROM executions").fetchone()[0])
    assert stored["reconcile"]["outcome"] == "CONFIRMED_ABSENT"


def test_reconcile_with_two_bot_matches_is_conflict(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=True, when=is_create)
    world.detect()
    (execution,) = world.executions()
    same = marker(SCOPE, log_fingerprint(), execution["id"])
    world.github.add_issue(title="중복", body=f"x {same}", author_id=BOT)
    assert world.router.reconcile_create_issue(execution["id"])["outcome"] == "CONFLICT"
    assert world.bindings() == []


def test_reconcile_lookup_failure_is_inconclusive(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=True, when=is_create)
    world.detect()
    (execution,) = world.executions()
    world.github.fail_next("server_error")
    assert world.router.reconcile_create_issue(execution["id"])["outcome"] == "INCONCLUSIVE"


def test_rate_limited_create_keeps_incident_new_and_retries_same_intent(world):
    world.activate()
    world.github.fail_next("rate_limited", when=is_create)
    incident_id = world.detect()
    assert world.results[0].action == "retry_later"
    assert world.incident(incident_id)["status"] == "NEW"
    (execution,) = world.executions()
    assert execution["status"] == "FAILED"
    (retried,) = world.router.route_pending()
    assert retried.action == "created" and retried.execution_id == execution["id"]
    assert len(world.github.issues) == 1 and len(world.executions()) == 1


def test_forbidden_create_escalates_without_retry(world):
    world.activate()
    world.github.fail_next("forbidden", when=is_create)
    incident_id = world.detect()
    assert world.results[0].action == "escalated"
    assert world.incident(incident_id)["reason_code"] == "PERMISSION_REQUIRED"
    assert world.executions()[0]["status"] == "FAILED"


def test_shadow_mode_only_plans_issue(store, conn, fake_clock):
    w = World(store, conn, fake_clock, write_enabled=False)
    w.activate()
    incident_id = w.detect()
    (result,) = w.results
    assert result.action == "planned"
    assert result.plan["labels"] == ["linemedic"] and "원인: 미확정" in result.plan["body"]
    assert w.executions() == [] and w.github.issues == {} and w.github.write_calls == 0
    assert w.incident(incident_id)["status"] == "NEW"


def test_detector_merge_does_not_route_again(world):
    world.activate()
    world.detect()
    world.detector.observe_line(error_line(9), "container:mes")  # 같은 사건에 병합
    assert len(world.results) == 1


# ── 운영 API·CLI ──────────────────────────────────────────────


def binding_body(incident, number, **overrides):
    body = {
        "schema_version": "linemedic.v4",
        "run_id": RUN,
        "issue_number": number,
        "expected_incident_version": incident["version"],
        "decision_note": "후보 중 #1이 같은 문제",
    }
    body.update(overrides)
    return body


def post_binding(api, incident_id, body, key="bind-1"):
    return api.client.post(
        f"/ops/incidents/{incident_id}/issue-binding",
        json=body,
        headers={**api.operator, "Idempotency-Key": key},
    )


def test_operator_binding_after_ambiguous_records_binding_for_retry(world):
    first = world.github.add_issue(title="mes-api KeyError 발생", author_id=STRANGER)
    world.github.add_issue(title="mes-api에서 KeyError 반복", author_id=STRANGER)
    world.activate()
    incident_id = world.detect()
    api = make_api(world.store, world.conn, issue_router=world.router)
    candidates = api.client.get(f"/ops/issues/candidates/{incident_id}", headers=api.operator)
    assert candidates.status_code == 200
    data = candidates.json()["data"]
    assert [c["issue_number"] for c in data["candidates"]] == [1, 2]
    assert data["mirror"]["complete"] is True and data["binding"] is None
    incident = world.incident(incident_id)
    response = post_binding(api, incident_id, binding_body(incident, first["number"]))
    assert response.status_code == 200
    bound = response.json()["data"]
    assert (bound["basis"], bound["retry_required"], bound["work_id"]) == ("OPERATOR", True, None)
    (binding,) = world.bindings()
    assert (binding["issue_number"], binding["basis"]) == (first["number"], "OPERATOR")
    calls = len(world.github.requests)
    replay = post_binding(api, incident_id, binding_body(incident, first["number"]))
    assert replay.content == response.content and len(world.github.requests) == calls


def test_operator_binding_of_new_incident_creates_work(store, conn, fake_clock):
    w = World(store, conn, fake_clock, write_enabled=False)
    issue = w.github.add_issue(title="요약 오류", author_id=STRANGER)
    w.activate()
    incident_id = w.detect()  # shadow: NEW로 남는다
    api = make_api(w.store, w.conn, issue_router=w.router)
    response = post_binding(
        api, incident_id, binding_body(w.incident(incident_id), issue["number"])
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["work_created"] is True and data["retry_required"] is False
    (work,) = w.works()
    assert (work["issue_number"], json.loads(work["authorization_json"])["basis"]) == (
        issue["number"],
        "operator_binding",
    )


def test_operator_binding_errors(world):
    world.github.add_issue(title="mes-api KeyError 발생", author_id=STRANGER)
    pull = world.github.add_pull(title="PR", author_id=STRANGER)
    world.activate()
    incident_id = world.detect()
    incident = world.incident(incident_id)
    api = make_api(world.store, world.conn, issue_router=world.router)
    stale = post_binding(api, incident_id, binding_body(incident, 1, expected_incident_version=0))
    assert stale.status_code == 409
    assert (
        post_binding(api, incident_id, binding_body(incident, pull["number"]), "k2").status_code
        == 422
    )
    assert post_binding(api, incident_id, binding_body(incident, 999), "k3").status_code == 404
    other_repo = binding_body(incident, 1, repository="other/repo")
    assert post_binding(api, incident_id, other_repo, "k4").status_code == 422
    assert world.bindings() == []
    ok = post_binding(
        api, incident_id, binding_body(incident, 1), "k2"
    )  # 거절된 키는 다시 쓸 수 있다
    assert ok.status_code == 200
    without = make_api(world.store, world.conn)
    assert post_binding(without, incident_id, binding_body(incident, 1), "k5").status_code == 503
    assert (
        without.client.get(
            f"/ops/issues/candidates/{incident_id}", headers=without.operator
        ).status_code
        == 503
    )
    missing = api.client.get("/ops/issues/candidates/INC-0000000000FF", headers=api.operator)
    assert missing.status_code == 404


def test_cli_issue_bind_calls_control_api_with_operator_token(world, tmp_path, monkeypatch):
    from linemedic import cli

    world.github.add_issue(title="mes-api KeyError 발생", author_id=STRANGER)
    world.activate()
    incident_id = world.detect()
    api = make_api(world.store, world.conn, issue_router=world.router)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("idempotency-key")))
        headers = {
            k: v for k, v in request.headers.items() if k in ("authorization", "idempotency-key")
        }
        if request.method == "GET":
            response = api.client.get(request.url.path, headers=headers)
        else:
            response = api.client.post(
                request.url.path,
                content=request.content,
                headers={**headers, "content-type": "application/json"},
            )
        return httpx.Response(response.status_code, content=response.content)

    monkeypatch.setenv("CONTROL_OPERATOR_TOKEN", OPERATOR_TOKEN)
    args = cli.build_parser().parse_args(
        [
            "issue-bind",
            "--incident-id",
            incident_id,
            "--issue-number",
            "1",
            "--env-file",
            str(tmp_path / "x"),
        ]
    )
    assert cli._issue_bind(args, transport=httpx.MockTransport(handler)) == 0
    assert [s[:2] for s in seen] == [
        ("GET", f"/ops/incidents/{incident_id}"),
        ("POST", f"/ops/incidents/{incident_id}/issue-binding"),
    ]
    assert seen[1][2].startswith(f"issue-bind:{incident_id}:1:")
    assert world.bindings()[0]["basis"] == "OPERATOR"
    monkeypatch.delenv("CONTROL_OPERATOR_TOKEN")
    assert cli._issue_bind(args, transport=httpx.MockTransport(handler)) == 2
    assert count(world.conn, "issue_bindings") == 1


# ── 경계 경우 ─────────────────────────────────────────────────


def clone_incident(world, incident_id, new_id="INC-0000000000B1") -> str:
    """W25 retry처럼 같은 fingerprint의 새 incident를 만든다(테스트 전용 직접 준비)."""
    world.conn.execute(
        "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, status, service, line_id, count, first_seen,"
        " last_seen, details_json) SELECT ?, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, 'NEW', service, line_id, count, first_seen,"
        " last_seen, details_json FROM incidents WHERE id = ?",
        (new_id, incident_id),
    )
    return new_id


def test_single_token_overlap_is_not_a_candidate(world):
    world.github.add_issue(title="mes-api 배포 일정 문의", author_id=STRANGER)
    world.activate()
    world.detect()
    assert (world.results[0].lookup, world.results[0].action) == ("NO_MATCH_IN_SCOPE", "created")


def test_new_incident_with_unknown_create_intent_does_not_post_again(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=False, when=is_create)
    first = world.detect()
    world.conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (first,))
    second = clone_incident(world, first)
    result = world.router.route(second)
    assert (result.lookup, result.detail["reason"]) == ("LOOKUP_INCOMPLETE", "create_issue_unknown")
    assert len(world.posts()) == 1 and len(world.executions()) == 1


def test_reconcile_requires_bot_author_marker_and_time_window(world):
    world.activate()
    world.github.fail_next("timeout", after_side_effect=False, when=is_create)
    world.detect()
    (execution,) = world.executions()
    expected = marker(SCOPE, log_fingerprint(), execution["id"])
    world.github.add_issue(title="복사", body=f"따라함 {expected}", author_id=STRANGER)
    world.github.add_issue(title="[linemedic-smoke] N11", body="marker 없음", author_id=BOT)
    old = world.github.add_issue(title="오래된 봇 Issue", body=expected, author_id=BOT)
    old["created_at"] = "2026-09-26T00:00:00Z"  # intent보다 훨씬 전
    outcome = world.router.reconcile_create_issue(execution["id"])
    assert outcome["outcome"] == "CONFIRMED_ABSENT" and outcome["matches"] == []


def test_issue_based_incident_is_not_routed(world):
    world.activate()
    world.github.add_issue(title="요청", author_id=TRUSTED)
    world.sync.poll_once()
    issue_incident = world.conn.execute("SELECT id FROM incidents").fetchone()["id"]
    result = world.router.route(issue_incident)
    assert (result.action, result.detail["reason"]) == ("skipped", "not_log_incident")


def test_lost_binding_is_restored_from_managed_receipt(world):
    world.activate()
    first = world.detect()
    issue_number = world.results[0].issue_number
    world.conn.execute("DELETE FROM issue_bindings")  # binding을 잃었다
    world.conn.execute(
        "UPDATE work_items SET status = 'BLOCKED' WHERE incident_id = ?", (first,)
    )  # 이전 generation은 끝났다
    world.conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (first,))
    second = clone_incident(world, first)
    result = world.router.route(second)
    assert (result.lookup, result.action, result.issue_number) == (
        "MANAGED_RECEIPT",
        "linked",
        issue_number,
    )
    (binding,) = world.bindings()
    assert binding["basis"] == "MANAGED_RECEIPT"
    generations = sorted(w["generation"] for w in world.works())
    assert generations == [1, 2] and len(world.posts()) == 1


def test_receipt_issue_without_our_marker_is_lookup_incomplete(world):
    world.activate()
    first = world.detect()
    issue_number = world.results[0].issue_number
    world.github.update_issue(issue_number, body="사람이 본문을 바꿨다")  # marker가 사라짐
    world.conn.execute("DELETE FROM issue_bindings")
    world.conn.execute("UPDATE work_items SET status = 'BLOCKED' WHERE incident_id = ?", (first,))
    world.conn.execute("UPDATE incidents SET status = 'ESCALATED' WHERE id = ?", (first,))
    result = world.router.route(clone_incident(world, first))
    assert (result.lookup, result.detail["reason"]) == ("LOOKUP_INCOMPLETE", "receipt_mismatch")
