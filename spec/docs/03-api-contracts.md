# 03. API·제안·도구 계약

> v4 규범 문서. v3의 기능 경계를 유지하되 Issue/work·알림·사례 계약을 추가하며 wire schema를 의도적으로 변경한다. **아래 API는 LineMedic이 새로 구현할 계약**이며 NVIDIA/OpenClaw의 기본 API가 아니다. OpenAPI/JSON Schema 파일은 구현 시 이 문서에서 생성한다. 서버의 실제 인증 principal이 권한 기준이며 요청 본문의 역할 문자열을 신뢰하지 않는다.

## 1. 공통 규칙

- `/tools/*`: run·incident·work·attempt 범위로 제한된 agent token. `/ops/*`: 신뢰된 운영자 token. 두 token의 용도를 교환할 수 없다.
- 에이전트 token은 호스트가 attempt를 만들 때 발급한다. 모델 prompt에 넣지 않고 도구 client 설정으로 전달한다. 제한된 credential이며 ‘샌드박스에 비밀이 전혀 없음’으로 표현하지 않는다.
- JSON UTF-8, `schema_version: linemedic.v4`. 날짜는 UTC RFC3339. Git SHA는 완전한 object ID를 저장하며 축약 SHA는 화면 전용이다.
- 알 수 없는 필드, 중복 JSON key, 잘못된 enum, 객체 대신 문자열, 허용 크기 초과를 거부한다. 명세에 없는 URL·path·command 전달 기능은 만들지 않는다.
- 변경 요청은 `Idempotency-Key` 필수. 키 범위는 `(principal, method, path, run_id)`다. `[core]` 같은 키·같은 본문은 기존 결과를 반환하고, 같은 키·다른 정규화 본문 SHA-256은 409다. `api_requests`와 업무별 logical key를 함께 사용한다. v3의 H02를 필수로 승격했다.
- `issue_snapshot_sha256`는 [15](15-issue-intake-workflow.md)의 승인 관련 필드 hash이며 단순 updated_at·댓글 수를 제외한다. 전체 mirror payload hash와 구분한다.
- 도구의 데이터는 관찰 자료다. 조회가 권한 상승이나 사건 상태 변경을 의미하지 않는다.
- 운영 HTTP 노출은 데모 전용 격리 네트워크로 제한한다. 공용 네트워크를 통과하면 TLS를 사용한다. 공개 무인 API로 배포하지 않는다.

### 응답 외피

```json
{
  "schema_version": "linemedic.v4",
  "request_id": "req-001",
  "data": {},
  "evidence_ids": []
}
```

### 오류 외피

```json
{
  "schema_version": "linemedic.v4",
  "request_id": "req-002",
  "error": {
    "code": "STATE_CONFLICT",
    "message": "현재 사건 상태에서 이 요청을 처리할 수 없습니다.",
    "retryable": false,
    "details": {
      "current_status": "PR_OPENED"
    }
  }
}
```

인증 오류에서는 다른 사건의 존재·내용을 누설하지 않는다. 오류·로그에 token·전체 환경변수를 넣지 않는다.

## 2. 에이전트 도구 목록

