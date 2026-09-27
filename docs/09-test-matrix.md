# 09. 테스트 매트릭스

> 원본: [spec 09 §6·§10·§11](../spec/docs/09-scenarios-evaluation.md), [spec 03 §7](../spec/docs/03-api-contracts.md), [PACKAGE-VALIDATION §3](../spec/PACKAGE-VALIDATION.md).
> 테스트를 목록에 적었다고 통과한 것이 아니다. STATUS.md의 작업표·완료 보고에는 **실제로 실행한** 테스트 ID와 결과만 적는다.

## 1. 배치와 마커 (D55)

| 디렉터리 | 내용 | 외부 의존 |
|---|---|---|
| `linemedic/tests/unit/` | 순수 로직(전이 표, schema, 정책, 판정) | 없음 |
| `linemedic/tests/integration/` | 실제 SQLite + `FakeGitHub`·`FakeDocker`·`FakeClock` | 없음 (docker 마커 제외) |
| `linemedic/tests/live/` | 실제 GitHub·모델·sandbox·SMTP | 게이트 필요 |

| pytest 마커 | 의미 | 실행 명령 |
|---|---|---|
| (없음) | 기본 | `make test` |
| `docker` | 로컬 Docker 필요 (runner, MES 기동) | `make test-docker` |
| `live_github` | 전용 데모 repo에 실제 호출 (G2·G10) | `make test-live` |
| `live_model` | 실제 NVIDIA endpoint (G3) | `make test-live` |
| `live_sandbox` | 실제 OpenShell sandbox (G5) | `make test-live` |
| `live_smtp` | SMTP 선택 시 (G12) | `make test-live` |

`make test`는 `-m "not docker and not live_github and not live_model and not live_sandbox and not live_smtp"`로 실행한다. mock 성공을 통합 성공으로 보고하지 않는다.

## 2. core 자동 테스트 (spec 09 §6)

