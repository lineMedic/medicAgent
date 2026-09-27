"""로그 incident → 등록 repo Issue 연결·신규 생성·binding (W24, spec 15 §3·§4, docs/05 ②·⑦).

lookup 순서(spec 15 §3.2)
  1 EXISTING_BINDING     같은 scope·repo·fingerprint binding → GitHub에서 Issue 실존·repo 재조회
  2 MANAGED_RECEIPT      우리 CREATE_ISSUE execution의 receipt Issue가 봇 작성·marker 일치
  3 STRUCTURED_APPROVED  승인 issue form의 서비스·signature가 정확히 같고 작성자가 승인된 작성자
  4 AMBIGUOUS            signature 토큰이 겹치는 후보만 있거나 후보가 여럿 → 생성·연결 없이 멈춤
  5 NO_MATCH_IN_SCOPE    mirror가 완전하고 후보가 없을 때만 → 신규 생성
  오류 LOOKUP_INCOMPLETE  권한·rate limit·네트워크·잔여 페이지·동기화 누락·결과 불명 intent → 멈춤

- 로그 본문의 `#42`·HTML marker는 읽지 않는다(연결 권한이 아니다).
  자연어 유사도로 자동 연결하지 않는다.
- route는 repo 단위 lock 안에서 Issue mirror를 먼저 갱신(생성 직전 delta)한 뒤 판정한다.
- 신규 생성: `CREATE_ISSUE` intent(logical key `issue:<scope>:<repo_id>:<fingerprint>`)를
  커밋한 뒤 POST한다.
  - 성공: execution SUCCEEDED·mirror·binding(CREATED)·work `WAITING_APPROVAL`
  - 결과 불명: UNKNOWN과 incident `EXECUTION_UNKNOWN`(두 번째 POST 없음)
  - 확정 거절: FAILED 뒤 멈춘다. rate limit이면 FAILED로 두고 incident는 NEW로 남겨 다시 시도한다
- `write_enabled=false`(shadow)면 intent·POST 없이 만들 Issue 계획만 돌려준다.
- 본문은 정제한 템플릿(서비스·관찰·영향 추정·증거 개수·"원인 미확정")과 marker뿐이다.
  raw 로그·비밀·실행 지시·링크를 넣지 않는다.
- AMBIGUOUS·LOOKUP_INCOMPLETE·연결 Issue closed → incident ESCALATED와 `WORK_BLOCKED` intent
  (work가 없으면 `notify:intake:...` 키).
- 결과 불명 조정(`reconcile_create_issue`): 봇 작성자 + 정확한 marker + 생성 시각 범위로
  정확히 1개일 때만 채택한다. 0개·2개 이상·조회 불완전은 기록만 하고 다시 POST하지 않는다.
- 타인의 Issue를 닫기·삭제·병합하지 않는다.
"""

import dataclasses
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.clock import Clock
from linemedic.common.ids import new_id
from linemedic.common.sanitize import disable_urls, mask_secrets, neutralize_mentions, sanitize_text
from linemedic.control_plane import audit, checkpoints, evidence, supervisor
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.errors import ApiError
from linemedic.control_plane.issue_sync import (
    INTEGRATION_ID,
    IssueSync,
    _gh,
    _parse,
    is_pull_request,
)
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.notifications.blocker import blocker_report
from linemedic.control_plane.state import Actor, transition_incident
from linemedic.control_plane.store import Store, Tx
from linemedic.control_plane.symptoms import observed_symptom
from linemedic.integrations.github import (
    Conflict,
    Forbidden,
    GitHubError,
    GitHubPort,
    GitHubResponse,
    NotFound,
    RateLimited,
    Unknown,
)

LABEL = "linemedic"
TITLE_MAX_CHARS = 120
MARKER_RE = re.compile(
    r"<!-- linemedic:issue scope=(?P<scope>\S+) fp=(?P<fp>\S+) exec=(?P<exec>EXE-[0-9A-F]{12}) -->"
)
_FORM_SECTION = re.compile(
    r"^###[ \t]+(?P<label>[^\n]+?)[ \t]*\n(?P<value>.*?)(?=^###[ \t]|\Z)", re.M | re.S
)
RECONCILE_BEFORE = timedelta(minutes=5)
RECONCILE_AFTER = timedelta(hours=1)
REUSE_KINDS = ("EXISTING_BINDING", "MANAGED_RECEIPT", "STRUCTURED_APPROVED")
FAILURE_CODES: dict[type[GitHubError], str] = {
    Forbidden: "PERMISSION_REQUIRED",
    NotFound: "SOURCE_CHANGED",
    Conflict: "VALIDATION_FAILED",
}


# ── 도우미 ────────────────────────────────────────────────────


def fingerprint_value(incident: Any) -> str:
    return f"{incident['fingerprint_version']}:{incident['fingerprint']}"


def marker(scope: str, fingerprint: str, execution_id: str) -> str:
    return f"<!-- linemedic:issue scope={scope} fp={fingerprint} exec={execution_id} -->"