| 도구 | 메서드·경로 | 요청 | 반환·제한 |
|---|---|---|---|
| `get_incident` | GET `/tools/incidents/{incident_id}` | 없음 | 현재 사건·특징·배포 identity·증거 ID. 권한 있는 사건만 |
| `search_logs` | GET `/tools/incidents/{incident_id}/logs` | `q` 선택, `limit` 1~20 | 사건 ±30분의 정제본. 정규식·shell 실행 없음 |
| `get_deploys` | GET `/tools/incidents/{incident_id}/deploys` | 없음 | 등록 서비스의 최근 24시간 배포, 현재 base SHA |
| `get_knowledge` | GET `/tools/incidents/{incident_id}/knowledge` | `q` 선택 | 허용된 정적 매뉴얼·런북 절. 과거 사례는 아래 cases API. 임의 URL fetch 금지 |
| `query_equipment_metrics` | GET `/tools/incidents/{incident_id}/equipment/{equipment_id}/metrics` | 없음 | 등록 설비, 최대 30분·60개 sample, baseline·품질 정보 |
| `get_bound_issue` | GET `/tools/incidents/{incident_id}/issue` | 없음 | 서버가 확정한 repo·Issue·work·현재 snapshot·상태. 임의 repo 검색 아님 |
| `search_cases` | GET `/tools/incidents/{incident_id}/cases/search` | `q` 선택, `limit` 1~5 | 현재 scope와 고정 snapshot 안의 사례. outcome·source·current evidence projection |
| `submit_proposal` | POST `/tools/proposals` | 아래 union | **202 접수는 허용·실행 성공이 아님**. proposal ID 반환 |
| `get_proposal` | GET `/tools/proposals/{proposal_id}` | 없음 | 해당 사건 제안의 검증 상태·거절 사유·정제 결과 |

`read_file/grep_code/run_tests`는 에이전트의 **로컬 sandbox 작업**이다. Control API에 범용 파일 접근·원격 exec endpoint를 추가하지 않는다. core는 하나의 supervisor가 Issue에 연결된 work를 선점하므로 공용 queue API를 만들지 않는다. agent가 Issue를 직접 생성하거나 댓글·메일을 발송하는 도구도 만들지 않는다.

### 사건 응답 예시

```json
{
  "schema_version": "linemedic.v4",
  "request_id": "req-101",
  "data": {
    "id": "INC-001",
    "run_id": "run-001",
    "attempt_id": "ATT-001",
    "version": 2,
    "status": "INVESTIGATING",
    "service": "mes-api",
    "line_id": "L3",
    "category": null,
    "symptom": "불량 집계 요청에서 예외 반복",
    "features": {
      "recent_deploy": true,
      "scope": "service"
    },
    "base_sha": "1111111111111111111111111111111111111111",
    "observed_at": "2026-09-26T14:00:00Z",
    "work_id": "WORK-001",
    "work_status": "RUNNING",
    "issue": {
      "repository_id": 100001,
      "number": 42,
      "snapshot_sha256": "example-snapshot-hash"
    },
    "start_notification": {
      "id": "NOT-START-001",
      "status": "ACCEPTED",
      "receipt_id": "example-comment-id"
    },
    "memory": {
      "mode": "cold_start",
      "snapshot_id": null
    }
  },
  "evidence_ids": [
    "EV-001",
    "EV-002"
  ]
}
```

SHA·ID·시각은 형식을 설명하는 가상값이다. `scenario_name`, 정답 category, 에이전트에게 보이지 않아야 할 기대 fixture를 응답에 넣지 않는다. `features`는 힌트이지 원인 확정 enum이 아니다.

## 3. 제안 스키마

### 공통 필드

| 필드 | 타입·규칙 | 권한 |
|---|---|---|
| `schema_version` | `linemedic.v4` | 고정 |
| `run_id`, `incident_id`, `work_id`, `attempt_id` | 서버가 배정한 ID | 현재 principal 범위와 일치 |
| `category` | `code_bug / equipment / config / infra / external_dependency / unknown` | 판단 결과, 사실 확정 아님 |
| `summary` | 1~2,000자 | plain text |
| `evidence_ids` | 중복 없는 ID, 최대 20개 | 같은 사건·run·허용 출처 |
| `action` | **정확히 한 개의 객체** | 아래 discriminated union |

`actions[]`, `confidence`, 클라이언트 지정 `actor/role/status/model/policy_version`는 v4 요청에 사용하지 않는다. 런타임·모델·prompt·policy ID는 서버의 attempt 기록에 붙인다. 자기보고 확신도는 승인/강등 경로에서 제거한다.

### A. 코드 제안