| ID | Given / When | 기대 | 파일 | 카드 | 등급 |
|---|---|---|---|---|---|
| T-AUTH-01 | agent token으로 `/ops/releases` 호출 | 403, 외부 변경 없음 | `unit/test_auth.py`, endpoint `integration/test_release_checks.py`(agent token·approve 역할 없는 operator → 403, GitHub·docker 호출 0) | W06·W12 | core |
| T-AUTH-02 | 다른 run/incident의 증거 요청 | 거부, 내용 미노출 | `unit/test_auth.py` | W06 | core |
| T-AUTH-03 | body에 `actor=verifier`·임의 status | schema/권한 거부 | `unit/test_auth.py` | W06 | core |
| T-IDEM-01 | 같은 키·같은 요청 두 번 | 같은 proposal/execution, 중복 PR 없음 | `integration/test_idempotency.py`, PR 경로 `integration/test_github_pr.py` | W06·W11 | core |
| T-IDEM-02 | 같은 키·다른 body | 409 | `integration/test_idempotency.py` | W06 | core (W25) |
| T-STATE-01 | log·Issue 처리 coroutine이 같은 work를 동시에 claim | 활성 work·attempt 1개, 시작 게이트 준수 | `integration/test_work_claim_race.py` | W25 | core |
| T-STATE-02 | broker/operator가 RESOLVED 시도 | 거부 | `unit/test_state_transitions.py` | W06 | core |
| T-STATE-03 | WORK_ORDER_DRAFTED 뒤 "사람 완료" 요청 | 복구 전이 없음 | `unit/test_state_transitions.py` | W06 | core |
| T-PATCH-01 | 기존 회귀·config·auth 파일 변경 | 거부 | `unit/test_patch_policy.py` | W10 | core |
| T-PATCH-02 | traversal·symlink·binary·rename patch | 거부 | `unit/test_patch_policy.py` | W10 | core |
| T-PATCH-03 | 파일 수/100줄 상한 초과 | 거부 | `unit/test_patch_policy.py` | W10 | core |
| T-REPRO-01 | 새 테스트가 base에서도 통과 | 거부 | `unit/test_repro_judgement.py` + `integration/test_patch_gate.py`(실제 git·로컬 pytest) + `integration/test_runner_docker.py`(docker) | W10 | core |
| T-REPRO-02 | 테스트 미수집·import 실패·timeout | 재현으로 불인정 | 위와 같음 | W10 | core |
| T-REPRO-03 | base 실패, candidate 실패 또는 회귀 실패 | PR 생성 안 함 | 위와 같음 | W10 | core |
| T-SOURCE-01 | 검사 뒤 PR head 변경 | 기존 검사로 배포 안 함 | `integration/test_release_checks.py` | W12 | core |
| T-SOURCE-02 | 최종 merge tree ≠ candidate tree | 재검사·승인 요구, 배포 중단 | `integration/test_release_checks.py` | W12 | core |
| T-SOURCE-03 | approved SHA가 test merge거나 unmerged | 거부 | `integration/test_release_checks.py` | W12 | core |
| T-EXEC-01 | PR 생성 직후 응답 timeout | UNKNOWN → 조회, 무조건 재생성 안 함 | `integration/test_execution_unknown.py` | W11 | core |
| T-EXEC-02 | 배포 중 프로세스 재시작 | 실제 image 관찰 전 재실행 안 함 | `integration/test_execution_unknown.py`(자동 재조회, H04). 재시작 → UNKNOWN·운영자 조정 전 재배포 없음은 W12 `integration/test_release_checks.py` | H04 | hardening |
| T-VERIFY-01 | 정상 로트·관찰 구간 정상 | t60 이후 PASS | `unit/test_verifier.py` | W05 | core |
| T-VERIFY-02 | HTTP 200·잘못된 집계/lot/schema | FAIL, RESOLVED 없음 | `unit/test_verifier.py` | W05 | core |
| T-VERIFY-03 | collector 중단·stream 누락 (heartbeat 기반) | INCONCLUSIVE | `unit/test_verifier.py` | H03 | hardening |
| T-VERIFY-04 | 4회 표본 통과 후 t45 오류 재발 | FAIL | `unit/test_verifier.py` | W05 | core |
| T-VERIFY-05 | 검증 중 image/fixture 변경 | INCONCLUSIVE | `unit/test_verifier.py` | W05 | core |
| T-RESET-01 | run archive/reset | 이전 run 증거·원격 main/PR 유지 | `integration/test_reset_archive.py` | W19 | core |
| T-UI-01 | 로그에 HTML/script | 문자열로 표시, 실행 안 됨 | `unit/test_dashboard_escape.py` | W18 | core |

core observer 기준(로그 스트림이 실제로 끊기면 INCONCLUSIVE)은 W05 core에서 시험한다. heartbeat·cursor 연속성 기반 공백 탐지(T-VERIFY-03)만 H03이다.

W12 배포: `integration/test_release_checks.py`가 사전 검사 거부(T-SOURCE-01~03·image 불일치·리뷰·lock), 재전송 멱등, 실패별 이관(fetch·재검사·빌드·stop·기동·timeout·inspect), DEPLOY 조정·재시작·CLI 체크리스트를 fake로 보고, `integration/test_release_docker.py`(docker)가 fixture commit을 실제 R0~R2·신뢰 레시피 빌드·image ID 기동·inspect·60초 검증·복원 절차까지 확인한다.

## 3. v4 core 회귀 테스트 (spec 09 §11)

