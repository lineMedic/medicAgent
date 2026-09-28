"""run 수명 주기 (W06: run 생성 DB 부분, W19: run-new 완성·archive·export·reset, D86).

- `create_run`: 한 트랜잭션에서 기존 활성 run을 비활성으로 바꾸고 새 run을 활성으로 넣는다
  (`one_active_run`)
- `new_run`(`make run-new`): 새 run ID(D50)와 manifest(config hash·병합 설정·run 식별 env·routing
  scope·host manifest, 모델·runtime·정책·prompt·계약 hash, memory mode·snapshot, 기준 브랜치)를
  남긴다. 비밀 env 값은 넣지 않는다. 요청하면(G2·G10) 먼저 `baseline/<run_id>`를 `BASELINE_COMMIT`에
  만들고, 실패하면 run을 만들지 않는다
- `archive`(`make reset`의 앞부분): ① 새 intake·dispatch 정지(active=0. 이 run의 루프가 멈춘다)
  ② 미해결 목록(외부 실행·알림·작업·검증·제안) ③ export(`run_export.py`) ④ 이 run 라벨이 붙은
  컨테이너·network와 `runs/<run>/workspaces`만 정리. DB 기록·원격 브랜치·Issue·PR·case note는
  보존한다. prune·wildcard 삭제·force push·DB 삭제를 하지 않는다
- `reset`: archive 뒤 새 run 준비 명령을 안내한다(자동으로 새 run을 만들지 않는다)
"""

import hashlib
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from linemedic.agent import rules
from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock
from linemedic.common.config import Settings
from linemedic.common.ids import is_valid_run_id, new_run_id
from linemedic.control_plane import audit
from linemedic.control_plane.memory.snapshot import SnapshotError, load_snapshot
from linemedic.control_plane.run_export import export_run
from linemedic.control_plane.state import (
    TERMINAL_INCIDENT_STATUSES,
    TERMINAL_WORK_STATUSES,
    Actor,
)
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.verifier import DEFAULT_CONTRACT, load_contract
from linemedic.integrations import sandbox
from linemedic.integrations.docker import DockerPort
from linemedic.integrations.github_baseline import BaselinePort, baseline_branch

DB_FILENAME = "linemedic.db"
REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_FILES = {
    "policy_sha256": REPO_ROOT / "linemedic" / "policies" / "broker_policy.toml",
    "manual_templates_sha256": REPO_ROOT / "linemedic" / "policies" / "manual_templates.toml",
}
WORKSPACES_DIR = "workspaces"
RUN_LABEL_KEYS = ("linemedic.run_id", "linemedic.run")  # MES·배포 컨테이너 / runner 컨테이너


class RunError(RuntimeError):
    """run을 만들거나 정리할 수 없음."""


def default_db_path(env: Mapping[str, str]) -> Path:
    """제어 DB 위치: `<RUNS_DIR 또는 runs>/linemedic.db` (git 제외 경로, D68)."""
    return Path(env.get("RUNS_DIR") or "runs") / DB_FILENAME


def _file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def identity(settings: Settings) -> dict[str, Any]:
    """같은 조건의 결과만 합치기 위한 식별(spec 09 §9): 모델·runtime·정책·prompt·계약 hash."""
    agent = settings.config.agent
    contract, contract_sha256 = load_contract(DEFAULT_CONTRACT)
    return {
        "model_id": agent.model_id,
        "runtime": agent.runtime,
        "agent_mode": agent.mode,
        **{key: _file_sha256(path) for key, path in IDENTITY_FILES.items()},
        "prompt_sha256": rules.bundle_sha256(),  # system prompt·skill·도구 설명 묶음(W14)
        "sandbox_policy_sha256": sandbox.policy_dir_sha256(sandbox.POLICY_DIR),  # G5 전 None(W15)
        "contract_id": contract.contract_id,
        "contract_sha256": contract_sha256,
    }


