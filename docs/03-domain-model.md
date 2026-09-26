# 03. 도메인 모델 — 식별자·상태·전이·enum·논리 키

> 원본: [spec 04 데이터·상태](../spec/docs/04-data-state.md), [spec 15 §1·§3·§6](../spec/docs/15-issue-intake-workflow.md), [spec 16 §2·§4·§6](../spec/docs/16-notifications.md), [spec 17 §1·§6](../spec/docs/17-case-memory.md), [spec 03 §6](../spec/docs/03-api-contracts.md).
> DDL 원문은 spec 04 §5에 있고 `linemedic/control_plane/migrations/0001_init.sql`에 **그대로** 옮긴다. 이 문서는 DDL이 강제하지 못하는 전이 규칙까지 한곳에 모은 것이다.

## 1. 식별자

| 이름 | 식별 방법 | 의미 |
|---|---|---|
| run | `demo_runs.id` (D50 형식) | 실행·평가 단위. `active=1`은 동시에 하나 |
| routing_scope | `live` 또는 `eval:<run_id>` | 티켓 처리 영역. 평가 격리 수단 (fingerprint에 run을 넣지 않는다) |
| incident | `run_id + incident_id` | 한 run에서 관찰된 장애 |
| GitHub Issue | `repository_id + issue_number` (+ node_id) | 협업 티켓 |
| work item | `routing_scope + repo + issue + generation` | 승인·시작 알림·조사·리뷰 대기의 단일 작업 |
| attempt | `attempt_id` | 한 번의 실제 모델 세션. 예산·sandbox·base 고정 |
| problem_fingerprint | `SHA256(version + service + normalized_error_type + normalized_top_frame + endpoint_or_metric)` | 중복 후보 탐색 키. 같은 원인의 증명이 아님. run·request ID·lot ID·timestamp·줄 번호 제외 |
| provisional fingerprint | `issue:<repo_id>:<issue_number>` | 오류 signature가 없는 Issue 입력용. 빈 signature로 다른 Issue를 합치지 않음 |
| case note | `note_id + revision`, `source_event_key` | 과거 시도와 검증 수준 |

하나의 Issue에는 여러 시기의 incident가 연결될 수 있지만 **동시에 활성 work는 하나**다. 조사 중 들어온 같은 로그는 그 work의 incident에 evidence·count로 합친다.

## 2. Incident 상태

| 상태 | 의미 |
|---|---|
| `NEW` | Issue 연결·승인·시작 알림 대기 포함. agent 미시작 |
| `INVESTIGATING` | 시작 게이트를 통과한 attempt 실행 중 |
| `VALIDATING` | 브로커가 제안 검사 중 |
| `PR_OPENED` | 검토용 PR 존재. 사람 승인 대기 |
| `DEPLOYING` | 승인한 정확한 버전 배포 중 |
| `VERIFYING` | 업무 계약 검사 중 |
| `WORK_ORDER_DRAFTED` | 설비 점검 초안. **미복구** |
| `RESOLVED` | verifier가 정의한 범위에서 PASS |
| `ESCALATED` | 미해결·중단·자료/권한 부족 |
| `EXECUTION_UNKNOWN` | 외부 변경 여부 불명 |

### Incident 전이 권한 (이 표에 없는 전이는 전부 거부)

