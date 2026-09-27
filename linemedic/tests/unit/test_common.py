"""B00 수용 기준 테스트: canonical JSON, ID, Clock, config 로더, doctor, host manifest."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from linemedic.common.canonical_json import (
    MAX_JSON_BYTES,
    StrictJSONError,
    canonical_dumps,
    loads_strict,
    sha256_hex,
)
from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.common.config import (
    MAX_CONFIG_BYTES,
    SECRET_ENV_NAMES,
    ConfigError,
    load_config,
    load_settings,
    read_env_file,
)
from linemedic.common.ids import ENTITY_PREFIXES, is_valid_run_id, new_id, new_run_id
from linemedic.integrations.github import FakeGitHub
from linemedic.scripts import doctor, host_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "config" / "linemedic.toml"


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "linemedic.toml"
    path.write_text(text, encoding="utf-8")
    return path


def default_text() -> str:
    return DEFAULT_CONFIG.read_text(encoding="utf-8")


# ── canonical JSON ─────────────────────────────────────────────


def test_loads_strict_rejects_duplicate_keys():
    with pytest.raises(StrictJSONError):
        loads_strict(b'{"a":1,"a":2}')
    with pytest.raises(StrictJSONError):
        loads_strict(b'{"outer":{"x":1,"x":1}}')


def test_loads_strict_enforces_128_kib_limit():
    assert MAX_JSON_BYTES == 128 * 1024
    at_limit = b'"' + b"a" * (MAX_JSON_BYTES - 2) + b'"'
    assert len(at_limit) == MAX_JSON_BYTES
    assert loads_strict(at_limit) == "a" * (MAX_JSON_BYTES - 2)
    over = b'"' + b"a" * (MAX_JSON_BYTES - 1) + b'"'
    with pytest.raises(StrictJSONError):
        loads_strict(over)


@pytest.mark.parametrize("raw", [b'{"a": NaN}', b"Infinity", b"[-Infinity]"])
def test_loads_strict_rejects_non_finite_numbers(raw):
    with pytest.raises(StrictJSONError):
        loads_strict(raw)


def test_loads_strict_rejects_invalid_utf8_and_non_bytes():
    with pytest.raises(StrictJSONError):
        loads_strict(b'{"a":"\xff"}')
    with pytest.raises(TypeError):
        loads_strict('{"a":1}')  # type: ignore[arg-type]


def test_canonical_dumps_ignores_key_order_and_whitespace():
    a = loads_strict('{"b": 1,  "a": {"y": [1, 2], "x": "한"}}'.encode())
    b = loads_strict('{"a":{"x":"한","y":[1,2]},"b":1}'.encode())
    assert canonical_dumps(a) == canonical_dumps(b) == '{"a":{"x":"한","y":[1,2]},"b":1}'


def test_canonical_dumps_rejects_nan():
    with pytest.raises(ValueError):
        canonical_dumps({"a": float("nan")})


def test_sha256_hex_is_stable():
    obj = {"z": [3, 2, 1], "a": {"k": "값"}}
    expected = hashlib.sha256(canonical_dumps(obj).encode("utf-8")).hexdigest()
    assert sha256_hex(obj) == expected
    assert sha256_hex(json.loads(json.dumps(obj))) == expected


# ── ID ─────────────────────────────────────────────────────────


def test_new_run_id_format(fake_clock):
    run_id = new_run_id(fake_clock)
    assert re.fullmatch(r"r-\d{8}-\d{6}-[0-9a-f]{4}", run_id)
    assert "/" not in run_id
    assert run_id.startswith("r-20260927-000000-")
    assert is_valid_run_id(run_id)
    assert not is_valid_run_id("r-2026/09/27")


def test_new_id_format_and_prefix_validation():
    assert re.fullmatch(r"INC-[0-9A-F]{12}", new_id("INC"))
    for prefix in ENTITY_PREFIXES:
        assert re.fullmatch(rf"{prefix}-[0-9A-F]{{12}}", new_id(prefix))
    with pytest.raises(ValueError):
        new_id("inc")
    with pytest.raises(ValueError):
        new_id("XYZ")


# ── Clock ──────────────────────────────────────────────────────


def test_fake_clock_advance_moves_both_clocks_exactly(fake_clock):
    mono_before = fake_clock.monotonic()
    now_before = fake_clock.utc_now()
    fake_clock.advance(10)
    assert fake_clock.monotonic() == mono_before + 10
    assert fake_clock.utc_now() == now_before + timedelta(seconds=10)
    fake_clock.sleep(5)
    assert fake_clock.monotonic() == mono_before + 15


def test_to_rfc3339_format():
    assert to_rfc3339(datetime(2026, 9, 27, 1, 2, 3, 4567, tzinfo=UTC)) == (
        "2026-09-27T01:02:03.004567Z"
    )
    kst = timezone(timedelta(hours=9))
    assert to_rfc3339(datetime(2026, 9, 27, 10, 0, 0, tzinfo=kst)) == "2026-09-27T01:00:00.000000Z"
    with pytest.raises(ValueError):
        to_rfc3339(datetime(2026, 9, 27, 1, 2, 3))


def test_system_clock_is_utc_aware():
    now = SystemClock().utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


# ── config ─────────────────────────────────────────────────────


def test_default_config_loads_with_omitted_optional_keys_as_none():
    config = load_config(DEFAULT_CONFIG)
    assert config.agent.deadline_seconds == 240
    assert config.patch.allowed_app_file == "app/defects.py"
    assert config.repository.id is None
    assert config.repository.full_name is None
    assert config.agent.model_id is None
    assert config.memory.snapshot_path is None
    assert config.issue_intake.auto_start.author_ids is None


def test_config_hash_ignores_secret_env_values():
    one = load_settings(DEFAULT_CONFIG, env={"NVIDIA_API_KEY": "secret-value-one"})
    two = load_settings(DEFAULT_CONFIG, env={"NVIDIA_API_KEY": "secret-value-two"})
    assert one.config_hash() == two.config_hash()
    assert "secret-value-one" not in repr(one)
    assert "secret-value-one" not in repr(one.secrets)
    assert "secret-value-one" not in canonical_dumps(one.config.model_dump(mode="json"))
    assert one.secrets.get("NVIDIA_API_KEY") == "secret-value-one"


def test_config_hash_changes_with_deadline(tmp_path):
    base = load_settings(DEFAULT_CONFIG, env={})
    changed_path = write_config(
        tmp_path, default_text().replace("deadline_seconds = 240", "deadline_seconds = 241")
    )
    changed = load_settings(changed_path, env={})
    assert changed.config.agent.deadline_seconds == 241
    assert base.config_hash() != changed.config_hash()


def test_env_overrides_merge_into_config_and_hash():
    base = load_settings(DEFAULT_CONFIG, env={})
    merged = load_settings(
        DEFAULT_CONFIG,
        env={
            "GITHUB_REPOSITORY_ID": "123456",
            "GITHUB_REPOSITORY": "demo-team/l3-mes-api",
            "ISSUE_TRUSTED_AUTHOR_IDS": "200001, 200002",
            "ISSUE_INTAKE_ENABLED": "true",
            "AGENT_MODE": "local",
            "DEMO_HOST_ID": "host-a",
        },
    )
    assert merged.config.repository.id == 123456
    assert merged.config.repository.full_name == "demo-team/l3-mes-api"
    assert merged.config.issue_intake.auto_start.author_ids == [200001, 200002]
    assert merged.config.issue_intake.enabled is True
    assert merged.config.agent.mode == "local"
    assert merged.runtime.demo_host_id == "host-a"
    assert merged.config_hash() != base.config_hash()


def test_host_identity_env_does_not_change_config_hash():
    base = load_settings(DEFAULT_CONFIG, env={})
    other = load_settings(DEFAULT_CONFIG, env={"DEMO_HOST_ID": "host-b", "RUNS_DIR": "/tmp/r"})
    assert base.config_hash() == other.config_hash()


def test_empty_env_value_is_treated_as_unset():
    settings = load_settings(DEFAULT_CONFIG, env={"GITHUB_REPOSITORY_ID": "", "AGENT_MODE": ""})
    assert settings.config.repository.id is None
    assert settings.config.agent.mode is None


@pytest.mark.parametrize(
    "env",
    [
        {"ISSUE_POLL_SECONDS": "abc"},
        {"ISSUE_INTAKE_ENABLED": "yes"},
        {"GITHUB_REPOSITORY_ID": "12a"},
        {"ISSUE_TRUSTED_AUTHOR_IDS": "1,,2"},
        {"AGENT_MODE": "cloud"},
        {"ROUTING_SCOPE": "eval:../x"},
    ],
)
def test_invalid_env_values_are_rejected(env):
    with pytest.raises(ConfigError):
        load_settings(DEFAULT_CONFIG, env=env)


def test_config_rejects_unknown_keys(tmp_path):
    unknown_section = write_config(tmp_path, default_text() + "\n[bogus]\nx = 1\n")
    with pytest.raises(ConfigError):
        load_config(unknown_section)
    unknown_field = write_config(
        tmp_path,
        default_text().replace("max_concurrent = 1", "max_concurrent = 1\nsurprise = true"),
    )
    with pytest.raises(ConfigError):
        load_config(unknown_field)


def test_config_rejects_wrong_types(tmp_path):
    path = write_config(
        tmp_path, default_text().replace("deadline_seconds = 240", 'deadline_seconds = "240"')
    )
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("literal", ["2026-09-27T00:00:00Z", "2026-09-27", "07:32:00"])
def test_config_rejects_toml_date_time_types(tmp_path, literal):
    path = write_config(
        tmp_path, default_text().replace('routing_scope = "live"', f"routing_scope = {literal}")
    )
    with pytest.raises(ConfigError, match="date/time"):
        load_config(path)


def test_config_rejects_oversize_file(tmp_path):
    padding = "# " + "x" * 200 + "\n"
    text = default_text() + padding * (MAX_CONFIG_BYTES // len(padding) + 1)
    path = write_config(tmp_path, text)
    with pytest.raises(ConfigError, match="size"):
        load_config(path)


def test_secrets_repr_shows_names_only():
    settings = load_settings(DEFAULT_CONFIG, env={"CONTROL_OPERATOR_TOKEN": "tok-abc-123"})
    text = repr(settings.secrets) + str(settings.secrets)
    assert "tok-abc-123" not in text
    assert "CONTROL_OPERATOR_TOKEN" in text
    assert set(SECRET_ENV_NAMES) >= {"NVIDIA_API_KEY", "CONTROL_OPERATOR_TOKEN"}


def test_read_env_file_parses_names_and_ignores_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\nNVIDIA_MODEL_ID=nvidia/model\nNVIDIA_API_KEY=  # secret\n"
        'QUOTED="a b"\n\nBROKEN LINE\n',
        encoding="utf-8",
    )
    values = read_env_file(env_path)
    assert values["NVIDIA_MODEL_ID"] == "nvidia/model"
    assert values["NVIDIA_API_KEY"] == ""
    assert values["QUOTED"] == "a b"
    assert "BROKEN LINE" not in values
    assert read_env_file(tmp_path / "missing.env") == {}


# ── doctor ─────────────────────────────────────────────────────


def test_doctor_reports_not_configured_and_exits_1_without_secret_values(capsys):
    secret = "nvapi-SHOULD-NOT-PRINT-0001"
    ctx = doctor.DoctorContext(config_path=DEFAULT_CONFIG, env={"NVIDIA_API_KEY": secret})
    results = doctor.run_checks(ctx)
    by_name = {r.name: r for r in results}
    assert by_name["python"].status == "OK"
    assert by_name["config"].status == "OK"
    assert by_name["env"].status == "NOT_CONFIGURED"
    assert "NVIDIA_MODEL_ID" in by_name["env"].detail
    assert "NVIDIA_API_KEY" not in by_name["env"].detail  # 값이 있는 변수는 누락 목록에 없다
    exit_code = doctor.main(["--config", str(DEFAULT_CONFIG)], env={"NVIDIA_API_KEY": secret})
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "NOT_CONFIGURED" in output
    assert secret not in output
    assert "ready" not in output.lower()


def test_doctor_config_failure_is_fail(tmp_path):
    broken = write_config(tmp_path, "schema_version = 1\n")
    results = doctor.run_checks(doctor.DoctorContext(config_path=broken, env={}))
    config_result = next(r for r in results if r.name == "config")
    assert config_result.status == "FAIL"
    assert config_result.required is True


def test_doctor_all_required_ok_exits_0(tmp_path):
    env = {name: "x" for name in doctor.REQUIRED_ENV}
    env["GITHUB_REPOSITORY_ID"] = "1"
    env["ISSUE_POLL_SECONDS"] = "60"
    env["ISSUE_TRUSTED_AUTHOR_IDS"] = "1"
    env["ISSUE_INTAKE_ENABLED"] = "false"
    env["AGENT_MODE"] = "local"
    env["ROUTING_SCOPE"] = "live"
    env["MEMORY_MODE"] = "cold_start"
    env["CASE_SEARCH_ENGINE"] = "sqlite_fts5"
    env["GITHUB_REPOSITORY"] = "demo-team/l3-mes-api"
    env["BASELINE_COMMIT"] = "a" * 40
    ctx = doctor.DoctorContext(
        config_path=DEFAULT_CONFIG,
        env=env,
        # github 항목이 실제 GitHub를 부르지 않도록 가짜 포트를 준다(W22)
        github_port=lambda rid, name, cred: FakeGitHub(rid, name),
    )
    results = doctor.run_checks(ctx)
    required_bad = [r for r in results if r.required and r.status != "OK"]
    assert required_bad == []
    assert doctor.exit_code(results) == 0


# ── host manifest ──────────────────────────────────────────────


def test_host_manifest_uses_null_for_missing_tools_and_no_env_dump():
    def fake_run(argv):
        if argv[0] == "git":
            return "git version 2.55.0"
        return None

    manifest = host_manifest.collect(
        run=fake_run,
        which=lambda name: "/usr/bin/git" if name == "git" else None,
        env={"DEMO_HOST_ID": "host-a", "NVIDIA_API_KEY": "nvapi-secret"},
    )
    assert manifest["demo_host_id"] == "host-a"
    assert manifest["git"] == "git version 2.55.0"
    assert manifest["docker"] is None
    assert manifest["openshell"] is None
    assert manifest["runtime"]["openclaw"] is None
    assert manifest["python"].startswith("3.")
    assert isinstance(manifest["sqlite"]["fts5"], bool)
    assert "nvapi-secret" not in json.dumps(manifest)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", manifest["collected_at"])


def test_loads_strict_rejects_deeply_nested_json_as_strict_error():
    """아주 깊은 중첩은 RecursionError가 아니라 StrictJSONError로 거부한다(검증에서 발견)."""
    deep = b"[" * 60_000 + b"]" * 60_000
    assert len(deep) <= 131072
    with pytest.raises(StrictJSONError):
        loads_strict(deep)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a": ' + b"1" * 5000 + b"}",  # int 자릿수 상한(4300) 초과 → 표준 json은 ValueError
        b'{"a": 1' + b"0" * 400 + b".5}",  # float로 바꾸면 inf
        b'{"a": 1e999}',
        b'{"a": -1e999}',
    ],
    ids=["huge_int", "huge_float", "exp_inf", "exp_neg_inf"],
)
def test_loads_strict_rejects_numbers_python_cannot_represent_exactly(raw):
    """숫자 변환 오류·무한대가 되는 값도 StrictJSONError로 거부한다(W09 리뷰에서 발견)."""
    with pytest.raises(StrictJSONError):
        loads_strict(raw)


def test_loads_strict_keeps_ordinary_numbers():
    assert loads_strict(b'{"a": 12, "b": -0.5, "c": 1e3, "d": 4300}') == {
        "a": 12,
        "b": -0.5,
        "c": 1000.0,
        "d": 4300,
    }