def parse_form(body: str | None) -> dict[str, str | None]:
    """승인 issue form의 렌더된 본문에서 `서비스`·`오류 signature` 값(첫 줄)만 읽는다."""
    fields: dict[str, str] = {}
    for match in _FORM_SECTION.finditer(body or ""):
        lines = [line.strip() for line in match.group("value").splitlines() if line.strip()]
        fields.setdefault(match.group("label").strip().casefold(), lines[0] if lines else "")
    return {"service": fields.get("서비스"), "signature": fields.get("오류 signature")}


def _tokens(incident: Any) -> list[str]:
    """후보 판별용 signature 토큰(서비스·오류 종류·endpoint 또는 설비·이상 종류)."""
    details = json.loads(incident["details_json"] or "{}")
    metric = details.get("metric") if isinstance(details.get("metric"), dict) else {}
    sig = details.get("signature") if isinstance(details.get("signature"), dict) else {}
    tokens = [incident["service"]]
    if metric.get("equipment_id"):
        tokens += [str(metric["equipment_id"]), str(metric.get("anomaly") or "")]
    else:
        tokens += [
            str(sig.get("error_type") or "").split(":", 1)[0],
            str(sig.get("endpoint") or ""),
        ]
    return sorted({token for token in tokens if len(token) >= 3})


def _plain(text: str) -> str:
    """제목용: 비밀·멘션·링크만 무력화한다(제목은 HTML로 렌더되지 않는다)."""
    return disable_urls(neutralize_mentions(mask_secrets(text))).replace("\n", " ").strip()


def issue_text(
    incident: Any, scope: str, execution_id: str, evidence_counts: dict[str, int]
) -> tuple[str, str]:
    """Issue 제목·본문. 비신뢰 값(로그에서 온 endpoint·오류 이름)은 모두 정제한다."""
    details = json.loads(incident["details_json"] or "{}")
    observed = observed_symptom(details) or f"{incident['service']} 오류 반복 관찰"
    title = _plain(f"[LineMedic] {incident['service']}: {observed}")[:TITLE_MAX_CHARS]
    counts = ", ".join(f"{kind} {n}건" for kind, n in sorted(evidence_counts.items())) or "없음"
    body = "\n".join(
        [
            f"## LineMedic 자동 보고: {sanitize_text(observed)}",
            "",
            f"- 서비스: {sanitize_text(incident['service'])}"
            f" (라인 {sanitize_text(incident['line_id'])})",
            f"- 관찰: {sanitize_text(observed)} — {incident['count']}회,"
            f" {incident['first_seen']} ~ {incident['last_seen']} (UTC)",
            "- 영향(추정): 해당 요청이 실패할 수 있다",
            f"- 확인한 증거: {counts} (정제본은 LineMedic에 보관)",
            "- 원인: 미확정. 이 Issue는 작업의 출발점이며 조치 지시가 아니다.",
            "- 다음 단계: 운영자 승인과 시작 알림 뒤에만 LineMedic이 조사를 시작한다.",
            "",
            marker(scope, fingerprint_value(incident), execution_id),
        ]
    )
    return title, body


# ── 결과 ──────────────────────────────────────────────────────


@dataclass
class Lookup:
    kind: str
    issue: dict[str, Any] | None = None  # 재조회한 GitHub payload(재사용 종류일 때)
    candidates: list[int] = field(default_factory=list)
    reason: str | None = None
    complete: bool | None = None

    def decision(self) -> dict[str, Any]:
        return {
            "result": self.kind,
            "issue_number": self.issue["number"] if self.issue else None,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "mirror_complete": self.complete,
        }


@dataclass
class RouteResult:
    incident_id: str
    lookup: str | None
    action: (
        str  # linked | attached | created | planned | escalated | unknown | retry_later | skipped
    )
    issue_number: int | None = None
    work_id: str | None = None
    execution_id: str | None = None
    plan: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ── router ────────────────────────────────────────────────────


