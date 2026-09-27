"""W10 패치 정책: diff 해석·경로·파일 형태·상한·보호 경로·적용 뒤 tree 재확인.

- T-PATCH-01: `tests/regression/*` 수정,
  `Dockerfile`·`pyproject.toml`·`.github/*`·`conftest.py` 변경 → 거부
- T-PATCH-02: `../x`, `/etc/x`, symlink, binary, rename → 거부
- T-PATCH-03: 파일 3개, 101줄 → 거부
- 적용 뒤 tree의 경로·mode·blob이 계획과 다르거나 보호 경로 blob이 바뀌면 거부
"""

import hashlib
from pathlib import Path

import pytest

from linemedic.common.config import ConfigError
from linemedic.control_plane.broker.patch_policy import (
    PatchDenied,
    check_diff,
    check_text,
    glob_match,
    is_safe_path,
    load_policy,
    verify_tree,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "patches"
POLICY = load_policy()
RULES = POLICY.rules
APP = "app/defects.py"
TEST = "tests/repro/test_x.py"


def modify(path=APP, changes=1, mode_line="index 1111111..2222222 100644"):
    minus = "".join(f"-    old_{i} = 1\n" for i in range(changes))
    plus = "".join(f"+    new_{i} = 2\n" for i in range(changes))
    return (
        f"diff --git a/{path} b/{path}\n{mode_line}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{changes + 1} +1,{changes + 1} @@\n def f():\n{minus}{plus}"
    )


def add(path=TEST, lines=2, mode="100644"):
    body = "".join(f"+line_{i} = {i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\nnew file mode {mode}\nindex 0000000..3333333\n"
        f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"
    )


def denied(diff, new_test=TEST):
    with pytest.raises(PatchDenied) as info:
        check_diff(diff, new_test, RULES)
    assert info.value.code == "PATCH_PATH_DENIED"
    return info.value


def rule(diff, new_test=TEST):
    return denied(diff, new_test).rule


# ── 정책 파일 ──────────────────────────────────────────────────


def test_policy_file_is_the_single_source_with_docs07_values():
    assert (RULES.allowed_app_file, RULES.allowed_new_test_glob) == (APP, "tests/repro/test_*.py")
    assert (RULES.max_files, RULES.max_changed_lines) == (2, 100)
    assert (
        "tests/regression/**" in RULES.protected_globs and "**/conftest.py" in RULES.protected_globs
    )
    assert RULES.regression_tests == "tests/regression"
    source = Path(__file__).resolve().parents[2] / "policies" / "broker_policy.toml"
    assert POLICY.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_policy_rejects_unknown_keys_and_unsafe_paths(tmp_path):
    source = (Path(__file__).resolve().parents[2] / "policies" / "broker_policy.toml").read_text()
    extra = tmp_path / "extra.toml"
    extra.write_text(source.replace("max_files = 2", "max_files = 2\nallow_all = true"))
    with pytest.raises(ConfigError):
        load_policy(extra)
    unsafe = tmp_path / "unsafe.toml"
    unsafe.write_text(source.replace('"app/defects.py"', '"../app/defects.py"'))
    with pytest.raises(ConfigError):
        load_policy(unsafe)


# ── 경로·glob ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("tests/repro/test_a.py", "tests/repro/test_*.py", True),
        ("tests/repro/test_a/b.py", "tests/repro/test_*.py", False),  # `*`는 `/`를 넘지 않는다
        ("tests/repro/x_test.py", "tests/repro/test_*.py", False),
        ("conftest.py", "**/conftest.py", True),
        ("tests/repro/conftest.py", "**/conftest.py", True),
        ("tests/repro/conftest.pyc", "**/conftest.py", False),
        ("tests/regression/a/b.py", "tests/regression/**", True),
        ("tests/regressionx/a.py", "tests/regression/**", False),
        ("requirements-dev.txt", "requirements*", True),
        ("app/requirements.txt", "requirements*", False),
        ("setup.py", "setup.?y", True),
    ],
)
def test_glob_is_segment_aware(path, pattern, expected):
    assert glob_match(path, pattern) is expected


@pytest.mark.parametrize(
    ("path", "safe"),
    [
        ("app/defects.py", True),
        ("../x", False),
        ("/etc/x", False),
        ("app/../x", False),
        ("app/./defects.py", False),
        ("app//defects.py", False),
        ("app\\defects.py", False),
        (".git/config", False),
        ("app/.GIT/hooks", False),
        ("app/de fects.py", False),
        ("app/défects.py", False),
        ("a/" * 101 + "x", False),
    ],
)
def test_safe_path_rules(path, safe):
    assert is_safe_path(path) is safe