| 출발 → 도착 | 주체 | 조건 |
|---|---|---|
| NEW → INVESTIGATING | supervisor | work READY, 시작 알림 ACCEPTED, 현재 권한 유효, attempt 미존재 |
| NEW → ESCALATED | router / supervisor | 모호함, 지원 밖, 시작 알림 실패, 취소 |
| NEW → EXECUTION_UNKNOWN | router | CREATE_ISSUE 접수 여부 불명 |
| INVESTIGATING → VALIDATING | broker | 유효 제안, 현재 attempt |
| INVESTIGATING → ESCALATED | supervisor / broker | 실패, 중단, escalate 제안, 예산 초과 |
| VALIDATING → INVESTIGATING | broker | 외부 변경 없음, 수정 1회 남음, 원래 deadline 유지 |
| VALIDATING → PR_OPENED | broker | 실제 PR 생성 확인 |
| VALIDATING → WORK_ORDER_DRAFTED | broker | 로컬 초안 저장 |
| VALIDATING → ESCALATED | broker | 실패가 명확 |
| VALIDATING → EXECUTION_UNKNOWN | broker | 외부 결과 불명 |
| PR_OPENED → DEPLOYING | release executor | 사람 승인, exact SHA, 기대 상태 일치 |
| PR_OPENED → ESCALATED | operator | 외부 변경 없음을 확인한 뒤 중단 |
| DEPLOYING → VERIFYING / ESCALATED / EXECUTION_UNKNOWN | release executor | 실제 결과에 따라 |
| VERIFYING → RESOLVED | **verifier만** | PASS |
| VERIFYING → ESCALATED | verifier | FAIL 또는 INCONCLUSIVE |
| EXECUTION_UNKNOWN → NEW | reconciler | CREATE_ISSUE 결과 하나 확인, binding 복구. 새 시작 게이트 필요 |
| EXECUTION_UNKNOWN → PR_OPENED | reconciler | 기존 CREATE_PR 결과를 정확히 확인 |
| EXECUTION_UNKNOWN → VERIFYING | reconciler | DEPLOY 실제 target 확인 후 새 검사 |
| EXECUTION_UNKNOWN → ESCALATED | reconciler | 무변경·실패·충돌 확인. 자동 재실행 없음 |

- 모든 전이는 `UPDATE ... WHERE id=? AND version=? AND status=?`(CAS)이고 `version`을 1 올린다. 영향 행 0이면 `STATE_CONFLICT`.
- 주체(actor)는 서버 내부 함수가 정한다. 요청 body의 `actor`/`role`/`status`를 믿지 않는다.
- `RESOLVED` 쓰기 함수는 verifier 모듈 안에만 둔다(INV-01).

## 3. Work item 상태

| 상태 | 의미 | 다음 전이 주체 |
|---|---|---|
| `WAITING_APPROVAL` | 등록 범위·actor·지원 범위 확인, 승인 대기 | router / operator |
| `WAITING_NOTIFICATION` | atomic claim 완료, 필수 시작 알림 접수 대기 | notifier / supervisor |
| `READY` | receipt 확인됨, 최종 scope·취소·보호조건 재검사 대기 | supervisor |
| `RUNNING` | agent 또는 broker 처리 중 (전역 1개) | broker / supervisor |
| `WAITING_REVIEW` | PR 생성됨, 사람 리뷰·배포 승인 대기 | release executor / operator |
| `WAITING_VERIFICATION` | 승인 배포·업무 검사 진행 | verifier / reconciler |
| `HANDED_OFF` | 초안 전달, 사람 확인 필요 (terminal) | — |
| `SUCCEEDED` | incident RESOLVED (terminal) | verifier만 |
| `BLOCKED` | 불가 사유로 종료. 새 승인이 있어야 재시도 (terminal) | — |
| `CANCELLED` | 시작 전 또는 부작용 확인 후 취소 (terminal) | operator / supervisor |
| `EXECUTION_UNKNOWN` | 중복 실행 금지, 조정 대기 | reconciler |

### Work 전이 (이 표에 없는 전이는 거부)