class IssueRouter:
    def __init__(
        self,
        store: Store,
        port: GitHubPort,
        sync: IssueSync,
        *,
        catalog: Catalog,
        clock: Clock,
        wait_seconds: float = 30.0,
    ) -> None:
        if sync.port is not port:
            raise ValueError("router와 sync는 같은 GitHub 포트를 써야 한다")
        self.store = store
        self.port = port
        self.sync = sync
        self.catalog = catalog
        self.clock = clock
        self.scope = sync.routing_scope
        self.route_id = sync.route_id
        self.wait_seconds = wait_seconds
        self._lock = threading.Lock()  # repo 단위 write 직렬화(프로세스 안)

    def logical_key(self, incident: Any) -> str:
        return f"issue:{self.scope}:{incident['repository_id']}:{incident['fingerprint']}"

    # 공개

    def route(self, incident_id: str) -> RouteResult:
        """새 로그 incident 하나를 Issue에 연결하거나 새 Issue를 만든다."""
        with self._lock:
            with self.store.read() as tx:
                incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
                skip = self._skip_reason(tx, incident)
            if skip is not None:
                return RouteResult(incident_id, None, "skipped", detail={"reason": skip})
            refresh = self.sync.poll_once(wait_seconds=self.wait_seconds)  # 생성 직전 delta
            if refresh.error or refresh.mode in ("busy", "backoff"):
                reason = f"refresh_{refresh.error or refresh.mode}"
                lookup = Lookup("LOOKUP_INCOMPLETE", reason=reason, complete=False)
            else:
                lookup = self.lookup(incident)
            if lookup.kind == "NO_MATCH_IN_SCOPE":
                return self._create(incident, lookup)
            return self._apply(incident_id, lookup)

    def route_pending(self, limit: int = 20) -> list[RouteResult]:
        """아직 연결되지 않은 NEW 로그 incident를 오래된 순서로 route한다(rate limit 재시도 등)."""
        with self.store.read() as tx:
            rows = tx.all(
                "SELECT i.id FROM incidents i WHERE i.status = 'NEW' AND i.source_kind = 'LOG'"
                " AND i.routing_scope = ? AND i.repository_id = ? AND NOT EXISTS"
                " (SELECT 1 FROM work_items w WHERE w.incident_id = i.id)"
                " ORDER BY i.first_seen, i.rowid LIMIT ?",
                (self.scope, self.port.repository_id, limit),
            )
        return [self.route(row["id"]) for row in rows]

    def lookup(self, incident: Any) -> Lookup:
        """spec 15 §3.2 순서 1~5. binding·receipt는 GitHub에서 재조회한다(트랜잭션 밖)."""
        gathered = self._gather(incident)
        binding, execution = gathered["binding"], gathered["execution"]
        if binding is not None:
            return self._recheck(binding["issue_number"], "EXISTING_BINDING")
        if execution is not None and execution["status"] != "FAILED":
            if execution["status"] != "SUCCEEDED":
                return Lookup("LOOKUP_INCOMPLETE", reason="create_issue_unknown")
            number = json.loads(execution["result_json"]).get("issue_number")
            return self._recheck_receipt(incident, execution, number)
        structured, similar = self._candidates(incident, gathered["open_issues"])
        if len(structured) == 1:
            return Lookup("STRUCTURED_APPROVED", issue=structured[0], complete=gathered["complete"])
        if structured or similar:
            numbers = sorted(item["number"] for item in structured + similar)
            reason = "multiple_structured" if len(structured) > 1 else "similar_only"
            return Lookup("AMBIGUOUS", candidates=numbers, reason=reason)
        if not gathered["complete"]:
            return Lookup("LOOKUP_INCOMPLETE", reason="mirror_incomplete", complete=False)
        return Lookup("NO_MATCH_IN_SCOPE", complete=True)

    def candidates(self, incident_id: str) -> dict[str, Any]:
        """운영자 화면용: GitHub를 부르지 않고 DB의 binding·intent·후보·완전성만 보여 준다."""
        with self.store.read() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            if incident is None or incident["routing_scope"] != self.scope:
                raise ApiError("RESOURCE_NOT_FOUND")
        gathered = self._gather(incident)
        structured, similar = self._candidates(incident, gathered["open_issues"])
        binding, execution = gathered["binding"], gathered["execution"]
        return {
            "incident_id": incident_id,
            "fingerprint": fingerprint_value(incident),
            "binding": (
                {"issue_number": binding["issue_number"], "basis": binding["basis"]}
                if binding is not None
                else None
            ),
            "create_issue": (
                {"execution_id": execution["id"], "status": execution["status"]}
                if execution is not None
                else None
            ),
            "candidates": [
                {
                    "issue_number": item["number"],
                    "title": str(item.get("title"))[:200],
                    "match": match,
                }
                for match, items in (("structured", structured), ("similar", similar))
                for item in items
            ],
            "mirror": {
                "complete": gathered["complete"],
                "activated_at": gathered["state"].get("activated_at"),
                "checkpoint": gathered["state"].get("checkpoint"),
            },
        }

    def operator_bind(
        self,
        incident_id: str,
        issue_number: int,
        expected_version: int,
        note: str,
        operator_scope: str,
    ) -> dict[str, Any]:
        """운영자가 등록 repo의 Issue 번호를 명시적으로 연결한다(basis=OPERATOR)."""
        with self.store.read() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        if (
            incident is None
            or incident["routing_scope"] != self.scope
            or incident["repository_id"] != self.port.repository_id
        ):
            raise ApiError("RESOURCE_NOT_FOUND")
        self._check_version(incident, expected_version)
        try:
            issue = self.port.get_issue(issue_number).data
        except NotFound:
            raise ApiError("RESOURCE_NOT_FOUND") from None
        except GitHubError as exc:
            raise ApiError("LOOKUP_INCOMPLETE", {"error": type(exc).__name__}) from None
        if is_pull_request(issue) or not self.sync.same_repository(issue):
            raise ApiError("INVALID_REQUEST", {"reason": "not_an_issue_of_registered_repository"})
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            self._check_version(incident, expected_version)
            _, issue_row, _ = self.sync.upsert_mirror(tx, issue)
            previous = self._binding(tx, incident)
            decision = {
                "result": "OPERATOR",
                "note": note,
                "operator": operator_scope,
                "previous_issue_number": previous["issue_number"] if previous else None,
            }
            self._bind(tx, incident, issue_row["issue_number"], "OPERATOR", decision)
            work = None
            created = False
            if (
                incident["status"] == "NEW"
                and issue_row["state"] == "open"
                and tx.one("SELECT 1 FROM work_items WHERE incident_id = ?", (incident_id,)) is None
            ):
                authorization = {
                    "policy": "router",
                    "basis": "operator_binding",
                    "operator": operator_scope,
                    "auto_start_eligible": False,
                }
                work, created = supervisor.ensure_work(
                    tx, incident, issue_row, authorization=authorization
                )
            self._audit(
                tx,
                incident,
                "ISSUE_BOUND_BY_OPERATOR",
                {
                    **decision,
                    "issue_number": issue_row["issue_number"],
                    "work_id": work and work["id"],
                },
            )
        return {
            "incident_id": incident_id,
            "issue_number": issue_row["issue_number"],
            "basis": "OPERATOR",
            "previous_issue_number": decision["previous_issue_number"],
            "work_id": work["id"] if work is not None else None,
            "work_created": created,
            "retry_required": incident["status"] != "NEW",  # 멈춘 사건은 W25 retry로 새 incident
        }

    def reconcile_create_issue(self, execution_id: str) -> dict[str, Any]:
        """결과 불명 CREATE_ISSUE를 조회만으로 조정한다. 새 POST는 하지 않는다."""
        with self.store.read() as tx:
            execution = tx.one("SELECT * FROM executions WHERE id = ?", (execution_id,))
            incident = (
                tx.one("SELECT * FROM incidents WHERE id = ?", (execution["incident_id"],))
                if execution is not None
                else None
            )
        if execution is None or execution["operation"] != "CREATE_ISSUE" or incident is None:
            raise ValueError("CREATE_ISSUE execution이 아니다")
        if execution["status"] != "UNKNOWN":
            return {
                "execution_id": execution_id,
                "outcome": "NOT_UNKNOWN",
                "status": execution["status"],
            }
        expected = marker(incident["routing_scope"], fingerprint_value(incident), execution_id)
        intended = _parse(execution["intended_at"])
        matches: list[dict[str, Any]] = []
        complete = False
        error = None
        try:
            bot_id = self._bot_id()
            for page in range(1, self.sync.intake.max_pages + 1):
                response = self.port.list_issues(
                    state="all",
                    since=_gh(intended - RECONCILE_BEFORE),
                    sort="updated",
                    direction="asc",
                    per_page=self.sync.intake.per_page,
                    page=page,
                )
                for item in response.data or []:
                    if (
                        isinstance(item, dict)
                        and not is_pull_request(item)
                        and self.sync.same_repository(item)
                        and (item.get("user") or {}).get("id")
                        == bot_id  # 다른 작성자의 marker는 무시
                        and expected in (item.get("body") or "")
                        and intended - RECONCILE_BEFORE
                        <= _parse(item["created_at"])
                        <= intended + RECONCILE_AFTER
                    ):
                        matches.append(item)
                if not response.has_next:
                    complete = True
                    break
        except GitHubError as exc:
            error = type(exc).__name__
        if error is None and len(matches) == 1:
            return self._adopt(execution_id, incident["id"], matches[0])
        if error is not None:
            outcome = "INCONCLUSIVE"
        elif len(matches) > 1:
            outcome = "CONFLICT"
        else:
            outcome = "CONFIRMED_ABSENT" if complete else "INCONCLUSIVE"
        record = {
            "outcome": outcome,
            "matches": sorted(item["number"] for item in matches),
            "complete": complete,
            "error": error,
        }
        with self.store.tx() as tx:
            row = tx.one("SELECT result_json FROM executions WHERE id = ?", (execution_id,))
            result = {
                **json.loads(row["result_json"]),
                "reconcile": {**record, "checked_at": tx.now},
            }
            tx.execute(
                "UPDATE executions SET result_json = ?, updated_at = ? WHERE id = ?",
                (canonical_dumps(result), tx.now, execution_id),
            )
            self._audit(
                tx, incident, "ISSUE_CREATE_RECONCILED", {"execution_id": execution_id, **record}
            )
        return {"execution_id": execution_id, **record}

    # lookup 도우미

    def _skip_reason(self, tx: Tx, incident: Any) -> str | None:
        if incident is None:
            return "not_found"
        if incident["status"] != "NEW":
            return "not_new"
        if incident["source_kind"] != "LOG":
            return "not_log_incident"  # Issue 기반 incident는 이미 자기 Issue가 있다
        if incident["routing_scope"] != self.scope:
            return "other_scope"
        if incident["repository_id"] != self.port.repository_id:
            return "other_repository"
        if tx.one("SELECT 1 FROM work_items WHERE incident_id = ?", (incident["id"],)) is not None:
            return "already_routed"
        return None

    def _binding(self, tx: Tx, incident: Any) -> Any:
        return tx.one(
            "SELECT * FROM issue_bindings WHERE routing_scope = ? AND repository_id = ?"
            " AND fingerprint_version = ? AND problem_fingerprint = ?",
            (
                self.scope,
                incident["repository_id"],
                incident["fingerprint_version"],
                incident["fingerprint"],
            ),
        )

    def _gather(self, incident: Any) -> dict[str, Any]:
        with self.store.read() as tx:
            state = checkpoints.get(tx, INTEGRATION_ID, self.sync.state_key) or {}
            rows = tx.all(
                "SELECT payload_json FROM github_issues WHERE repository_id = ? AND state = 'open'"
                " ORDER BY issue_number",
                (incident["repository_id"],),
            )
            return {
                "binding": self._binding(tx, incident),
                "execution": tx.one(
                    "SELECT * FROM executions WHERE logical_key = ?", (self.logical_key(incident),)
                ),
                "open_issues": [json.loads(row["payload_json"]) for row in rows],
                "state": state,
                "complete": bool(state.get("activated_at") and state.get("mirror_complete")),
            }

    def _candidates(
        self, incident: Any, open_issues: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(승인 form 정확 일치, 토큰이 겹치는 후보). 로그 본문은 보지 않는다."""
        own_fp = fingerprint_value(incident)
        bot_id = self._bot_id(cached_only=True)
        tokens = _tokens(incident)
        structured: list[dict[str, Any]] = []
        similar: list[dict[str, Any]] = []
        for item in open_issues:
            if is_pull_request(item):
                continue
            body, title = item.get("body") or "", item.get("title") or ""
            author_id = (item.get("user") or {}).get("id")
            found = MARKER_RE.search(body)
            if (
                found is not None
                and bot_id is not None
                and author_id == bot_id
                and (found.group("scope"), found.group("fp")) != (self.scope, own_fp)
            ):
                continue  # 우리 봇이 다른 scope·다른 문제로 만든 Issue
            form = parse_form(body)
            if form["service"] == incident["service"] and form["signature"] == own_fp:
                if self.catalog.is_trusted_author(author_id):
                    structured.append(item)
                else:
                    similar.append(item)  # 승인되지 않은 작성자의 정확한 form은 운영자가 판단
                continue
            text = f"{title}\n{body}".casefold()
            if sum(token.casefold() in text for token in tokens) >= 2:
                similar.append(item)
        return structured, similar

    def _recheck(self, number: int | None, kind: str) -> Lookup:
        if not isinstance(number, int) or number < 1:
            return Lookup("LOOKUP_INCOMPLETE", reason="receipt_without_issue_number")
        try:
            issue = self.port.get_issue(number).data
        except NotFound:
            return Lookup("LOOKUP_INCOMPLETE", reason="bound_issue_not_found")
        except GitHubError as exc:
            return Lookup("LOOKUP_INCOMPLETE", reason=f"github_{type(exc).__name__}")
        if (
            not isinstance(issue, dict)
            or is_pull_request(issue)
            or not self.sync.same_repository(issue)
        ):
            return Lookup("LOOKUP_INCOMPLETE", reason="bound_issue_not_in_repository")
        return Lookup(kind, issue=issue)

    def _recheck_receipt(self, incident: Any, execution: Any, number: int | None) -> Lookup:
        lookup = self._recheck(number, "MANAGED_RECEIPT")
        if lookup.issue is None:
            return lookup
        expected = marker(self.scope, fingerprint_value(incident), execution["id"])
        try:
            bot_id = self._bot_id()
        except GitHubError as exc:
            return Lookup("LOOKUP_INCOMPLETE", reason=f"github_{type(exc).__name__}")
        issue = lookup.issue
        if (issue.get("user") or {}).get("id") != bot_id or expected not in (
            issue.get("body") or ""
        ):
            return Lookup("LOOKUP_INCOMPLETE", reason="receipt_mismatch")
        return lookup

    def _bot_id(self, cached_only: bool = False) -> int | None:
        with self.store.read() as tx:
            state = checkpoints.get(tx, INTEGRATION_ID, self.sync.state_key) or {}
        if state.get("bot_id") is not None or cached_only:
            return state.get("bot_id")
        return self.port.get_identity().data["id"]

    # 결과 적용

    def _apply(self, incident_id: str, lookup: Lookup) -> RouteResult:
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            if self._skip_reason(tx, incident) is not None:
                return RouteResult(
                    incident_id, lookup.kind, "skipped", detail={"reason": "state_changed"}
                )
            if lookup.kind not in REUSE_KINDS:
                self._escalate(tx, incident, lookup)
                return RouteResult(incident_id, lookup.kind, "escalated", detail=lookup.decision())
            assert lookup.issue is not None
            _, issue_row, _ = self.sync.upsert_mirror(tx, lookup.issue)
            if issue_row["state"] != "open":  # closed_issue_policy = require_operator
                closed = Lookup(lookup.kind, issue=lookup.issue, reason="linked_issue_closed")
                self._escalate(tx, incident, closed, code="PERMISSION_REQUIRED")
                return RouteResult(
                    incident_id,
                    lookup.kind,
                    "escalated",
                    issue_row["issue_number"],
                    detail=closed.decision(),
                )
            if lookup.kind != "EXISTING_BINDING":
                self._bind(tx, incident, issue_row["issue_number"], lookup.kind, lookup.decision())
            return self._link_work(tx, incident, issue_row, lookup)

    def _link_work(self, tx: Tx, incident: Any, issue_row: Any, lookup: Lookup) -> RouteResult:
        authorization = {
            "policy": "router",
            "basis": lookup.kind.lower(),
            "auto_start_eligible": False,
        }
        work, created = supervisor.ensure_work(tx, incident, issue_row, authorization=authorization)
        payload = {**lookup.decision(), "work_id": work["id"], "work_created": created}
        # 활성 work가 이미 있으면(예: Issue 기반 work) 복제하지 않고 이 사건을 그 work에 잇는다
        self._audit(
            tx, incident, "ISSUE_LINKED" if created else "INCIDENT_ATTACHED_TO_WORK", payload
        )
        return RouteResult(
            incident["id"],
            lookup.kind,
            "linked" if created else "attached",
            issue_row["issue_number"],
            work["id"],
            detail=lookup.decision(),
        )

    def _bind(
        self, tx: Tx, incident: Any, number: int, basis: str, decision: dict[str, Any]
    ) -> None:
        tx.execute(
            "INSERT INTO issue_bindings(routing_scope, repository_id, fingerprint_version,"
            " problem_fingerprint, issue_number, basis, decision_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(routing_scope, repository_id,"
            " fingerprint_version, problem_fingerprint) DO UPDATE SET"
            " issue_number = excluded.issue_number, basis = excluded.basis,"
            " decision_json = excluded.decision_json, created_at = excluded.created_at",
            (
                self.scope,
                incident["repository_id"],
                incident["fingerprint_version"],
                incident["fingerprint"],
                number,
                basis,
                canonical_dumps(decision),
                tx.now,
            ),
        )

    def _escalate(self, tx: Tx, incident: Any, lookup: Lookup, code: str | None = None) -> None:
        """work 없이 멈춘다: incident NEW→ESCALATED(router)와 `notify:intake` WORK_BLOCKED."""
        blocker = code or (
            "LOOKUP_INCOMPLETE" if lookup.kind == "LOOKUP_INCOMPLETE" else "PERMISSION_REQUIRED"
        )
        reason_code = lookup.kind if code is None else code
        transition_incident(
            tx,
            incident["id"],
            incident["version"],
            "ESCALATED",
            Actor.ROUTER,
            reason_code,
            details=lookup.decision(),
        )
        details = json.loads(incident["details_json"] or "{}")
        explain = {
            "AMBIGUOUS": "대응 Issue 후보가 여럿이거나 제목·본문만 비슷해 자동으로 연결하지 않았다",
            "LOOKUP_INCOMPLETE": "등록 repo Issue 조회가 완전하지 않아 '없음'으로 판단하지 않았다",
        }.get(lookup.kind, "연결할 Issue가 닫혀 있어 운영자 결정이 필요하다")
        report = blocker_report(
            blocker_code=blocker,
            stage="intake",
            incident=incident,
            work=None,
            symptom_impact=observed_symptom(details) or f"{incident['service']} 오류 반복 관찰",
            evidence_ids=evidence.evidence_ids(tx, incident["run_id"], incident["id"]),
            owner_route_id=self.route_id,
            observed_at=tx.now,
            missing_requirements=["연결할 Issue 결정(운영자 issue-binding) 또는 조회 복구"],
            operator_next_step=["후보 Issue를 확인하고 연결하거나 새 Issue 생성을 결정"],
            retry_condition="운영자가 Issue를 연결하거나 조회가 복구된 뒤 새 incident로 재시도",
            reason_detail=explain,
        )
        outbox.enqueue(
            tx,
            None,
            "WORK_BLOCKED",
            report,
            self.route_id,
            run_id=incident["run_id"],
            incident_id=incident["id"],
        )
        self._audit(tx, incident, "ISSUE_LOOKUP", lookup.decision())

    # 신규 생성

    def _create(self, incident: Any, lookup: Lookup) -> RouteResult:
        incident_id = incident["id"]
        if not self.port.write_enabled:  # shadow: intent·POST 없이 계획만
            with self.store.tx() as tx:
                counts = self._evidence_counts(tx, incident)
                title, body = issue_text(incident, self.scope, "EXE-000000000000", counts)
                plan = {
                    "title": title,
                    "body": body,
                    "labels": [LABEL],
                    "logical_key": self.logical_key(incident),
                }
                self._audit(
                    tx,
                    incident,
                    "ISSUE_CREATE_PLANNED",
                    {"title": title, "logical_key": plan["logical_key"]},
                )
            return RouteResult(
                incident_id, lookup.kind, "planned", plan=plan, detail=lookup.decision()
            )
        intended = self._intend(incident)
        if intended is None:  # 이미 intent가 있다: 두 번째 POST 금지
            incomplete = Lookup("LOOKUP_INCOMPLETE", reason="create_issue_unknown")
            return self._apply(incident_id, incomplete)
        execution_id, title, body = intended
        try:
            response = self.port.create_issue(title, body, [LABEL])
        except Unknown as exc:
            return self._record_unknown(incident_id, execution_id, exc)
        except RateLimited as exc:
            self._record_failed(
                incident_id, execution_id, "RateLimited", {"retry_after": exc.retry_after}
            )
            return RouteResult(incident_id, lookup.kind, "retry_later", execution_id=execution_id)
        except GitHubError as exc:
            code = FAILURE_CODES.get(type(exc), "LOOKUP_INCOMPLETE")
            self._record_failed(incident_id, execution_id, type(exc).__name__, {}, escalate=code)
            return RouteResult(
                incident_id,
                lookup.kind,
                "escalated",
                execution_id=execution_id,
                detail={"code": code},
            )
        assert isinstance(response, GitHubResponse)
        issue = response.data
        try:
            confirmed = self.port.get_issue(issue["number"]).data  # 직접 조회로 확인
        except GitHubError:
            confirmed = None
        return self._record_created(incident_id, execution_id, issue, confirmed)

    def _evidence_counts(self, tx: Tx, incident: Any) -> dict[str, int]:
        rows = tx.all(
            "SELECT kind, COUNT(*) AS n FROM evidence WHERE run_id = ? AND incident_id = ?"
            " GROUP BY kind",
            (incident["run_id"], incident["id"]),
        )
        return {row["kind"]: row["n"] for row in rows}

    def _intend(self, incident: Any) -> tuple[str, str, str] | None:
        key = self.logical_key(incident)
        with self.store.tx() as tx:
            existing = tx.one("SELECT * FROM executions WHERE logical_key = ?", (key,))
            if existing is not None and existing["status"] != "FAILED":
                return None
            execution_id = existing["id"] if existing is not None else new_id("EXE")
            title, body = issue_text(
                incident, self.scope, execution_id, self._evidence_counts(tx, incident)
            )
            request = {
                "title": title,
                "labels": [LABEL],
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
            request_sha = hashlib.sha256(canonical_dumps(request).encode()).hexdigest()
            if existing is None:
                tx.execute(
                    "INSERT INTO executions(id, run_id, incident_id, work_id, proposal_id,"
                    " operation, logical_key, idempotency_key, request_sha256, status, stage,"
                    " intended_at, updated_at, request_json, result_json)"
                    " VALUES (?, ?, ?, NULL, NULL, 'CREATE_ISSUE', ?, ?, ?, 'INTENDED',"
                    " 'intended', ?, ?, ?, '{}')",
                    (
                        execution_id,
                        incident["run_id"],
                        incident["id"],
                        key,
                        key,
                        request_sha,
                        tx.now,
                        tx.now,
                        canonical_dumps(request),
                    ),
                )
            else:  # 확정 거절(FAILED) 뒤 재시도: 같은 intent·marker를 다시 쓴다
                tx.execute(
                    "UPDATE executions SET status = 'INTENDED', stage = 'intended',"
                    " incident_id = ?, intended_at = ?, updated_at = ?, request_json = ?,"
                    " request_sha256 = ? WHERE id = ? AND status = 'FAILED'",
                    (
                        incident["id"],
                        tx.now,
                        tx.now,
                        canonical_dumps(request),
                        request_sha,
                        execution_id,
                    ),
                )
            self._audit(
                tx,
                incident,
                "ISSUE_CREATE_INTENDED",
                {"execution_id": execution_id, "logical_key": key},
            )
        return execution_id, title, body

    def _set_execution(
        self, tx: Tx, execution_id: str, status: str, stage: str, result: dict[str, Any]
    ) -> None:
        tx.execute(
            "UPDATE executions SET status = ?, stage = ?, updated_at = ?, result_json = ?"
            " WHERE id = ? AND status IN ('INTENDED', 'UNKNOWN')",
            (status, stage, tx.now, canonical_dumps(result), execution_id),
        )

    def _record_unknown(self, incident_id: str, execution_id: str, exc: Unknown) -> RouteResult:
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            result = {"observation": exc.observation, "request_sent": exc.request_sent}
            self._set_execution(tx, execution_id, "UNKNOWN", "create_result_unknown", result)
            transition_incident(
                tx,
                incident_id,
                incident["version"],
                "EXECUTION_UNKNOWN",
                Actor.ROUTER,
                "EXTERNAL_RESULT_UNKNOWN",
                details={"execution_id": execution_id},
            )
            self._audit(
                tx, incident, "ISSUE_CREATE_UNKNOWN", {"execution_id": execution_id, **result}
            )
        return RouteResult(incident_id, "NO_MATCH_IN_SCOPE", "unknown", execution_id=execution_id)

    def _record_failed(
        self,
        incident_id: str,
        execution_id: str,
        error: str,
        extra: dict[str, Any],
        escalate: str | None = None,
    ) -> None:
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            self._set_execution(
                tx, execution_id, "FAILED", "create_rejected", {"error": error, **extra}
            )
            self._audit(
                tx, incident, "ISSUE_CREATE_FAILED", {"execution_id": execution_id, "error": error}
            )
            if escalate is not None and incident["status"] == "NEW":
                failed = Lookup("NO_MATCH_IN_SCOPE", reason=f"create_{error}", complete=True)
                self._escalate(tx, incident, failed, code=escalate)

    def _record_created(
        self,
        incident_id: str,
        execution_id: str,
        issue: dict[str, Any],
        confirmed: dict[str, Any] | None,
    ) -> RouteResult:
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            result = {
                "issue_number": issue["number"],
                "node_id": issue["node_id"],
                "issue_id": issue["id"],
                "html_url": issue.get("html_url"),
                "confirmed": confirmed is not None,
            }
            self._set_execution(tx, execution_id, "SUCCEEDED", "created", result)
            _, issue_row, _ = self.sync.upsert_mirror(tx, confirmed or issue)
            decision = {
                "result": "CREATED",
                "execution_id": execution_id,
                "confirmed": confirmed is not None,
            }
            self._bind(tx, incident, issue["number"], "CREATED", decision)
            self._audit(
                tx, incident, "ISSUE_CREATED", {**decision, "issue_number": issue["number"]}
            )
            if incident["status"] != "NEW":
                return RouteResult(
                    incident_id,
                    "NO_MATCH_IN_SCOPE",
                    "created",
                    issue["number"],
                    execution_id=execution_id,
                )
            linked = self._link_work(
                tx, incident, issue_row, Lookup("NO_MATCH_IN_SCOPE", issue=issue, complete=True)
            )
        return dataclasses.replace(linked, action="created", execution_id=execution_id)

    def _adopt(self, execution_id: str, incident_id: str, issue: dict[str, Any]) -> dict[str, Any]:
        """조정에서 정확히 하나를 찾았다: SUCCEEDED·binding(MANAGED_RECEIPT)·incident NEW·work."""
        with self.store.tx() as tx:
            incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            result = {
                "issue_number": issue["number"],
                "node_id": issue["node_id"],
                "issue_id": issue["id"],
                "html_url": issue.get("html_url"),
                "confirmed": True,
                "reconcile": {"outcome": "FOUND", "checked_at": tx.now},
            }
            self._set_execution(tx, execution_id, "SUCCEEDED", "reconciled", result)
            _, issue_row, _ = self.sync.upsert_mirror(tx, issue)
            decision = {
                "result": "MANAGED_RECEIPT",
                "execution_id": execution_id,
                "reconciled": True,
            }
            self._bind(tx, incident, issue["number"], "MANAGED_RECEIPT", decision)
            work_id = None
            if incident["status"] == "EXECUTION_UNKNOWN":
                transition_incident(
                    tx,
                    incident_id,
                    incident["version"],
                    "NEW",
                    Actor.RECONCILER,
                    details={"execution_id": execution_id, "issue_number": issue["number"]},
                )
                incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
                authorization = {
                    "policy": "router",
                    "basis": "managed_receipt",
                    "auto_start_eligible": False,
                }
                work, _ = supervisor.ensure_work(
                    tx, incident, issue_row, authorization=authorization
                )
                work_id = work["id"]
            self._audit(
                tx,
                incident,
                "ISSUE_CREATE_RECONCILED",
                {"execution_id": execution_id, "outcome": "FOUND", "issue_number": issue["number"]},
            )
        return {
            "execution_id": execution_id,
            "outcome": "FOUND",
            "issue_number": issue["number"],
            "work_id": work_id,
        }

    # 공통

    def _check_version(self, incident: Any, expected: int) -> None:
        if incident["version"] != expected:
            raise ApiError(
                "STATE_CONFLICT",
                {"current_status": incident["status"], "current_version": incident["version"]},
            )
        if incident["status"] not in ("NEW", "ESCALATED"):
            raise ApiError("STATE_CONFLICT", {"current_status": incident["status"]})

    def _audit(self, tx: Tx, incident: Any, event: str, payload: dict[str, Any]) -> None:
        audit.append(tx, incident["run_id"], incident["id"], Actor.ROUTER, event, payload)
