"""run ID와 엔티티 ID 발급 (D50).

- run_id: `r-YYYYMMDD-HHMMSS-xxxx` (소문자·숫자·하이픈만, `/` 없음).
  `baseline/<run_id>` 보호 패턴과 맞추기 위해서다.
- 엔티티 ID: `접두사-` + 대문자 16진 12자. 접두사는 서버가 정한 목록만 허용한다.
"""

import re
import secrets

from linemedic.common.clock import Clock

ENTITY_PREFIXES: tuple[str, ...] = (
    "INC",
    "WORK",
    "ATT",
    "PROP",
    "EXE",
    "EV",
    "NOT",
    "VER",
    "CASE",
    "RET",
    "MEM",
    "REQ",
)

_RUN_ID_RE = re.compile(r"^r-\d{8}-\d{6}-[0-9a-f]{4}$")
_ENTITY_ID_RE = re.compile(r"^(?P<prefix>[A-Z]+)-[0-9A-F]{12}$")


def new_run_id(clock: Clock) -> str:
    stamp = clock.utc_now().strftime("%Y%m%d-%H%M%S")
    return f"r-{stamp}-{secrets.token_hex(2)}"


def new_id(prefix: str) -> str:
    if prefix not in ENTITY_PREFIXES:
        raise ValueError(f"unknown entity ID prefix: {prefix!r}")
    return f"{prefix}-{secrets.token_hex(6).upper()}"


def is_valid_run_id(value: str) -> bool:
    return bool(_RUN_ID_RE.fullmatch(value))


def is_valid_entity_id(value: str, prefix: str | None = None) -> bool:
    match = _ENTITY_ID_RE.fullmatch(value)
    if not match or match.group("prefix") not in ENTITY_PREFIXES:
        return False
    return prefix is None or match.group("prefix") == prefix
