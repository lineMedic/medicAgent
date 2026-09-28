"""대시보드 읽기 모델 (W18, spec 13 §1·§2, spec 07 §3, docs/11 §5, D49·D85).

화면(`python -m linemedic.dashboard`)과 `GET /ops/dashboard`가 같은 함수(`build`)로
run 상태를 모은다.

- DB를 읽기만 한다. 값은 저장된 사실 그대로다. 있어야 할 값을 확인하지 못했으면 `미확인`, 이 경우에
  해당하지 않는 값이면 `N/A`로 구분한다
- 상태 문구는 docs/11 §5 표를 옮긴 매핑(`*_LABELS`)으로만 만든다. 표에 없는 상태의 문구도 금지 표현
  (`BANNED_PHRASES`)을 쓰지 않는다
- Issue 제목·로그·case 본문·운영자 메모는 비신뢰 텍스트다. 여기서는 문자열로만 담고 화면이
  escape한다. 링크를 만들지 않는다
- 알림은 이벤트·상태·route·receipt만 보인다(수신 주소·본문 없음)
- 모델의 native thought는 보이지 않는다. 도구 trace는 기록이 생기면(W14) 도구 이름·순서만 보인다
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from linemedic.agent import trace
from linemedic.control_plane.store import Tx

UNKNOWN = "미확인"
NOT_APPLICABLE = "N/A"
KST = timezone(timedelta(hours=9), "KST")
MAX_WORKS = 10
MAX_ITEMS = 20
SUMMARY_MAX_CHARS = 200

# docs/11 §5 표(spec 13 §2). 표에 없는 상태(READY·WAITING_VERIFICATION·CANCELLED 등)도
# 같은 원칙으로 쓴다.
WORK_LABELS = {
    "WAITING_APPROVAL": "처리 범위 승인 대기",
    "WAITING_NOTIFICATION": "시작 알림 확인 대기, 수정 미시작",
    "READY": "시작 알림 접수, 조사 시작 대기",
    "RUNNING": "원인 조사·수정안 작성 중",
    "WAITING_REVIEW": "PR 준비, 사람 리뷰·배포 승인 필요",
    "WAITING_VERIFICATION": "승인된 배포의 업무 검증 대기",
    "HANDED_OFF": "정비 요청 초안·담당자 확인 필요",
    "SUCCEEDED": "지정한 업무 계약·관찰 범위 통과",
    "BLOCKED": "진행 불가, 사유와 필요한 조치",
    "CANCELLED": "취소됨",
    "EXECUTION_UNKNOWN": "외부 조치 여부 미확인",
}
INCIDENT_LABELS = {
    "NEW": "감지됨, 처리 시작 전",
    "INVESTIGATING": "원인 조사·수정안 작성 중",
    "VALIDATING": "제안 검사 중",
    "PR_OPENED": "PR 준비, 사람 리뷰·배포 승인 필요",
    "DEPLOYING": "승인된 배포 진행 중",
    "VERIFYING": "업무 검증 중",
    "WORK_ORDER_DRAFTED": "정비 요청 초안·담당자 확인 필요",
    "RESOLVED": "지정한 업무 계약·관찰 범위 통과",
    "ESCALATED": "진행 불가, 사유와 필요한 조치",
    "EXECUTION_UNKNOWN": "외부 조치 여부 미확인",
}
NOTIFICATION_LABELS = {
    "PENDING": "발송 대기",
    "SENDING": "발송 중(결과 확인 전)",
    "FAILED": "발송 실패",
    "UNKNOWN": "발송 결과 미확인",
}
ACCEPTED_LABELS = {"github_comment": "댓글 등록", "smtp": "메일 서버 접수"}
ACCEPTED_DEFAULT = "댓글 등록 / 메일 서버 접수"
CASE_LABELS = {
    "VERIFIED_SUCCESS": "검증된 사례(지정한 계약·관찰 범위 안)",
    "VERIFIED_FAILURE": "업무 검사에서 실패한 시도",
    "UNVERIFIED": "아직 업무 검증 없는 시도",
    "BLOCKED": "진행 불가 기록 — 현재도 틀린 패치라는 뜻 아님",
    "INCONCLUSIVE": "결론 미확인 — 성공·실패 근거로 쓰지 않음",
    "HANDOFF": "정비 요청 초안 — 실제 정비·복구 확인 아님",
}
VERIFICATION_LABELS = {
    None: "아직 미검증",
    "RUNNING": "업무 검증 진행 중",
    "PASS": "지정한 업무 계약·관찰 범위 통과",
    "FAIL": "업무 계약 검사 실패",
    "INCONCLUSIVE": "결론 미확인",
}
EXECUTION_LABELS = {
    "INTENDED": "실행 예정",
    "RUNNING": "실행 중",
    "SUCCEEDED": "완료",
    "FAILED": "실패",
    "UNKNOWN": "외부 조치 여부 미확인",
}
BINDING_LABELS = {
    "CREATED": "새 Issue 생성",
    "MANAGED_RECEIPT": "기존 Issue 재사용(LineMedic 접수 기록)",
    "STRUCTURED_APPROVED": "기존 Issue 재사용(구조화 승인)",
    "OPERATOR": "운영자 연결",
}
# 화면·JSON 어디에도 쓰지 않는 표현(docs/11 §5의 금지 표현). 테스트가 모든 문구를 대조한다.
BANNED_PHRASES = (
    "자동 처리 시작",
    "작업 중",
    "복구됨",
    "자동 복구 완료",
    "정비 완료",
    "사유 없는 단순 오류",
    "실패했으니 재실행",
    "모든 장애 해결",
    "사람이 읽음",
    "읽음",
    "정답 사례",
)


def label(table: Mapping[Any, str], value: Any) -> str:
    """저장 상태 → 표시 문구. 표에 없는 값은 지어내지 않고 원래 값과 함께 미확인으로 둔다."""
    if value in table:
        return table[value]
    return f"{UNKNOWN}({value})"


def notification_label(status: str, adapter: str | None) -> str:
    if status == "ACCEPTED":
        return ACCEPTED_LABELS.get(adapter or "", ACCEPTED_DEFAULT)
    return label(NOTIFICATION_LABELS, status)


def kst(value: str | None) -> str:
    """저장된 UTC 시각을 KST로 보인다. 없거나 읽을 수 없으면 미확인."""
    if not value:
        return UNKNOWN
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return UNKNOWN
    if parsed.tzinfo is None:
        return UNKNOWN
    return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _loads(value: str | None) -> dict[str, Any]:
    try:
        data = json.loads(value) if value else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(value: Any, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _or(value: Any, missing: str = UNKNOWN) -> Any:
    return missing if value in (None, "") else value


# ── run 머리 ──────────────────────────────────────────────────


def select_run(tx: Tx, run_id: str | None = None) -> Any:
    """지정한 run, 없으면 활성 run, 그것도 없으면 가장 최근 run."""
    if run_id is not None:
        return tx.one("SELECT * FROM demo_runs WHERE id = ?", (run_id,))
    return tx.one("SELECT * FROM demo_runs ORDER BY active DESC, created_at DESC, id DESC")


def _snapshot(tx: Tx, run_id: str, mode: str | None) -> str:
    used = tx.one(
        "SELECT snapshot_id FROM case_retrievals WHERE run_id = ? AND snapshot_id IS NOT NULL"
        " ORDER BY created_at DESC, id DESC",
        (run_id,),
    )
    if used is not None:
        return f"{used['snapshot_id']}(검색에 사용)"
    made = tx.one(
        "SELECT payload_json FROM audit_events WHERE run_id = ?"
        " AND event_type = 'MEMORY_SNAPSHOT_CREATED' ORDER BY seq DESC",
        (run_id,),
    )
    if made is not None and _loads(made["payload_json"]).get("snapshot_id"):
        return f"{_loads(made['payload_json'])['snapshot_id']}(만듦, 검색 기록 없음)"
    return NOT_APPLICABLE if mode == "cold_start" else UNKNOWN


def _polling(tx: Tx, manifest: dict[str, Any], repo: dict[str, Any]) -> dict[str, Any]:
    if not repo.get("full_name") and not repo.get("id"):
        return {"last_success": f"{NOT_APPLICABLE}(등록 repo 없음)", "failures": NOT_APPLICABLE}
    scope = manifest.get("routing_scope") or ""
    row = tx.one(
        "SELECT payload_json FROM integration_state WHERE integration_id = 'github_issues'"
        " AND state_key = ?",
        (f"sync:{scope}",),
    )
    state = _loads(row["payload_json"]) if row is not None else {}
    return {
        "last_success": kst(state.get("last_success_at")),
        "last_failure": kst(state.get("last_failure_at"))
        if state.get("last_failure_at")
        else NOT_APPLICABLE,
        "failures": state.get("consecutive_failures", 0) if state else UNKNOWN,
    }


def header(tx: Tx, run: Any) -> dict[str, Any]:
    manifest = _loads(run["config_json"])
    config = manifest.get("config") or {}
    agent = config.get("agent") or {}
    memory = config.get("memory") or {}
    repo = config.get("repository") or {}
    runtime_env = manifest.get("runtime_env") or {}
    host_manifest = manifest.get("host_manifest") or {}
    mode = agent.get("mode")
    if mode == "local":
        sandbox = f"{NOT_APPLICABLE}(local 모드)"
    else:  # sandbox 검증 기록은 W15가 남긴다. 그 전에는 확인하지 않은 것이다
        sandbox = f"{UNKNOWN}(sandbox 검증 기록 없음)"
    repo_text = UNKNOWN
    if repo.get("full_name") or repo.get("id"):
        repo_text = f"{_or(repo.get('full_name'))} (ID {_or(repo.get('id'))})"
    return {
        "run_id": run["id"],
        "active": "활성" if run["active"] == 1 else "비활성",
        "created_at": kst(run["created_at"]),
        "host_id": _or(runtime_env.get("demo_host_id")),
        "host_manifest": (
            f"{host_manifest.get('path')} (sha256 {str(host_manifest.get('sha256'))[:12]})"
            if host_manifest.get("path")
            else UNKNOWN
        ),
        "runtime": _or(agent.get("runtime")),
        "runtime_version": UNKNOWN,  # runtime 버전 기록은 W14(G4 결정 뒤)
        "agent_mode": _or(mode),
        "sandbox_verified": sandbox,
        "model_id": _or(agent.get("model_id")),
        "memory_mode": _or(memory.get("mode")),
        "memory_snapshot": _snapshot(tx, run["id"], memory.get("mode")),
        "repository": repo_text,
        "polling": _polling(tx, manifest, repo),
    }


# ── work 카드 ─────────────────────────────────────────────────


def _routes(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = ((manifest.get("config") or {}).get("notifications") or {}).get("routes") or {}
    return {key: value for key, value in routes.items() if isinstance(value, dict)}


def _binding(tx: Tx, work: Any, incident: Any) -> str:
    row = tx.one(
        "SELECT basis FROM issue_bindings WHERE routing_scope = ? AND repository_id = ?"
        " AND fingerprint_version = ? AND problem_fingerprint = ? AND issue_number = ?",
        (
            work["routing_scope"],
            work["repository_id"],
            incident["fingerprint_version"],
            incident["fingerprint"],
            work["issue_number"],
        ),
    )
    if row is not None:
        return f"{label(BINDING_LABELS, row['basis'])} / basis={row['basis']}"
    basis = _loads(work["authorization_json"]).get("basis")
    if incident["source_kind"] == "GITHUB_ISSUE":
        return f"Issue로 접수 / basis={_or(basis)}"
    return f"basis={basis}" if basis else UNKNOWN


def _mail(tx: Tx, work: Any, routes: dict[str, dict[str, Any]]) -> str:
    mail_routes = [key for key, route in routes.items() if route.get("adapter") == "smtp"]
    if not any(routes[key].get("enabled") for key in mail_routes):
        return "사용하지 않음"
    rows = tx.all(
        "SELECT status FROM notifications WHERE work_id = ? AND route_id IN"
        " (SELECT value FROM json_each(?)) ORDER BY created_at DESC",
        (work["id"], json.dumps(mail_routes)),
    )
    if not rows:
        return UNKNOWN
    return notification_label(rows[0]["status"], "smtp")


# 시작 알림을 거쳐야만 올 수 있는 work 상태(여기서 알림이 없으면 미확인이다)
STARTED_WORK_STATUSES = frozenset(
    {
        "WAITING_NOTIFICATION",
        "READY",
        "RUNNING",
        "WAITING_REVIEW",
        "WAITING_VERIFICATION",
        "HANDED_OFF",
        "SUCCEEDED",
    }
)


def _start_notice(tx: Tx, work: Any, routes: dict[str, dict[str, Any]]) -> str:
    if not work["start_notification_id"]:
        expected = work["status"] in STARTED_WORK_STATUSES or work["attempt_id"]
        return UNKNOWN if expected else NOT_APPLICABLE  # 승인 전·시작 전 차단·취소는 해당 없음
    notice = tx.one(
        "SELECT status, route_id, receipt_id FROM notifications WHERE id = ?",
        (work["start_notification_id"],),
    )
    if notice is None:
        return UNKNOWN
    adapter = routes.get(notice["route_id"], {}).get("adapter")
    text = notification_label(notice["status"], adapter)
    if notice["status"] == "ACCEPTED":
        text += f" #{_or(notice['receipt_id'])}"
    return text


def _blocker(tx: Tx, work: Any) -> dict[str, Any] | None:
    """진행 불가면 사유와 필요한 조치(host가 쓴 보고의 사실 필드)를 보인다."""
    if work["status"] not in ("BLOCKED", "EXECUTION_UNKNOWN") and not work["reason_code"]:
        return None
    row = tx.one(
        "SELECT payload_json FROM notifications WHERE work_id = ? AND event_type = 'WORK_BLOCKED'"
        " ORDER BY created_at DESC",
        (work["id"],),
    )
    report = _loads(row["payload_json"]) if row is not None else {}
    return {
        "code": _or(report.get("blocker_code") or work["reason_code"]),
        "detail": _text(report.get("reason_detail") or report.get("operator_note") or UNKNOWN),
        "next_steps": [
            _text(step)
            for step in (report.get("operator_next_step") or report.get("missing_requirements"))
            or []
        ][:5],
    }


def _step(name: str, state: str, at: str | None = None, detail: str = "") -> dict[str, str]:
    return {"name": name, "state": state, "at": kst(at) if at else "", "detail": detail}


def _transition_at(tx: Tx, run_id: str, incident_id: str, work_to: str) -> str | None:
    for row in tx.all(
        "SELECT created_at, payload_json FROM audit_events WHERE run_id = ? AND incident_id = ?"
        " AND event_type = 'WORK_TRANSITION' ORDER BY seq",
        (run_id, incident_id),
    ):
        if _loads(row["payload_json"]).get("to") == work_to:
            return row["created_at"]
    return None


def timeline(tx: Tx, work: Any, routes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Issue 연결 → 시작 알림 → agent 시작 → 사례/근거 조회 → 검사 → PR → 사람 승인 → 배포
    → 검증."""
    run_id, incident_id = work["run_id"], work["incident_id"]
    steps = [_step("Issue 연결", "done", work["created_at"], f"Issue #{work['issue_number']}")]
    notice = (
        tx.one("SELECT * FROM notifications WHERE id = ?", (work["start_notification_id"],))
        if work["start_notification_id"]
        else None
    )
    if notice is None:
        steps.append(_step("시작 알림 접수", "waiting"))
    else:
        adapter = routes.get(notice["route_id"], {}).get("adapter")
        state = {"ACCEPTED": "done", "FAILED": "failed", "UNKNOWN": "unknown"}.get(
            notice["status"], "current"
        )
        steps.append(
            _step(
                "시작 알림 접수",
                state,
                notice["accepted_at"] or notice["updated_at"],
                notification_label(notice["status"], adapter),
            )
        )
    started = tx.one(
        "SELECT created_at, payload_json FROM audit_events WHERE run_id = ? AND incident_id = ?"
        " AND event_type = 'ATTEMPT_STARTED' ORDER BY seq DESC",
        (run_id, incident_id),
    )
    if started is None:
        steps.append(_step("agent 시작", "waiting"))
    else:
        info = _loads(started["payload_json"])
        detail = f"{_or(info.get('adapter'))} / origin {_or(info.get('origin'))}"
        steps.append(_step("agent 시작", "done", started["created_at"], detail))
    retrievals = tx.all(
        "SELECT status, created_at FROM case_retrievals WHERE work_id = ? ORDER BY created_at",
        (work["id"],),
    )
    evidence_count = tx.one(
        "SELECT COUNT(*) FROM evidence WHERE run_id = ? AND incident_id = ?", (run_id, incident_id)
    )[0]
    if retrievals or evidence_count:
        statuses = ", ".join(r["status"] for r in retrievals) or "사례 조회 없음"
        at = retrievals[-1]["created_at"] if retrievals else None
        detail = f"근거 {evidence_count}건 / 사례 검색 {len(retrievals)}회({statuses})"
        steps.append(_step("사례/근거 조회", "done", at, detail))
    else:
        steps.append(_step("사례/근거 조회", "waiting"))
    proposal = tx.one(
        "SELECT * FROM proposals WHERE run_id = ? AND incident_id = ?"
        " ORDER BY received_at DESC, id DESC",
        (run_id, incident_id),
    )
    if proposal is None:
        steps.append(_step("테스트·패치 검사", "waiting"))
    else:
        record = _loads(proposal["checks_json"])
        checks = [
            f"{c.get('check')} {c.get('result')}"
            for c in record.get("checks") or []
            if c.get("check") in ("PATCH_POLICY", "BASE", "R0", "R1", "R2", "RUNNER", "TEMPLATE")
        ]
        state = {"ALLOWED": "done", "REJECTED": "failed"}.get(proposal["decision"], "current")
        detail = f"{proposal['decision']} {record.get('decision_reason') or ''}".strip()
        steps.append(
            _step(
                "테스트·패치 검사",
                state,
                proposal["received_at"],
                detail + (f" — {', '.join(checks)}" if checks else ""),
            )
        )
    for name, operation in (("PR", "CREATE_PR"), ("배포", "DEPLOY")):
        execution = tx.one(
            "SELECT * FROM executions WHERE work_id = ? AND operation = ?"
            " ORDER BY intended_at DESC, id DESC",
            (work["id"], operation),
        )
        if name == "배포":
            review_at = _transition_at(tx, run_id, incident_id, "WAITING_REVIEW")
            if review_at is not None:
                state = "done" if execution is not None else "current"
                steps.append(_step("사람 승인 대기", state, review_at, "사람 리뷰·배포 승인 필요"))
            else:
                steps.append(_step("사람 승인 대기", "waiting"))
        if execution is None:
            steps.append(_step(name, "waiting"))
            continue
        result = _loads(execution["result_json"])
        state = {"SUCCEEDED": "done", "FAILED": "failed", "UNKNOWN": "unknown"}.get(
            execution["status"], "current"
        )
        detail = label(EXECUTION_LABELS, execution["status"])
        if operation == "CREATE_PR" and result.get("pr_number"):
            detail = f"PR #{result['pr_number']} / {detail}"
        steps.append(_step(name, state, execution["updated_at"], detail))
    verification = tx.one(
        "SELECT verdict, ended_at, started_at FROM verifications"
        " WHERE run_id = ? AND incident_id = ? ORDER BY started_at DESC, id DESC",
        (run_id, incident_id),
    )
    if verification is None:
        steps.append(_step("검증", "waiting", detail=label(VERIFICATION_LABELS, None)))
    else:
        state = {"PASS": "done", "FAIL": "failed", "INCONCLUSIVE": "unknown"}.get(
            verification["verdict"], "current"
        )
        steps.append(
            _step(
                "검증",
                state,
                verification["ended_at"] or verification["started_at"],
                label(VERIFICATION_LABELS, verification["verdict"]),
            )
        )
    return steps


