"""외부로 나가거나 기록되는 텍스트 정제 (W06, spec 07 §9·16 §5, INV-15).

- `mask_secrets`: token·key·private key 형태를 `[REDACTED:<종류>]`로 바꾼다
- `neutralize_mentions`: GitHub `@사용자`·`@조직/팀` 멘션이 알림을 만들지 않게 한다
- `disable_urls`: 허용한 repo 밖 URL을 링크가 되지 않는 형태로 바꾼다
- `escape_html`: `<`·`>`·`&`를 문자로 남긴다
- `sanitize_text`: 위 네 가지를 순서대로 적용한다(알림·PR 본문 등 외부 출력용)
- `redact_json`: JSON 값 안의 모든 문자열에 `mask_secrets`를 적용한다(감사 기록용)

정제는 비밀이 새지 않게 줄이는 보조 장치다. 모든 비밀 형태를 찾는다고 보장하지 않는다.
"""

import html
import re
from typing import Any

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
    ),
    ("pem", re.compile(r"-----BEGIN [A-Z0-9 ]+-----")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("nvidia_key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{16,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
)

# 앞이 단어 문자가 아닌 @만 멘션이다(메일 주소 a@b.com은 그대로 둔다).
_MENTION = re.compile(r"(?<![\w@`])@(?=[A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9_.-]+)?)")
# 단어 경계(\b)를 두지 않는다. 한글 등 앞 글자에 붙은 URL(`절차는https://…`)도 잡는다.
_URL = re.compile(r"(?i)(?:https?|ftp)://[^\s<>()\[\]\"'`]+|www\.[^\s<>()\[\]\"'`]+")
_ZERO_WIDTH_SPACE = "\u200b"


def mask_secrets(text: str) -> str:
    for kind, pattern in _SECRET_PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text


def neutralize_mentions(text: str) -> str:
    return _MENTION.sub("@" + _ZERO_WIDTH_SPACE, text)


def _allowed_prefixes(allowed_repo: str | None) -> tuple[str, ...]:
    if not allowed_repo:
        return ()
    base = f"https://github.com/{allowed_repo}".lower()
    return (base + "/", base + "#", base + "?")


def disable_urls(text: str, allowed_repo: str | None = None) -> str:
    """`owner/name` 등록 repo의 github.com URL만 링크로 남기고 나머지는 `[:]`·`[.]`로 무력화한다."""
    allowed = _allowed_prefixes(allowed_repo)
    exact = allowed[0][:-1] if allowed else None

    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        lowered = url.lower()
        if exact and (lowered == exact or lowered.startswith(allowed)):
            return url
        if "://" in url:
            return url.replace("://", "[:]//", 1)
        return url.replace(".", "[.]", 1)

    return _URL.sub(replace, text)


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def sanitize_text(text: str, allowed_repo: str | None = None) -> str:
    """외부로 나가는 사람용 텍스트. 비밀 → 멘션 → URL → HTML 순서로 정제한다."""
    return escape_html(disable_urls(neutralize_mentions(mask_secrets(text)), allowed_repo))


def redact_json(value: Any) -> Any:
    """dict·list 안의 모든 문자열 값과 key에서 비밀 형태를 가린다."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {mask_secrets(str(key)): redact_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact_json(item) for item in value]
    return value
