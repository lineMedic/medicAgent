"""run 증거 export (W19, spec 04 §9, spec 09 §9, spec 11 §3·§7, D86).

`runs/<run_id>/export/<UTC 시각>/`에 새로 쓴다. 같은 경로가 있으면 실패한다(이전 export·실행 원본을
덮어쓰지 않는다). audit DB가 기준이고 JSONL은 export다.

- `private/`(디렉터리 0700, 파일 0600): 이 run의 DB 행 원본 JSONL. 로컬 제한 경로의 비공개 사본이다
- `shared/`: 공유본. 비밀 형태·평가 식별자를 가리고 로컬 절대 경로를 자리표시로 바꾼다. 알림은 본문
  없이 상태·receipt만, Issue는 제목·상태만 남긴다. `run-record.md`(Issue·시작 댓글·PR·merge SHA·
  image·검증·결과 댓글 연결)와 `unresolved.json`(미해결 외부 실행·알림·작업)을 함께 쓴다
- `export-manifest.json`: 파일별 SHA-256·행 수와 미해결 수. 무결성 확인용이며
  서명·attestation이 아니다
"""

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock
from linemedic.control_plane.redaction import clean_value
from linemedic.control_plane.store import Store, Tx

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_SCHEMA = "linemedic.run-export.v1"
SHARED_MAX_CHARS = 1_000_000  # 공유본은 가리기만 하고 자르지 않는다(diff 등)
RESULT_EVENTS = ("PR_READY", "HANDOFF_DRAFTED", "WORK_BLOCKED", "RECOVERY_VERIFIED",
                 "RECOVERY_NOT_VERIFIED", "WORK_CANCELLED")  # fmt: skip
NO_RECORD = "기록 없음"

# (이름, SQL). 매개변수는 모두 run_id 하나다.
TABLES: tuple[tuple[str, str], ...] = (
    ("demo_runs", "SELECT * FROM demo_runs WHERE id = ?"),
    ("incidents", "SELECT * FROM incidents WHERE run_id = ? ORDER BY first_seen, id"),
    ("work_items", "SELECT * FROM work_items WHERE run_id = ? ORDER BY created_at, id"),
    (
        "github_issues",
        "SELECT g.* FROM github_issues g WHERE EXISTS (SELECT 1 FROM work_items w"
        " WHERE w.run_id = ? AND w.repository_id = g.repository_id"
        " AND w.issue_number = g.issue_number) ORDER BY g.repository_id, g.issue_number",
    ),
    (
        "issue_bindings",
        "SELECT b.* FROM issue_bindings b WHERE b.routing_scope = 'eval:' || ?"
        " ORDER BY b.created_at, b.problem_fingerprint",
    ),
    ("evidence", "SELECT * FROM evidence WHERE run_id = ? ORDER BY observed_at, rowid"),
    ("proposals", "SELECT * FROM proposals WHERE run_id = ? ORDER BY received_at, id"),
    ("executions", "SELECT * FROM executions WHERE run_id = ? ORDER BY intended_at, id"),
    ("verifications", "SELECT * FROM verifications WHERE run_id = ? ORDER BY started_at, id"),
    ("notifications", "SELECT * FROM notifications WHERE run_id = ? ORDER BY created_at, id"),
    (
        "case_notes",
        "SELECT * FROM case_notes WHERE source_run_id = ? ORDER BY series_id, revision",
    ),
    ("case_retrievals", "SELECT * FROM case_retrievals WHERE run_id = ? ORDER BY created_at, id"),
    ("audit_events", "SELECT * FROM audit_events WHERE run_id = ? ORDER BY seq"),
)


class ExportError(RuntimeError):
    """export를 쓸 수 없음(같은 경로가 이미 있는 등)."""


