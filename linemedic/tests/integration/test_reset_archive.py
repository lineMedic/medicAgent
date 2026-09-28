"""W19 run-new·reset·export·archive (T-RESET-01, FR-12, D86).

실제 migration·SQLite·파일 시스템과 FakeDocker·FakeGitHub·FakeBaseline으로 시험한다.

- reset 뒤 이전 run의 DB 행·evidence·case note가 남고, 원격(FakeGitHub)의 브랜치·PR·Issue는 그대로다
- 정리는 이 run 라벨의 컨테이너·network와 `runs/<run>/workspaces`뿐이다. prune·wildcard 삭제가 없다
- archive된 run의 프로세스와 새 run의 프로세스 모두 과거 run 알림·제안·작업을 처리하지 않는다
"""

import argparse
import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path

import httpx
import pytest

from linemedic import cli
from linemedic.agent import rules
from linemedic.common.config import load_settings
from linemedic.common.ids import new_id
from linemedic.control_plane import release, run_export, runs
from linemedic.control_plane.auth import OperatorPrincipal, TokenRegistry
from linemedic.control_plane.broker.intake import Broker
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.main import build_control_plane
from linemedic.control_plane.memory.search import CaseSearch
from linemedic.control_plane.notifications.worker import OutboxWorker
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.control_plane.supervisor import Supervisor
from linemedic.integrations.docker import CommandResult, FakeDocker
from linemedic.integrations.github import FakeGitHub
from linemedic.integrations.github_baseline import (
    BaselineError,
    FakeBaseline,
    HttpBaseline,
    baseline_branch,
)
from linemedic.tests.helpers.api import make_api
from linemedic.tests.helpers.db_rows import insert_incident, insert_issue, insert_work

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO, REPO_ID = "demo-team/l3-mes-api", 100001
BASE = "1" * 40
T0 = "2026-09-27T01:00:00.000000Z"
SECRET = "ghp_" + "S" * 36
READER_TOKEN = "test-reader-token-" + "d" * 32
PRUNE_COMMAND = re.compile(r"\b(system|image|container|volume|network|builder)\s+prune\b")


def settings(**env):
    base = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_REPOSITORY_ID": str(REPO_ID),
        "BASELINE_COMMIT": BASE,
    }
    return load_settings(REPO_ROOT / "config" / "linemedic.toml", {**base, **env})