def _evidence_summary(kind: str, payload: dict[str, Any]) -> str:
    if kind == "log_error":
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        parts = [event.get(key) for key in ("error_type", "path", "message") if event.get(key)]
        return _text(" · ".join(str(part) for part in parts) or payload)
    if kind == "equipment_metric":
        return f"{_or(payload.get('equipment_id'))} · 표본 {len(payload.get('samples') or [])}개"
    if kind == "history_projection":
        outcome = payload.get("outcome")
        return _text(f"{payload.get('note_id')} · {label(CASE_LABELS, outcome)}")
    return _text(payload)


def evidence(tx: Tx, run_id: str, incident_id: str) -> list[dict[str, str]]:
    rows = tx.all(
        "SELECT id, kind, observed_at, source_identity, payload_json FROM evidence"
        " WHERE run_id = ? AND incident_id = ? ORDER BY observed_at, rowid LIMIT ?",
        (run_id, incident_id, MAX_ITEMS),
    )
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "observed_at": kst(row["observed_at"]),
            "summary": _evidence_summary(row["kind"], _loads(row["payload_json"])),
        }
        for row in rows
    ]


def retrieved_cases(tx: Tx, run_id: str, incident_id: str) -> list[dict[str, str]]:
    """이 incident가 조회한 과거 사례(history projection). 해석 경계를 문구에 붙인다."""
    rows = tx.all(
        "SELECT id, payload_json FROM evidence WHERE run_id = ? AND incident_id = ?"
        " AND kind = 'history_projection' ORDER BY observed_at, rowid LIMIT ?",
        (run_id, incident_id, MAX_ITEMS),
    )
    items = []
    for row in rows:
        payload = _loads(row["payload_json"])
        conditions = payload.get("failure_conditions") or []
        items.append(
            {
                "note_id": _or(payload.get("note_id")),
                "outcome": _or(payload.get("outcome")),
                "meaning": label(CASE_LABELS, payload.get("outcome")),
                "summary": _text(payload.get("summary") or UNKNOWN),
                "condition": _text(conditions[0]) if conditions else NOT_APPLICABLE,
                "evidence_id": row["id"],
            }
        )
    return items


