# W25 — work 상태·단일 claim·409·approve/retry/cancel

| 항목 | 값 |
|---|---|
| 등급 | core (H01 claim 경합, H02 409 승격분 포함) |
| 자율성 | A |
| 선행 | W06, W22 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 04 §3·§5·§7·§8](../spec/docs/04-data-state.md), [spec 15 §1·§6·§7·§8](../spec/docs/15-issue-intake-workflow.md), [spec 03 §5 최소 요청](../spec/docs/03-api-contracts.md), [spec 01 FR-19·FR-25·FR-26](../spec/docs/01-requirements.md) |
| 참조 | [docs/03 §3](../docs/03-domain-model.md), [docs/05 ④·⑧](../docs/05-workflows.md) |
| 요구·테스트 | FR-19, FR-25, FR-26, HR-01, HR-02 / T-STATE-01, T-ISS-04, T-V4-02, T-ISS-06(일부) |

## 목표

로그와 Issue polling이 같은 Issue에 동시에 도착해도 활성 work·attempt·시작 알림은 하나이고, 승인·재시도·취소가 CAS와 멱등성으로 중복 없이 처리된다.

## 만들 파일

- `linemedic/control_plane/supervisor.py`
  - `ensure_work(tx, incident, issue)`: 같은 `(routing_scope, repo, issue)`의 활성 work가 있으면 그것을 반환하고 incident에 evidence만 연결. 없으면 generation = 마지막+1로 `WAITING_APPROVAL` 생성. unique 위반은 "이미 있음"으로 처리(예외를 삼키지 말고 기존 행을 다시 읽음)
  - `approve(work_id, expected_version, expected_snapshot, principal)`: snapshot hash 일치 확인 → `WAITING_NOTIFICATION` + `WORK_STARTING` outbox INSERT(같은 트랜잭션). 자동 승인 정책(trusted author)도 이 함수를 쓴다
  - `check_human_work(issue)`: 사람 assignee, 타인이 연 PR(본문에 Issue 참조) → `HUMAN_WORK_IN_PROGRESS`
  - `recheck_scope(work)`: Issue open·snapshot hash·권한·cancel flag. 실패 시 `ISSUE_SCOPE_CHANGED`로 BLOCKED 또는 재승인 대기
  - `retry(work_id, expected_version, note)`: terminal(BLOCKED·HANDED_OFF)만. 새 incident(`reopened_from`)·새 generation. 같은 요청 중복은 멱등 처리
  - `cancel(work_id)`: 시작 전 → CANCELLED + `WORK_CANCELLED` intent. RUNNING → `cancel_requested=1`(안전 경계에서 정지). 외부 결과 불명이면 바로 CANCELLED 금지
  - `start_attempt(work_id)`: **시작 게이트 가드**(시작 알림 ACCEPTED 확인)와 `READY → RUNNING` 트랜잭션(incident NEW→INVESTIGATING, attempt 발급, deadline·budget). 알림 쪽 조건은 W26에서 채운다. 이 함수 말고는 attempt를 만들 수 없다
- `ops_api.py` 추가 — `POST /ops/work-items/{id}/approve`, `/cancel`, `/retry`(docs/04 §4 최소 body)
- CLI·Makefile — `make approve-work WORK_ID= EXPECTED_VERSION=`, `make retry-work WORK_ID= REASON=`, `make cancel-work WORK_ID=`
- 테스트: `integration/test_work_claim_race.py`, `integration/test_work_lifecycle.py`

## 구현 단계

1. 경합 테스트를 먼저 쓴다: 스레드 2~8개가 각자 DB 연결로 같은 Issue에 `ensure_work`+`approve`를 동시에 호출.
2. 전이 함수는 W06의 `state.py`를 쓰고, 여기서는 업무 규칙(snapshot·사람 작업·scope)만 더한다.
3. ops endpoint는 W06의 idempotency를 거친다. 같은 키·다른 body → 409.
4. `one_running_work` 슬롯이 차 있으면 READY work는 대기 상태로 남는다(실패 아님).

## 수용 기준

- T-STATE-01 / T-ISS-04: 동시 claim·중복 poll·동시 로그 → 활성 work 1, `WORK_STARTING` 알림 1, attempt 최대 1.
- T-V4-02: retry 승인 중복 → 새 generation 1개. 같은 키·다른 body → 409.
- snapshot hash 불일치 승인 → 409 `ISSUE_SCOPE_CHANGED`. 오래된 version → 409 `STATE_CONFLICT`.
- T-ISS-06(일부): closed Issue·사람 PR·요구 변경 → 자동 진행 없음, 충돌 보고, reopen·force-push 없음.
- terminal work에 새 로그 → evidence만 추가, 새 generation 자동 생성 없음.
- `start_attempt` 외 경로로 attempt를 만들 수 없다(코드 검색 테스트 또는 구조로 보장).

## 금지·함정

- unique 위반을 "실패"로 올려 사용자에게 오류를 보내지 않는다. 기존 work를 반환한다.
- 늦게 온 receipt로 BLOCKED work를 되살리지 않는다.
- 타인이 진행 중인 작업을 가져오지 않는다.