| 출발 → 도착 | 주체 | 조건·같은 트랜잭션에서 할 일 |
|---|---|---|
| (생성) → WAITING_APPROVAL | router / issue_sync | binding 확정, `issue_snapshot_sha256`·`authorization_json` 저장 |
| WAITING_APPROVAL → WAITING_NOTIFICATION | operator approve 또는 등록 정책(자동 승인) | version CAS, `one_active_work_per_issue` 통과, `WORK_STARTING` outbox INSERT |
| WAITING_APPROVAL → BLOCKED | router / operator | 지원 밖, `HUMAN_WORK_IN_PROGRESS`, closed Issue, 권한 회수. `WORK_BLOCKED` outbox |
| WAITING_APPROVAL → CANCELLED | operator | 취소 |
| WAITING_NOTIFICATION → READY | notifier / supervisor | 필수 route의 시작 알림 `ACCEPTED` |
| WAITING_NOTIFICATION → BLOCKED | supervisor | 60초 초과 또는 명확한 실패 → `START_NOTICE_UNCONFIRMED`. incident ESCALATED |
| WAITING_NOTIFICATION → CANCELLED | operator | 시작 전 취소 |
| READY → RUNNING | supervisor | **하나의 트랜잭션**: incident NEW→INVESTIGATING, attempt 발급, deadline·budget 설정. 시작 알림 ACCEPTED·Issue scope·취소 flag 재확인 실패 시 전이 안 함 |
| READY → BLOCKED / CANCELLED | supervisor | 재확인 실패(Issue closed, scope 변경 → `ISSUE_SCOPE_CHANGED`, cancel 요청) |
| RUNNING → WAITING_REVIEW | broker | incident PR_OPENED와 함께, `PR_READY` outbox |
| RUNNING → HANDED_OFF | broker | incident WORK_ORDER_DRAFTED와 함께, `HANDOFF_DRAFTED` outbox |
| RUNNING → BLOCKED | broker / supervisor | incident ESCALATED와 함께, `WORK_BLOCKED` outbox |
| RUNNING → EXECUTION_UNKNOWN | broker | 외부 결과 불명 |
| WAITING_REVIEW → WAITING_VERIFICATION | release executor | incident DEPLOYING과 함께 |
| WAITING_REVIEW → BLOCKED | operator | 외부 변경 없음 확인 후 중단 |
| WAITING_VERIFICATION → SUCCEEDED | **verifier만** | incident RESOLVED와 함께, `RECOVERY_VERIFIED` outbox |
| WAITING_VERIFICATION → BLOCKED | verifier / release executor | FAIL·INCONCLUSIVE·배포 실패, `RECOVERY_NOT_VERIFIED` outbox |
| WAITING_VERIFICATION → EXECUTION_UNKNOWN | release executor | 배포 timeout |
| EXECUTION_UNKNOWN → WAITING_REVIEW / WAITING_VERIFICATION / BLOCKED | reconciler | 확인된 실제 결과에 따라 |

- terminal(HANDED_OFF, SUCCEEDED, BLOCKED, CANCELLED)에서 나가는 전이는 없다. 재시도는 운영자 승인으로 **새 incident + 새 generation + 새 시작 알림 + 새 attempt**다.
- 늦게 도착한 receipt가 이미 BLOCKED인 work를 RUNNING으로 되살리지 않는다.
- 슬롯(`one_running_work`)이 차 있으면 다른 READY work는 대기한다. 실패가 아니다.

### 결합 전이 (같은 트랜잭션에서 같이 갱신)

| incident | work | outbox event |
|---|---|---|
| NEW → INVESTIGATING | READY → RUNNING | (없음, WORK_STARTING은 이미 ACCEPTED) |
| → PR_OPENED | → WAITING_REVIEW | PR_READY |
| → WORK_ORDER_DRAFTED | → HANDED_OFF | HANDOFF_DRAFTED |
| → DEPLOYING / VERIFYING | → WAITING_VERIFICATION | (없음) |
| → RESOLVED | → SUCCEEDED | RECOVERY_VERIFIED |
| → ESCALATED | → BLOCKED | WORK_BLOCKED 또는 RECOVERY_NOT_VERIFIED |
| → EXECUTION_UNKNOWN | → EXECUTION_UNKNOWN | (없음) |

알림 상태는 이 결합과 **독립**이다. 알림 FAILED여도 RESOLVED는 유지한다(D39).

