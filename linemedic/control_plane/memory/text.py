"""사례 검색 텍스트·질의 정규화 (W27, spec 17 §4, D54).

- 검색 텍스트: 노트의 구조화 필드(서비스·fingerprint·오류 종류·함수·필드·경로·outcome·검사 코드)와
  정제된 문장을 공백으로 잇는다. 한국어 형태소·동의어를 이해한다고 주장하지 않는다
- 질의: `[0-9A-Za-z_가-힣]+` 토큰을 최대 N개 뽑아 각각 큰따옴표로 감싸 OR로 잇는다. 따옴표·`*`·
  `NEAR`·`OR` 같은 FTS 연산자는 토큰 안에 남지 않거나 문자열로만 쓰인다. SQL에는 파라미터로 넘긴다
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

TOKEN_RE = re.compile(r"[0-9A-Za-z_가-힣]+")
TOKENIZER = "unicode61"
NORMALIZATION_VERSION = "d54-v1"


def query_tokens(text: str, max_tokens: int) -> list[str]:
    """중복 없이 앞에서부터 최대 `max_tokens`개."""
    seen: list[str] = []
    for token in TOKEN_RE.findall(text or ""):
        if token not in seen:
            seen.append(token)
        if len(seen) >= max_tokens:
            break
    return seen


def fts_query(tokens: Iterable[str]) -> str:
    """토큰마다 큰따옴표로 감싼 문자열 검색어를 OR로 잇는다(연산자로 해석되지 않는다)."""
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


SEARCH_FIELDS = (
    "service_id",
    "problem_fingerprint",
    "signature",
    "outcome",
    "phase",
    "origin",
    "symptom",
    "hypothesis",
    "attempted_change",
    "failure_conditions",
    "checks",
    "applicability",
    "summary",
)


def search_text(payload: Mapping[str, Any]) -> str:
    """노트 payload의 검색 대상 필드만 모은다(원문 diff·로그 전체는 넣지 않는다)."""
    parts: list[str] = []
    for key in SEARCH_FIELDS:
        parts.extend(_strings(payload.get(key)))
    return " ".join(" ".join(TOKEN_RE.findall(part)) for part in parts if part)