def _loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _placeholders(runs_dir: Path) -> list[tuple[str, str]]:
    pairs = [(str(runs_dir.resolve()), "<RUNS_DIR>"), (str(REPO_ROOT), "<REPO>")]
    home = str(Path.home())
    if home not in ("", "/"):
        pairs.append((home, "~"))
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def _replace_paths(value: Any, pairs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for original, mark in pairs:
            value = value.replace(original, mark)
        return value
    if isinstance(value, dict):
        return {key: _replace_paths(item, pairs) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_paths(item, pairs) for item in value]
    return value


def _shared_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """공유본 행: JSON 열을 풀고, 본문이 필요 없는 열을 뺀다."""
    data = {key[:-5] if key.endswith("_json") else key: _loads(value) for key, value in row.items()}
    if table == "notifications":  # 본문·결과 원문 없이 상태·receipt만
        data.pop("payload", None)
        data.pop("result", None)
    elif table == "github_issues":
        payload = data.pop("payload", None)
        data["title"] = payload.get("title") if isinstance(payload, dict) else None
    elif table == "demo_runs":
        config = data.get("config")
        if isinstance(config, dict):
            (config.get("runtime_env") or {}).pop("runs_dir", None)
            host = config.get("host_manifest")
            if isinstance(host, dict) and host.get("path"):
                host["path"] = Path(str(host["path"])).name
    return data


def _write(path: Path, text: str, *, private: bool) -> str:
    """배타 생성으로 쓴다(덮어쓰지 않음). SHA-256을 돌려준다."""
    data = text.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def _jsonl(rows: Iterable[Mapping[str, Any]]) -> str:
    return "".join(canonical_dumps(dict(row)) + "\n" for row in rows)


def _record_cells(tx: Tx, work: Any) -> list[str]:
    """run-record 한 줄: Issue·시작 댓글·PR·merge SHA·image·검증·결과 댓글."""
    start = (
        tx.one("SELECT receipt_id, status FROM notifications WHERE id = ?",
               (work["start_notification_id"],))
        if work["start_notification_id"]
        else None
    )  # fmt: skip
    pr = tx.one(
        "SELECT result_json FROM executions WHERE work_id = ? AND operation = 'CREATE_PR'"
        " ORDER BY intended_at DESC, id DESC",
        (work["id"],),
    )
    deploy = tx.one(
        "SELECT request_json, result_json, status FROM executions WHERE work_id = ?"
        " AND operation = 'DEPLOY' ORDER BY intended_at DESC, id DESC",
        (work["id"],),
    )
    verification = tx.one(
        "SELECT id, verdict FROM verifications WHERE run_id = ? AND incident_id = ?"
        " ORDER BY started_at DESC, id DESC",
        (work["run_id"], work["incident_id"]),
    )
    results = tx.all(
        "SELECT event_type, status, receipt_id FROM notifications WHERE work_id = ?"
        " AND event_type IN (SELECT value FROM json_each(?)) ORDER BY created_at, id",
        (work["id"], json.dumps(RESULT_EVENTS)),
    )
    pr_number = (_loads(pr["result_json"]) or {}).get("pr_number") if pr else None
    request = _loads(deploy["request_json"]) if deploy else {}
    result = _loads(deploy["result_json"]) if deploy else {}
    image = ((result or {}).get("target") or {}).get("image_id") if deploy else None
    return [
        f"#{work['issue_number']}",
        f"{work['id']}/g{work['generation']}",
        (f"{start['receipt_id'] or NO_RECORD} ({start['status']})" if start else NO_RECORD),
        f"#{pr_number}" if pr_number else NO_RECORD,
        (request or {}).get("approved_merge_sha") or NO_RECORD,
        image or NO_RECORD,
        f"{verification['id']} {verification['verdict']}" if verification else NO_RECORD,
        ", ".join(f"{r['event_type']} {r['receipt_id'] or r['status']}" for r in results)
        or NO_RECORD,
        work["status"],
    ]


RECORD_HEADER = (
    "| Issue | work/generation | 시작 댓글 receipt | PR | merge SHA | image | 검증"
    " | 결과 알림 | work 상태 |"
)


def _sandbox_rows(tx: Tx, run_id: str, agent_mode: Any) -> list[str]:
    """attempt마다 준비한 sandbox(W15): identity·effective policy·확인 여부·확인 안 된 보호."""
    rows = [
        _loads(row["payload_json"]) or {}
        for row in tx.all(
            "SELECT payload_json FROM audit_events WHERE run_id = ?"
            " AND event_type = 'SANDBOX_PREPARED' ORDER BY seq",
            (run_id,),
        )
    ]
    if not rows:
        return ["N/A(local 모드)" if agent_mode == "local" else NO_RECORD]
    lines = [
        "| attempt | sandbox | effective policy | sandbox_verified | 확인 안 된 보호 |",
        "|---|---|---|---|---|",
    ]
    for record in rows:
        cells = [
            str(record.get("attempt_id") or NO_RECORD),
            str(record.get("identity") or NO_RECORD),
            str(record.get("effective_policy_sha256") or NO_RECORD)[:12],
            "true" if record.get("verified") is True else "false",
            ", ".join(str(item) for item in record.get("unverified") or []) or "-",
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return lines


def run_record(tx: Tx, run: Any, unresolved: Mapping[str, list[Any]], stamp: str) -> str:
    manifest = _loads(run["config_json"]) or {}
    ident = manifest.get("identity") or {}
    memory = manifest.get("memory") or {}
    baseline = manifest.get("baseline") or {}
    host = manifest.get("host_manifest") or {}

    def get(data: Mapping[str, Any], key: str) -> str:
        return str(data.get(key) or NO_RECORD)

    state = "활성" if run["active"] == 1 else "비활성"
    contract = f"{get(ident, 'contract_id')} {ident.get('contract_sha256') or ''}".rstrip()
    lines = [
        f"# run-record — {run['id']}",
        "",
        f"- 생성: {run['created_at']} / 상태: {state} / export: {stamp}",
        f"- config hash: {get(manifest, 'config_hash')}",
        f"- 모델: {get(ident, 'model_id')} / runtime: {get(ident, 'runtime')}"
        f" / agent mode: {get(ident, 'agent_mode')}",
        f"- 정책 hash: {get(ident, 'policy_sha256')} / prompt hash: {get(ident, 'prompt_sha256')}",
        f"- sandbox 정책 hash: {get(ident, 'sandbox_policy_sha256')}",
        f"- host manifest: {get(host, 'path')} (sha256 {get(host, 'sha256')})",
        f"- 계약: {contract}",
        f"- memory: {get(memory, 'mode')} / snapshot {get(memory, 'snapshot_id')}",
        f"- baseline: {get(baseline, 'branch')} @ {get(baseline, 'commit')}"
        f" ({get(baseline, 'status')})",
        "",
        "## work",
        "",
        RECORD_HEADER,
        "|---|---|---|---|---|---|---|---|---|",
    ]
    works = tx.all(
        "SELECT * FROM work_items WHERE run_id = ? ORDER BY created_at, id", (run["id"],)
    )
    for work in works:
        cells = [cell.replace("|", "\\|") for cell in _record_cells(tx, work)]
        lines.append("| " + " | ".join(cells) + " |")
    if not works:
        lines.append("| " + " | ".join([NO_RECORD] * 9) + " |")
    lines += ["", "## sandbox (attempt별)", ""]
    lines += _sandbox_rows(tx, run["id"], ident.get("agent_mode"))
    lines += ["", "## 미해결 (export 시점)", ""]
    for key, items in unresolved.items():
        lines.append(f"- {key}: {len(items)}건")
    lines += [
        "",
        "모르는 값은 `기록 없음`이다. 이 기록은 DB에서 만든 요약이고 원본은 audit DB와"
        " `private/`다.",
        "",
    ]
    return "\n".join(lines)


def export_run(
    store: Store,
    run_id: str,
    runs_dir: Path,
    *,
    clock: Clock,
    unresolved: Mapping[str, list[Any]],
    terms: Iterable[str] = (),
) -> tuple[Path, dict[str, Any]]:
    """export를 쓰고 (경로, export-manifest)를 돌려준다."""
    stamp = clock.utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    root = runs_dir / run_id / "export" / stamp
    if root.exists():
        raise ExportError(f"같은 export 경로가 이미 있다: {root.name}")
    private, shared = root / "private", root / "shared"
    root.mkdir(parents=True)
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    shared.mkdir()
    terms = tuple(terms)
    pairs = _placeholders(runs_dir)
    files: dict[str, dict[str, Any]] = {}
    with store.read() as tx:
        run = tx.one("SELECT * FROM demo_runs WHERE id = ?", (run_id,))
        if run is None:
            raise ExportError(f"없는 run이다: {run_id}")
        for table, sql in TABLES:
            rows = [dict(row) for row in tx.all(sql, (run_id,))]
            digest = _write(private / f"{table}.jsonl", _jsonl(rows), private=True)
            files[f"private/{table}.jsonl"] = {"sha256": digest, "rows": len(rows)}
            clean = [
                _replace_paths(clean_value(_shared_row(table, row), terms, SHARED_MAX_CHARS), pairs)
                for row in rows
            ]
            digest = _write(shared / f"{table}.jsonl", _jsonl(clean), private=False)
            files[f"shared/{table}.jsonl"] = {"sha256": digest, "rows": len(clean)}
        record = run_record(tx, run, unresolved, stamp)
    record = _replace_paths(clean_value(record, terms, SHARED_MAX_CHARS), pairs)
    files["shared/run-record.md"] = {
        "sha256": _write(shared / "run-record.md", record, private=False)
    }
    pending = _replace_paths(clean_value(dict(unresolved), terms, SHARED_MAX_CHARS), pairs)
    text = json.dumps(pending, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    files["shared/unresolved.json"] = {
        "sha256": _write(shared / "unresolved.json", text, private=False)
    }
    manifest = {
        "schema_version": EXPORT_SCHEMA,
        "run_id": run_id,
        "created_at": stamp,
        "files": files,
        "unresolved": {key: len(items) for key, items in unresolved.items()},
        "shared_rule": (
            "비밀 형태·평가 식별자 가림, 로컬 절대 경로 자리표시, 알림 본문·Issue 본문 제외"
        ),
        "private_rule": "DB 행 원본, 로컬 제한 경로(0700)에만 둔다",
        "note": "SHA-256은 무결성 확인용이며 서명·attestation이 아니다",
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write(root / "export-manifest.json", manifest_text, private=False)
    return root, manifest
