"""엄격한 JSON 파서와 canonical 직렬화 (D44).

- `loads_strict`: 크기 상한 → UTF-8 → 중첩 깊이 상한 → 중복 key·NaN·Infinity 거부
  (float로 무한대가 되는 수, 자릿수 상한을 넘는 정수도 거부)
- `canonical_dumps`: 키 정렬, 구분자 `,` `:`, `ensure_ascii=False`
- `sha256_hex`: canonical 문자열의 SHA-256
"""

import hashlib
import json
import math
import re
from typing import Any

MAX_JSON_BYTES = 128 * 1024
# 인터프리터의 재귀 한도에 기대지 않는다. Python 3.14의 json은 아주 깊은 중첩도
# RecursionError 없이 파싱하므로 상한을 직접 검사한다.
MAX_JSON_DEPTH = 64

# 문자열 토큰(이스케이프 포함)과 구조 괄호. 문자열 안의 괄호는 깊이에 세지 않는다.
_DEPTH_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"?|[\[\]{}]', re.DOTALL)


class StrictJSONError(ValueError):
    """엄격한 JSON 규칙을 어긴 입력."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise StrictJSONError(f"non-finite number is not allowed: {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 1e999처럼 float로 바꾸면 무한대가 되는 수
        raise StrictJSONError("non-finite number is not allowed")
    return value


def _check_depth(text: str, max_depth: int) -> None:
    depth = 0
    for match in _DEPTH_TOKEN_RE.finditer(text):
        token = match.group()
        if token in "[{":
            depth += 1
            if depth > max_depth:
                raise StrictJSONError(f"JSON nesting exceeds depth {max_depth}")
        elif token in "]}":
            depth -= 1  # 짝이 맞지 않는 괄호는 json.loads가 거부한다


def loads_strict(data: bytes, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if not isinstance(data, bytes | bytearray):
        raise TypeError("loads_strict expects bytes")
    if len(data) > max_bytes:
        raise StrictJSONError(f"JSON body exceeds {max_bytes} bytes")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJSONError("JSON body is not valid UTF-8") from exc
    _check_depth(text, MAX_JSON_DEPTH)
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except StrictJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"invalid JSON: {exc.msg}") from exc
    except ValueError:  # 정수 자릿수 상한(sys.int_max_str_digits) 초과
        raise StrictJSONError("JSON number is too large") from None
    except RecursionError:
        raise StrictJSONError("JSON nesting is too deep") from None


def canonical_dumps(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_dumps(obj).encode("utf-8")).hexdigest()
