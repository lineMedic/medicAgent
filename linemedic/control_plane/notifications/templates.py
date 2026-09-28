"""알림 본문 템플릿 (W26, spec 16 §2·§6·§7, spec templates/blocker-report.md).

- 이벤트 7종의 한국어 본문. 모두 "이 메시지가 뜻하지 않는 것"을 적는다
  (예: PR_READY는 업무 복구 미확인).
- 차단 보고는 host가 기록한 필드(docs/03 §5의 10개)만으로 완성한다. 모델이 없어도 만들 수 있다.
  에이전트 요약은 "검증되지 않은 판단"으로 따로 표시한다.
- 링크는 서버가 등록 repo의 Issue·PR 번호로만 만든다. payload의 문자열(모델·로그에서 온 값 포함)은
  비밀 마스킹 → 멘션 무력화 → 등록 repo 밖 URL 무력화 → HTML 이스케이프를 거친다.
- 끝에 알림 marker `<!-- linemedic:notify id=NOT-... h=<payload_sha256 앞 16자> -->`를 붙인다.
  reconcile은 봇 작성자 + marker + 본문 hash가 모두 맞는 댓글만 우리 receipt로 본다.
- "읽음"·"배달" 같은 표현을 쓰지 않는다. 댓글 등록은 사람이 읽었다는 뜻이 아니다.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from linemedic.common.sanitize import sanitize_text

ALLOWED_ACTIONS = "create_pr, create_work_order_draft, escalate"
STATUS_LABELS = {
    "PENDING": "전송 대기",
    "SENDING": "전송 확인 중",
    "FAILED": "전송 실패",
    "UNKNOWN": "전송 여부 미확인",
}
ACCEPTED_LABELS = {"github_comment": "댓글 등록", "smtp": "메일 서버 접수"}


def status_label(status: str, adapter: str) -> str:
    if status == "ACCEPTED":
        return ACCEPTED_LABELS.get(adapter, "접수")
    return STATUS_LABELS.get(status, status)


def notify_marker(notification_id: str, payload_sha256: str) -> str:
    return f"<!-- linemedic:notify id={notification_id} h={payload_sha256[:16]} -->"


@dataclass(frozen=True)
class Rendered:
    title: str
    body: str

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


class _Text:
    """등록 repo 기준으로 비신뢰 문자열을 정제한다."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def __call__(self, value: Any, limit: int = 1000) -> str:
        return sanitize_text(str(value if value is not None else ""), self.repo)[:limit]

    def items(self, values: Any, empty: str = "없음") -> str:
        if not isinstance(values, list | tuple) or not values:
            return empty
        return ", ".join(self(v, 300) for v in values[:20])

    def issue_link(self, number: Any) -> str:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            return "Issue 미연결"
        return f"[Issue #{number}](https://github.com/{self.repo}/issues/{number})"

    def pull_link(self, number: Any) -> str:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            return "PR 미확인"
        return f"[PR #{number}](https://github.com/{self.repo}/pull/{number})"


def _lines(values: Iterable[str]) -> str:
    return "\n".join(values)


def _ref(issue: Any) -> str:
    return f"#{issue}" if isinstance(issue, int) and not isinstance(issue, bool) else "미연결"