def memory_selection(settings: Settings) -> dict[str, Any]:
    """memory mode와 snapshot. 읽지 못한 snapshot은 ID를 비워 둔다(기동 때 UNAVAILABLE로 보인다)."""
    memory = settings.config.memory
    snapshot_id = None
    if memory.snapshot_path:
        try:
            snapshot_id = load_snapshot(Path(memory.snapshot_path)).snapshot_id
        except SnapshotError:
            snapshot_id = None
    return {"mode": memory.mode, "snapshot_path": memory.snapshot_path, "snapshot_id": snapshot_id}


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
        "identity": identity(settings),
        "memory": memory_selection(settings),
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
    store: Store,
    settings: Settings,
    clock: Clock,
    host_manifest: Path | None = None,
    *,
    baseline: BaselinePort | None = None,
) -> dict[str, Any]:
    """DB migration을 적용하고 새 run을 만든다. 출력용 요약을 돌려준다.

    `baseline`을 주면 DB에 run을 넣기 전에 기준 브랜치를 준비한다(BaselineError면 run을
    만들지 않는다).
    """
    store.migrate()
    run_id = new_run_id(clock)
    manifest = build_manifest(settings, run_id, host_manifest)
    commit = settings.runtime.baseline_commit
    status = "NOT_REQUESTED"
    if baseline is not None:
        if commit is None:
            raise RunError("BASELINE_COMMIT가 없어 기준 브랜치를 만들 수 없다")
        status = baseline.ensure_branch(baseline_branch(run_id), commit)
    manifest["baseline"] = {"branch": baseline_branch(run_id), "commit": commit, "status": status}
    with store.tx() as tx:
        previous = create_run(tx, run_id, manifest)
        if status != "NOT_REQUESTED":
            audit.append(
                tx, run_id, None, Actor.OPERATOR, "BASELINE_BRANCH_READY", manifest["baseline"]
            )
    return {
        "run_id": run_id,
        "previous_run_id": previous,
        "routing_scope": manifest["routing_scope"],
        "config_hash": manifest["config_hash"],
        "host_manifest": manifest["host_manifest"],
        "baseline": manifest["baseline"],
        "memory": manifest["memory"],
        "db": str(store.path),
    }


# ── archive·reset ─────────────────────────────────────────────


def stop_intake(tx: Tx, run_id: str, principal: str) -> bool:
    """새 intake·dispatch를 멈춘다(active=0). 이 run의 루프는 활성이 아니면 새 일을 하지 않는다."""
    cursor = tx.execute("UPDATE demo_runs SET active = 0 WHERE id = ? AND active = 1", (run_id,))
    if cursor.rowcount != 1:
        return False
    audit.append(tx, run_id, None, Actor.OPERATOR, "RUN_INTAKE_STOPPED", {"by": principal})
    return True


def unresolved(tx: Tx, run_id: str) -> dict[str, list[dict[str, Any]]]:
    """결과를 모르거나 끝나지 않은 것. 버리지 않고 export에 넣고 조정 명령을 안내한다."""
    executions = [
        {
            **dict(row),
            "next": f"make reconcile RUN_ID={run_id} EXECUTION_ID={row['id']}"
            if row["status"] == "UNKNOWN"
            else "결과 기록 대기",
        }
        for row in tx.all(
            "SELECT id, operation, status, stage, updated_at FROM executions WHERE run_id = ?"
            " AND status IN ('INTENDED', 'RUNNING', 'UNKNOWN') ORDER BY intended_at, id",
            (run_id,),
        )
    ]
    notifications = [
        {
            **dict(row),
            "next": f"make notification-reconcile NOTIFICATION_ID={row['id']}"
            if row["status"] in ("SENDING", "UNKNOWN")
            else "보내지 않음(새 run에서 다른 Issue로 다시 보내지 않는다)",
        }
        for row in tx.all(
            "SELECT id, event_type, route_id, status, attempt_count, updated_at FROM notifications"
            " WHERE run_id = ? AND status IN ('PENDING', 'SENDING', 'UNKNOWN', 'FAILED')"
            " ORDER BY created_at, id",
            (run_id,),
        )
    ]
    works = [
        dict(row)
        for row in tx.all(
            "SELECT id, status, issue_number, generation FROM work_items WHERE run_id = ?"
            " ORDER BY created_at, id",
            (run_id,),
        )
        if row["status"] not in TERMINAL_WORK_STATUSES
    ]
    incidents = [
        dict(row)
        for row in tx.all(
            "SELECT id, status, service FROM incidents WHERE run_id = ? ORDER BY first_seen, id",
            (run_id,),
        )
        if row["status"] not in TERMINAL_INCIDENT_STATUSES
    ]
    verifications = [
        dict(row)
        for row in tx.all(
            "SELECT id, incident_id, started_at FROM verifications WHERE run_id = ?"
            " AND verdict = 'RUNNING' ORDER BY started_at, id",
            (run_id,),
        )
    ]
    proposals = [
        dict(row)
        for row in tx.all(
            "SELECT id, incident_id, decision FROM proposals WHERE run_id = ?"
            " AND decision IN ('RECEIVED', 'CHECKING') ORDER BY received_at, id",
            (run_id,),
        )
    ]
    return {
        "executions": executions,
        "notifications": notifications,
        "works": works,
        "incidents": incidents,
        "verifications": verifications,
        "proposals": proposals,
    }


def _workspaces(runs_dir: Path, run_id: str) -> Path | None:
    """이 run의 workspace 경로. run 경로 밖이거나 symlink이면 None(지우지 않는다)."""
    if not is_valid_run_id(run_id):
        raise RunError(f"run_id 형식이 아니다: {run_id!r}")
    run_dir = runs_dir / run_id
    path = run_dir / WORKSPACES_DIR
    if run_dir.is_symlink() or path.is_symlink() or not path.exists():
        return None
    resolved = path.resolve()
    if resolved.parent != (runs_dir.resolve() / run_id) or resolved.name != WORKSPACES_DIR:
        return None
    return resolved