```json
{
  "schema_version": "linemedic.v4",
  "run_id": "run-001",
  "incident_id": "INC-001",
  "attempt_id": "ATT-001",
  "category": "code_bug",
  "summary": "검사자 필드가 누락된 입력에서 집계 함수가 실패하는 것으로 관찰됩니다.",
  "evidence_ids": [
    "EV-001",
    "EV-002"
  ],
  "action": {
    "type": "create_pr",
    "base_sha": "1111111111111111111111111111111111111111",
    "root_cause_hypothesis": "필수라고 가정한 필드를 직접 조회합니다.",
    "diff": "<에이전트가 생성한 실제 UTF-8 unified diff>",
    "new_test_path": "tests/repro/test_missing_inspector.py",
    "local_test_observation": {
      "before": "failed",
      "after": "passed"
    }
  },
  "work_id": "WORK-001"
}
```

`diff` placeholder는 문서 예시다. 실제 요청에서는 적용 가능한 diff가 필요하다. `local_test_observation`은 선택적인 참고 필드이며 브로커가 재검사한다. pytest 명령·환경·GitHub URL·branch 이름은 클라이언트가 지정하지 않는다.

### B. 정비 요청 초안

```json
{
  "schema_version": "linemedic.v4",
  "run_id": "run-002",
  "incident_id": "INC-002",
  "attempt_id": "ATT-002",
  "category": "equipment",
  "summary": "카메라 2번에 한정된 밝기와 신뢰도 이상으로 설비 점검이 필요합니다.",
  "evidence_ids": [
    "EV-101",
    "EV-102"
  ],
  "action": {
    "type": "create_work_order_draft",
    "equipment_id": "L3-CAM-2",
    "symptom": "다른 카메라 대비 밝기와 판정 신뢰도가 낮음",
    "probable_cause": "렌즈·조명·설정 중 원인은 미확정",
    "manual_ref_id": "MANUAL-L3-VISION-4.2",
    "open_questions": [
      "현장 담당자의 승인된 절차에 따른 점검이 필요함"
    ]
  },
  "work_id": "WORK-002"
}
```

자유 생성한 정비 절차·제어값·shell·외부 URL·수신자 주소는 받지 않는다. 초안의 안내 문구는 브로커가 **팀이 만든 가상 매뉴얼의 승인 템플릿**에서 채운다. 실제 산업 매뉴얼이나 안전 절차의 대체물이 아니다.

### C. 이관

```json
{
  "schema_version": "linemedic.v4",
  "run_id": "run-003",
  "incident_id": "INC-003",
  "attempt_id": "ATT-003",
  "category": "unknown",
  "summary": "증거가 부족하여 안전하게 조치를 선택할 수 없습니다.",
  "evidence_ids": [],
  "action": {
    "type": "escalate",
    "reason": "INSUFFICIENT_EVIDENCE",
    "open_questions": [
      "현재 의존 서비스 상태를 확인하지 못함"
    ],
    "missing_requirements": [
      "현재 의존 서비스의 상태를 확인할 자료"
    ],
    "retry_condition": "담당자가 자료를 제공하고 새 generation을 승인한 뒤"
  },
  "work_id": "WORK-003"
}
```

`escalate.reason`은 [16](16-notifications.md)의 고정 blocker code를 사용한다. `missing_requirements`, `retry_condition`은 선택적인 plain text다. 실제 수행한 동작·부작용·수신자는 서버 기록과 route catalog에서만 정한다. 모델이 부족한 자료를 정확히 제시하지 못해도 runtime 오류에 대한 host 템플릿을 사용할 수 있다.

증거를 얻지 못한 이관은 빈 ID 목록을 허용한다. `create_pr`·`create_work_order_draft`는 유효한 근거 ID가 최소 1개 필요하다. category가 `config/infra/external_dependency/unknown`이면 P0 액션은 `escalate`뿐이다. `equipment`에서 `create_pr`를 제출하면 category/action 불일치로 거절하지만, 잘못 `code_bug`로 분류한 모든 제안을 탐지한다는 보장은 아니다.

## 4. 접수·검증·재제출

