"""사건 증거 (W07, spec 04 §1·§6).

evidence는 한 사건(run·incident)에 속한 관찰 기록이다.
저장 전에 정제(비밀 마스킹·평가 전용 식별자 가림·문자열 길이 상한)하고,
조회는 항상 run_id와 incident_id를 함께 건다.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from linemedic.common.canonical_json import canonical_dumps, sha256_hex
from linemedic.common.ids import new_id
from linemedic.control_plane.redaction import clean_value
from linemedic.control_plane.store import Tx

# history_projection(W27): 권한·snapshot을 확인한 과거 사례를 현재 사건이 인용하게 만든 읽기 투영
KINDS = frozenset({"log_error", "equipment_metric", "history_projection"})
MAX_STRING_CHARS = 2048
MAX_PAYLOAD_BYTES = 16384
MAX_LOG_EVIDENCE_PER_INCIDENT = 20  # 도구 응답·제안 근거 상한(20)과 같게 둔다
MAX_EVIDENCE_IDS = 20


def clean_payload(payload: Mapping[str, Any], terms: Iterable[str] = ()) -> dict[str, Any]:
    """정제 뒤에도 16 KiB를 넘으면 문자열을 더 짧게 자르고, 그래도 크면 요약만 남긴다."""
    terms = tuple(terms)
    for limit in (MAX_STRING_CHARS, 256):
        clean = clean_value(dict(payload), terms, limit)
        if len(canonical_dumps(clean).encode("utf-8")) <= MAX_PAYLOAD_BYTES:
            return clean
    return {"truncated": True, "keys": sorted(str(key)[:64] for key in payload)[:50]}


def add_evidence(
    tx: Tx,
    *,
    run_id: str,
    incident_id: str,
    kind: str,
    observed_at: str,
    source_identity: str,
    payload: Mapping[str, Any],
    terms: Iterable[str] = (),
) -> str:
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 evidence 종류: {kind!r}")
    clean = clean_payload(payload, terms)
    evidence_id = new_id("EV")
    tx.execute(
        "INSERT INTO evidence(id, run_id, incident_id, kind, observed_at, source_identity,"
        " payload_json, content_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evidence_id,
            run_id,
            incident_id,
            kind,
            observed_at,
            source_identity[:256],
            canonical_dumps(clean),
            sha256_hex(clean),
        ),
    )
    return evidence_id


def count_kind(tx: Tx, run_id: str, incident_id: str, kind: str) -> int:
    row = tx.one(
        "SELECT COUNT(*) FROM evidence WHERE run_id = ? AND incident_id = ? AND kind = ?",
        (run_id, incident_id, kind),
    )
    return int(row[0])


def evidence_ids(tx: Tx, run_id: str, incident_id: str, limit: int = MAX_EVIDENCE_IDS) -> list[str]:
    rows = tx.all(
        "SELECT id FROM evidence WHERE run_id = ? AND incident_id = ?"
        " ORDER BY observed_at, rowid LIMIT ?",
        (run_id, incident_id, limit),
    )
    return [row["id"] for row in rows]
