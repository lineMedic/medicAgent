# 04. 데이터 모델·상태 전이·재시작 처리

> **v4의 SQL·상태 enum 기준.** 기존 incident 의미는 유지하고, Issue 접수·시작 알림·작업 선점을 work item으로 분리한다. 아래는 fresh DB용 구현 초안이며 운영 DB migration 스크립트가 아니다. 실제 전이·권한·cross-row validation은 서버와 테스트가 필요하다.

## 1. 식별과 테이블

run은 실행/평가 단위, routing_scope는 티켓 처리 영역, incident는 관찰된 사건이다. repo 숫자 ID와 issue number로 GitHub 티켓을 식별한다. work_id/generation은 재시도와 단일 수행, attempt는 실제 모델 세션이다. problem fingerprint는 run을 제외한 정규화 signature이며 source code version은 별도로 비교한다.

| 테이블 | 목적 |
|---|---|
| `demo_runs` | 활성 run, config·memory snapshot manifest |
| `incidents`, `evidence` | 장애 상태와 해당 사건의 관찰·history projection |
| `proposals`, `executions`, `verifications`, `audit_events` | 기존 제안·실행·검사·감사 |
| `github_issues`, `issue_bindings` | 외부 Issue mirror와 승인된 signature 대응 |
| `work_items` | Issue 단위 승인·시작 게이트·중복 선점·사람 대기 |
| `integration_state` | sync checkpoint·ETag·snapshot 완전성·처리 기준시각 |
| `api_requests` | 변경 요청 scope+멱등키+본문 hash·응답 연결 |
| `notifications` | 실제 알림 outbox·provider receipt·미전송 사유 |
| `case_notes`, `case_retrievals` | 결과별 사례 revision·검색과 인용 증거 |
| `case_search` | 재구축 가능한 선택 FTS5 인덱스, 원본 아님 |

Issue 승인 snapshot은 15의 의미 있는 필드만 hash하며, 우리 시작 댓글의 updated_at 변경으로 승인 충돌을 만들지 않는다. global `one_running_work` 제약으로 core 동시 RUNNING은 1개다. 슬롯이 차 있으면 다른 READY work는 대기하고 실패로 처리하지 않는다.

memory snapshot은 run config가 가리키는 **불변 manifest**(note ID/revision/hash 목록·cutoff·scope)로 둔다. core에서 별도 벡터 DB·event bus·workflow 엔진을 만들지 않는다.

## 2. Incident 상태

| 값 | 의미 |
|---|---|
| `NEW` | Issue 연결·승인·시작 알림 대기 포함, 아직 agent 미시작 |
| `INVESTIGATING` | start gate 통과한 한 agent attempt 실행 |
| `VALIDATING` | 브로커 제안 검사 |
| `PR_OPENED` | 검토용 PR 존재, 사람 승인 대기 |
| `DEPLOYING` | 승인한 정확한 버전 실행 중 |
| `VERIFYING` | 실제 업무 계약 검사 |
| `WORK_ORDER_DRAFTED` | 설비 점검 초안, 미복구 |
| `RESOLVED` | verifier가 정의한 범위에서 PASS |
| `ESCALATED` | 미해결·중단·자료/권한 부족 |
| `EXECUTION_UNKNOWN` | 외부 변경 여부 불명 |

GitHub 티켓 상태와 작업 상태는 이 enum에 섞지 않는다. Issue close는 RESOLVED가 아니다.

## 3. Work item 상태와 시작 규칙

