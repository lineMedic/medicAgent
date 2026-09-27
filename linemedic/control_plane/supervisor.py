"""work 생성·사람 작업 확인 (W23: Issue polling에 필요한 부분을 먼저 만든다. W25가 이어서 채운다).

- `ensure_work`: 같은 `(routing_scope, repo, issue)`에 활성 work가 있으면 그것을 돌려주고, 없으면
  generation = 마지막 + 1로 `WAITING_APPROVAL` work를 만든다.
  unique 위반은 삼키지 않고 기존 행을 다시 읽는다.
- `check_human_work`: 사람 assignee, 우리 봇이 아닌 사람이 연 open PR이 Issue를 참조하면
  `HUMAN_WORK_IN_PROGRESS`. 목록을 끝까지 보지 못했으면 판단하지 않고 따로 알린다.

approve·retry·cancel·시작 게이트(`start_attempt`)는 W25·W26에서 더한다.
"""

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.ids import new_id
from linemedic.control_plane.state import TERMINAL_WORK_STATUSES
from linemedic.control_plane.store import Tx

HUMAN_WORK_IN_PROGRESS = "HUMAN_WORK_IN_PROGRESS"


def active_work(tx: Tx, routing_scope: str, repository_id: int, issue_number: int) -> Any:
    placeholders = ", ".join("?" for _ in TERMINAL_WORK_STATUSES)
    return tx.one(
        "SELECT * FROM work_items WHERE routing_scope = ? AND repository_id = ?"
        f" AND issue_number = ? AND status NOT IN ({placeholders})"
        " ORDER BY generation DESC LIMIT 1",
        (routing_scope, repository_id, issue_number, *sorted(TERMINAL_WORK_STATUSES)),
    )


def ensure_work(
    tx: Tx, incident: Any, issue: Any, *, authorization: Mapping[str, Any]
) -> tuple[Any, bool]:
    """incident를 Issue의 활성 work에 잇는다. (work 행, 새로 만들었는가)를 돌려준다."""
    scope, repository_id, number = (
        incident["routing_scope"],
        issue["repository_id"],
        issue["issue_number"],
    )
    existing = active_work(tx, scope, repository_id, number)
    if existing is not None:
        return existing, False
    last = tx.one(
        "SELECT MAX(generation) AS g FROM work_items WHERE routing_scope = ? AND repository_id = ?"
        " AND issue_number = ?",
        (scope, repository_id, number),
    )
    work_id = new_id("WORK")
    try:
        tx.execute(
            "INSERT INTO work_items(id, run_id, incident_id, routing_scope, repository_id,"
            " issue_number, generation, status, issue_snapshot_sha256, authorization_json,"
            " created_at, updated_at, details_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'WAITING_APPROVAL', ?, ?, ?, ?, ?)",
            (
                work_id,
                incident["run_id"],
                incident["id"],
                scope,
                repository_id,
                number,
                (last["g"] or 0) + 1,
                issue["snapshot_sha256"],
                canonical_dumps(dict(authorization)),
                tx.now,
                tx.now,
                canonical_dumps({"issue_node_id": issue["node_id"]}),
            ),
        )
    except sqlite3.IntegrityError:
        existing = active_work(tx, scope, repository_id, number)
        if existing is None:
            raise
        return existing, False
    return tx.one("SELECT * FROM work_items WHERE id = ?", (work_id,)), True


def _references(text: str | None, number: int) -> bool:
    return bool(text) and re.search(rf"(?<![\w/&#])#{number}(?!\d)", text) is not None


def check_human_work(
    issue: Mapping[str, Any], open_pulls: Iterable[Mapping[str, Any]], bot_id: int | None
) -> str | None:
    """사람이 이미 이 Issue를 맡았거나 PR을 열었으면 `HUMAN_WORK_IN_PROGRESS`."""
    for assignee in issue.get("assignees") or []:
        if isinstance(assignee, Mapping) and assignee.get("id") != bot_id:
            return HUMAN_WORK_IN_PROGRESS
    number = issue["number"]
    for pull in open_pulls:
        author = pull.get("user") or {}
        if author.get("id") == bot_id:
            continue  # 우리 봇의 PR은 같은 work의 산출물이다
        if _references(pull.get("title"), number) or _references(pull.get("body"), number):
            return HUMAN_WORK_IN_PROGRESS
    return None


def work_authorization(row: Any) -> dict[str, Any]:
    return json.loads(row["authorization_json"])