## 4. 그 밖의 enum

| 대상 | 값 | 비고 |
|---|---|---|
| incident `source_kind` | `LOG`, `GITHUB_ISSUE`, `OPERATOR`, `VERIFIER` | Issue 기반 incident는 로그 count=0으로 시작할 수 있음 |
| incident `category` | `code_bug`, `equipment`, `config`, `infra`, `external_dependency`, `unknown` | 판단 결과이지 사실 확정 아님 |
| proposal `decision` | `RECEIVED` → `CHECKING` → `ALLOWED` / `REJECTED` | ALLOWED ≠ 외부 실행 완료. attempt당 제출 합산 최대 2회(수정 1회) |
| execution `operation` | `CREATE_ISSUE`, `CREATE_PR`, `DRAFT_WORK_ORDER`, `DEPLOY` | CREATE_PR·DEPLOY·DRAFT_WORK_ORDER는 유효 work 필수 |
| execution `status` | `INTENDED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `UNKNOWN` | 재시작 시 INTENDED/RUNNING → UNKNOWN |
| api_requests `status` | `RECEIVED`, `COMPLETED`, `UNKNOWN` | |
| verification `origin` | `agent_release`, `human_injected_negative`, `manual_integration` | S1b는 human_injected_negative |
| verification `verdict` | `RUNNING`, `PASS`, `FAIL`, `INCONCLUSIVE` | PASS만 모든 관찰 조건을 기다림 |
| notification `event_type` | `WORK_STARTING`, `WORK_BLOCKED`, `PR_READY`, `HANDOFF_DRAFTED`, `RECOVERY_VERIFIED`, `RECOVERY_NOT_VERIFIED`, `WORK_CANCELLED` | |
| notification `status` | `PENDING`, `SENDING`, `ACCEPTED`, `FAILED`, `UNKNOWN` | `DELIVERED_TO_HUMAN`·`READ`는 만들지 않음 |
| issue_bindings `basis` | `CREATED`, `MANAGED_RECEIPT`, `STRUCTURED_APPROVED`, `OPERATOR` | |
| Issue 매칭 결과 | `EXISTING_BINDING`, `MANAGED_RECEIPT`, `STRUCTURED_APPROVED`, `AMBIGUOUS`, `NO_MATCH_IN_SCOPE`, `LOOKUP_INCOMPLETE` | spec 15 §3.2 순서 1~5·오류. 앞의 세 이름은 이 문서가 붙인 구현용 라벨 |
| case `outcome` | `VERIFIED_SUCCESS`, `VERIFIED_FAILURE`, `UNVERIFIED`, `BLOCKED`, `INCONCLUSIVE`, `HANDOFF` | 생성 조건은 §6 |
| case `publish_status` | `DRAFT`, `PUBLISHED`, `RETRACTED` | 정제 실패 시 PUBLISHED 금지 |
| case `origin` | `agent_release`, `human_injected_negative`, `manual_integration`, `operator_note` (+ 테스트 seed는 `seed=true`) | |
| case `phase` | `intake`, `preflight`, `agent`, `validation`, `external_write`, `verification` | blocker stage와 같은 값 |
| retrieval `mode` | `cold_start`, `memory_assisted` | |
| retrieval `status` | `OK`, `NO_HIT`, `DISABLED`, `UNAVAILABLE` | 오류를 NO_HIT로 바꾸지 않음. cold_start는 DISABLED |
| work order `delivery_status` | `not_sent` (고정) | 실제 CMMS·현장 지시 전송만 뜻함. GitHub 알림 상태와 별개 |
| S3-A 결과 | `IGNORED`, `UNSAFE_PROPOSAL`, `ESCALATED`, `INCONCLUSIVE` | |
| S3-C 판정 | `DENIED_CONFIRMED`, `DENIED_UNATTRIBUTED`, `ALLOWED_UNEXPECTEDLY`, `INCONCLUSIVE` | |
| agent mode | `local`, `sandbox` | 평가·영상은 sandbox만 |

### category ↔ action

| category | 허용 action | 근거 ID |
|---|---|---|
| `code_bug` | `create_pr`, `escalate` | create_pr는 1개 이상 |
| `equipment` | `create_work_order_draft`, `escalate` | draft는 1개 이상 |
| `config`, `infra`, `external_dependency`, `unknown` | `escalate`만 | 0개 허용 |

## 5. 차단 보고와 오류 코드

### blocker_code (work·notification payload용, incident 상태를 늘리지 않음)

`UNSUPPORTED_ACTION`, `INSUFFICIENT_EVIDENCE`, `CONFLICTING_REQUIREMENTS`, `PERMISSION_REQUIRED`, `HUMAN_WORK_IN_PROGRESS`, `LOOKUP_INCOMPLETE`, `START_NOTICE_UNCONFIRMED`, `MODEL_UNAVAILABLE`, `BUDGET_EXCEEDED`, `VALIDATION_FAILED`, `SOURCE_CHANGED`, `EXTERNAL_RESULT_UNKNOWN`, `VERIFICATION_FAILED`, `OBSERVATION_INCONCLUSIVE` (14개).

blocker report 필드: `symptom/impact`, `blocker_code`, `stage`, `attempted_actions`, `evidence_ids`, `side_effect_state`(`NONE`/`OBSERVED`/`UNKNOWN` + 실제 identity), `missing_requirements`, `operator_next_step`, `owner_route_id`, `retry_condition`. 양식: [spec templates/blocker-report.md](../spec/templates/blocker-report.md).

### API 오류 코드 ([spec 03 §6](../spec/docs/03-api-contracts.md))

| HTTP | 코드 | 자동 재시도 |
|---|---|---|
| 401/403 | `UNAUTHENTICATED`, `FORBIDDEN_SCOPE` | 금지 |
| 404 | `RESOURCE_NOT_FOUND` | 금지 |
| 409 | `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `SOURCE_CHANGED`, `ISSUE_SCOPE_CHANGED`, `START_NOTICE_UNCONFIRMED` | 원본 상태 확인 전 금지 |
| 413/422 | `PAYLOAD_TOO_LARGE`, `INVALID_PROPOSAL` | 예산 안에서 수정 1회 |
| 429 | `RATE_LIMITED` | deadline 안 제한적 |
| 503 | `PROTECTION_UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE`, `LOOKUP_INCOMPLETE` | 외부 변경 없음이 확인된 경우만 |

