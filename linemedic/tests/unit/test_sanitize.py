"""W06 단위 테스트: 비밀 마스킹·멘션 무력화·허용 repo 밖 URL 비활성화·HTML 이스케이프."""

import pytest

from linemedic.common.sanitize import (
    disable_urls,
    escape_html,
    mask_secrets,
    neutralize_mentions,
    redact_json,
    sanitize_text,
)

REPO = "lineMedic/l3-mes-api"
# 비밀 스캐너가 가짜 값을 실제 키로 오인하지 않게 실행 중에 조립한다.
KEY_WORDS = "PRIVATE" + " KEY"
PEM = f"-----BEGIN RSA {KEY_WORDS}-----\nMIIEow\nabc\n-----END RSA {KEY_WORDS}-----"


@pytest.mark.parametrize(
    ("secret", "kind"),
    [
        ("ghp_" + "a" * 36, "github_token"),
        ("gho_" + "b" * 36, "github_token"),
        ("github_pat_" + "C1_" * 10, "github_token"),
        ("nvapi-" + "d" * 40, "nvidia_key"),
        ("sk-" + "e" * 40, "api_key"),
        ("AKIA" + "F" * 16, "aws_key"),
        ("Bearer abcdef.ghijk-lmn", "bearer"),
        ("authorization: bearer " + "z" * 30, "bearer"),
        (PEM, "private_key"),
    ],
)
def test_mask_secrets(secret, kind):
    masked = mask_secrets(f"앞 {secret} 뒤")
    assert secret not in masked
    assert f"[REDACTED:{kind}]" in masked
    assert masked.startswith("앞 ") and masked.endswith(" 뒤")


def test_mask_secrets_keeps_ordinary_text():
    text = "sk- 로 시작하는 짧은 말, skip-list, task-3, AKIA, Bearer 없음"
    assert mask_secrets(text) == text


def test_unterminated_private_key_is_masked_to_end():
    text = f"config:\n-----BEGIN OPENSSH {KEY_WORDS}-----\nb3BlbnNzaC1rZXk\n..."
    assert "b3BlbnNzaC1rZXk" not in mask_secrets(text)


def test_neutralize_mentions_but_not_emails():
    text = "@octocat 확인 (@lineMedic/reviewers), mail ops@example.com, `@code`"
    result = neutralize_mentions(text)
    assert "@octocat" not in result and "@\u200boctocat" in result
    assert "@\u200blineMedic/reviewers" in result
    assert "ops@example.com" in result
    assert "`@code`" in result


def test_disable_urls_outside_registered_repo():
    text = (
        f"PR https://github.com/{REPO}/pull/17 참고. "
        "악성 https://evil.example/x?a=1 와 http://github.com/other/repo, www.evil.example, "
        f"[link](https://github.com/{REPO}.evil/pull/1)"
    )
    result = disable_urls(text, REPO)
    assert f"https://github.com/{REPO}/pull/17" in result
    assert "https://evil.example" not in result and "https[:]//evil.example/x?a=1" in result
    assert "http[:]//github.com/other/repo" in result
    assert "www[.]evil.example" in result
    assert f"https[:]//github.com/{REPO}.evil/pull/1" in result


def test_disable_urls_without_allowed_repo_disables_all():
    assert disable_urls("https://github.com/a/b") == "https[:]//github.com/a/b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("절차는https://evil.example/x", "절차는https[:]//evil.example/x"),
        ("참고www.evil.example", "참고www[.]evil.example"),
        ("받기ftp://evil.example/f", "받기ftp[:]//evil.example/f"),
        ("xhttps://evil.example", "xhttps[:]//evil.example"),
    ],
    ids=["korean_https", "korean_www", "korean_ftp", "ascii_prefix"],
)
def test_disable_urls_catches_urls_glued_to_preceding_text(text, expected):
    """앞 글자에 붙은 URL도 무력화한다(단어 경계가 없어도). W09 리뷰에서 발견."""
    assert disable_urls(text, REPO) == expected


def test_disable_urls_keeps_registered_repo_link_glued_to_korean():
    text = f"PR은https://github.com/{REPO}/pull/17"
    assert disable_urls(text, REPO) == text


def test_disable_urls_is_idempotent():
    once = disable_urls("a https://evil.example b www.evil.example 절차는ftp://x.example")
    assert disable_urls(once) == once


def test_escape_html():
    assert escape_html('<img src=x onerror="alert(1)"> & ok') == (
        '&lt;img src=x onerror="alert(1)"&gt; &amp; ok'
    )


def test_sanitize_text_combines_all():
    token = "ghp_" + "Q" * 36
    text = f"@admin <b>긴급</b> {token} https://evil.example"
    result = sanitize_text(text, REPO)
    assert token not in result and "@admin" not in result
    assert "<b>" not in result and "https://evil.example" not in result


def test_redact_json_walks_nested_values():
    token = "nvapi-" + "R" * 30
    value = {"a": [token, {"b": f"x {token}"}], token: 1, "n": 3, "t": True, "none": None}
    redacted = redact_json(value)
    assert token not in repr(redacted)
    assert redacted["n"] == 3 and redacted["t"] is True and redacted["none"] is None