def work_cases(tx: Tx, work_id: str) -> list[dict[str, str]]:
    """이 work의 결과로 만든 사례 노트(revision 순서)."""
    rows = tx.all(
        "SELECT id, revision, outcome, phase, origin, publish_status FROM case_notes"
        " WHERE work_id = ? ORDER BY revision",
        (work_id,),
    )
    return [
        {
            "note_id": row["id"],
            "outcome": row["outcome"],
            "meaning": label(CASE_LABELS, row["outcome"]),
            "phase": row["phase"],
            "origin": row["origin"],
            "publish_status": row["publish_status"],
        }
        for row in rows
    ]


def notifications(
    tx: Tx, run_id: str, routes: dict[str, dict[str, Any]], work_id: str | None
) -> list[dict[str, Any]]:
    """상태·route·receipt만. 수신 주소·본문은 넣지 않는다."""
    clause, params = (
        ("work_id = ?", (run_id, work_id)) if work_id else ("work_id IS NULL", (run_id,))
    )
    rows = tx.all(
        "SELECT id, event_type, route_id, status, receipt_id, attempt_count, updated_at"
        f" FROM notifications WHERE run_id = ? AND {clause} ORDER BY created_at, id LIMIT ?",
        (*params, MAX_ITEMS),
    )
    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "route_id": row["route_id"],
            "status": row["status"],
            "status_text": notification_label(
                row["status"], routes.get(row["route_id"], {}).get("adapter")
            ),
            "receipt_id": row["receipt_id"] or NOT_APPLICABLE,
            "attempt_count": row["attempt_count"],
            "updated_at": kst(row["updated_at"]),
        }
        for row in rows
    ]