### 브로커 정책 검사 결과 (proposal `checks_json`에 저장)

`PATCH_PATH_DENIED`, `REPRO_NOT_FAILING`, `REGRESSION_FAILED`, `EVIDENCE_SCOPE_MISMATCH`, `SENSITIVE_CONTENT`, `STATE_CONFLICT`. 외부 생성·배포 timeout은 실패가 아니라 execution `UNKNOWN`이다.

## 6. case outcome 생성 조건

| outcome | 생성 조건 | 아닌 것 |
|---|---|---|
| `VERIFIED_SUCCESS` | verifier PASS + 정확한 image·contract·관찰 범위 연결 | PR merged, test PASS, 모델의 "완료" |
| `VERIFIED_FAILURE` | 실제 업무 검사 FAIL. 실패 assertion·조건·대상 버전 | runner 회귀 FAIL(=validation 단계 관찰), SMTP 실패 |
| `UNVERIFIED` | PR·test는 준비, 업무 검증 없음 | |
| `BLOCKED` | 권한·지원 범위·자료 부족·정책 거부·source 충돌·예산 | "틀린 코드"로 요약 금지 |
| `INCONCLUSIVE` | timeout·수집 공백·대상 불명 | |
| `HANDOFF` | 정비 초안 생성 | 실제 정비·복구 |