1. 인증·run/work/attempt·시작 알림 receipt·Issue 현재 scope·크기·JSON 구조 검사. 실패는 401/403/413/422이며 외부 실행하지 않는다.
2. 유효한 접수는 proposal 저장 후 202. 검증은 백그라운드 작업이 수행한다.
3. `RECEIVED → CHECKING → ALLOWED / REJECTED`를 proposal의 `decision`으로 기록한다. `ALLOWED`도 외부 실행 완료를 뜻하지 않는다.
4. 정책/테스트 거절은 원본 결과와 이유를 보존한다. 동일 attempt에 **수정 1회** 허용한다. 별도의 proposal ID와 Idempotency-Key를 사용하되 deadline을 새로 부여하지 않는다.
5. 형식·정책 오류의 서로 다른 제출은 합산 최대 2회다. 동일 요청의 네트워크 재전송은 예산을 추가 소비하지 않는다. 두 번째 제출까지 실패하면 이관한다.
6. 이미 외부 실행 intent가 있는 제안은 수정·재실행하지 않는다. 불명확한 결과는 reconcile 전까지 중단한다.

## 5. 운영 API

| 메서드·경로 | 허용 주체 | 의미 |
|---|---|---|
| GET `/ops/dashboard` | operator read | 실제 상태·관찰·검사 읽기 모델 |
| GET `/ops/incidents/{id}` | operator read | 감사·proposal·execution·verification 연결 |
| POST `/ops/integrations/github/sync` | operator integration | 현재 등록 repo의 조회 1회 실행. repo/URL 임의 지정 불가 |
| GET `/ops/issues/candidates/{incident_id}` | operator read | binding 후보·match 근거·조회 완전성 |
| POST `/ops/incidents/{id}/issue-binding` | operator triage | 등록 repo의 Issue 번호를 명시적으로 연결. evidence·현재 version 검사 |
| POST `/ops/work-items/{id}/approve` | operator authorize | 정확한 Issue snapshot·지원 scope 승인 후 시작 gate로 이동 |
| POST `/ops/work-items/{id}/cancel` | operator | 취소 요청. 외부 결과 불명은 곧바로 CANCELLED로 만들지 않음 |
| POST `/ops/work-items/{id}/retry` | operator authorize | blocker 해소 확인·새 incident/generation 생성, 새 시작 알림 필수 |
| GET `/ops/notifications` | operator read | outbox·receipt·실패 상태. 민감 수신 주소 비노출 |
| POST `/ops/notifications/{id}/reconcile` | operator reconcile | provider 기록 읽기·상태 조정만, 맹목 재발송 금지 |
| POST `/ops/cases/rebuild-index` | operator maintenance | 기존 PUBLISHED 노트로 파생 index 재구축, outcome 변경 안 함 |
| GET `/ops/cases/{note_id}` | operator read | 권한 있는 revision·source·검증 수준 |
| POST `/ops/releases` | operator approve | 정확한 최종 SHA에 대한 **명시적 배포 승인**, 멱등성 필수 |
| POST `/ops/executions/{id}/reconcile` | operator reconcile | 외부 상태 읽기·기록만. 새로운 변경 실행 금지. `[core]` 사람이 CLI로 호출, `[hardening H04]` 제한 횟수 자동 재조회 |
| POST `/ops/incidents/{id}/escalate` | operator | 중단 이유 기록. `RESOLVED` 전이 없음 |
| POST `/ops/runs` | operator demo | 새 실행 준비. 외부 기준 브랜치 준비는 trusted setup에서 수행 |
| POST `/ops/runs/{id}/archive` | operator demo | 신규 작업 중단·현재 실행 증거 export. 삭제 아님 |

`force-resolve`, 임의 state PATCH, arbitrary exec, 임의 URL 검증 API를 만들지 않는다. 시나리오 주입·reset은 공개 endpoint 대신 **호스트의 demo 전용 CLI**로 수행한다.

