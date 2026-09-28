"""W14 에이전트 workspace·규칙: 넣지 않을 자료가 없고, 규칙은 읽기 전용이며,
검사가 실제로 잡는다 (D87).

- prompt·skill·도구 설명에 평가 식별자·기대값 표지·시나리오 ID·token 형태·내부 경로가 없다
- base 사본(repo)도 평가 식별자·`.git`·내부 경로 없이 만들어진다
- 검사기는 일부러 넣은 금지 자료(holdout 로트 ID, `expected_category`, `.git`, symlink, token 형태,
  `runs/` 경로, docker socket, 규칙의 시나리오 ID)를 찾고, 내용 대신 위치·종류만 알린다
"""

import os
import stat

import pytest

from linemedic.agent import rules
from linemedic.control_plane.broker.candidate import prepare_workspace
from linemedic.control_plane.redaction import eval_identifiers
from linemedic.tests.helpers.pr_world import build_seed_mirror

TERMS = tuple(sorted(eval_identifiers()))
TOKEN_SHAPE = "ghp_" + "Z" * 36


def test_rule_sources_carry_no_forbidden_material(tmp_path):
    installed = tmp_path / "agent_rules"
    rules.install_rules(installed)
    assert rules.forbidden_findings(installed, terms=TERMS, rules=True) == []
    text = "".join(source.read_text(encoding="utf-8") for _, source in rules.RULE_SOURCES)
    for word in ("S1", "S2-lite", "expected_category", "localhost", "127.0.0.1", "Bearer"):
        assert word not in text, word
    assert "미지정" in text  # 업무 규칙은 제품 요구사항으로 줄 수 있다


def test_rules_are_installed_read_only_with_a_stable_hash(tmp_path):
    installed = tmp_path / "agent_rules"
    digest = rules.install_rules(installed)
    assert digest == rules.bundle_sha256() == rules.prompt_sha256(installed)
    for path in [installed, *installed.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o555 if path.is_dir() else 0o444), path
    assert not os.access(installed / "system.md", os.W_OK)
    with pytest.raises(FileExistsError):  # 이미 있는 경로에 덮어쓰지 않는다
        rules.install_rules(installed)


def test_base_workspace_from_the_seed_has_no_forbidden_material(tmp_path):
    mirror, base = build_seed_mirror((tmp_path / "seed").resolve())
    repo = prepare_workspace(mirror=mirror, root=tmp_path / "attempt" / "work", base_sha=base)
    assert (repo / "app" / "defects.py").is_file()
    assert rules.forbidden_findings(repo.parent, terms=TERMS) == []


@pytest.mark.parametrize(
    ("plant", "kind"),
    [
        (lambda root: (root / "a.txt").write_text(f"lot {TERMS[0]}"), "eval_identifier"),
        (
            lambda root: (root / "a.json").write_text('{"expected_category": "code_bug"}'),
            "eval_marker",
        ),
        (lambda root: (root / "a.txt").write_text(f"auth {TOKEN_SHAPE}"), "secret_shape"),
        (lambda root: (root / "a.txt").write_text("Bearer abcdefghijklmnopqrstu"), "secret_shape"),
        (lambda root: (root / "a.txt").write_text("see runs/r-1/verifications"), "internal_path"),
        (lambda root: (root / "a.txt").write_text("mount /var/run/docker.sock"), "docker_socket"),
        (lambda root: (root / ".git").mkdir(), "git_metadata"),
        (lambda root: (root / "link").symlink_to("/etc"), "symlink"),
    ],
)
def test_checker_finds_planted_material_by_location_and_kind(tmp_path, plant, kind):
    root = tmp_path / "work"
    root.mkdir()
    plant(root)
    found = rules.forbidden_findings(root, terms=TERMS)
    assert kind in {f.kind for f in found}
    assert all(TOKEN_SHAPE not in str(f) and TERMS[0] not in str(f) for f in found)  # 내용 없음


def test_scenario_ids_are_checked_only_in_rules(tmp_path):
    root = tmp_path / "rules"
    root.mkdir()
    (root / "skill.md").write_text("S1 시나리오에서는 이렇게", encoding="utf-8")
    assert {f.kind for f in rules.forbidden_findings(root, rules=True)} == {"scenario_id"}
    assert rules.forbidden_findings(root) == []  # 제품 코드의 문자열은 시나리오 검사를 하지 않는다
    (root / "skill.md").write_text("AS1·S10·S8·S1x는 시나리오 ID가 아니다", encoding="utf-8")
    assert rules.forbidden_findings(root, rules=True) == []


def test_symlinked_directory_is_reported_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(TOKEN_SHAPE, encoding="utf-8")
    root = tmp_path / "work"
    root.mkdir()
    (root / "leak").symlink_to(outside, target_is_directory=True)
    found = rules.forbidden_findings(root)
    assert [(f.path, f.kind) for f in found] == [("leak", "symlink")]  # 밖의 내용은 읽지 않는다
