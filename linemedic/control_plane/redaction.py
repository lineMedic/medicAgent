"""관찰 자료 정제 (W07, INV-09, spec 07 §9).

로그 줄·증거·도구 응답에 들어가기 전에 적용한다.
- 비밀 형태 마스킹(`common.sanitize.mask_secrets`)
- 평가 전용 식별자 가림: `linemedic/eval/*.json`의 입력 로트 ID(holdout 등)를
  `[REDACTED:eval]`로 바꾼다.
  runtime 에이전트가 평가 입력을 알아채 특별 처리하지 못하게 한다
- 문자열 길이 상한

관찰 자료는 비신뢰 데이터다. 안의 지시문·Issue 번호·URL을 해석하지 않는다.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from linemedic.common.sanitize import mask_secrets

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "linemedic" / "eval"
EVAL_MARK = "[REDACTED:eval]"
TRUNCATED_MARK = "…[truncated]"


def eval_identifiers(eval_dir: Path = EVAL_DIR) -> frozenset[str]:
    """평가 fixture의 입력 로트 ID 목록. 기대값은 읽기만 하고 돌려주지 않는다."""
    terms = set()
    for path in sorted(eval_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        lot_id = data.get("input", {}).get("lot_id") if isinstance(data, dict) else None
        if isinstance(lot_id, str) and lot_id:
            terms.add(lot_id)
    return frozenset(terms)


def redact_terms(text: str, terms: Iterable[str]) -> str:
    for term in sorted(terms, key=len, reverse=True):
        text = text.replace(term, EVAL_MARK)
    return text


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - len(TRUNCATED_MARK))] + TRUNCATED_MARK


def clean_text(text: str, terms: Iterable[str] = (), max_chars: int = 8192) -> str:
    return truncate(redact_terms(mask_secrets(text), terms), max_chars)


def clean_value(value: Any, terms: Iterable[str] = (), max_chars: int = 2048) -> Any:
    """JSON 값 안의 모든 문자열(key 포함)을 정제한다."""
    terms = tuple(terms)
    if isinstance(value, str):
        return clean_text(value, terms, max_chars)
    if isinstance(value, dict):
        return {
            clean_text(str(key), terms, 128): clean_value(item, terms, max_chars)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [clean_value(item, terms, max_chars) for item in value]
    return value