# ── 통과 ──────────────────────────────────────────────────────


def test_real_fix_patch_passes_with_plan_and_hash():
    diff = (FIXTURES / "fix_missing_inspector.patch").read_text(encoding="utf-8")
    plan = check_diff(diff, "tests/repro/test_missing_inspector.py", RULES)
    assert [(f.path, f.kind) for f in plan.files] == [
        (APP, "modify"),
        ("tests/repro/test_missing_inspector.py", "add"),
    ]
    assert plan.changed_lines == 17
    assert plan.patch_sha256 == hashlib.sha256(diff.encode("utf-8")).hexdigest()
    assert plan.record()["files"][0] == {
        "path": APP,
        "kind": "modify",
        "additions": 1,
        "deletions": 1,
    }


def test_traditional_unified_diff_and_no_newline_marker_are_accepted():
    diff = (
        f"--- a/{APP}\n+++ b/{APP}\n@@ -1,2 +1,2 @@\n def f():\n-    x = 1\n+    x = 2\n"
        "\\ No newline at end of file\n"
        f"--- /dev/null\n+++ b/{TEST}\n@@ -0,0 +1 @@\n+assert True\n"
    )
    plan = check_diff(diff, TEST, RULES)
    assert [(f.path, f.kind) for f in plan.files] == [(APP, "modify"), (TEST, "add")]


def test_empty_context_line_counts_as_context():
    diff = (
        f"diff --git a/{APP} b/{APP}\n--- a/{APP}\n+++ b/{APP}\n"
        "@@ -1,3 +1,3 @@\n def f():\n\n-    x = 1\n+    x = 2\n" + add()
    )
    assert check_diff(diff, TEST, RULES).changed_lines == 4


def test_exactly_100_lines_passes():
    assert check_diff(modify(changes=1) + add(lines=98), TEST, RULES).changed_lines == 100


# ── T-PATCH-01: 보호 경로·기존 테스트 ──────────────────────────


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (modify("tests/regression/test_summary_regression.py"), "protected_path"),
        (add("Dockerfile"), "protected_path"),
        (modify("pyproject.toml"), "protected_path"),
        (add(".github/workflows/ci.yml"), "protected_path"),
        (add("conftest.py"), "protected_path"),
        (add("tests/repro/conftest.py"), "protected_path"),
        (modify("requirements.txt"), "protected_path"),
        (add("pytest.ini"), "protected_path"),
        (modify("tests/repro/.gitkeep"), "existing_test_modified"),
        (modify("tests/repro/test_old.py"), "existing_test_modified"),
        (add("tests/repro/helper.py"), "path_not_allowed"),
        (modify("app/main.py"), "path_not_allowed"),
        (add("app/defects.py"), "app_file_must_be_modified"),
    ],
)
def test_protected_paths_and_existing_tests_are_denied(diff, expected):  # T-PATCH-01
    assert rule(diff + add()) == expected


def test_denial_keeps_only_safe_paths():
    assert denied(modify("pyproject.toml") + add()).detail() == {
        "rule": "protected_path",
        "path": "pyproject.toml",
    }
    assert denied("diff --git a/../x b/../x\n").detail() == {"rule": "invalid_path"}