| 상태 | 의미 | 다음 전이 주체 |
|---|---|---|
| `WAITING_APPROVAL` | 등록 범위·actor·지원 범위 확인, 미승인 대기 | router/operator |
| `WAITING_NOTIFICATION` | atomic claim 완료, 필수 start 알림 접수 대기 | notifier/supervisor |
| `READY` | receipt 확인, 최종 scope·취소·보호조건 재검사 대기 | supervisor |
| `RUNNING` | agent 또는 broker 처리 | broker/supervisor |
| `WAITING_REVIEW` | PR 생성, 사람 리뷰·정확한 배포 승인 대기 | release executor/operator |
| `WAITING_VERIFICATION` | 승인 배포/업무 검사 진행 | verifier/reconciler |
| `HANDED_OFF` | 초안 전달·후속 사람 확인 필요 | core terminal |
| `SUCCEEDED` | 해당 incident RESOLVED, 업무 검사 범위만 성공 | verifier only |
| `BLOCKED` | 불가 사유로 종료, 새 승인이 있어야 재시도 | core terminal |
| `CANCELLED` | 시작 전 또는 부작용 상태 확인 후 취소 | operator/supervisor |
| `EXECUTION_UNKNOWN` | 중복 실행 금지·상태 조정 대기 | reconciler |

`WAITING_APPROVAL → WAITING_NOTIFICATION`에서 version CAS와 활성 work unique를 확인한다. 자동 승인도 등록 정책을 통해 이 전이를 수행한다. `READY → RUNNING`과 incident `NEW → INVESTIGATING`, attempt 발급·예산 설정을 **하나의 트랜잭션**으로 기록한다. 시작 notice가 ACCEPTED가 아니거나 Issue 내용/권한이 바뀌면 실패한다.

`PR_OPENED`와 work `WAITING_REVIEW`, `DEPLOYING/VERIFYING`과 work `WAITING_VERIFICATION`, `RESOLVED`와 work `SUCCEEDED`, `WORK_ORDER_DRAFTED`와 work `HANDED_OFF`는 각각 같이 갱신한다. 알림의 상태는 별개다. 실패·중단은 대부분 incident ESCALATED/work BLOCKED이며 reason으로 상세를 보존한다.

## 4. Incident 전이 권한

| 출발 → 도착 | 주체·조건 |
|---|---|
| NEW → INVESTIGATING | supervisor: work READY·알림 receipt·현재 권한·attempt 미존재 |
| NEW → ESCALATED | router/supervisor: 모호함·지원 밖·시작 알림 실패·취소 |
| NEW → EXECUTION_UNKNOWN | router: CREATE_ISSUE 접수 여부 불명 |
| INVESTIGATING → VALIDATING | broker: 유효 제안·현재 attempt |
| INVESTIGATING → ESCALATED | dispatcher/broker: 실패·중단·escalate |
| VALIDATING → INVESTIGATING | broker: 외부 변경 없음·제안 수정 1회·원래 deadline |
| VALIDATING → PR_OPENED / WORK_ORDER_DRAFTED | broker: 실제 PR 확인 또는 로컬 초안 저장 |
| VALIDATING → ESCALATED / EXECUTION_UNKNOWN | broker: 실패 명확 / 외부 결과 불명 |
| PR_OPENED → DEPLOYING | release executor: 사람·exact SHA·기대 상태 승인 |
| PR_OPENED → ESCALATED | operator: 외부 변경 없음 확인 후 중단 |
| DEPLOYING → VERIFYING / ESCALATED / EXECUTION_UNKNOWN | release executor: 실제 결과에 따라 |
| VERIFYING → RESOLVED / ESCALATED | **verifier only**: PASS / FAIL·INCONCLUSIVE |
| EXECUTION_UNKNOWN → NEW | reconciler: CREATE_ISSUE 하나 확인·binding 복구, 새 start gate 필요 |
| EXECUTION_UNKNOWN → PR_OPENED | reconciler: 기존 CREATE_PR 결과 정확히 확인 |
| EXECUTION_UNKNOWN → VERIFYING | reconciler: DEPLOY 실제 target 확인 후 새 검사 |
| EXECUTION_UNKNOWN → ESCALATED | reconciler: 무변경·실패·충돌 확인, 자동 재실행 없음 |

UNKNOWN 조정은 과거 결과를 확인하는 것이지 새 외부 변경 승인이 아니다. 이미 알림 timeout으로 끝난 work를 늦은 receipt가 자동으로 RUNNING으로 바꾸면 안 된다. core는 자동 repair/reinvestigation을 하지 않는다.

