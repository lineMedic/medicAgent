"""W15 sandbox port: 필수 보호 확인은 host가 정하고, 정책 hash·effective policy를 남기며,
설정이 없으면 준비하지 않는다 (D88).

- `sandbox_verified`는 필수 보호가 모두 PASS이고 고정한 정책 파일이 있을 때만 true다
- 정책 파일 묶음 hash는 파일이 없으면 None이다(G5 전). symlink는 받지 않는다
- effective policy는 내용 hash 이름으로 한 번만 쓰고, 비밀 형태는 가린 뒤 그 내용의 hash를 쓴다
"""

import pytest

from linemedic.integrations import sandbox
from linemedic.integrations.sandbox import (
    REQUIRED_PROTECTIONS,
    FakeSandbox,
    SandboxUnavailable,
    UnconfiguredSandbox,
    policy_dir_sha256,
    save_effective_policy,
    verification,
)

ALL_PASS = {name: "PASS" for name in REQUIRED_PROTECTIONS}


def test_verified_only_when_every_required_protection_passed_and_policy_is_pinned():
    assert verification(ALL_PASS, policy_sha256="a" * 64) == (True, [])
    assert verification(ALL_PASS, policy_sha256=None) == (False, ["policy_file"])
    missing = {k: v for k, v in ALL_PASS.items() if k != "ops_api_denied"}
    assert verification(missing, policy_sha256="a" * 64) == (False, ["ops_api_denied"])
    weak = {**ALL_PASS, "non_root": "NOT_RUN", "egress_allowlist": "OK"}
    verified, unverified = verification(weak, policy_sha256="a" * 64)
    assert not verified and unverified == ["egress_allowlist", "non_root"]  # PASS만 인정한다
    extra = {**ALL_PASS, "something_else": "FAIL"}  # 필수가 아닌 항목은 판정에 쓰지 않는다
    assert verification(extra, policy_sha256="a" * 64) == (True, [])


def test_policy_dir_hash_is_none_without_files_and_follows_content(tmp_path):
    policy = tmp_path / "openshell"
    assert policy_dir_sha256(policy) is None
    policy.mkdir()
    assert policy_dir_sha256(policy) is None
    (policy / "policy.yaml").write_text("a: 1\n", encoding="utf-8")
    first = policy_dir_sha256(policy)
    assert first is not None and first == policy_dir_sha256(policy)
    (policy / "policy.yaml").write_text("a: 2\n", encoding="utf-8")
    assert policy_dir_sha256(policy) != first
    (policy / "link.yaml").symlink_to(policy / "policy.yaml")
    with pytest.raises(ValueError):
        policy_dir_sha256(policy)


def test_repo_has_no_policy_file_before_g5():
    # 설치 버전 schema를 확인하기 전에는 실행 가능한 정책을 두지 않는다(spec 07 §4)
    assert policy_dir_sha256(sandbox.POLICY_DIR) is None


def test_effective_policy_is_stored_once_by_content_hash_with_secrets_masked(tmp_path):
    runs_dir = tmp_path / "runs"
    first = save_effective_policy(runs_dir, "r-20260928-000000-abcd", "network:\n  allow: tools\n")
    again = save_effective_policy(runs_dir, "r-20260928-000000-abcd", "network:\n  allow: tools\n")
    assert first == again
    path = runs_dir / first.ref
    assert path.parent == runs_dir / "r-20260928-000000-abcd" / "sandbox"
    assert path.name.startswith("effective-policy-") and path.read_text(encoding="utf-8")
    assert not first.masked
    path.write_text("tampered\n", encoding="utf-8")  # 남긴 증거는 다시 쓰지 않는다(대조는 hash로)
    assert save_effective_policy(runs_dir, "r-20260928-000000-abcd", "network:\n  allow: tools\n")
    assert path.read_text(encoding="utf-8") == "tampered\n"
    token = "ghp_" + "Q" * 36
    masked = save_effective_policy(runs_dir, "r-20260928-000000-abcd", f"auth: {token}\n")
    stored = (runs_dir / masked.ref).read_text(encoding="utf-8")
    assert masked.masked and token not in stored
    assert masked.sha256 == sandbox.sha256_text(stored)  # 남긴 파일과 hash가 맞는다


def test_unconfigured_sandbox_refuses_to_prepare(tmp_path):
    with pytest.raises(SandboxUnavailable) as caught:
        UnconfiguredSandbox().prepare(
            run_id="r-1", attempt_id="ATT-1", workspace=tmp_path, rules_dir=tmp_path
        )
    assert caught.value.reason == "not_configured"


def test_fake_sandbox_records_prepare_and_close(tmp_path):
    fake = FakeSandbox(checks=ALL_PASS)
    session = fake.prepare(run_id="r-1", attempt_id="ATT-1", workspace=tmp_path, rules_dir=tmp_path)
    assert session.identity.startswith("fake-sbx-") and session.rules_path == "/agent_rules"
    fake.close(session)
    assert [c[0] for c in fake.calls] == ["prepare", "close"]


# ── doctor openshell ───────────────────────────────────────────


def openshell_check(tmp_path, env, *, cli=True, policy=True, port=None):
    from linemedic.scripts import doctor

    policy_dir = tmp_path / "openshell"
    policy_dir.mkdir(parents=True, exist_ok=True)
    if policy:
        (policy_dir / "policy.yaml").write_text("pinned: true\n", encoding="utf-8")
    ctx = doctor.DoctorContext(
        env=env,
        which=lambda name: "/usr/local/bin/openshell" if cli else None,
        run=lambda argv: "openshell 0.0-test" if argv == ["openshell", "--version"] else None,
        sandbox=port,
        sandbox_policy_dir=policy_dir,
    )
    return doctor.check_openshell(ctx)


def test_doctor_openshell_is_not_needed_in_local_mode(tmp_path):
    status, detail = openshell_check(tmp_path, {"AGENT_MODE": "local"}, cli=False, policy=False)
    assert status == "OK" and "local" in detail


def test_doctor_openshell_in_sandbox_mode_needs_cli_policy_and_a_launch(tmp_path):
    sandbox_env = {"AGENT_MODE": "sandbox"}
    assert openshell_check(tmp_path, {}, cli=False)[0] == "NOT_CONFIGURED"  # 모드 미정
    assert openshell_check(tmp_path, sandbox_env, cli=False)[0] == "MISSING"
    assert openshell_check(tmp_path / "a", sandbox_env, policy=False)[0] == "NOT_CONFIGURED"
    status, detail = openshell_check(tmp_path / "b", sandbox_env)  # 기동 구현 없음(G5 전)
    assert status == "NOT_CONFIGURED" and "G5" in detail
    fake = FakeSandbox(checks=ALL_PASS)
    status, detail = openshell_check(tmp_path / "c", sandbox_env, port=fake)
    assert status == "OK" and "openshell 0.0-test" in detail
    assert [c[0] for c in fake.calls] == ["prepare", "close"]  # 시험 sandbox를 만들고 정리했다

    class Broken(FakeSandbox):
        def prepare(self, **kwargs):
            raise SandboxUnavailable("launch_failed")

    status, detail = openshell_check(tmp_path / "d", sandbox_env, port=Broken(checks=ALL_PASS))
    assert status == "FAIL" and "launch_failed" in detail
