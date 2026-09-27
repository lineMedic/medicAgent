"""변경 요청 멱등성 (W06, spec 03 §1·04 §7, docs/03 §8).

범위는 `(principal_scope, method, path, run_id, idempotency_key)`(= `api_requests` PK)다.

- 처음 보는 키 → `NEW`(RECEIVED 행 기록)
- 같은 키·같은 본문 hash·COMPLETED → `REPLAY`(저장된 응답을 그대로 돌려줌, 다시 실행하지 않음)
- 같은 키·다른 본문 hash → `CONFLICT`(409 `IDEMPOTENCY_CONFLICT`)
- 같은 키·같은 본문이지만 RECEIVED/UNKNOWN(처리 중·결과 불명) → `IN_FLIGHT`
  (409 `STATE_CONFLICT`, 재실행 없음)

본문 hash는 schema 검증을 거친 값의 canonical JSON SHA-256이다.
key 순서·공백이 달라도 같은 요청이다. 외부 호출이 있는 요청은 `begin`을 한 트랜잭션에서
커밋하고, 외부 호출 뒤 다른 트랜잭션에서 `complete`한다.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.control_plane.store import StoreError, Tx

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Outcome(StrEnum):
    NEW = "NEW"
    REPLAY = "REPLAY"
    CONFLICT = "CONFLICT"
    IN_FLIGHT = "IN_FLIGHT"


@dataclass(frozen=True)
class Begin:
    outcome: Outcome
    response: dict[str, Any] | None = None  # REPLAY일 때 {"status_code", "body"}


def valid_key(key: str | None) -> bool:
    return key is not None and KEY_RE.fullmatch(key) is not None


def body_sha256(normalized_body: Mapping[str, Any]) -> str:
    return sha256_hex(dict(normalized_body))


def _where() -> str:
    return "principal_scope = ? AND method = ? AND path = ? AND run_id = ? AND idempotency_key = ?"


def begin(
    tx: Tx,
    *,
    principal_scope: str,
    method: str,
    path: str,
    run_id: str,
    key: str,
    body_sha256: str,
) -> Begin:
    if not valid_key(key):
        raise ValueError("Idempotency-Key 형식이 아니다")
    scope = (principal_scope, method, path, run_id, key)
    row = tx.one(
        f"SELECT body_sha256, status, response_json FROM api_requests WHERE {_where()}", scope
    )
    if row is None:
        tx.execute(
            "INSERT INTO api_requests(principal_scope, method, path, run_id, idempotency_key,"
            " body_sha256, status, response_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED', NULL, ?)",
            (*scope, body_sha256, tx.now),
        )
        return Begin(Outcome.NEW)
    if row["body_sha256"] != body_sha256:
        return Begin(Outcome.CONFLICT)
    if row["status"] == "COMPLETED" and row["response_json"] is not None:
        return Begin(Outcome.REPLAY, json.loads(row["response_json"]))
    return Begin(Outcome.IN_FLIGHT)


def complete(
    tx: Tx,
    *,
    principal_scope: str,
    method: str,
    path: str,
    run_id: str,
    key: str,
    status_code: int,
    body: Mapping[str, Any],
) -> None:
    """RECEIVED 요청을 COMPLETED로 바꾸고 재전송 때 돌려줄 응답을 저장한다."""
    response = canonical_dumps({"status_code": status_code, "body": dict(body)})
    cursor = tx.execute(
        f"UPDATE api_requests SET status = 'COMPLETED', response_json = ? WHERE {_where()}"
        " AND status = 'RECEIVED'",
        (response, principal_scope, method, path, run_id, key),
    )
    if cursor.rowcount != 1:
        raise StoreError("완료할 RECEIVED 요청이 없다")


def mark_unknown(tx: Tx) -> int:
    """재시작 때 끝나지 않은 RECEIVED 요청을 UNKNOWN으로 바꾼다. 재전송은 재실행하지 않는다."""
    return tx.execute(
        "UPDATE api_requests SET status = 'UNKNOWN' WHERE status = 'RECEIVED'"
    ).rowcount