def tool_trace(tx: Tx, run_id: str, incident_id: str) -> dict[str, Any]:
    """마지막 attempt에서 서버가 받은 도구 호출 순서(W14 `TOOL_CALL`). 없으면 지어내지 않는다.

    사람이 미리 쓴 제안(manual_integration)의 호출은 보이되 모델이 고른 순서가 아니라고 적는다.
    """
    started = tx.one(
        "SELECT payload_json FROM audit_events WHERE run_id = ? AND incident_id = ?"
        " AND event_type = 'ATTEMPT_STARTED' ORDER BY seq DESC",
        (run_id, incident_id),
    )
    if started is None:
        return {"status": f"{NOT_APPLICABLE}(agent 시작 전)", "tools": []}
    info = _loads(started["payload_json"])
    calls = trace.server_calls(tx, run_id, incident_id, str(info.get("attempt_id")))
    tools = trace.summarize(calls)
    counted = sum(1 for c in calls if not c["budget_exempt"] and c["result"] == "served")
    if info.get("origin") == "manual_integration":
        status = f"{NOT_APPLICABLE}(사람이 미리 작성한 제안, 모델 도구 선택 없음)"
        if tools:
            status += f" — 제안 제출 흐름의 호출 {len(calls)}회"
        return {"status": status, "tools": tools}
    if not calls:
        return {"status": f"{UNKNOWN}(도구 trace 기록 없음)", "tools": []}
    return {
        "status": f"서버가 받은 도구 호출 {len(calls)}회(예산 계산 {counted}회)",
        "tools": tools,
    }


