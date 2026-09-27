"""run 생성 (W06: `make run-new`의 DB 부분. baseline 브랜치·archive·reset은 W19).

`create_run`은 한 트랜잭션에서 기존 활성 run을 비활성으로 바꾸고
새 run을 활성으로 넣는다(`one_active_run`).
run manifest(`demo_runs.config_json`)에는 config hash·병합 설정·run 식별 env·routing scope·
host manifest 경로와 SHA-256을 남긴다. 비밀 env 값은 넣지 않는다.
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock
from linemedic.common.config import Settings
from linemedic.common.ids import is_valid_run_id, new_run_id
from linemedic.control_plane import audit
from linemedic.control_plane.state import Actor
from linemedic.control_plane.store import Store, Tx

DB_FILENAME = "linemedic.db"


def default_db_path(env: Mapping[str, str]) -> Path:
    """제어 DB 위치: `<RUNS_DIR 또는 runs>/linemedic.db` (git 제외 경로, D68)."""
    return Path(env.get("RUNS_DIR") or "runs") / DB_FILENAME


def build_manifest(settings: Settings, run_id: str, host_manifest: Path | None) -> dict[str, Any]:
    host: dict[str, Any] | None = None
    if host_manifest is not None:
        host = {
            "path": str(host_manifest),
            "sha256": hashlib.sha256(host_manifest.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": "linemedic.v4",
        "run_id": run_id,
        "routing_scope": f"eval:{run_id}",
        "config_hash": settings.config_hash(),
        "config": settings.config.model_dump(mode="json"),
        "runtime_env": settings.runtime.model_dump(mode="json", exclude_none=True),
        "host_manifest": host,
    }


def active_run(tx: Tx) -> Any:
    return tx.one("SELECT id, created_at, config_json FROM demo_runs WHERE active = 1")


def create_run(tx: Tx, run_id: str, manifest: Mapping[str, Any]) -> str | None:
    """새 run을 활성으로 넣는다. 비활성으로 바뀐 이전 run ID를 돌려준다."""
    if not is_valid_run_id(run_id):
        raise ValueError(f"run_id 형식이 아니다: {run_id!r}")
    previous = active_run(tx)
    tx.execute("UPDATE demo_runs SET active = 0 WHERE active = 1")
    tx.execute(
        "INSERT INTO demo_runs(id, active, created_at, config_json) VALUES (?, 1, ?, ?)",
        (run_id, tx.now, canonical_dumps(dict(manifest))),
    )
    previous_id = previous["id"] if previous is not None else None
    if previous_id is not None:
        audit.append(
            tx, previous_id, None, Actor.OPERATOR, "RUN_DEACTIVATED", {"next_run_id": run_id}
        )
    audit.append(
        tx,
        run_id,
        None,
        Actor.OPERATOR,
        "RUN_CREATED",
        {"previous_run_id": previous_id, "config_hash": manifest.get("config_hash")},
    )
    return previous_id


def new_run(
    store: Store, settings: Settings, clock: Clock, host_manifest: Path | None = None
) -> dict[str, Any]:
    """DB migration을 적용하고 새 run을 만든다. 출력용 요약을 돌려준다."""
    store.migrate()
    run_id = new_run_id(clock)
    manifest = build_manifest(settings, run_id, host_manifest)
    with store.tx() as tx:
        previous = create_run(tx, run_id, manifest)
    return {
        "run_id": run_id,
        "previous_run_id": previous,
        "routing_scope": manifest["routing_scope"],
        "config_hash": manifest["config_hash"],
        "host_manifest": manifest["host_manifest"],
        "db": str(store.path),
    }