## 5. Fresh schema DDL

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE demo_runs (
  id TEXT PRIMARY KEY,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  created_at TEXT NOT NULL,
  config_json TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_run ON demo_runs(active) WHERE active=1;

CREATE TABLE incidents (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  fingerprint_version TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK(source_kind IN ('LOG','GITHUB_ISSUE','OPERATOR','VERIFIER')),
  status TEXT NOT NULL CHECK(status IN (
    'NEW','INVESTIGATING','VALIDATING','PR_OPENED','DEPLOYING','VERIFYING',
    'WORK_ORDER_DRAFTED','RESOLVED','ESCALATED','EXECUTION_UNKNOWN')),
  category TEXT CHECK(category IS NULL OR category IN (
    'code_bug','equipment','config','infra','external_dependency','unknown')),
  service TEXT NOT NULL,
  line_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  count INTEGER NOT NULL DEFAULT 0 CHECK(count>=0),
  attempt_id TEXT,
  attempt_deadline TEXT,
  submissions INTEGER NOT NULL DEFAULT 0 CHECK(submissions>=0 AND submissions<=2),
  reason_code TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  reopened_from TEXT REFERENCES incidents(id),
  details_json TEXT NOT NULL,
  UNIQUE(run_id,id)
);
CREATE UNIQUE INDEX one_active_fingerprint ON incidents(run_id,fingerprint)
WHERE status IN ('NEW','INVESTIGATING','VALIDATING','PR_OPENED','DEPLOYING','VERIFYING','EXECUTION_UNKNOWN');

CREATE TABLE github_issues (
  repository_id INTEGER NOT NULL,
  issue_number INTEGER NOT NULL CHECK(issue_number>0),
  node_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','closed')),
  author_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  last_observed_at TEXT NOT NULL,
  PRIMARY KEY(repository_id,issue_number),
  UNIQUE(repository_id,node_id)
);
CREATE TABLE issue_bindings (
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  fingerprint_version TEXT NOT NULL,
  problem_fingerprint TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  basis TEXT NOT NULL CHECK(basis IN ('CREATED','MANAGED_RECEIPT','STRUCTURED_APPROVED','OPERATOR')),
  decision_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(routing_scope,repository_id,fingerprint_version,problem_fingerprint),
  FOREIGN KEY(repository_id,issue_number) REFERENCES github_issues(repository_id,issue_number)
);
CREATE TABLE work_items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  issue_number INTEGER NOT NULL,
  generation INTEGER NOT NULL CHECK(generation>0),
  status TEXT NOT NULL CHECK(status IN (
    'WAITING_APPROVAL','WAITING_NOTIFICATION','READY','RUNNING','WAITING_REVIEW',
    'WAITING_VERIFICATION','HANDED_OFF','SUCCEEDED','BLOCKED','CANCELLED','EXECUTION_UNKNOWN')),
  version INTEGER NOT NULL DEFAULT 0,
  issue_snapshot_sha256 TEXT NOT NULL,
  authorization_json TEXT NOT NULL,
  start_notification_id TEXT,
  attempt_id TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
  reason_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  details_json TEXT NOT NULL,
  UNIQUE(routing_scope,repository_id,issue_number,generation),
  UNIQUE(run_id,incident_id),
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id),
  FOREIGN KEY(repository_id,issue_number) REFERENCES github_issues(repository_id,issue_number)
);
CREATE UNIQUE INDEX one_active_work_per_issue
ON work_items(routing_scope,repository_id,issue_number)
WHERE status NOT IN ('HANDED_OFF','SUCCEEDED','BLOCKED','CANCELLED');
CREATE UNIQUE INDEX one_running_work ON work_items((1)) WHERE status='RUNNING';