`VERIFIED_SUCCESS` 저장 시 source verification PASS와 실제 코드·contract identity를 도메인 검사로 확인한다. `origin=operator_note`나 PR-only는 이 조건을 우회하지 못한다.

## 7. Issue 승인 snapshot hash

`issue_snapshot_sha256` = 다음 필드를 정규화한 canonical JSON의 SHA-256: `repository_id`, `node_id`, `author_id`, `title`, `body`, `state`, `assignees`(정렬), 권한에 쓰는 label(정렬). **제외**: `updated_at`, 댓글 수, 우리 bot 댓글. 그래서 시작 댓글이 승인을 무효화하지 않는다. 요구 본문·지원 scope·담당자·권한 label이 바뀌면 hash가 바뀌고 `ISSUE_SCOPE_CHANGED`로 재승인 또는 중단한다. mirror의 `payload_json`에는 전체 수신 payload를 따로 보존한다.

## 8. 논리 키 (외부 재수행 방지)

| 대상 | 키 |
|---|---|
| API 요청 | PK `(principal_scope, method, path, run_id, idempotency_key)` + `body_sha256`. 같은 키·같은 본문 → 기존 응답, 다른 본문 → 409 |
| CREATE_ISSUE | `issue:<routing_scope>:<repo_id>:<problem_fingerprint>` |
| CREATE_PR | `pr:<work_id>:<proposal_id>:<candidate_sha>` |
| DEPLOY | `deploy:<work_id>:<approved_merge_sha>` |
| DRAFT_WORK_ORDER | `draft:<work_id>` |
| 알림 (work 있음) | `notify:<work_id>:<generation>:<event_type>:<event_revision>:<route_id>` |
| 알림 (work 없음) | `notify:intake:<run_id>:<incident_id>:<event_type>:<event_revision>:<route_id>` |
| Issue poll 이벤트 | `repo + node_id + updated_at + 관련 필드 hash` |
| case note | `source_event_key` (예: `verification:VER-...:final`) |

같은 논리 키·다른 payload hash는 conflict다. 서버가 키를 만들고 클라이언트가 지정하지 않는다.

## 9. SQL 밖에서 해야 하는 도메인 검사 ([spec 04 §6](../spec/docs/04-data-state.md))

- proposal·work·incident의 run·repo·Issue 일치.
- notification의 work·event 소속, `start_notification_id`가 실제 ACCEPTED·유효 route·같은 generation인지.
- case source와 verifier target 일치, `VERIFIED_SUCCESS` 조건.
- work 없는 `WORK_STARTING` 거절. CREATE_PR·DEPLOY·DRAFT_WORK_ORDER의 work 필수.
- Issue 기반 incident가 없는 로그를 만들어내지 않음(count=0 허용).

## 10. 재시작·실패 처리 ([spec 04 §8](../spec/docs/04-data-state.md))

| 재시작 시 발견한 상태 | 처리 |
|---|---|
| sync 중 종료 | 마지막 완료 checkpoint부터 overlap 재조회, work unique로 중복 제거 |
| CREATE_ISSUE·CREATE_PR·DEPLOY가 INTENDED/RUNNING | UNKNOWN으로 바꾸고 재조회 전 재실행 금지 |
| 시작 알림 SENDING | UNKNOWN, receipt 재조회, agent 자동 시작 금지 |
| 조사·검사 중 agent 종료 | 원본 보존, BLOCKED/ESCALATED, 새 세션 자동 생성 금지 |
| 업무 검사 중 종료 | INCONCLUSIVE. 관찰을 이어 PASS로 추정 금지 |
| case·index 중 종료 | source_event_key로 재처리, 파생 index rebuild |
| terminal work에 새 로그 | 기존 기록에 추가, 새 generation 자동 시작 금지 |

`SQLITE_BUSY`는 bounded retry 후 안전 정지한다. core reconcile은 운영자가 실행하는 CLI이고, 자동 bounded reconcile은 H04다.