# ── T-PATCH-02: 경로 우회·파일 형태 ────────────────────────────


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        ("diff --git a/../x b/../x\n", "invalid_path"),
        ("diff --git a//etc/x b//etc/x\n", "invalid_path"),
        (f"--- a/{APP}\n+++ /etc/x\n", "invalid_path"),
        (f"--- {APP}\n+++ b/{APP}\n", "invalid_path"),
        ('diff --git "a/app/de fects.py" "b/app/de fects.py"\n', "invalid_path"),
        ("diff --git a/app\\defects.py b/app\\defects.py\n", "invalid_path"),
        ("diff --git a/.git/config b/.git/config\n", "invalid_path"),
        (add(TEST, mode="120000"), "file_mode_not_allowed"),  # symlink
        (add(TEST, mode="160000"), "file_mode_not_allowed"),  # submodule
        (add(TEST, mode="100755"), "file_mode_not_allowed"),  # 실행 권한
        (modify(mode_line="index 1111111..2222222 100755"), "file_mode_not_allowed"),
        (
            f"diff --git a/{APP} b/{APP}\nold mode 100644\nnew mode 100755\n",
            "mode_change_not_allowed",
        ),
        (
            "diff --git a/app/x.png b/app/x.png\nnew file mode 100644\nindex 0000000..1111111\n"
            "Binary files /dev/null and b/app/x.png differ\n",
            "binary_not_allowed",
        ),
        (
            f"diff --git a/{APP} b/{APP}\nindex 1111111..2222222 100644\nGIT binary patch\n",
            "binary_not_allowed",
        ),
        ("Binary files a/x and b/x differ\n", "binary_not_allowed"),
        (
            f"diff --git a/app/old.py b/{APP}\nsimilarity index 90%\nrename from app/old.py\n"
            f"rename to {APP}\n",
            "rename_not_allowed",
        ),
        (
            f"diff --git a/{APP} b/{APP}\nsimilarity index 90%\n",
            "rename_not_allowed",
        ),
        (f"--- a/app/old.py\n+++ b/{APP}\n@@ -1 +1 @@\n-a\n+b\n", "rename_not_allowed"),
        (  # header의 a/·b/가 다르면 `---`/`+++`가 맞아도 rename이다
            f"diff --git a/app/old.py b/{APP}\n--- a/{APP}\n+++ b/{APP}\n@@ -1 +1 @@\n-a\n+b\n",
            "rename_not_allowed",
        ),
        (f"diff --git a/{APP} b/{APP}\ncopy from app/x.py\ncopy to {APP}\n", "copy_not_allowed"),
        (
            f"diff --git a/{APP} b/{APP}\ndeleted file mode 100644\nindex 1111111..0000000\n",
            "delete_not_allowed",
        ),
        (f"--- a/{APP}\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n", "delete_not_allowed"),
    ],
)
def test_path_tricks_and_file_types_are_denied(diff, expected):  # T-PATCH-02
    assert rule(diff) == expected


@pytest.mark.parametrize(
    "bad",
    ["\x00", "\r", "\u202e", "\u2066", "\u200b", "\ufeff", "\x1b", "\u2028"],
)
def test_control_and_bidi_characters_are_denied(bad):
    assert rule(modify() + add().replace("line_0 = 0", f"line_0 = '{bad}'")) == "control_character"


# ── T-PATCH-03: 상한 ──────────────────────────────────────────


def test_three_files_are_denied():  # T-PATCH-03
    assert rule(modify() + add() + add("tests/repro/test_y.py")) == "too_many_files"


def test_101_changed_lines_are_denied():  # T-PATCH-03
    assert rule(modify(changes=1) + add(lines=99)) == "too_many_lines"


def test_new_test_count_and_declared_path():
    assert rule(add() + add("tests/repro/test_y.py")) == "too_many_new_tests"
    assert rule(modify() + add("tests/repro/test_y.py")) == "new_test_path_mismatch"
    assert rule(modify()) == "new_test_path_mismatch"  # 새 재현 테스트가 없다
    assert rule(modify() + modify()) == "duplicate_file"


# ── 문법 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        ("아래 패치를 적용하세요\n" + modify() + add(), "unexpected_line"),
        (modify().replace("@@ -1,2 +1,2 @@", "@@ -1,3 +1,2 @@") + add(), "hunk_count_mismatch"),
        (modify().replace("@@ -1,2 +1,2 @@", "@@ -1,2 +1,1 @@") + add(), "hunk_count_mismatch"),
        # header가 옛 줄 수를 적게 적었다(삭제 줄이 옛 쪽을 음수로 만든다)
        (modify().replace("@@ -1,2 +1,2 @@", "@@ -1,1 +1,2 @@") + add(), "hunk_count_mismatch"),
        (modify().replace("@@ -1,2 +1,2 @@", "@@ bogus @@") + add(), "malformed_hunk"),
        (add().replace("@@ -0,0 +1,2 @@", "@@ -1,0 +1,2 @@"), "malformed_hunk"),
        (modify().replace(" def f():\n", "?def f():\n") + add(), "hunk_count_mismatch"),
        (f"diff --git a/{APP} b/{APP}\n--- a/{APP}\n+++ b/{APP}\n" + add(), "no_hunks"),
        (
            f"diff --git a/{TEST} b/{TEST}\nnew file mode 100644\nindex 0000000..e69de29\n",
            "no_hunks",
        ),
        (f"--- a/{APP}\n", "malformed_header"),
        (add().replace("new file mode 100644\n", ""), "malformed_header"),
        (f"diff --git a/{APP} b/{APP}\nfoo bar\n", "unexpected_line"),
        (
            modify() + "\\ No newline at end of file\n\\ No newline at end of file\n" + add(),
            "unexpected_line",
        ),
    ],
)
def test_diff_grammar_is_strict(diff, expected):
    assert rule(diff) == expected