CREATE TABLE integration_state (
  integration_id TEXT NOT NULL,
  state_key TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(integration_id,state_key)
);
CREATE TABLE api_requests (
  principal_scope TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  idempotency_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('RECEIVED','COMPLETED','UNKNOWN')),
  response_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(principal_scope,method,path,run_id,idempotency_key)
);
CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_identity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT NOT NULL REFERENCES work_items(id),
  attempt_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('RECEIVED','CHECKING','ALLOWED','REJECTED')),
  received_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  checks_json TEXT NOT NULL,
  UNIQUE(run_id,incident_id,attempt_id,idempotency_key),
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE executions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  proposal_id TEXT REFERENCES proposals(id),
  operation TEXT NOT NULL CHECK(operation IN ('CREATE_ISSUE','CREATE_PR','DRAFT_WORK_ORDER','DEPLOY')),
  logical_key TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('INTENDED','RUNNING','SUCCEEDED','FAILED','UNKNOWN')),
  stage TEXT NOT NULL,
  intended_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE verifications (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  execution_id TEXT REFERENCES executions(id),
  origin TEXT NOT NULL CHECK(origin IN ('agent_release','human_injected_negative','manual_integration')),
  verdict TEXT NOT NULL CHECK(verdict IN ('RUNNING','PASS','FAIL','INCONCLUSIVE')),
  contract_id TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  incident_id TEXT,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE notifications (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  event_type TEXT NOT NULL CHECK(event_type IN (
    'WORK_STARTING','WORK_BLOCKED','PR_READY','HANDOFF_DRAFTED',
    'RECOVERY_VERIFIED','RECOVERY_NOT_VERIFIED','WORK_CANCELLED')),
  route_id TEXT NOT NULL,
  logical_key TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PENDING','SENDING','ACCEPTED','FAILED','UNKNOWN')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
  next_attempt_at TEXT,
  receipt_id TEXT,
  accepted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE case_notes (
  id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  supersedes_id TEXT REFERENCES case_notes(id),
  repository_id INTEGER NOT NULL,
  service TEXT NOT NULL,
  problem_fingerprint TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  source_event_key TEXT NOT NULL UNIQUE,
  outcome TEXT NOT NULL CHECK(outcome IN (
    'VERIFIED_SUCCESS','VERIFIED_FAILURE','UNVERIFIED','BLOCKED','INCONCLUSIVE','HANDOFF')),
  phase TEXT NOT NULL,
  origin TEXT NOT NULL,
  publish_status TEXT NOT NULL CHECK(publish_status IN ('DRAFT','PUBLISHED','RETRACTED')),
  observed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(series_id,revision),
  FOREIGN KEY(source_run_id,source_incident_id) REFERENCES incidents(run_id,id)
);
CREATE INDEX case_lookup ON case_notes(repository_id,service,problem_fingerprint,publish_status);
CREATE TABLE case_retrievals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT NOT NULL REFERENCES work_items(id),
  mode TEXT NOT NULL CHECK(mode IN ('cold_start','memory_assisted')),
  snapshot_id TEXT,
  engine TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OK','NO_HIT','DISABLED','UNAVAILABLE')),
  query_json TEXT NOT NULL,
  results_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
```

### 5.1 선택 FTS5 파생 인덱스

아래는 FTS5 지원을 확인한 환경에서만 만든다. metadata ACL은 case_notes와 run snapshot으로 검사한다. 인덱스만 읽고 권한 확인을 생략하지 않는다.

```sql
CREATE VIRTUAL TABLE case_search USING fts5(note_id UNINDEXED, search_text, tokenize='unicode61');
```

note publish/수정 revision과 index insert는 같은 transaction에서 수행하거나 명시적 index rebuild를 한다. 재구축은 PUBLISHED인 불변 revision들을 보존해 색인한다. live snapshot 생성 시에는 series별 최신 revision을 선택하고, 고정 snapshot 검색은 당시 선택한 정확한 revision만 사용한다. 후속 revision으로 몰래 교체하지 않는다. RETRACTED는 즉시 검색에서 제외하며 해당 snapshot의 조회 결과에 철회 사실을 표시한다. 검색 예제의 SQL은 [17](17-case-memory.md)의 필터 순서를 구현해야 하며 raw 입력을 문자열로 이어 붙이지 않는다.

## 6. SQL 외 필수 도메인 검사

FK가 모든 신뢰 경계를 보장하지 않는다. proposal/work/incident의 run·repo·Issue 일치, notification의 work·event 소속, start_notification_id의 실제 ACCEPTED 상태·유효 route·generation, case source와 verifier의 target 일치는 transaction 안의 도메인 검사를 거친다.

`VERIFIED_SUCCESS` 저장 시 source verification PASS와 실제 코드/contract identity를 검사한다. `origin=operator_note`나 PR-only는 이 조건을 우회하지 못한다. Human S1b는 negative case로 저장 가능하지만 agent 성공/실패 성능과 별도 cohort다.

Issue 기반 incident는 로그 count=0으로 시작할 수 있다. 미관찰 오류를 실제 로그가 있었다고 만들어내지 않는다. GitHub Issue 생성 전 lookup 불가 사건에는 work가 없을 수 있어 executions/notifications/case의 work_id가 nullable이다. `CREATE_PR/DEPLOY/DRAFT_WORK_ORDER`에는 유효 work가 필수다. work 없는 WORK_STARTING은 거절한다.

## 7. 원자성·외부 호출·중복

```text
BEGIN IMMEDIATE
  현재 scope·work/incident version·허용 전이 검사
  UPDATE ... WHERE version=? AND status=?
  논리 key unique 검증
  api_requests / external intent / notification outbox 기록
  audit_events INSERT
COMMIT
외부 API 호출
별도 transaction에서 receipt·상태·case event 기록
```

DB 안에서 모델·GitHub·SMTP·Docker 응답을 기다리지 않는다. `SQLITE_BUSY`는 bounded retry 후 안전 정지한다. claim 경합과 같은 key의 다른 본문 409는 v4 core다. body hash는 schema 정규화된 canonical JSON으로 만들고 임의 필드 추가·중복 key를 거부한다. principal identity는 서버에서 얻는다.

소스별 외부 key: `CREATE_ISSUE`는 scope/repo/fingerprint, PR은 work/proposal/candidate, 배포는 work/approved_merge_sha, 초안은 work ID, 알림은 work/generation/event/revision/route다. API idem key와 외부 논리 key는 각각 요청 재전송과 같은 업무의 재수행을 방지한다.

## 8. 재시작·실패·재발

| 관찰 | 처리 |
|---|---|
| sync 중 종료 | 마지막 완료 checkpoint부터 overlap, work unique로 중복 제거 |
| CREATE_ISSUE/CREATE_PR/DEPLOY INTENDED·RUNNING | UNKNOWN, receipt/대상 identity 재조회 전 재실행 금지 |
| start 알림 SENDING | UNKNOWN, provider receipt 재조회, agent 자동 시작 금지 |
| READ/검사 중 agent 종료 | 원본 보존·BLOCKED/ESCALATED, 새 세션 자동 생성 금지 |
| 업무 검사 중 종료 | INCONCLUSIVE, 관찰을 이어 PASS로 추정하지 않음 |
| case/index 중 종료 | source_event_key로 재처리, 파생 index rebuild |
| terminal work에 새 로그 | 기존 기록에 추가, 새 generation 자동 시작 금지 |

core reconcile은 운영자가 실행하는 CLI다. 자동 bounded reconcile은 H04다. 수신한 새 이벤트 처리와 결과 불명 쓰기의 **재실행**을 혼동하지 않는다.

인시던트 active fingerprint unique는 terminal을 제외하도록 v3 대비 v4에서 바꿨다. 이것이 terminal 뒤 자동 반복을 허용한다는 뜻은 아니다. router는 가장 최근 binding/work를 확인하고 명시적 retry 승인 때만 새 incident/generation을 만든다. 실제 복구 뒤 새로운 장애는 새 occurrence를 기록하되 닫힌 Issue는 운영자 정책에 따라 연결/신규 생성한다.

## 9. 보존과 상태 투영

audit DB가 기준이고 JSONL은 export다. case/알림 요약이 원본 사실을 대체하지 않는다. 호스트 관리자까지 막는 불변 저장소는 아니며 해시를 서명·attestation으로 표현하지 않는다.

reset은 새 demo run·새 scope·새 workspace를 만들고 원본 Issue/PR/실행/case를 보존한다. 기존 상태를 current run의 미완료 작업으로 가져오지 않는다. memory snapshot 선택은 별도이며 cold_start는 과거 cases 접근을 차단한다. 전환 절차는 [18](18-migration-validation.md).
