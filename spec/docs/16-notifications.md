# 16. 작업 시작·진행 불가·결과 알림

> **v4 신규 규범 문서.** ‘상태를 DB에 기록한다’와 ‘사용자에게 알림을 보낸다’를 분리한다. 실제 외부 알림은 설정된 공급자의 접수 증거가 있어야 한다. 읽음 확인·최종 이메일 배달은 다른 수준의 증거다.

## 1. 최소 채널과 교체 경계

core 기본은 **해당 GitHub Issue에 댓글**이다. 시작·차단·PR 준비·검증 결과가 같은 작업 맥락에 남고 별도 메신저 구축이 필요 없다. 댓글 API는 알림을 트리거하지만 실제 이메일·앱 알림은 구독 설정의 영향을 받는다. 댓글 ID를 저장했다고 ‘담당자 메일 배달 완료’라고 표시하지 않는다. [W14](14-decisions-sources.md#w14), [W19](14-decisions-sources.md#w19)

메일이 필수인 팀은 **SMTP 어댑터 하나를 core 채널로 선택**한다. 여러 채널 동시 지원은 P1이다. 관리자 route catalog가 실제 수신자를 결정하며 모델/Issue/로그가 주소를 지정하지 않는다. 동봉 문서는 어댑터 명세이지 Gmail 계정 연동이나 실제 발송이 아니다.

```yaml
notifications:
  schema_version: linemedic.v4
  required_start_route_id: github-issue-primary
  start_wait_seconds: 60
  retry_max_attempts: 3
  routes:
    github-issue-primary:
      adapter: github_comment
      repository_id: 100001
      target: bound_issue
      enabled: true
    ops-mail:
      adapter: smtp
      recipient_config_key: LINEMEDIC_OPS_RECIPIENT
      enabled: false
```

위는 LineMedic 자체 스키마다. 실제 recipient/token/password는 환경·secret store에 두고 문서/DB/case/RAG에 기록하지 않는다. 임의 URL을 보내는 범용 webhook tool을 에이전트에 제공하지 않는다.

## 2. 사건 종류

| event_type | 생성 주체 | 핵심 내용 | 코드 작업을 시작하는가 |
|---|---|---|---|
| `WORK_STARTING` | dispatcher | Issue·영향·조사/수정 예정 범위·승인 경계 | **필수 route 접수 확인 후에만** |
| `WORK_BLOCKED` | broker/dispatcher/operator | 이유·근거·이미 한 일·필요 권한/자료·다음 조치 | 아니오 |
| `PR_READY` | broker | 관련 Issue·PR·candidate·검사 결과·사람 리뷰 필요 | agent 구간 완료, 배포 아님 |
| `HANDOFF_DRAFTED` | broker | 설비 요청 초안·미확정 원인·담당자 확인사항 | 서버 수정/설비 제어 없음 |
| `RECOVERY_VERIFIED` | verifier | 검사 target·contract·범위·PASS 근거 | 이미 판정된 결과 통지 |
| `RECOVERY_NOT_VERIFIED` | verifier/reconciler | FAIL/INCONCLUSIVE와 현재 상태 | 추가 수정 자동 시작 안 함 |
| `WORK_CANCELLED` | dispatcher/operator | 취소 이유·실제 수행 범위 | 아니오 |

`ISSUE_LINKED` 같은 내부 이벤트는 audit에만 기록해도 된다. 모델이 도구를 부를 때마다 외부 메시지를 보내지 않는다. 이벤트별 logical key 하나로 알림 폭주와 bot loop를 줄인다. 중복 로그는 count/최근 시각만 갱신하고 core에서는 같은 시작 메시지를 반복하지 않는다.

## 3. 시작 알림 게이트

```text
원자적 work 선점 + WORK_STARTING outbox INSERT
  → 외부 provider 요청 (DB transaction 밖)
  → provider receipt 저장
  → 필요한 모든 start route ACCEPTED 확인
  → 최신 Issue open/권한/취소/보호조건 재확인
  → work READY → RUNNING + attempt 생성
  → writable workspace 전달 / agent 실행
```

기본 required route는 하나다. 선택 채널이 둘이라면 ‘하나만 되면 시작’인지 ‘모두 필요’인지 별도 정책을 명시해야 하므로 core에서 그런 조합을 만들지 않는다. UI에 표시한 시작 이벤트, 큐 등록, 단순 stdout는 게이트를 열지 못한다.

60초 대기 초기 한도를 넘기거나 명확히 실패하면 work를 BLOCKED, incident를 ESCALATED로 두고 `START_NOTICE_UNCONFIRMED`를 기록한다. 늦게 receipt가 도착해도 이미 끝난 work를 자동 부활시키지 않는다. 재시도는 운영자 승인으로 새 generation이다.

알림 실패 때문에 장애 조사를 할 수 없는 것은 아니다. 로그 수집·읽기 전용 triage·차단 보고 작성은 허용한다. **패치를 만드는 agent attempt와 코드·배포 변경**만 게이트 뒤로 둔다. 이 게이트를 준비하는 Issue 생성·연결·알림 발송은 선행 조정 작업이므로 금지 대상이 아니다.

## 4. outbox 데이터와 상태

DB의 `notifications`가 durable outbox다. 상태 변경과 알림 intent는 동일 SQLite transaction에 저장한다. provider 호출은 commit 이후다. `logical_key = notify:<work_id>:<generation>:<event_type>:<event_revision>:<route_id>`를 서버가 만든다. 같은 키·다른 본문 hash는 conflict다. Issue 연결 전 차단처럼 work가 없는 경우는 `notify:intake:<run_id>:<incident_id>:<event_type>:<event_revision>:<route_id>`를 사용한다. bound Issue가 없고 설정된 별도 route도 없으면 미전송을 표시하며, 외부 알림 성공으로 주장하지 않는다.

| 상태 | 의미 | UI 표현 |
|---|---|---|
| `PENDING` | 발송 의도 저장, 미전송 | 전송 대기 |
| `SENDING` | 호출 intent 이후 처리 중 | 전송 확인 중 |
| `ACCEPTED` | provider 접수 또는 댓글 존재 확인 | 댓글 등록 / 메일 서버 접수 |
| `FAILED` | 접수되지 않았음이 명확하거나 제한 횟수 소진 | 전송 실패 |
| `UNKNOWN` | 접수 여부 판단 불가 | 전송 여부 미확인 |

수신자가 실제 읽었는지 저장하려면 별도 검증 가능한 이벤트가 필요하다. core에 `DELIVERED_TO_HUMAN`, `READ`를 만들지 않는다. 같은 알림 key를 DB로 한 번만 enqueue할 수 있어도 외부 서비스까지 exactly-once 배달을 보장하지 않는다.

### 어댑터 내부 계약 (공식 SDK API 아님)

```text
send(notification_id, route_config, rendered_payload)
  -> ACCEPTED(receipt_id, canonical_ref, accepted_at)
   | REJECTED(reason, retry_after, safe_to_retry)
   | UNKNOWN(observation)

reconcile(notification_id, known_identity)
  -> FOUND(receipt) | CONFIRMED_ABSENT | INCONCLUSIVE | CONFLICT
```

렌더링은 host 템플릿을 사용한다. Issue URL·PR URL은 허용 repo에서 받은 정규 identity로 구성·검사한다. 모델 출력의 임의 URL·@mention·수신 주소는 활성 링크/멘션으로 통과시키지 않는다.

## 5. 네트워크 실패와 재시도

GitHub 댓글에는 알림 ID marker를 넣고 bot 작성자·bound Issue·원본 body hash·provider receipt로 대응한다. timeout이면 동일 Issue 댓글의 관련 페이지를 재조회한다. marker 문자열만으로 다른 사용자의 댓글을 우리 receipt로 채택하지 않는다. 조회가 불완전하면 UNKNOWN을 유지한다.

SMTP는 message-ID를 저장하되 이것이 중복 배달 방지 보장은 아니다. 본문 전송 후 응답이 끊겼으면 UNKNOWN으로 보존하고 공급자 로그/운영자 확인 없이는 재발송하지 않는다. 명시적인 거절·연결 전 실패처럼 접수되지 않았다는 근거가 있을 때만 bounded retry를 한다.

초기 제한은 최대 3번과 점증 backoff, GitHub의 Retry-After/rate-limit 신호가 우선이다. 동일 route에 생성 요청을 직렬화한다. 재시도 예산 소진 후에는 다른 주소를 모델이 찾도록 하지 않는다. 실패 원인과 알림 자체의 미전송 상태를 관리 화면에 표시한다.

**완료 알림 실패는 verifier PASS를 취소하지 않는다.** `incident=RESOLVED / notification=FAILED` 조합은 정상적으로 표현해야 한다. 반대로 알림 성공이 `RESOLVED`를 만들지 않는다.

## 6. 진행 불가 보고의 내용

기계적 실패를 모델 한 번 더 호출해 해석하는 것을 필수 의존성으로 만들지 않는다. 모델 API가 죽어도 Control Plane은 reason과 마지막 관찰로 템플릿을 완성할 수 있어야 한다.

| 필드 | 작성 기준 |
|---|---|
| symptom / impact | 정제된 로그·Issue에 근거한 현상, 추정 영향은 표시 |
| blocker_code | 아래 고정 분류 |
| stage | intake / preflight / agent / validation / external_write / verification |
| attempted_actions | 실제 도구·변경만, 실행하지 않은 제안을 분리 |
| evidence_ids | 해당 사건에서 조회한 근거 |
| side_effect_state | NONE / OBSERVED / UNKNOWN, 실제 identity 포함 |
| missing_requirements | 필요한 자료·권한·승인·지원 기능 |
| operator_next_step | 판단 질문·인계 요청. 실제 현장 작업 지시 아님 |
| owner_route_id | host가 정한 담당 route |
| retry_condition | 무엇이 바뀌어야 새 작업을 허용하는지 |

reason 예: `UNSUPPORTED_ACTION`, `INSUFFICIENT_EVIDENCE`, `CONFLICTING_REQUIREMENTS`, `PERMISSION_REQUIRED`, `HUMAN_WORK_IN_PROGRESS`, `LOOKUP_INCOMPLETE`, `START_NOTICE_UNCONFIRMED`, `MODEL_UNAVAILABLE`, `BUDGET_EXCEEDED`, `VALIDATION_FAILED`, `SOURCE_CHANGED`, `EXTERNAL_RESULT_UNKNOWN`, `VERIFICATION_FAILED`, `OBSERVATION_INCONCLUSIVE`.

이 reason은 work/notification payload 계약이며 기존 incident status를 수십 개로 늘리지 않는다. 양식은 [차단 보고서](../templates/blocker-report.md).

## 7. 메시지 예시

> **작업 시작 예정 — Issue #42 / WORK-001**  
> 불량 집계 오류의 원인을 조사하고, 허용된 경우 테스트·수정안을 작성합니다. 자동 머지·배포는 하지 않습니다. 설비 문제이거나 자료가 부족하면 코드 변경 없이 이유를 남깁니다. 이 메시지의 접수가 확인된 뒤 작업을 시작합니다.

> **진행 중단 — Issue #42 / PERMISSION_REQUIRED**  
> 현재 근거로는 DB 스키마 변경이 필요할 가능성이 있으나, 이 데모는 해당 변경을 지원하지 않습니다. 실제 DB 변경은 수행하지 않았습니다. 확인 자료 EV-12·EV-13과 예상 영향은 연결된 보고서에 있습니다. 담당자가 변경 필요성·권한을 검토한 뒤 새 작업을 승인해야 합니다.

> **수정안 준비 — Issue #42 / PR #51**  
> 보호된 회귀 검사와 재현 검사를 통과한 수정안입니다. 아직 업무 복구가 확인된 것은 아닙니다. 사람 리뷰·머지와 지정 SHA 배포 승인 후 별도 업무 검사가 필요합니다.

위 번호와 결과는 예시다. 실제 데이터와 receipt로 치환하기 전 데모 성공 메시지로 사용하지 않는다.

## 8. 완료 시험

발송 성공 전 writable workspace 없음, 성공 이후 시각 순서, 중복 poll의 시작 댓글 1개, 잘못된 recipient 무시/거절, provider timeout UNKNOWN, 재시작 후 receipt 재조회, 성공 복구+알림 실패 상태 독립, 모델 오류에도 blocker 생성, 외부 알림 채널 실제 1개 접수 증거를 시험한다. UI만 구현하면 FR-20/21/24 미완료다.