def test_no_file_sections_is_empty_diff():
    assert rule("\n\n") == "unexpected_line"
    with pytest.raises(PatchDenied) as info:
        check_diff("", TEST, RULES)
    assert info.value.rule == "empty_diff"


# ── 적용 뒤 tree 재확인 ────────────────────────────────────────

BASE = {
    APP: ("100644", "blob", "a" * 40),
    "app/main.py": ("100644", "blob", "b" * 40),
    "tests/regression/test_summary_regression.py": ("100644", "blob", "c" * 40),
}


def planned():
    return check_diff(modify() + add(), TEST, RULES)


def candidate(**changes):
    tree = {**BASE, APP: ("100644", "blob", "d" * 40), TEST: ("100644", "blob", "e" * 40)}
    for path, entry in changes.items():
        if entry is None:
            tree.pop(path, None)
        else:
            tree[path] = entry
    return tree


def tree_rule(tree):
    with pytest.raises(PatchDenied) as info:
        verify_tree(BASE, tree, planned(), RULES)
    return info.value.rule


def test_tree_matching_the_plan_passes():
    verify_tree(BASE, candidate(), planned(), RULES)


def test_tree_rechecks_paths_modes_and_protected_blobs():
    regression = "tests/regression/test_summary_regression.py"
    assert tree_rule(candidate(**{regression: ("100644", "blob", "f" * 40)})) == "protected_changed"
    assert (
        tree_rule({**candidate(), "app/other.py": ("100644", "blob", "1" * 40)}) == "tree_mismatch"
    )
    assert tree_rule(candidate(**{APP: ("100755", "blob", "d" * 40)})) == "tree_mismatch"
    assert tree_rule(candidate(**{TEST: ("120000", "blob", "e" * 40)})) == "tree_mismatch"
    assert tree_rule(candidate(**{APP: None})) == "tree_mismatch"
    assert (
        tree_rule({**candidate(), "app/main.py": ("100644", "blob", "9" * 40)}) == "tree_mismatch"
    )
    with pytest.raises(PatchDenied) as info:  # 새 파일이라던 경로가 base에 이미 있었다
        verify_tree({**BASE, TEST: ("100644", "blob", "8" * 40)}, candidate(), planned(), RULES)
    assert info.value.rule == "tree_mismatch"
    with pytest.raises(PatchDenied) as info:  # 수정하던 파일이 base에서 실행 파일이었다
        verify_tree({**BASE, APP: ("100755", "blob", "a" * 40)}, candidate(), planned(), RULES)
    assert info.value.rule == "tree_mismatch"


@pytest.mark.parametrize("content", [b"\xff\xfe", b"a\x00b", b"a\r\nb", "x\u202ey".encode()])
def test_changed_files_must_be_plain_utf8_text(content):
    with pytest.raises(PatchDenied) as info:
        check_text(content, APP)
    assert info.value.rule == "not_text"


def test_plain_text_passes():
    check_text("def f():\n\treturn '미지정'\n".encode(), APP)


def test_repository_files_hold_no_literal_invisible_characters():
    """보이지 않는 서식·양방향 문자는 코드·설정·문서에 글자 그대로 두지 않는다(escape로 쓴다).

    패치 정책이 거부하는 문자를 이 저장소도 쓰지 않는다. spec/은 수정하지 않으므로 뺀다.
    """
    points = [*range(0x200B, 0x2010), *range(0x2028, 0x202F), *range(0x2060, 0x2065)]
    points += [*range(0x2066, 0x206A), 0xFEFF, 0xAD]
    invisible = set(map(chr, points))
    root = Path(__file__).resolve().parents[3]
    patterns = ("linemedic/**/*.py", "linemedic/**/*.toml", "config/*.toml", "*.md", "docs/*.md")
    offenders = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            if any(char in invisible for char in text):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