def work_card(tx: Tx, work: Any, routes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    incident = tx.one(
        "SELECT * FROM incidents WHERE run_id = ? AND id = ?", (work["run_id"], work["incident_id"])
    )
    issue = tx.one(
        "SELECT state, payload_json FROM github_issues"
        " WHERE repository_id = ? AND issue_number = ?",
        (work["repository_id"], work["issue_number"]),
    )
    verification = tx.one(
        "SELECT verdict FROM verifications WHERE run_id = ? AND incident_id = ?"
        " ORDER BY started_at DESC, id DESC",
        (work["run_id"], work["incident_id"]),
    )
    title = _loads(issue["payload_json"]).get("title") if issue is not None else None
    return {
        "title": (
            f"Issue #{work['issue_number']} · {work['id']}/g{work['generation']}"
            f" · {work['incident_id']} · {incident['service']}"
        ),
        "issue_title": _text(title) if title else UNKNOWN,
        "issue_state": issue["state"] if issue is not None else UNKNOWN,
        "binding": _binding(tx, work, incident),
        "work_status": work["status"],
        "work_text": label(WORK_LABELS, work["status"]),
        "incident_status": incident["status"],
        "incident_text": label(INCIDENT_LABELS, incident["status"]),
        "verification_text": label(
            VERIFICATION_LABELS, verification["verdict"] if verification is not None else None
        ),
        "start_notice": _start_notice(tx, work, routes),
        "mail": _mail(tx, work, routes),
        "blocker": _blocker(tx, work),
        "timeline": timeline(tx, work, routes),
        "evidence": evidence(tx, work["run_id"], work["incident_id"]),
        "retrieved_cases": retrieved_cases(tx, work["run_id"], work["incident_id"]),
        "cases": work_cases(tx, work["id"]),
        "notifications": notifications(tx, work["run_id"], routes, work["id"]),
        "trace": tool_trace(tx, work["run_id"], work["incident_id"]),
        "updated_at": kst(work["updated_at"]),
    }


def unbound_incidents(tx: Tx, run_id: str) -> list[dict[str, Any]]:
    """아직 Issue·work에 연결되지 않은 사건(로그 감지 직후 등)."""
    rows = tx.all(
        "SELECT i.* FROM incidents i WHERE i.run_id = ? AND NOT EXISTS"
        " (SELECT 1 FROM work_items w WHERE w.run_id = i.run_id AND w.incident_id = i.id)"
        " ORDER BY i.first_seen DESC, i.id LIMIT ?",
        (run_id, MAX_ITEMS),
    )
    return [
        {
            "incident_id": row["id"],
            "service": row["service"],
            "status": row["status"],
            "status_text": label(INCIDENT_LABELS, row["status"]),
            "count": row["count"],
            "first_seen": kst(row["first_seen"]),
            "last_seen": kst(row["last_seen"]),
        }
        for row in rows
    ]


def build(tx: Tx, run_id: str | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """화면·`GET /ops/dashboard` 공용 읽기 모델."""
    generated = kst((now or datetime.now(UTC)).isoformat())
    run = select_run(tx, run_id)
    if run is None:
        return {
            "generated_at": generated,
            "run": None,
            "works": [],
            "unbound_incidents": [],
            "run_notifications": [],
            "labels_source": "docs/11 §5",
        }
    routes = _routes(_loads(run["config_json"]))
    works = tx.all(
        "SELECT * FROM work_items WHERE run_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
        (run["id"], MAX_WORKS),
    )
    return {
        "generated_at": generated,
        "run": header(tx, run),
        "works": [work_card(tx, work, routes) for work in works],
        "unbound_incidents": unbound_incidents(tx, run["id"]),
        "run_notifications": notifications(tx, run["id"], routes, None),
        "labels_source": "docs/11 §5",
    }