def cleanup(docker: DockerPort | None, runs_dir: Path, run_id: str) -> dict[str, Any]:
    """이 run 라벨의 컨테이너·network와 `runs/<run>/workspaces`만 정확한 ID·경로로 지운다."""
    result: dict[str, Any] = {"containers": [], "networks": [], "workspaces": None, "errors": []}
    if docker is None:
        result["errors"].append("docker 연결 없음: 컨테이너·network는 정리하지 않았다")
    else:
        for key in RUN_LABEL_KEYS:
            label = f"{key}={run_id}"
            listed = docker.list_containers(label)
            if listed is None:
                result["errors"].append(f"컨테이너 조회 실패({key})")
                continue
            for item in listed:
                info = docker.inspect(item["id"]) or {}
                if ((info.get("Config") or {}).get("Labels") or {}).get(key) != run_id:
                    result["errors"].append(f"라벨 재확인 실패: {item['id'][:12]}")
                    continue  # 목록과 실제가 다르면 지우지 않는다
                removed = docker.remove_container(item["id"])
                if removed.returncode == 0:
                    result["containers"].append({"id": item["id"], "name": item["name"]})
                else:
                    result["errors"].append(f"컨테이너 삭제 실패: {item['name']}")
        networks = docker.list_networks(f"linemedic.run_id={run_id}")
        if networks is None:
            result["errors"].append("network 조회 실패")
        for item in networks or []:
            removed = docker.network_remove(item["id"])
            if removed.returncode == 0:
                result["networks"].append(item["name"])
            else:
                result["errors"].append(f"network 삭제 실패: {item['name']}")
    workspaces = _workspaces(runs_dir, run_id)
    if workspaces is not None:
        # attempt의 `agent_rules`는 읽기 전용(0555)이다. 지우기 전에 이 경로 안의 디렉터리만
        # 쓰기 가능하게 바꾼다(symlink는 따라가지 않는다)
        for directory, _, _ in os.walk(workspaces, followlinks=False):
            os.chmod(directory, 0o700)
        shutil.rmtree(workspaces)  # 정확한 한 경로(run_id 검증·symlink 거부·상위 경로 확인 뒤)
        result["workspaces"] = f"{run_id}/{WORKSPACES_DIR}"
    return result


def archive(
    store: Store,
    run_id: str,
    *,
    runs_dir: Path,
    clock: Clock,
    principal: str,
    docker: DockerPort | None = None,
    clean: bool = True,
    terms: Iterable[str] = (),
) -> dict[str, Any]:
    """정지 → 미해결 확인 → export → (clean이면) 정리. 삭제하는 DB 행은 없다."""
    with store.tx() as tx:
        if tx.one("SELECT 1 FROM demo_runs WHERE id = ?", (run_id,)) is None:
            raise RunError(f"없는 run이다: {run_id}")
        stopped = stop_intake(tx, run_id, principal)
    with store.read() as tx:
        pending = unresolved(tx, run_id)
    root, manifest = export_run(
        store, run_id, runs_dir, clock=clock, unresolved=pending, terms=terms
    )
    cleaned = cleanup(docker, runs_dir, run_id) if clean else None
    export_path = root.relative_to(runs_dir).as_posix()
    with store.tx() as tx:
        audit.append(
            tx,
            run_id,
            None,
            Actor.OPERATOR,
            "RUN_ARCHIVED",
            {
                "by": principal,
                "export": export_path,
                "unresolved": manifest["unresolved"],
                "cleanup": {
                    "containers": len(cleaned["containers"]),
                    "networks": len(cleaned["networks"]),
                    "workspaces": cleaned["workspaces"],
                    "errors": cleaned["errors"],
                }
                if cleaned is not None
                else None,
            },
        )
    return {
        "run_id": run_id,
        "intake_stopped": "stopped_now" if stopped else "already_inactive",
        "unresolved": pending,
        "export": export_path,
        "cleanup": cleaned,
    }


def reset(
    store: Store,
    run_id: str,
    *,
    runs_dir: Path,
    clock: Clock,
    principal: str,
    docker: DockerPort | None = None,
    terms: Iterable[str] = (),
) -> dict[str, Any]:
    """archive(정리 포함) 뒤 새 run 준비 명령을 안내한다. DB·원격 기록은 지우지 않는다."""
    result = archive(
        store,
        run_id,
        runs_dir=runs_dir,
        clock=clock,
        principal=principal,
        docker=docker,
        terms=terms,
    )
    result["next"] = [
        f"make stop RUN_ID={run_id} (이 run의 make start가 떠 있으면)",
        "make run-new (G2 뒤 기준 브랜치까지: make run-new CREATE_BASELINE=1)",
        "memory_assisted 평가면: make memory-snapshot RUN_ID=<새 run>",
    ]
    return result
