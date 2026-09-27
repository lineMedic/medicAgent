"""memory snapshot manifest (W27, spec 17 §2·§6, docs/05 사례 기억 흐름, D84).

`make memory-snapshot RUN_ID=`: memory_assisted 평가 run을 시작하기 전에 사례 corpus를 고정한다.

- 선택 기준: series마다 cutoff 이전에 기록된 최신 PUBLISHED revision. 대상 run에서 나온 노트,
  cutoff 뒤 노트(미래 revision), 평가 식별자(holdout 입력 로트 ID 등)가 든 노트, 등록 repo 밖
  노트는 넣지 않는다
- manifest에는 note ID·series·revision·content hash·created_at·observed_at·outcome·origin·seed,
  cutoff, scope, 선택 기준, 제외 수, tokenizer·질의 정규화 버전을 남긴다. 노트 본문은 넣지 않는다
- snapshot ID는 manifest 내용의 hash(`MEM-<12 hex>`)다. 같은 이름의 파일을 덮어쓰지 않는다
- 검색은 manifest의 정확한 revision·hash만 쓴다. 뒤에 생긴 revision으로 바꾸지 않는다
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import sha256_hex
from linemedic.common.clock import to_rfc3339
from linemedic.control_plane import audit
from linemedic.control_plane.memory.builder import fts_enabled
from linemedic.control_plane.memory.text import NORMALIZATION_VERSION, TOKENIZER
from linemedic.control_plane.state import Actor
from linemedic.control_plane.store import Tx

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_DIR = REPO_ROOT / "linemedic" / "eval" / "snapshots"
SNAPSHOT_SCHEMA = "linemedic.memory-snapshot.v1"
SELECTION_RULE = "series_latest_published_before_cutoff"
SNAPSHOT_ID_RE = re.compile(r"^MEM-[0-9A-F]{12}$")
NOTE_FIELDS = (
    "note_id",
    "series_id",
    "revision",
    "content_sha256",
    "repository_id",
    "service",
    "outcome",
    "origin",
    "seed",
    "observed_at",
    "created_at",
)


class SnapshotError(ValueError):
    """manifest를 만들거나 읽을 수 없음."""


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    manifest: dict[str, Any]
    path: Path | None = None

    @property
    def members(self) -> dict[str, str]:
        """note ID → content hash (이 hash와 같은 revision만 검색 대상)."""
        return {note["note_id"]: note["content_sha256"] for note in self.manifest["notes"]}


def _snapshot_id(body: dict[str, Any]) -> str:
    return "MEM-" + sha256_hex(body)[:12].upper()


def normalize_cutoff(value: str) -> str:
    """cutoff를 DB 시각과 같은 `...ffffffZ` 형식으로 바꾼다(문자열 비교가 시각 순서와 같게)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SnapshotError("cutoff는 RFC3339 시각이어야 한다") from None
    if parsed.tzinfo is None:
        raise SnapshotError("cutoff에 시간대(Z 등)가 없다")
    return to_rfc3339(parsed)