### 릴리스 승인 요청

```json
{
  "schema_version": "linemedic.v4",
  "run_id": "run-001",
  "incident_id": "INC-001",
  "proposal_id": "PROP-001",
  "pr_number": 17,
  "approved_merge_sha": "3333333333333333333333333333333333333333",
  "expected_incident_version": 5,
  "expected_current_image_id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "approval_note": "PR을 검토했고 이 SHA의 데모 배포를 승인함",
  "work_id": "WORK-001"
}
```

GitHub URL·원격 저장소·이미지 태그는 서버 카탈로그에서 결정한다. `work_id`는 해당 proposal·incident의 연결 작업이어야 한다. principal과 승인 시각은 서버가 붙인다. `approved_merge_sha`는 PR의 최종 merged 상태에서 얻은 값과 일치해야 하고, [08](08-release-verification.md)의 추가 검사를 통과해야 한다.

### 작업 승인/연결의 최소 요청

Issue binding 요청은 `{schema_version, run_id, issue_number, expected_incident_version, decision_note}`이며 repo는 incident의 등록 scope로 결정한다. approve 요청은 `{schema_version, expected_work_version, expected_issue_snapshot_sha256, approval_note}`다. retry 요청은 `{schema_version, expected_work_version, blocker_resolution_note}`이고, **새 generation·attempt ID는 서버가 발급**한다. 모든 POST는 Idempotency-Key를 요구하며 인증 principal과 현재 역할을 확인한다.

Issue body가 바뀌었거나 승인 조건이 철회되면 `ISSUE_SCOPE_CHANGED`다. arbitrary title/body edit·remote issue close·force-resolve API는 만들지 않는다. GET polling은 정보를 읽을 뿐 agent 배정은 내부 supervisor만 수행한다.

## 6. 오류 코드와 의미

| HTTP | 코드 예시 | 자동 재시도 |
|---|---|---|
| 401/403 | `UNAUTHENTICATED`, `FORBIDDEN_SCOPE` | 금지 |
| 404 | `RESOURCE_NOT_FOUND` | 금지 |
| 409 | `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `SOURCE_CHANGED`, `ISSUE_SCOPE_CHANGED`, `START_NOTICE_UNCONFIRMED` | 원본 상태 확인 전 금지 |
| 413/422 | `PAYLOAD_TOO_LARGE`, `INVALID_PROPOSAL` | 조사 예산 안에서 수정 1회만 |
| 429 | `RATE_LIMITED` | deadline 내 제한적 재시도 |
| 503 | `PROTECTION_UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE`, `LOOKUP_INCOMPLETE` | 외부 변경 없음이 확인된 경우만 |

정책 검사 실패(`PATCH_PATH_DENIED`, `REPRO_NOT_FAILING`, `REGRESSION_FAILED`, `EVIDENCE_SCOPE_MISMATCH`)는 저장된 proposal의 결과다. 외부 생성/배포 timeout은 일반 ‘실패이므로 다시 호출’로 처리하지 않고 execution `UNKNOWN`으로 다룬다.

## 7. 계약 테스트 (core 우선)

`[core]` 서버 schema와 client schema의 필드·enum 일치, 잘못된 category/action, `actions[]` 제출, token 교환, 다른 run 접근, 오래된 version, 위조 model/role 필드, 임의 path·URL, 202를 실행 성공으로 오해하는 client를 시험한다. `[core]` 같은 멱등키 다른 body 409, 다른 work/Issue 범위, 시작 알림 미확인 상태의 제안, spoofed issue marker, history projection ACL, 중복 approval·retry도 시험한다. 안전한 API 계약을 이유로 모델의 조사 순서까지 고정하지 않는다.

현재 work의 history evidence projection을 인용할 수 있지만 과거 run의 evidence ID를 직접 제출하면 scope mismatch다. `GET cases/search`의 응답은 [17](17-case-memory.md) 5절을 따른다. API version 전환은 [18](18-migration-validation.md)을 따른다.
