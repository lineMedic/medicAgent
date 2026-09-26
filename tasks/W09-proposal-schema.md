# W09 — 제안 schema·접수(B01~B06)·정비 초안·escalate

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A |
| 선행 | W06, W07, W08 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 03 §3·§4](../spec/docs/03-api-contracts.md), [spec 06 §1·§7·§10](../spec/docs/06-broker-runner.md), [spec 16 §6](../spec/docs/16-notifications.md) |
| 참조 | [docs/04 §3](../docs/04-api-reference.md), [docs/03 §4·§5·§8](../docs/03-domain-model.md), [docs/05 ⑤](../docs/05-workflows.md), DECISIONS D44·D58 |
| 요구·테스트 | INV-05, INV-10 / T-V4-01, 계약 테스트 |

## 목표

`POST /tools/proposals`가 union 3종을 엄격히 검증해 202로 접수하고, 백그라운드 브로커가 B01~B06을 거쳐 `create_work_order_draft`는 초안으로, `escalate`는 차단으로 처리한다. `create_pr`의 패치 검사는 W10에서 붙인다.

## 만들 파일

- `linemedic/control_plane/broker/proposals.py` — pydantic 모델: 공통 필드 + `action` discriminated union(`create_pr`, `create_work_order_draft`, `escalate`). `extra="forbid"`, `strict=True`, 길이·개수 제한. 금지 필드(`actions`, `confidence`, `actor`, `role`, `status`, `model`, `policy_version`)는 extra로 거부됨
- `linemedic/control_plane/broker/intake.py` — 동기 검사(principal 범위·work·attempt·**시작 알림 ACCEPTED 여부**·Issue scope·크기·구조) → proposal `RECEIVED` 저장·incident `INVESTIGATING→VALIDATING` → 202. 백그라운드 worker: `CHECKING` → B03~B06 → 액션별 처리 → `ALLOWED`/`REJECTED`. 수정 1회·제출 합산 2회
- `linemedic/control_plane/broker/work_order.py` — 초안: `equipment_id, symptom, probable_cause, evidence_ids, manual_ref_id, open_questions, review_required: true, delivery_status: not_sent` + 승인 템플릿 문구. execution `DRAFT_WORK_ORDER`(logical_key `draft:<work_id>`) SUCCEEDED, incident `WORK_ORDER_DRAFTED`, work `HANDED_OFF`, `HANDOFF_DRAFTED` outbox intent
- escalate 처리 — incident `ESCALATED`, work `BLOCKED(reason)`, blocker report payload(W26 템플릿이 없으면 필드만 채운 dict), `WORK_BLOCKED` outbox intent
- `linemedic/control_plane/tools_api.py` 추가 — `submit_proposal`, `get_proposal`
- `linemedic/contracts/api/*.json` — pydantic에서 생성한 JSON Schema(`make api-schema`)
- 테스트: `unit/test_proposal_schema.py`, `integration/test_proposal_intake.py`

## 구현 단계

1. schema 테스트를 먼저 쓴다(아래 수용 기준).
2. 동기 검사와 202 경로를 구현한다. 202 응답은 `{"proposal_id", "decision": "RECEIVED"}`이며 "실행됨"을 뜻하는 필드를 넣지 않는다.
3. 백그라운드 worker를 같은 프로세스의 루프로 구현한다(DB의 RECEIVED를 가져가 처리). 재시작 시 CHECKING 상태는 외부 intent가 없으면 다시 검사하고, 있으면 UNKNOWN 규칙을 따른다.
4. B03(증거가 같은 run·incident 또는 현재 incident의 history projection), B04(기존 execution·초안), B05(민감 값), B06(직전 version 재확인)을 구현한다.
5. draft·escalate 처리를 구현한다. `create_pr`는 W10 전까지 패치 검사 모듈이 없으므로 `REJECTED(PROTECTION_UNAVAILABLE)`로 처리한다. 가짜 PASS나 임시 PR 생성 경로를 넣지 않는다. W10이 이 분기를 실제 검사로 바꾼다.

## 수용 기준

- T-V4-01: `schema_version: linemedic.v2` → 422, 다른 work·incident의 ID → 403/409.
- `equipment` + `create_pr` → 거절. `unknown` + `create_work_order_draft` → 거절. `code_bug` + `create_pr` + 증거 0개 → 거절. `escalate` + 증거 0개 → 허용.
- `actions: [...]`, `confidence`, 중복 key, 128 KiB 초과 → 422/413.
- 시작 알림이 ACCEPTED가 아닌 work의 제안 → 409 `START_NOTICE_UNCONFIRMED`.
- 같은 Idempotency-Key 재전송은 제출 횟수를 늘리지 않는다. 세 번째 서로 다른 제출 → 거부.
- 초안에 자유 절차·제어값·URL·수신자 필드가 들어오면 422. 등록되지 않은 equipment·manual_ref → 거절.
- draft 처리 후 `delivery_status`는 `not_sent`이고 incident는 복구 상태가 아니다.

## 금지·함정

- 202를 성공으로 보고하는 client 코드를 만들지 않는다.
- 브로커가 모델 문장을 작업자 지시로 그대로 옮기지 않는다. 안내 문구는 승인 템플릿에서만.