class Seed:
    """과거 run 하나에 남는 기록: 사건·work·근거·사례·알림·외부 실행·제안·검증."""

    def __init__(self, store, conn, clock, runs_dir: Path) -> None:
        self.store, self.conn, self.clock, self.runs_dir = store, conn, clock, runs_dir
        self.run_id = runs.new_run(store, settings(), clock)["run_id"]
        insert_issue(conn, 42)
        self.incident = insert_incident(
            conn, self.run_id, "PR_OPENED", attempt_id="ATT-0000000000E1"
        )
        self.work = insert_work(
            conn, self.run_id, self.incident, 42, "WAITING_REVIEW", attempt_id="ATT-0000000000E1"
        )
        conn.execute(
            "INSERT INTO evidence(id, run_id, incident_id, kind, observed_at, source_identity,"
            " payload_json, content_sha256) VALUES (?, ?, ?, 'log_error', ?, 'test', ?, ?)",
            (
                new_id("EV"),
                self.run_id,
                self.incident,
                T0,
                json.dumps({"event": {"message": f"token {SECRET}", "path": str(runs_dir / "x")}}),
                "0" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO case_notes(id, series_id, revision, supersedes_id, repository_id,"
            " service, problem_fingerprint, source_run_id, source_incident_id, work_id,"
            " source_event_key, outcome, phase, origin, publish_status, observed_at, created_at,"
            " content_sha256, payload_json) VALUES ('CASE-00000000000A-R1', 'CASE-00000000000A',"
            " 1, NULL, ?, 'mes-api', 'fp', ?, ?, ?, 'pr:x:opened', 'UNVERIFIED', 'review',"
            " 'agent_release', 'PUBLISHED', ?, ?, ?, ?)",
            (
                REPO_ID,
                self.run_id,
                self.incident,
                self.work,
                T0,
                T0,
                "0" * 64,
                json.dumps({"summary": f"{sorted(eval_identifiers())[0]} 입력 시도"}),
            ),
        )
        self.notifications = {
            status: self.notification(status) for status in ("PENDING", "SENDING", "UNKNOWN")
        }
        self.notifications["ACCEPTED"] = self.notification("ACCEPTED", receipt="IC_9")
        self.proposal = new_id("PROP")
        conn.execute(
            "INSERT INTO proposals(id, run_id, incident_id, work_id, attempt_id, idempotency_key,"
            " body_sha256, decision, received_at, payload_json, checks_json) VALUES (?, ?, ?, ?,"
            " 'ATT-0000000000E1', 'k', ?, 'RECEIVED', ?, '{}', '{}')",
            (self.proposal, self.run_id, self.incident, self.work, "0" * 64, T0),
        )
        self.execution = new_id("EXE")
        conn.execute(
            "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id, operation,"
            " logical_key, idempotency_key, request_sha256, status, stage, intended_at, updated_at,"
            " request_json, result_json) VALUES (?, ?, ?, ?, ?, 'CREATE_PR', 'pr:old', 'k', ?,"
            " 'UNKNOWN', 'create', ?, ?, '{}', ?)",
            (
                self.execution,
                self.run_id,
                self.incident,
                self.work,
                self.proposal,
                "0" * 64,
                T0,
                T0,
                json.dumps({"pr_number": 51, "log": str(runs_dir / self.run_id / "x.log")}),
            ),
        )
        for name in ("workspaces/ATT-0000000000E1/repo", "checkouts/PROP-1", "releases/EXE-1",
                     "verifications"):  # fmt: skip
            (runs_dir / self.run_id / name).mkdir(parents=True)
            (runs_dir / self.run_id / name / "file.txt").write_text("x", encoding="utf-8")

    def notification(self, status, receipt=None):
        notification_id = new_id("NOT")
        self.conn.execute(
            "INSERT INTO notifications(id, run_id, incident_id, work_id, event_type, route_id,"
            " logical_key, payload_sha256, status, receipt_id, created_at, updated_at,"
            " payload_json, result_json) VALUES (?, ?, ?, ?, 'PR_READY', 'github-issue-primary',"
            " ?, ?, ?, ?, ?, ?, ?, '{}')",
            (
                notification_id,
                self.run_id,
                self.incident,
                self.work,
                f"notify:{notification_id}",
                "0" * 64,
                status,
                receipt,
                T0,
                T0,
                json.dumps({"recipient_hint": "RECIPIENT-MARKER-19"}),
            ),
        )
        return notification_id

    def counts(self) -> dict[str, int]:
        tables = {
            "incidents": "run_id",
            "work_items": "run_id",
            "evidence": "run_id",
            "notifications": "run_id",
            "executions": "run_id",
            "proposals": "run_id",
            "case_notes": "source_run_id",
        }
        return {
            table: self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (self.run_id,)
            ).fetchone()[0]
            for table, column in tables.items()
        }


@pytest.fixture
def seed(store, conn, fake_clock, tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return Seed(store, conn, fake_clock, runs_dir)


class ReadyAdapter:
    def not_ready(self) -> str | None:
        return None


def labeled_docker(seed) -> FakeDocker:
    docker = FakeDocker()
    for name, label in (
        (f"linemedic-mes-{seed.run_id}", f"linemedic.run_id={seed.run_id}"),
        (f"runner-{seed.run_id}", f"linemedic.run={seed.run_id}"),
        ("linemedic-mes-r-20260101-000000-beef", "linemedic.run_id=r-20260101-000000-beef"),
    ):
        docker.run(["--name", name, "--label", label], "linemedic-mes:base")
    docker.network_create(f"linemedic-net-{seed.run_id}", {"linemedic.run_id": seed.run_id})
    docker.network_create("linemedic-net-other", {"linemedic.run_id": "r-20260101-000000-beef"})
    return docker


def plane_for(seed, run_id, github):
    return build_control_plane(
        settings(),
        run_id,
        store=seed.store,
        clock=seed.clock,
        tokens=TokenRegistry(),
        runs_dir=seed.runs_dir,
        docker=FakeDocker(),
        github=github,
    )


def fake_github(seed, remote: bool = True) -> FakeGitHub:
    """remote면 보존 확인용 원격 상태(브랜치·PR·Issue 표시)를 넣는다."""
    github = FakeGitHub(REPO_ID, REPO, clock=seed.clock, write_enabled=True)
    if remote:
        github.branches.update(
            {"main": BASE, f"baseline/{seed.run_id}": BASE, "autofix/x": "2" * 40}
        )
        github.pulls[51] = {"number": 51, "state": "open"}
        github.issues[42] = {"number": 42, "state": "open", "title": "불량 집계"}
    return github


# ── T-RESET-01: 보존 ──────────────────────────────────────────


def test_reset_keeps_records_remote_state_and_new_cold_start_sees_no_past_cases(seed):
    before = seed.counts()
    github = fake_github(seed)
    remote = (dict(github.branches), dict(github.pulls), dict(github.issues))
    docker = labeled_docker(seed)
    result = runs.reset(
        seed.store,
        seed.run_id,
        runs_dir=seed.runs_dir,
        clock=seed.clock,
        principal="operator:test",
        docker=docker,
        terms=eval_identifiers(),
    )
    assert seed.counts() == before  # DB 행을 지우지 않는다
    assert (dict(github.branches), dict(github.pulls), dict(github.issues)) == remote
    assert github.requests == []
    assert result["intake_stopped"] == "stopped_now"
    assert "make run-new" in " ".join(result["next"])
    active = seed.conn.execute("SELECT COUNT(*) FROM demo_runs WHERE active = 1").fetchone()[0]
    assert active == 0  # 새 run은 자동으로 만들지 않는다
    new_run = runs.new_run(seed.store, settings(), seed.clock)["run_id"]
    incident = insert_incident(seed.conn, new_run, "INVESTIGATING", attempt_id="ATT-0000000000E2")
    insert_issue(seed.conn, 43)
    work = insert_work(seed.conn, new_run, incident, 43, "RUNNING", attempt_id="ATT-0000000000E2")
    cold = CaseSearch(seed.store, mode="cold_start", engine="sqlite_fts5")
    data = cold.search(run_id=new_run, incident_id=incident, work_id=work, q="입력 시도").data
    assert (data["status"], data["hits"]) == ("DISABLED", [])
    events = [
        row[0]
        for row in seed.conn.execute(
            "SELECT event_type FROM audit_events WHERE run_id = ? ORDER BY seq", (seed.run_id,)
        )
    ]
    assert events[-2:] == [
        "RUN_INTAKE_STOPPED",
        "RUN_ARCHIVED",
    ]  # 이미 비활성이라 새 run이 또 바꾸지 않는다


def test_archive_twice_is_safe_and_keeps_both_exports(seed):
    first = runs.archive(
        seed.store, seed.run_id, runs_dir=seed.runs_dir, clock=seed.clock, principal="op"
    )
    seed.clock.advance(1)
    second = runs.archive(
        seed.store, seed.run_id, runs_dir=seed.runs_dir, clock=seed.clock, principal="op"
    )
    assert (first["intake_stopped"], second["intake_stopped"]) == (
        "stopped_now",
        "already_inactive",
    )
    assert first["export"] != second["export"]
    assert (seed.runs_dir / first["export"] / "export-manifest.json").is_file()
    assert (seed.runs_dir / second["export"] / "export-manifest.json").is_file()


def test_unknown_run_cannot_be_archived(seed):
    with pytest.raises(runs.RunError):
        runs.archive(
            seed.store,
            "r-20990101-000000-dead",
            runs_dir=seed.runs_dir,
            clock=seed.clock,
            principal="op",
        )


# ── 배포·검증 lock (spec 08 §2, PR #57 리뷰) ──────────────────

HOLDER = "EXE-0000000000EE"


def _hold_release(seed) -> None:
    with seed.store.tx() as tx:
        release._hold(tx, seed.run_id, HOLDER)  # W12: 승인 배포·업무 검증 중


def _unhold_release(seed) -> None:
    with seed.store.tx() as tx:
        release._unhold(tx, seed.run_id, HOLDER)


def test_reset_is_refused_while_a_deploy_holds_the_run_lock(seed):
    _hold_release(seed)
    docker = labeled_docker(seed)
    before = sorted(docker.containers)

    def reset():
        return runs.reset(
            seed.store,
            seed.run_id,
            runs_dir=seed.runs_dir,
            clock=seed.clock,
            principal="op",
            docker=docker,
        )

    with pytest.raises(runs.RunError) as raised:
        reset()
    assert (raised.value.code, raised.value.details) == (
        "release_locked",
        {"holder_execution_id": HOLDER},
    )
    assert sorted(docker.containers) == before  # 검증 대상 MES를 지우지 않는다
    assert "remove_container" not in [call[0] for call in docker.calls]
    active = seed.conn.execute(
        "SELECT active FROM demo_runs WHERE id = ?", (seed.run_id,)
    ).fetchone()[0]
    assert active == 1  # 정지도 하지 않는다
    assert not (seed.runs_dir / seed.run_id / "export").exists()
    assert (seed.runs_dir / seed.run_id / "workspaces").is_dir()
    _unhold_release(seed)
    result = reset()  # lock이 풀리면 진행한다
    assert f"linemedic-mes-{seed.run_id}" in [c["name"] for c in result["cleanup"]["containers"]]


def test_ops_archive_is_refused_while_a_deploy_holds_the_run_lock(seed):
    _hold_release(seed)
    api = run_api(seed)
    path, body = f"/ops/runs/{seed.run_id}/archive", {"schema_version": "linemedic.v4"}
    refused = post(api, path, body)
    error = refused.json()["error"]
    assert (refused.status_code, error["code"]) == (409, "STATE_CONFLICT")
    assert error["details"] == {"reason": "release_locked", "holder_execution_id": HOLDER}
    active = seed.conn.execute(
        "SELECT active FROM demo_runs WHERE id = ?", (seed.run_id,)
    ).fetchone()[0]
    assert active == 1 and not (seed.runs_dir / seed.run_id / "export").exists()
    _unhold_release(seed)
    retried = post(api, path, body)  # 거부는 부작용이 없어 같은 키로 다시 보낼 수 있다
    assert retried.status_code == 200, retried.text


def test_cli_reset_reports_the_lock_and_unknown_run_without_a_traceback(seed, monkeypatch, capsys):
    monkeypatch.setenv("RUNS_DIR", str(seed.runs_dir))
    _hold_release(seed)
    common = {"run_id": seed.run_id, "db": seed.store.path, "env_file": seed.runs_dir / "none.env"}
    assert cli._reset(argparse.Namespace(**common), docker=labeled_docker(seed)) == 2
    err = capsys.readouterr().err
    assert HOLDER in err and f"make reconcile RUN_ID={seed.run_id}" in err
    missing = argparse.Namespace(**{**common, "run_id": "r-20990101-000000-dead"})
    assert cli._reset(missing, docker=FakeDocker()) == 2


# ── 정지·과거 run 작업 비처리 ─────────────────────────────────


def test_archived_run_process_stops_all_new_work_and_external_writes(seed):
    github = fake_github(seed)
    plane = plane_for(seed, seed.run_id, github)
    runs.archive(seed.store, seed.run_id, runs_dir=seed.runs_dir, clock=seed.clock, principal="op")
    step = plane.step()
    assert step["intake_open"] is False
    assert (step["detect"], step["poll"], step["route"], step["outbox"], step["broker"]) == (
        None,
        None,
        [],
        [],
        [],
    )
    assert step["supervisor"] == {
        "start_notices_expired": [],
        "attempts_closed": [],
        "attempts": [],
    }
    assert github.requests == []
    status = seed.conn.execute(
        "SELECT status FROM notifications WHERE id = ?", (seed.notifications["PENDING"],)
    ).fetchone()[0]
    assert status == "PENDING"  # 보내지 않았고 버리지도 않았다(export 미해결 목록)


def test_new_run_process_does_not_pick_up_past_run_work(seed):
    runs.reset(seed.store, seed.run_id, runs_dir=seed.runs_dir, clock=seed.clock, principal="op")
    new_run = runs.new_run(seed.store, settings(), seed.clock)["run_id"]
    github = fake_github(seed, remote=False)  # 새 run의 poll이 읽는다(빈 repo)
    plane = plane_for(seed, new_run, github)
    seed.conn.execute(  # 과거 run의 시작 알림 대기 work: 새 run의 supervisor가 만료시키면 안 된다
        "UPDATE work_items SET status = 'WAITING_NOTIFICATION' WHERE id = ?", (seed.work,)
    )
    seed.clock.advance(3600)
    step = plane.step()
    work_status = seed.conn.execute("SELECT status FROM work_items WHERE id = ?", (seed.work,))
    assert work_status.fetchone()[0] == "WAITING_NOTIFICATION"
    assert step["intake_open"] is True and step["outbox"] == [] and step["broker"] == []
    assert [r for r in github.requests if r.method != "GET"] == []  # 댓글·PR 쓰기 없음
    row = seed.conn.execute(
        "SELECT status FROM notifications WHERE id = ?", (seed.notifications["PENDING"],)
    ).fetchone()
    assert row[0] == "PENDING"
    decision = seed.conn.execute(
        "SELECT decision FROM proposals WHERE id = ?", (seed.proposal,)
    ).fetchone()[0]
    assert decision == "RECEIVED"


def test_components_scoped_to_a_run_ignore_other_runs(seed, store):
    other = "r-20990101-000000-beef"
    config = settings().config
    github = fake_github(seed)
    adapters = {"github_comment": ReadyAdapter()}  # _claim은 준비 여부만 본다(보내지 않음)
    scoped = OutboxWorker(
        store, adapters=adapters, config=config, repo=REPO, clock=seed.clock, run_id=other
    )
    assert scoped._claim() is None
    unscoped = OutboxWorker(store, adapters=adapters, config=config, repo=REPO, clock=seed.clock)
    assert unscoped._claim() is not None  # 필터가 없으면 과거 run 알림을 집는다(기존 동작)
    broker = Broker(store, Catalog.from_config(config), {}, "github-issue-primary", run_id=other)
    assert broker._claim() is None
    assert github.requests == []
    seed.conn.execute("UPDATE work_items SET status = 'WAITING_NOTIFICATION' WHERE id = ?",
                      (seed.work,))  # fmt: skip
    supervisor = Supervisor(store, config=config, clock=seed.clock, run_id=other)
    assert supervisor.expire_start_notices() == []
    status = seed.conn.execute("SELECT status FROM work_items WHERE id = ?", (seed.work,))
    assert status.fetchone()[0] == "WAITING_NOTIFICATION"


# ── 정리: 라벨·경로 ────────────────────────────────────────────


def test_cleanup_removes_only_this_runs_labeled_resources_and_workspaces(seed):
    docker = labeled_docker(seed)
    other_ws = seed.runs_dir / "r-20260101-000000-beef" / "workspaces" / "ATT-X"
    other_ws.mkdir(parents=True)
    result = runs.cleanup(docker, seed.runs_dir, seed.run_id)
    assert sorted(c["name"] for c in result["containers"]) == [
        f"linemedic-mes-{seed.run_id}",
        f"runner-{seed.run_id}",
    ]
    assert set(docker.containers) == {"linemedic-mes-r-20260101-000000-beef"}
    assert result["networks"] == [f"linemedic-net-{seed.run_id}"]
    assert docker.networks == {"linemedic-net-other"}
    assert result["workspaces"] == f"{seed.run_id}/workspaces"
    run_dir = seed.runs_dir / seed.run_id
    assert not (run_dir / "workspaces").exists()
    for kept in ("checkouts", "releases", "verifications"):  # 검사·배포·검증 증거는 둔다
        assert list((run_dir / kept).rglob("file.txt")), kept
    assert other_ws.is_dir() and result["errors"] == []
    removed = [call for call in docker.calls if call[0] == "remove_container"]
    assert all(len(call[1]) == 64 for call in removed)  # 정확한 컨테이너 ID로만


def test_cleanup_refuses_a_symlinked_workspace(seed, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    workspaces = seed.runs_dir / seed.run_id / "workspaces"
    for child in workspaces.rglob("*"):
        if child.is_file():
            child.unlink()
    for child in sorted(workspaces.rglob("*"), reverse=True):
        child.rmdir()
    workspaces.rmdir()
    workspaces.symlink_to(outside, target_is_directory=True)
    result = runs.cleanup(None, seed.runs_dir, seed.run_id)
    assert result["workspaces"] is None
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert result["errors"] == ["docker 연결 없음: 컨테이너·network는 정리하지 않았다"]


def test_cleanup_does_not_remove_a_container_whose_label_changed(seed, monkeypatch):
    docker = labeled_docker(seed)
    other_id = docker.containers["linemedic-mes-r-20260101-000000-beef"]["Id"]
    monkeypatch.setattr(docker, "list_containers", lambda label: [{"id": other_id, "name": "x"}])
    result = runs.cleanup(docker, seed.runs_dir, seed.run_id)
    assert "linemedic-mes-r-20260101-000000-beef" in docker.containers
    assert any(error.startswith("라벨 재확인 실패") for error in result["errors"])


def test_docker_lookup_failure_is_reported(seed):
    docker = labeled_docker(seed)
    docker.daemon_down = True
    result = runs.cleanup(docker, seed.runs_dir, seed.run_id)
    assert result["containers"] == [] and len(docker.containers) == 3
    assert "컨테이너 조회 실패(linemedic.run_id)" in result["errors"]
    assert "network 조회 실패" in result["errors"]


def test_bad_run_id_is_refused_before_any_path_use(seed):
    with pytest.raises(runs.RunError):
        runs.cleanup(None, seed.runs_dir, "../escape")


def test_code_has_no_prune_or_wildcard_deletion():
    offenders = []
    for path in sorted((REPO_ROOT / "linemedic").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("linemedic/tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                text = node.value  # 명령 인자·명령줄 모양만 본다(설명 문장의 단어는 제외)
                if text == "prune" or PRUNE_COMMAND.search(text) or "rm -rf" in text:
                    offenders.append((rel, text[:40]))
            if (  # glob 결과를 지우는 호출
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") in ("rmtree", "unlink", "remove")
                and any("glob" in ast.unparse(arg) for arg in node.args)
            ):
                offenders.append((rel, ast.unparse(node)[:60]))
    assert offenders == []


# ── export ────────────────────────────────────────────────────


def test_export_writes_private_original_and_sanitized_shared_copy(seed):
    with seed.store.read() as tx:
        pending = runs.unresolved(tx, seed.run_id)
    root, manifest = run_export.export_run(
        seed.store, seed.run_id, seed.runs_dir, clock=seed.clock, unresolved=pending,
        terms=eval_identifiers(),
    )  # fmt: skip
    private, shared = root / "private", root / "shared"
    assert stat.S_IMODE(os.stat(private).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(private / "evidence.jsonl").st_mode) == 0o600
    assert SECRET in (private / "evidence.jsonl").read_text(encoding="utf-8")  # 원본 그대로
    shared_text = "".join(p.read_text(encoding="utf-8") for p in sorted(shared.iterdir()))
    assert SECRET not in shared_text and "[REDACTED:github_token]" in shared_text
    assert sorted(eval_identifiers())[0] not in shared_text
    assert str(seed.runs_dir.resolve()) not in shared_text and "<RUNS_DIR>" in shared_text
    assert "RECIPIENT-MARKER-19" not in shared_text  # 알림 본문을 공유본에 넣지 않는다
    rows = [
        json.loads(line)
        for line in (shared / "notifications.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all("payload" not in row and "result" not in row for row in rows)
    for rel, info in manifest["files"].items():
        digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        assert digest == info["sha256"], rel
    assert manifest["unresolved"] == {
        "executions": 1,
        "notifications": 3,
        "works": 1,
        "incidents": 1,
        "verifications": 0,
        "proposals": 1,
    }
    record = (shared / "run-record.md").read_text(encoding="utf-8")
    assert "#42" in record and "#51" in record and "IC_9" in record
    with pytest.raises(run_export.ExportError):  # 같은 시각의 경로를 덮어쓰지 않는다
        run_export.export_run(
            seed.store, seed.run_id, seed.runs_dir, clock=seed.clock, unresolved=pending
        )


def test_unresolved_lists_unknown_and_pending_with_next_steps(seed):
    with seed.store.read() as tx:
        pending = runs.unresolved(tx, seed.run_id)
    (execution,) = pending["executions"]
    assert execution["next"] == (
        f"make reconcile RUN_ID={seed.run_id} EXECUTION_ID={seed.execution}"
    )
    by_status = {n["status"]: n["next"] for n in pending["notifications"]}
    assert by_status["UNKNOWN"].startswith("make notification-reconcile NOTIFICATION_ID=")
    assert by_status["SENDING"].startswith("make notification-reconcile")
    assert "다른 Issue로 다시 보내지 않는다" in by_status["PENDING"]
    assert [p["id"] for p in pending["proposals"]] == [seed.proposal]


# ── run-new: manifest·기준 브랜치 ─────────────────────────────


def test_new_run_manifest_records_identity_memory_and_baseline_request(store, fake_clock):
    created = runs.new_run(store, settings(), fake_clock)
    with store.read() as tx:
        row = tx.one("SELECT config_json FROM demo_runs WHERE id = ?", (created["run_id"],))
    manifest = json.loads(row[0])
    policy = REPO_ROOT / "linemedic" / "policies" / "broker_policy.toml"
    assert manifest["identity"]["policy_sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert manifest["identity"]["contract_id"] == "defect-summary-v1"
    assert len(manifest["identity"]["contract_sha256"]) == 64
    assert manifest["identity"]["prompt_sha256"] == rules.bundle_sha256()  # W14 규칙 묶음
    assert manifest["memory"] == {"mode": "cold_start", "snapshot_path": None, "snapshot_id": None}
    assert manifest["baseline"] == {
        "branch": f"baseline/{created['run_id']}",
        "commit": BASE,
        "status": "NOT_REQUESTED",
    }


def test_new_run_creates_the_baseline_branch_before_the_run(store, fake_clock):
    port = FakeBaseline()
    created = runs.new_run(store, settings(), fake_clock, baseline=port)
    branch = f"baseline/{created['run_id']}"
    assert port.branches == {branch: BASE} and created["baseline"]["status"] == "CREATED"
    with store.read() as tx:
        audit = tx.one(
            "SELECT payload_json FROM audit_events WHERE event_type = 'BASELINE_BRANCH_READY'"
        )
    assert json.loads(audit[0])["branch"] == branch


def test_baseline_failure_creates_no_run(store, fake_clock, monkeypatch):
    port = FakeBaseline()
    monkeypatch.setattr(runs, "new_run_id", lambda clock: "r-20260927-000000-abcd")
    port.branches["baseline/r-20260927-000000-abcd"] = "9" * 40  # 다른 SHA
    with pytest.raises(BaselineError, match="branch_points_elsewhere"):
        runs.new_run(store, settings(), fake_clock, baseline=port)
    with store.read() as tx:
        assert tx.one("SELECT COUNT(*) FROM demo_runs")[0] == 0
    no_commit = settings(BASELINE_COMMIT="")
    with pytest.raises(runs.RunError):
        runs.new_run(store, no_commit, fake_clock, baseline=FakeBaseline())


def test_baseline_branch_name_has_no_slash_in_the_run_part():
    assert baseline_branch("r-20260927-000000-abcd") == "baseline/r-20260927-000000-abcd"
    with pytest.raises(BaselineError):
        baseline_branch("r/../x")


def baseline_transport(repo_id=REPO_ID, ref_sha=None, create_status=201, race_sha=None):
    calls = []
    state = {"created": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer setup-credential-value"
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"id": repo_id})
        if request.method == "GET" and "/git/ref/heads/" in request.url.path:
            sha = ref_sha if not state["created"] else race_sha
            if sha is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"object": {"sha": sha}})
        if request.method == "POST":
            state["created"] = True
            return httpx.Response(create_status, json={})
        return httpx.Response(500)

    return httpx.MockTransport(handler), calls


def test_http_baseline_checks_repo_then_creates_or_confirms():
    transport, calls = baseline_transport()
    port = HttpBaseline(REPO_ID, REPO, "setup-credential-value", transport=transport)
    assert port.ensure_branch("baseline/r-20260927-000000-abcd", BASE) == "CREATED"
    assert [m for m, _ in calls] == ["GET", "GET", "POST"]
    assert "setup-credential-value" not in repr(port)
    transport, calls = baseline_transport(ref_sha=BASE)
    port = HttpBaseline(REPO_ID, REPO, "setup-credential-value", transport=transport)
    assert port.ensure_branch("baseline/r-20260927-000000-abcd", BASE) == "EXISTS"
    assert "POST" not in [m for m, _ in calls]
    transport, _ = baseline_transport(create_status=422, race_sha=BASE)
    port = HttpBaseline(REPO_ID, REPO, "setup-credential-value", transport=transport)
    assert port.ensure_branch("baseline/r-20260927-000000-abcd", BASE) == "EXISTS"


def test_http_baseline_never_writes_to_another_repo_or_moves_a_branch():
    transport, calls = baseline_transport(repo_id=999)
    port = HttpBaseline(REPO_ID, REPO, "setup-credential-value", transport=transport)
    with pytest.raises(BaselineError, match="repo_id_mismatch"):
        port.ensure_branch("baseline/r-20260927-000000-abcd", BASE)
    assert calls == [("GET", f"/repos/{REPO}")]
    transport, calls = baseline_transport(ref_sha="9" * 40)
    port = HttpBaseline(REPO_ID, REPO, "setup-credential-value", transport=transport)
    with pytest.raises(BaselineError, match="branch_points_elsewhere"):
        port.ensure_branch("baseline/r-20260927-000000-abcd", BASE)
    assert "POST" not in [m for m, _ in calls]


# ── 운영 API·CLI ───────────────────────────────────────────────


def run_api(seed):
    api = make_api(seed.store, seed.conn, settings=settings(), runs_dir=seed.runs_dir)
    api.tokens.register_operator(READER_TOKEN, OperatorPrincipal("reader", frozenset({"read"})))
    return api


def post(api, path, body, key="k-1", headers=None):
    return api.client.post(
        path, json=body, headers={**(headers or api.operator), "Idempotency-Key": key}
    )


def test_ops_archive_stops_and_exports_without_deleting(seed):
    api = run_api(seed)
    body = {"schema_version": "linemedic.v4"}
    path = f"/ops/runs/{seed.run_id}/archive"
    reader = {"Authorization": f"Bearer {READER_TOKEN}"}
    assert post(api, path, body, headers=reader).status_code == 403
    first = post(api, path, body)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["intake_stopped"] == "stopped_now"
    assert data["unresolved"]["notifications"] == 3
    assert (seed.runs_dir / data["export"] / "export-manifest.json").is_file()
    assert (seed.runs_dir / seed.run_id / "workspaces").is_dir()  # API는 지우지 않는다
    assert post(api, path, body).json() == first.json()  # 같은 키 재전송
    missing = post(api, "/ops/runs/r-20990101-000000-dead/archive", body, key="k-2")
    assert missing.status_code == 404


def test_ops_run_new_requires_the_current_active_run(seed):
    api = run_api(seed)
    stale = post(api, "/ops/runs", {"schema_version": "linemedic.v4",
                                     "current_run_id": "r-20260101-000000-beef"})  # fmt: skip
    assert stale.status_code == 404  # 없는 run
    other = runs.new_run(seed.store, settings(), seed.clock)["run_id"]  # 지금 활성은 other
    conflict = post(
        api, "/ops/runs", {"schema_version": "linemedic.v4", "current_run_id": seed.run_id}
    )
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "STATE_CONFLICT")
    created = post(
        api,
        "/ops/runs",
        {"schema_version": "linemedic.v4", "current_run_id": other},
        key="k-2",
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["previous_run_id"] == other and data["baseline"]["status"] == "NOT_REQUESTED"
    assert "db" not in data
    active = seed.conn.execute("SELECT id FROM demo_runs WHERE active = 1").fetchone()[0]
    assert active == data["run_id"]


def test_cli_run_new_baseline_requires_g2_and_g10(tmp_path, monkeypatch):
    for name in (
        "GITHUB_SETUP_CREDENTIAL",
        "GITHUB_REPOSITORY",
        "GITHUB_REPOSITORY_ID",
        "RUNS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASELINE_COMMIT", BASE)
    args = argparse.Namespace(
        db=tmp_path / "runs" / "linemedic.db",
        host_manifest=None,
        create_baseline=True,
        config=cli.DEFAULT_CONFIG_PATH,
        env_file=tmp_path / "none.env",
    )
    assert cli._run_new(args) == 2
    assert not args.db.exists()  # 준비가 안 됐으면 run도 만들지 않는다
    port, missing = cli.baseline_port(settings(GITHUB_SETUP_CREDENTIAL="x" * 40))
    assert port is None and missing == ["github.write_enabled(G10)"]
    args.create_baseline = False
    assert cli._run_new(args, baseline=FakeBaseline()) == 0  # 주입한 port로만 만든다


def test_cli_export_and_reset(seed, monkeypatch, capsys):
    monkeypatch.setenv("RUNS_DIR", str(seed.runs_dir))
    common = {"run_id": seed.run_id, "db": seed.store.path, "env_file": seed.runs_dir / "none.env"}
    assert cli._export_run(argparse.Namespace(**common)) == 0
    exported = json.loads(capsys.readouterr().out)
    assert Path(exported["export"]).is_dir() and exported["notifications"] == 3
    seed.clock.advance(1)
    docker = labeled_docker(seed)
    assert cli._reset(argparse.Namespace(**common), docker=docker) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cleanup"]["workspaces"] == f"{seed.run_id}/workspaces"
    assert result["unresolved"]["executions"] == 1
    docker.daemon_down = True
    seed.clock.advance(1)
    assert cli._reset(argparse.Namespace(**common), docker=docker) == 1  # 정리 오류는 숨기지 않는다
    missing = argparse.Namespace(**{**common, "run_id": "r-20990101-000000-dead"})
    assert cli._export_run(missing) == 2


def test_cli_docker_listing_parses_ids_and_names(monkeypatch):
    from linemedic.integrations.docker import CliDocker

    docker = CliDocker()
    seen = []

    def fake_run(args, timeout=None):
        seen.append(args)
        return CommandResult(0, "abc123\tlinemedic-mes-r-1\ndef456\trunner-r-1\n", "")

    monkeypatch.setattr(docker, "_run", fake_run)
    assert docker.list_containers("linemedic.run_id=r-1") == [
        {"id": "abc123", "name": "linemedic-mes-r-1"},
        {"id": "def456", "name": "runner-r-1"},
    ]
    assert seen[0][:5] == ["ps", "-a", "--no-trunc", "--filter", "label=linemedic.run_id=r-1"]
    assert "prune" not in " ".join(seen[0])
    monkeypatch.setattr(docker, "_run", lambda args, timeout=None: CommandResult(1, "", "down"))
    assert docker.list_networks("linemedic.run_id=r-1") is None


def test_cleanup_removes_workspaces_with_read_only_agent_rules(seed):
    attempt = seed.runs_dir / seed.run_id / "workspaces" / "ATT-0000000000E9"
    rules.install_rules(attempt / "agent_rules")  # W14: 0555 디렉터리·0444 파일
    result = runs.cleanup(None, seed.runs_dir, seed.run_id)
    assert result["workspaces"] == f"{seed.run_id}/workspaces"
    assert not (seed.runs_dir / seed.run_id / "workspaces").exists()