def build_snapshot(
    tx: Tx,
    *,
    run_id: str,
    cutoff: str | None = None,
    repository_id: int | None = None,
    terms: Iterable[str] = (),
) -> Snapshot:
    """대상 run을 위한 manifest를 만든다(파일은 `write_snapshot`이 쓴다)."""
    cutoff = normalize_cutoff(cutoff) if cutoff else tx.now
    terms = tuple(terms)
    excluded = dict.fromkeys(
        ("not_published", "target_run", "after_cutoff", "other_repository", "eval_identifier"), 0
    )
    latest: dict[str, Any] = {}
    for row in tx.all("SELECT * FROM case_notes ORDER BY series_id, revision"):
        if row["publish_status"] != "PUBLISHED":
            reason = "not_published"
        elif row["source_run_id"] == run_id:
            reason = "target_run"  # 같은 run의 결과를 같은 run의 memory로 되먹이지 않는다
        elif row["created_at"] > cutoff:
            reason = "after_cutoff"
        elif repository_id is not None and row["repository_id"] != repository_id:
            reason = "other_repository"
        elif any(term in row["payload_json"] for term in terms):
            reason = "eval_identifier"
        else:
            reason = None
        if reason is not None:
            excluded[reason] += 1
            continue
        latest[row["series_id"]] = row  # revision 오름차순이라 마지막이 series의 최신이다
    notes = []
    for series_id in sorted(latest):
        row = latest[series_id]
        payload = json.loads(row["payload_json"])
        values = {
            "note_id": row["id"],
            "series_id": series_id,
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
            "repository_id": row["repository_id"],
            "service": row["service"],
            "outcome": row["outcome"],
            "origin": row["origin"],
            "seed": bool(payload.get("seed")),
            "observed_at": row["observed_at"],
            "created_at": row["created_at"],
        }
        notes.append({key: values[key] for key in NOTE_FIELDS})
    body = {
        "schema_version": SNAPSHOT_SCHEMA,
        "target_run_id": run_id,
        "created_at": tx.now,
        "cutoff": cutoff,
        "scope": {"repository_id": repository_id},
        "selection": {
            "rule": SELECTION_RULE,
            "publish_status": "PUBLISHED",
            "created_at_lte_cutoff": True,
            "exclude_source_run_ids": [run_id],
            "exclude_eval_identifiers": True,
        },
        "search": {
            "engine_at_creation": "sqlite_fts5" if fts_enabled(tx) else "keyword_fallback",
            "tokenizer": TOKENIZER,
            "normalization_version": NORMALIZATION_VERSION,
        },
        "notes": notes,
        "counts": {
            "notes": len(notes),
            "seed": sum(1 for note in notes if note["seed"]),
            "excluded": excluded,
        },
    }
    snapshot_id = _snapshot_id(body)
    return Snapshot(snapshot_id, {"snapshot_id": snapshot_id, **body})


def write_snapshot(snapshot: Snapshot, directory: Path = SNAPSHOT_DIR) -> Path:
    """`<directory>/<snapshot_id>.json`에 쓴다. 같은 이름이 있으면 내용이 같을 때만 그대로 둔다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot.snapshot_id}.json"
    text = json.dumps(snapshot.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:  # 덮어쓰지 않는다
            handle.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise SnapshotError(f"같은 snapshot ID의 다른 manifest가 있다: {path.name}") from None
    return path


def load_snapshot(path: Path) -> Snapshot:
    """manifest를 읽고 내용 hash가 ID와 같은지 확인한다(바뀐 manifest는 쓰지 않는다)."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"snapshot manifest를 읽을 수 없다: {type(exc).__name__}") from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise SnapshotError("snapshot manifest 형식이 아니다")
    snapshot_id = manifest.get("snapshot_id")
    body = {key: value for key, value in manifest.items() if key != "snapshot_id"}
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise SnapshotError("snapshot ID 형식이 아니다")
    if _snapshot_id(body) != snapshot_id:
        raise SnapshotError("snapshot manifest 내용이 ID와 다르다(만든 뒤 바뀌었다)")
    notes = manifest.get("notes")
    if not isinstance(notes, list) or any(
        not isinstance(note, dict) or set(note) != set(NOTE_FIELDS) for note in notes
    ):
        raise SnapshotError("snapshot note 목록 형식이 아니다")
    return Snapshot(snapshot_id, manifest, path)


def record_snapshot(tx: Tx, snapshot: Snapshot, path: Path) -> None:
    """대상 run의 감사 기록에 snapshot ID·파일 hash·note 수를 남긴다."""
    audit.append(
        tx,
        snapshot.manifest["target_run_id"],
        None,
        Actor.OPERATOR,
        "MEMORY_SNAPSHOT_CREATED",
        {
            "snapshot_id": snapshot.snapshot_id,
            "file": path.name,
            "manifest_sha256": sha256_hex(snapshot.manifest),
            "cutoff": snapshot.manifest["cutoff"],
            "notes": snapshot.manifest["counts"]["notes"],
        },
    )