def _blocked(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    code = t(p.get("blocker_code"), 64)
    title = f"진행 중단 — Issue {_ref(issue)} / {code}"
    if "symptom_impact" not in p:  # 운영자 중단 기록(W06)
        body = _lines(
            [
                f"운영자가 이 작업을 중단했습니다. 사유 코드: {code}.",
                f"- 운영자 메모: {t(p.get('operator_note'))}",
                f"- 중단 전 상태: incident {t(p.get('incident_status_before'), 64)},"
                f" work {t(p.get('work_status_before'), 64)}",
            ]
        )
    else:
        side = p.get("side_effect_state") if isinstance(p.get("side_effect_state"), dict) else {}
        rows = [
            f"- 현상·영향: {t(p.get('symptom_impact'))}",
            f"- 중단 사유: {code} (단계: {t(p.get('stage'), 32)})",
        ]
        if p.get("reason_detail"):
            rows.append(f"- 설명: {t(p.get('reason_detail'))}")
        if p.get("agent_summary"):
            rows.append(f"- 에이전트 판단(검증되지 않음): {t(p.get('agent_summary'))}")
        rows += [
            f"- 실제로 한 일: {t.items(p.get('attempted_actions'))}",
            f"- 확인한 근거: {t.items(p.get('evidence_ids'))}",
            f"- 부작용 상태: {t(side.get('state') or 'UNKNOWN', 16)}"
            f" {t.items(side.get('identities'), '')}".rstrip(),
            f"- 필요한 것: {t.items(p.get('missing_requirements'))}",
            f"- 담당자 다음 단계: {t.items(p.get('operator_next_step'))}",
            f"- 담당 route: {t(p.get('owner_route_id'), 64)}",
            f"- 다시 시작 조건: {t(p.get('retry_condition'))}",
        ]
        body = _lines(rows)
    note = (
        "이 메시지는 코드가 틀렸다거나 원인이 확정됐다는 뜻이 아닙니다. "
        "적힌 일 외에 배포·머지·DB 변경·설비 제어는 하지 않았습니다."
    )
    return Rendered(title, _lines([body, "", note]))


def _starting(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    scope = p.get("scope") if isinstance(p.get("scope"), dict) else {}
    title = f"작업 시작 예정 — Issue {_ref(issue)} / {t(p.get('work_id'), 32)}"
    body = _lines(
        [
            f"{t(scope.get('service'), 64)} 관련 문제를 조사하고, 허용된 경우 테스트·수정안이나"
            " 정비 요청 초안을 작성합니다.",
            "자동 머지·배포는 하지 않습니다.",
            "설비 문제이거나 자료가 부족하면 코드 변경 없이 이유를 남깁니다.",
            f"- 허용 조치: {ALLOWED_ACTIONS}",
            f"- 작업 세대: generation {t(p.get('generation'), 8)}",
            f"- 승인: {t(p.get('approved_by'), 80)}",
            "이 댓글의 등록이 확인된 뒤에 작업을 시작합니다.",
            "",
            "이 메시지는 작업 완료·복구·배포 승인을 뜻하지 않습니다.",
        ]
    )
    return Rendered(title, body)


def _handoff(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    order = p.get("work_order") if isinstance(p.get("work_order"), dict) else {}
    guidance = order.get("guidance") if isinstance(order.get("guidance"), dict) else {}
    title = f"정비 요청 초안 — Issue {_ref(issue)} / 설비 {t(order.get('equipment_id'), 32)}"
    body = _lines(
        [
            f"- 관찰 증상: {t(order.get('symptom'))}",
            f"- 추정 원인(가설, 확정 아님): {t(order.get('probable_cause'))}",
            f"- 확인한 근거: {t.items(order.get('evidence_ids'))}",
            f"- 매뉴얼: {t(order.get('manual_ref_id'), 64)}"
            f" {t.items(guidance.get('section_ids'), '')}",
            f"- 승인 안내 문구: {t(guidance.get('text'))}",
            f"- 안내: {t(guidance.get('disclaimer'))}",
            f"- 확인할 질문: {t.items(order.get('open_questions'))}",
            f"- 현장 전송: 하지 않음({t(order.get('delivery_status') or 'not_sent', 16)})",
            "",
            "이 메시지는 현장 작업 지시·설비 제어·복구 완료가 아닙니다."
            " 실제 점검은 담당자의 승인된 절차로 합니다.",
        ]
    )
    return Rendered(title, body)


def _pr_ready(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    title = f"수정안 준비 — Issue {_ref(issue)} / PR #{t(p.get('pr_number'), 12)}"
    body = _lines(
        [
            f"{t.pull_link(p.get('pr_number'))}:"
            " 보호된 회귀 검사와 재현 검사를 통과한 수정안입니다.",
            "",
            "아직 업무 복구가 확인된 것은 아닙니다. 사람 리뷰·머지와 지정 SHA 배포 승인 후"
            " 별도 업무 검사가 필요합니다.",
        ]
    )
    return Rendered(title, body)


ENVIRONMENT_TEXT = {
    "unchanged": "이전 MES 컨테이너가 그대로 실행 중임을 확인했습니다.",
    "previous_removed": "이전 MES 컨테이너를 지운 뒤 새 컨테이너가 기동하지 못했습니다."
    " 운영자가 execution 기록의 복원 절차를 직접 실행해야 합니다.",
}


def _not_deployed(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    """승인한 수정안을 배포하지 못해 업무 검사를 하지 않은 경우(W12)."""
    environment = p.get("environment")
    title = f"업무 복구 미확인 — Issue {_ref(issue)} / 배포 안 됨"
    body = _lines(
        [
            f"승인한 수정안을 배포하지 못해 업무 계약 {t(p.get('contract_id'), 64)} 검사를"
            f" 하지 않았습니다(단계 {t(p.get('stage'), 32)}, 사유 {t(p.get('reason'), 64)}).",
            f"- 기존 환경: "
            f"{ENVIRONMENT_TEXT.get(environment, f'확인하지 못함({t(environment, 32)})')}",
            "",
            "자동 rollback·자동 재시도·추가 수정은 하지 않습니다. 복구 완료가 아닙니다.",
        ]
    )
    return Rendered(title, body)


def _recovery(t: _Text, p: Mapping[str, Any], issue: Any, verified: bool) -> Rendered:
    if not verified and p.get("verdict") == "NOT_DEPLOYED":
        return _not_deployed(t, p, issue)
    verdict = t(p.get("verdict"), 16)
    observed = f"{t(p.get('samples_completed'), 8)}/{t(p.get('samples_required'), 8)}"
    if verified:
        title = f"업무 복구 확인 — Issue {_ref(issue)}"
        head = f"업무 계약 {t(p.get('contract_id'), 64)} 검사가 PASS입니다(관찰 {observed})."
        note = (
            "이 확인은 지정한 업무 계약의 관찰 범위에 한정되며"
            " 다른 기능의 정상을 보장하지 않습니다."
        )
    else:
        title = f"업무 복구 미확인 — Issue {_ref(issue)} / {verdict}"
        head = (
            f"업무 계약 {t(p.get('contract_id'), 64)} 검사가 {verdict}입니다"
            f"(사유 {t(p.get('reason'), 64)}, 관찰 {observed})."
        )
        note = (
            "이 메시지는 원인이 확정됐다는 뜻이 아닙니다. 추가 수정은 자동으로 시작하지 않습니다."
        )
    body = _lines(
        [head, f"- 검사 기간: {t(p.get('started_at'), 40)} ~ {t(p.get('ended_at'), 40)}", "", note]
    )
    return Rendered(title, body)


def _cancelled(t: _Text, p: Mapping[str, Any], issue: Any) -> Rendered:
    title = f"작업 취소 — Issue {_ref(issue)} / {t(p.get('work_id'), 32)}"
    body = _lines(
        [
            f"취소 전 상태: {t(p.get('work_status_before'), 32)}."
            f" 부작용 상태: {t(p.get('side_effect_state'), 16)}.",
            f"- 메모: {t(p.get('note'))}",
            "",
            "이 메시지는 문제가 해결됐다는 뜻이 아닙니다.",
        ]
    )
    return Rendered(title, body)


def render(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    repo: str,
    issue_number: int | None,
    notification_id: str,
    payload_sha256: str,
) -> Rendered:
    """알림 본문. `issue_number`는 호출자가 DB의 work에서 읽은 값이다(payload 값을 쓰지 않는다)."""
    t = _Text(repo)
    issue = issue_number if isinstance(issue_number, int) else None
    renderers = {
        "WORK_STARTING": lambda: _starting(t, payload, issue),
        "WORK_BLOCKED": lambda: _blocked(t, payload, issue),
        "HANDOFF_DRAFTED": lambda: _handoff(t, payload, issue),
        "PR_READY": lambda: _pr_ready(t, payload, issue),
        "RECOVERY_VERIFIED": lambda: _recovery(t, payload, issue, verified=True),
        "RECOVERY_NOT_VERIFIED": lambda: _recovery(t, payload, issue, verified=False),
        "WORK_CANCELLED": lambda: _cancelled(t, payload, issue),
    }
    if event_type not in renderers:
        raise ValueError(f"알 수 없는 알림 event_type: {event_type}")
    rendered = renderers[event_type]()
    body = _lines(
        [
            f"### {rendered.title}",
            "",
            rendered.body,
            "",
            f"_LineMedic 자동 알림 · {t.issue_link(issue)}_",
            notify_marker(notification_id, payload_sha256),
        ]
    )
    return Rendered(rendered.title, body)