| ID | 시험 | 필수 확인 | 파일 | 카드 |
|---|---|---|---|---|
| T-ISS-01 | 기존 Issue·신규 Issue | 확정 매칭 재사용 / no-match 신규 생성 | `integration/test_issue_matching.py` | W24 |
| T-ISS-02 | 유사 후보·incomplete 조회·PR 포함 목록 | 애매하면 triage, incomplete면 생성 금지, PR 제외 | `integration/test_issue_matching.py` | W24 |
| T-ISS-03 | 생성 후 timeout·bot marker 위조 | UNKNOWN 보존, 실제 author/receipt 재조회, 중복 POST 없음 | `integration/test_issue_matching.py` | W24 |
| T-ISS-04 | log/poll 동시 선점·중복 delivery | work·attempt·시작 알림 각각 1개 | `integration/test_work_claim_race.py` | W25 |
| T-ISS-05 | backlog·untrusted author·다른 repo | 자동 수정 0건, 기존 자료는 승인 경로 | `integration/test_issue_polling.py` | W23 |
| T-ISS-06 | 닫힌 Issue·사람 PR·요구 변경·재시작 | scope 재검사, 충돌 보고, 임의 reopen·force-push 없음 | `integration/test_issue_polling.py` | W23·W25 |
| T-NOT-01 | 시작 receipt보다 이른 실행 | writable workspace·attempt·패치 모두 금지 | `integration/test_start_gate.py`, workspace·token은 `integration/test_attempts.py`(W13) | W26·W13 |
| T-NOT-02 | 댓글/메일 접수 vs 실제 수신 표현 | receipt 저장, 열람·배달 추정 없음 | `integration/test_notifications.py` | W26 |
| T-NOT-03 | 발송 timeout·duplicate·재시작 | UNKNOWN·재조회, 같은 event 재발송 없음 | `integration/test_notifications.py` | W26 |
| T-NOT-04 | 실행 불가·모델 API 실패 | 고정 blocker report와 외부 알림/미전송 상태 | `integration/test_notifications.py` | W26 |
| T-NOT-05 | 복구 PASS + 결과 알림 실패 | RESOLVED 유지, 알림만 FAILED/UNKNOWN | `integration/test_notifications.py` | W26 |
| T-NOT-06 | 악성 수신자·링크·본문 | catalog 밖 전송 0, 비밀·멘션 정제 | `integration/test_notifications.py` | W26 |
| T-MEM-01 | PASS/FAIL/PR-only/권한 부족 | SUCCESS/FAILURE/UNVERIFIED/BLOCKED | `integration/test_case_memory.py` | W27 |
| T-MEM-02 | case event 재처리·revision·철회 | 중복 없음, live=최신, frozen=지정 revision, RETRACTED 제외 | `integration/test_case_memory.py` | W27 |
| T-MEM-03 | 다른 repo·미래 note·holdout | ACL/snapshot/cutoff 위반 비노출 | `integration/test_case_memory.py` | W27 |
| T-MEM-04 | exact/keyword·no-hit·unavailable | 실제 검색·인용, 오류를 no-hit로 바꾸지 않음 | `integration/test_case_memory.py` | W27 |
| T-MEM-05 | 과거 잘못된 해결·stale source | 실패 조건 표시, 현재 source·업무 재검증 | `integration/test_case_memory.py` | W27 |
| T-MEM-06 | case 안 prompt injection | 권한 확대·임의 외부 전송·검증 생략 없음 | `integration/test_agent_context.py` | W28 |
| T-V4-01 | v2 요청·work scope 바꿔치기 | schema reject, cross-work/incident 거절 | `unit/test_proposal_schema.py` | W09 |
| T-V4-02 | 같은 key·다른 body·retry 승인 중복 | 409, 새 generation 중복 생성 없음 | `integration/test_work_lifecycle.py` | W25 |

W13 통합: `integration/test_e2e_fake.py`가 `make start`와 같은 조립(`build_control_plane`)으로 S1 감지 → Issue 생성 → 승인 → 시작 댓글 receipt → attempt → 사람 제안(ScriptedAdapter) → 게이트 → 봇 PR → (사람) 머지 → (사람) 배포 승인 → verifier PASS → 결과 댓글을 돌고, receipt < attempt 시작·결합 전이 표 일치·origin manual_integration을 확인한다. `integration/test_attempts.py`는 attempt 실행(workspace·context·token·결과별 종료·deadline·재시작), `integration/test_control_plane.py`는 조립·기동 복구·pid 파일과 실제 `start`·`stop` 프로세스를 본다.

