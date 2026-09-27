"""W04 단위 테스트: 합성 MES 시드·fixture·holdout·시드 git 이력·S1 주입 명령.

실제 컨테이너 실행은 `linemedic/tests/integration/test_mes_container.py`(docker 마커)에서 한다.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from linemedic.common.clock import FakeClock
from linemedic.factory_sim import scenarios
from linemedic.scripts import seed_demo_repo

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED = REPO_ROOT / "l3-mes-api-seed"
LOTS = SEED / "data" / "lots"
HOLDOUT = REPO_ROOT / "linemedic" / "eval" / "holdout-defects-v1.json"
EMPTY_LOT = REPO_ROOT / "linemedic" / "factory_sim" / "fixtures" / "lots" / "L3-EMPTY-000.json"
D57_FIELDS = {
    "ts",
    "level",
    "service",
    "event",
    "request_id",
    "lot_id",
    "path",
    "status",
    "error_type",
    "error_field",
    "top_frame",
    "top_frame_line",
    "stack",
}
RUN_ID = "r-20260927-040000-abcd"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def reference_summary(lot_id, records):
    """테스트 전용 참조 집계(업무 규칙). 시드에는 두지 않는다."""
    counts = Counter(r.get("inspector_id", "미지정") for r in records)
    return {"lot_id": lot_id, "total_defects": len(records), "by_inspector": dict(counts)}


# ── fixture 무결성 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("document", "total", "missing"),
    [
        (lambda: load_json(LOTS / "L3-0927-118.json"), 7, 2),
        (lambda: load_json(LOTS / "L3-0927-101.json"), 3, 0),
        (lambda: load_json(HOLDOUT)["input"], 5, 3),
    ],
)
def test_lot_fixture_integrity(document, total, missing):
    doc = document()
    records = doc["records"]
    assert len(records) == total
    assert sum("inspector_id" not in r for r in records) == missing
    assert len({r["defect_id"] for r in records}) == total
    assert all(r.get("inspector_id") not in (None, "") for r in records if "inspector_id" in r)
    assert "key 없음" not in json.dumps(doc, ensure_ascii=False)


def test_lot_file_names_match_lot_ids_and_empty_lot():
    for path in LOTS.glob("*.json"):
        assert load_json(path)["lot_id"] == path.stem
    assert load_json(EMPTY_LOT) == {"lot_id": "L3-EMPTY-000", "records": []}


def test_holdout_expected_matches_business_rule():
    holdout = load_json(HOLDOUT)
    lot = holdout["input"]
    assert holdout["expected"] == reference_summary(lot["lot_id"], lot["records"])
    assert holdout["expected"]["by_inspector"] == {"I-03": 2, "미지정": 3}


# ── 시드 앱 (버그 base) ───────────────────────────────────────


@pytest.fixture
def seed_app(monkeypatch):
    monkeypatch.syspath_prepend(str(SEED))
    monkeypatch.delenv("MES_DATA_DIR", raising=False)
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        monkeypatch.delitem(sys.modules, name)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    yield TestClient(main.app)
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        sys.modules.pop(name, None)


def log_lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_bug_lot_fails_with_structured_keyerror_log(seed_app, capsys, monkeypatch):
    monkeypatch.setenv("LINEMEDIC_TEST_SECRET", "secret-value-should-not-be-logged")
    response = seed_app.get("/defects/summary", params={"lot_id": "L3-0927-118"})
    assert response.status_code == 500
    errors = [
        line for line in log_lines(capsys.readouterr().out) if line["event"] == "request_failed"
    ]
    assert len(errors) == 1
    error = errors[0]
    assert set(error) == D57_FIELDS
    assert error["service"] == "mes-api"
    assert error["level"] == "ERROR"
    assert error["status"] == 500
    assert error["path"] == "/defects/summary"
    assert error["lot_id"] == "L3-0927-118"
    assert error["error_type"] == "KeyError"
    assert error["error_field"] == "inspector_id"
    assert error["top_frame"] == "app.defects:summarize"
    assert isinstance(error["top_frame_line"], int)
    assert error["stack"][-1].startswith("app.defects:summarize:")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", error["ts"])
    serialized = json.dumps(error, ensure_ascii=False)
    assert "secret-value-should-not-be-logged" not in serialized
    assert str(SEED) not in serialized  # 절대 경로를 로그에 넣지 않는다


def test_normal_lot_and_error_responses(seed_app, capsys):
    ok = seed_app.get("/defects/summary", params={"lot_id": "L3-0927-101"})
    assert ok.status_code == 200
    assert ok.json() == {
        "lot_id": "L3-0927-101",
        "total_defects": 3,
        "by_inspector": {"I-01": 2, "I-02": 1},
    }
    assert seed_app.get("/defects/summary", params={"lot_id": "L3-0000-999"}).status_code == 404
    assert seed_app.get("/defects/summary", params={"lot_id": "../etc"}).status_code == 400
    assert seed_app.get("/healthz").json() == {"status": "ok"}
    completed = [
        line for line in log_lines(capsys.readouterr().out) if line["event"] == "request_completed"
    ]
    assert {line["status"] for line in completed} >= {200, 400, 404}


def test_empty_lot_via_api(seed_app, monkeypatch, tmp_path):
    (tmp_path / "lots").mkdir()
    shutil.copyfile(EMPTY_LOT, tmp_path / "lots" / "L3-EMPTY-000.json")
    monkeypatch.setenv("MES_DATA_DIR", str(tmp_path))
    response = seed_app.get("/defects/summary", params={"lot_id": "L3-EMPTY-000"})
    assert response.json() == {"lot_id": "L3-EMPTY-000", "total_defects": 0, "by_inspector": {}}


def test_protected_regression_passes_on_bug_base(tmp_path):
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("MES_DATA_DIR", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(ini),
            "--rootdir",
            str(SEED),
            "tests/regression",
        ],
        cwd=SEED,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── 시드에 정답·평가 자료가 없는지 ────────────────────────────


def seed_text_files():
    for path in SEED.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path, path.read_text(encoding="utf-8")


def test_seed_has_no_answer_holdout_or_scenario_names():
    forbidden = ["L3-HOLDOUT", "I-03", "expected_category", "scenario", "holdout"]
    for path, text in seed_text_files():
        for word in forbidden:
            assert word not in text, f"{path}: {word}"
        assert not re.search(r"\bS1b?\b", text), path
    defects = (SEED / "app" / "defects.py").read_text(encoding="utf-8")
    assert 'row["inspector_id"]' in defects  # 버그 base: 직접 접근
    # 검사자 필드를 안전하게 읽는 수정 코드가 없어야 한다
    assert not re.search(r"row\.get\(|\.get\(\s*[\"']inspector_id[\"']|in row", defects)
    for path in (SEED / "app").glob("*.py"):
        assert "미지정" not in path.read_text(encoding="utf-8"), path
    assert list((SEED / "tests" / "repro").iterdir()) == [SEED / "tests" / "repro" / ".gitkeep"]


def test_mes_recipe_copies_only_app_and_pins_dependencies():
    recipe = (REPO_ROOT / "linemedic" / "runner" / "mes.Dockerfile").read_text(encoding="utf-8")
    copies = [line for line in recipe.splitlines() if line.startswith("COPY")]
    assert copies == ["COPY app/ /srv/app/"]
    assert "requirements" not in "\n".join(copies)
    installed = re.findall(r"^\s+([A-Za-z0-9_.-]+)==[0-9]", recipe, re.M)
    assert {"fastapi", "uvicorn", "starlette", "pydantic"} <= set(installed)
    assert "USER 10001" in recipe


# ── 시드 git 이력 ─────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git 없음")
def test_seed_repo_history_is_deterministic(tmp_path):
    first = seed_demo_repo.build_seed_repo(tmp_path / "one")
    second = seed_demo_repo.build_seed_repo(tmp_path / "two")
    assert first["tree"] == second["tree"]
    assert first["commit"] == second["commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", first["commit"])
    files = subprocess.run(
        ["git", "ls-files"], cwd=tmp_path / "one", capture_output=True, text=True, check=True
    ).stdout.split()
    assert "app/defects.py" in files and "tests/repro/.gitkeep" in files
    assert not any("__pycache__" in f for f in files)


def test_seed_repo_refuses_non_empty_output(tmp_path):
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(seed_demo_repo.SeedError):
        seed_demo_repo.build_seed_repo(tmp_path)


# ── S1 주입 명령 (Docker 없이) ─────────────────────────────────


class FakeDocker:
    def __init__(self, health_after: int = 1):
        self.calls: list[list[str]] = []
        self.health_checks = 0
        self.health_after = health_after

    def __call__(self, argv: list[str]) -> scenarios.CommandResult:
        self.calls.append(argv)
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return scenarios.CommandResult(0, "sha256:" + "c" * 64 + "\n", "")
        if argv[:3] == ["docker", "network", "create"]:
            return scenarios.CommandResult(0, "net-id\n", "")
        if argv[:2] == ["docker", "run"]:
            return scenarios.CommandResult(0, "container-id\n", "")
        if argv[:2] == ["docker", "exec"]:
            path = argv[-1]
            if path == "/healthz":
                self.health_checks += 1
                return scenarios.CommandResult(
                    0, "200\n" if self.health_checks >= self.health_after else "0\n", ""
                )
            return scenarios.CommandResult(0, "500\n" if "118" in path else "200\n", "")
        return scenarios.CommandResult(0, "", "")


def test_inject_s1_runs_isolated_container_and_sends_requests(tmp_path):
    fake = FakeDocker(health_after=2)
    result = scenarios.inject_s1(
        RUN_ID, runs_dir=tmp_path, run=fake, clock=FakeClock(), sleep=lambda s: None
    )
    network_create = next(c for c in fake.calls if c[:3] == ["docker", "network", "create"])
    assert "--internal" in network_create
    run = next(c for c in fake.calls if c[:2] == ["docker", "run"])
    for flag in (
        "--read-only",
        "--cap-drop",
        "--security-opt",
        "--user",
        "--pids-limit",
        "--memory",
    ):
        assert flag in run
    assert f"linemedic.run_id={RUN_ID}" in run
    assert run[run.index("--network") + 1] == f"linemedic-net-{RUN_ID}"
    volume = run[run.index("--volume") + 1]
    assert volume.endswith(":/data:ro")
    assert "-p" not in run and "--publish" not in run
    assert [r["status"] for r in result["requests"]] == [500, 500, 500, 200]
    assert [r["lot_id"] for r in result["requests"]] == ["L3-0927-118"] * 3 + ["L3-0927-101"]
    lots = sorted(p.name for p in (tmp_path / RUN_ID / "mes-data" / "lots").iterdir())
    assert lots == ["L3-0927-101.json", "L3-0927-118.json"]  # holdout은 넣지 않는다


def test_inject_s1_mounts_absolute_data_dir_for_relative_runs_dir(tmp_path, monkeypatch):
    """기본 `RUNS_DIR=runs`(상대 경로)도 host 경로로 mount한다. 상대 경로는 named volume이 된다."""
    monkeypatch.chdir(tmp_path)
    fake = FakeDocker()
    scenarios.inject_s1(
        RUN_ID, runs_dir=Path("runs"), run=fake, clock=FakeClock(), sleep=lambda s: None
    )
    run = next(c for c in fake.calls if c[:2] == ["docker", "run"])
    data_dir = (tmp_path / "runs" / RUN_ID / "mes-data").resolve()
    assert run[run.index("--volume") + 1] == f"{data_dir}:/data:ro"


def test_inject_s1_rejects_bad_run_id_and_missing_image(tmp_path):
    with pytest.raises(scenarios.ScenarioError):
        scenarios.inject_s1("r-2026/09/27", runs_dir=tmp_path, run=FakeDocker())

    def no_image(argv):
        return scenarios.CommandResult(1, "", "No such image")

    with pytest.raises(scenarios.ScenarioError, match="make mes-image"):
        scenarios.inject_s1(RUN_ID, runs_dir=tmp_path, run=no_image)


def test_stop_s1_targets_exact_names_only():
    fake = FakeDocker()
    scenarios.stop_s1(RUN_ID, run=fake)
    assert fake.calls == [
        ["docker", "rm", "--force", f"linemedic-mes-{RUN_ID}"],
        ["docker", "network", "rm", f"linemedic-net-{RUN_ID}"],
    ]
