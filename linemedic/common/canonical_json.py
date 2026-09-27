"""엄격한 JSON 파서와 canonical 직렬화 (D44).

- `loads_strict`: 크기 상한 → UTF-8 → 중복 key·NaN·Infinity 거부
- `canonical_dumps`: 키 정렬, 구분자 `,` `:`, `ensure_ascii=False`
- `sha256_hex`: canonical 문자열의 SHA-256
"""

import hashlib
import json
from typing import Any

MAX_JSON_BYTES = 128 * 1024


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


def loads_strict(data: bytes, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if not isinstance(data, bytes | bytearray):
        raise TypeError("loads_strict expects bytes")
    if len(data) > max_bytes:
        raise StrictJSONError(f"JSON body exceeds {max_bytes} bytes")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJSONError("JSON body is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except StrictJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"invalid JSON: {exc.msg}") from exc
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