## 4. DDL 제약 재현 (W06, `integration/test_ddl_constraints.py`)

PACKAGE-VALIDATION §3의 20건을 fresh DB에서 다시 확인한다: single active run, active fingerprint unique, incident FK run, incident status CHECK, incident nonnegative count, single active work per Issue, work generation unique, work incident unique, work Issue FK, work generation positive, single global RUNNING work, API request scope-key unique, notification logical key unique, notification state CHECK, case source event unique, case revision unique, case outcome CHECK, case supersedes FK, FTS5 필터 질의(W27), `PRAGMA foreign_key_check` 빈 결과.

DDL이 강제하지 못하는 것(cross-row 도메인 검사, 권한, start receipt와 generation 결합, 409 응답, reconcile)은 위 §2·§3 테스트로 확인한다.

## 5. 계약 테스트 (W06·W09, `unit/test_api_contract.py`)

[04-api-reference.md §6](04-api-reference.md)의 목록: schema 필드·enum 일치, 잘못된 category/action, `actions[]`, token 교환, 다른 run 접근, 오래된 version, 위조 model/role, 임의 path·URL, 202 오해, 다른 work/Issue 범위, 시작 알림 미확인 상태의 제안, spoofed issue marker, history projection ACL, 중복 approval·retry.

## 6. live 테스트 (게이트 후)

| 파일 | 마커 | 확인 | 카드 |
|---|---|---|---|
| `live/test_github_smoke.py` | live_github | 전용 repo 조회·Issue 생성·댓글 1회, repo 숫자 ID, bot author | W22 |
| `live/test_issue_live.py` | live_github | S4-new, S4-existing 각 1회, S5-new polling 감지 1회 | W23·W24 |
| `live/test_notification_live.py` | live_github | 시작 댓글 receipt, S6 차단 알림 1회 | W26 |
| `live/test_model_toolcall.py` | live_model | Nemotron tool 선택 → 결과 재입력 → 제안 | W02·W14 |
| `live/test_sandbox_probe.py` | live_sandbox | 허용(tools API·추론)·금지(mock sink) 대조, 거절 로그 | W17 |

## 7. 시나리오 실행 (E2E, `make evaluate`)

pytest가 아니라 실제 run으로 수행하고 [spec templates/run-record.md](../spec/templates/run-record.md) 양식으로 `runs/<run_id>/run-record.md`에 기록한다.

| 시나리오 | 목표 횟수 (spec 09 §7·§12) | 분모 주의 | 카드 |
|---|---:|---|---|
| S1 실제 agent 전체 경로 | 3 | 사람 리뷰·승인 개입 시간 기록 | W15·W20 |
| S2-lite 기본 / recent-deploy | 2 / 1 | 변형별 분리 | W16·W20 |
| S1b | ≥1 | agent 성능 분모에서 제외 | W05·W20 |
| S3-A | ≥1 | 공격하 업무 완주 여부 별도 | W17 |
| S3-B / S3-C | 필수 항목 각 1세트 | 결정론 결과, 모델 성공률과 합산 안 함 | W17 |
| S4-new / S4-existing / S4-ambiguous | 실제 API 각 1 | | W24·W29 |
| S5-new / S5-duplicate | 실제 감시 1 | | W23·W25·W29 |
| S6-blocked | 외부 알림 ≥1 | | W26·W29 |
| S7 cold_start vs memory_assisted | 최소 3쌍 (부족하면 1쌍 시연, 통계 주장 금지) | 같은 모델·예산·snapshot 고정 | W29 |

1회만 했으면 1회라고 적는다. run 시작 뒤의 API 오류·timeout·오판을 누락하지 않는다.
