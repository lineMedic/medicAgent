# SPEC — LineMedic v4 기술 명세 개요

> **문서 지위**: 구현이 지켜야 할 계약을 한 파일에서 찾도록 정리한 **계약 지도**이며 정본이 아니다. 필드·enum·DDL·상수의 원문은 각 절의 링크(spec/docs)에 있고, 이 문서는 원문을 복사하지 않는다. 충돌하면 spec → [DECISIONS.md](DECISIONS.md) → 이 문서 순으로 따른다([AGENTS §8](AGENTS.md)).
> **현재 상태**: 코드 없음. 아래 API·명령·파일은 모두 **구현 목표**다. 구현되지 않은 명령을 있다고 안내하지 않는다.
> 관련 개요: [PRD.md](PRD.md)(무엇을·왜) · [ARCHITECTURE.md](ARCHITECTURE.md)(구조) · [ADR.md](ADR.md)(결정 색인)

## 1. 버전과 변경 규칙

- wire schema는 `linemedic.v4`다. 모든 요청·응답에 `schema_version`이 있어야 하고, 다른 값(`linemedic.v2` 등)은 422로 거부한다([D40](ADR.md)).
- 이 API는 LineMedic이 새로 만드는 계약이다. NVIDIA·OpenClaw 기본 API가 아니다.
- 공개 계약(API 필드·enum·DDL·상태 전이·검증 조건)을 바꾸면 docs·테스트·관련 카드를 **같은 커밋**에서 고치고 DECISIONS.md에 `결정 / 이유 / 영향 문서 / 재시험`을 남긴다([spec 14 §6](spec/docs/14-decisions-sources.md), [ADR.md §5](ADR.md)).

## 2. 시스템 경계

| 방향 | 대상 | 방식 | 정본 |
|---|---|---|---|
| 입력 | 합성 MES 로그 | stdout JSON Lines를 host가 읽음(D57) | [spec 15 §3](spec/docs/15-issue-intake-workflow.md) |
| 입력 | 등록 repo의 GitHub Issue | 60초 주기 outbound polling, 한 repo | [spec 15 §5](spec/docs/15-issue-intake-workflow.md) |
| 입력 | 카메라 지표(S2-lite) | 합성 지표를 도구로 조회 | [spec 09 §3](spec/docs/09-scenarios-evaluation.md) |
| 입력 | 운영자 명령 | `/ops/*` HTTP + host CLI | [spec 03 §5](spec/docs/03-api-contracts.md), [spec 11 §3](spec/docs/11-runbook.md) |
| 출력 | GitHub Issue 생성·댓글·PR | 봇 credential로 Control Plane만 씀 | [spec 06 §6](spec/docs/06-broker-runner.md), [spec 16](spec/docs/16-notifications.md) |
| 출력 | 정비 요청 초안 | 로컬 저장만, `delivery_status=not_sent` | [spec 06 §7](spec/docs/06-broker-runner.md) |
| 출력 | 배포 | 사람이 승인한 exact SHA의 image만 | [spec 08 §2](spec/docs/08-release-verification.md) |
| 외부 | NVIDIA 추론 endpoint | sandbox 안 에이전트가 승인된 경로로만 | [spec 12](spec/docs/12-nvidia-requirements.md) |

공개 webhook 수신, 여러 알림 채널 동시 발송, 다중 repo는 P1이다.

## 3. API

정본: [spec 03](spec/docs/03-api-contracts.md). 요약 참조: [docs/04](docs/04-api-reference.md).

### 3.1 공통 규칙

| 항목 | 규칙 |
|---|---|
| 인증 | Bearer token. `/tools/*`는 run·incident·work·attempt 범위의 agent token, `/ops/*`는 operator token. 서로 바꿔 쓰면 403 |
| 권한 | 서버가 token에서 얻은 principal만 쓴다. body의 `actor`·`role`·`status`·`model`·`policy_version` 같은 필드는 **거부**한다 |
| 입력 검증 | 알 수 없는 필드, 중복 JSON key, 잘못된 enum·타입, 크기 초과를 거부(D44) |
| 멱등성 | 모든 변경 요청에 `Idempotency-Key` 필수. 같은 키·같은 본문이면 저장된 응답, 다른 본문이면 409 `IDEMPOTENCY_CONFLICT` |
| 외피 | 성공은 `data`·`evidence_ids`, 실패는 `error{code, message, retryable, details}` |
| 형식 | 시각은 UTC RFC3339 `Z`, Git SHA는 40자 전체 |
| 금지 | force-resolve, 임의 state PATCH, arbitrary exec, 임의 URL 호출·검증, 시나리오 주입·reset의 공개 endpoint |

### 3.2 에이전트 도구 `/tools/*` — 정확히 9개

| 도구 | 목적 |
|---|---|
| `get_incident` | 사건·특징·배포 identity·work·Issue ref·시작 알림·memory mode·증거 ID |
| `search_logs` | 사건 주변의 정제된 로그(개수·크기 상한, 정규식·shell 없음) |
| `get_deploys` | 등록 서비스의 최근 배포와 현재 base SHA |
| `get_knowledge` | 허용된 정적 매뉴얼·런북 절(과거 사례 아님, URL fetch 없음) |
| `query_equipment_metrics` | 등록 설비의 지표·baseline·품질 |
| `get_bound_issue` | 서버가 확정한 repo·Issue·work·snapshot·상태 |
| `search_cases` | 현재 scope와 고정 snapshot 안의 과거 사례 |
| `submit_proposal` | 제안 제출. **202 접수**이며 실행 성공이 아니다 |
| `get_proposal` | 제안의 decision·거절 사유 |

- 파일 읽기·grep·로컬 테스트는 에이전트의 **sandbox 로컬 작업**이다. 범용 파일 접근이나 원격 exec endpoint를 만들지 않는다.
- `create_issue`·`comment_issue`·`send_mail`·`notify` 같은 도구는 만들지 않는다. Issue·알림은 Control Plane의 lifecycle 기능이다.
- 도구 응답에 시나리오 이름, 정답 category, 기대 fixture를 넣지 않는다.

### 3.3 제안 — 액션 정확히 1개

| `action.type` | 쓰는 경우 | 핵심 제약 |
|---|---|---|
| `create_pr` | `category=code_bug` | unified diff + 새 재현 테스트 경로. 명령·환경·URL·branch 이름은 받지 않고 브로커가 재검사 |
| `create_work_order_draft` | `category=equipment` | 등록 설비 ID, 가설로서의 원인, 허용 매뉴얼 참조. 안내 문구는 브로커가 승인 템플릿으로 채움(D58) |
| `escalate` | 모든 category | blocker_code, 열린 질문, 필요한 조치. 증거 0개 허용 |

- `config`·`infra`·`external_dependency`·`unknown`은 `escalate`만 허용한다(category ↔ action 대응: [docs/03 §4](docs/03-domain-model.md)).
- `actions[]`, `confidence`는 받지 않는다(422).
- 한 attempt에서 형식·정책 오류로 인한 제출은 합산 2회(수정 1회)까지다. deadline은 그대로다.
- 접수 순서: 인증·scope·시작 알림 receipt·크기·구조 검사 → 저장 후 202 → 백그라운드 검사 `RECEIVED → CHECKING → ALLOWED / REJECTED`. ALLOWED도 외부 실행 완료가 아니다.

### 3.4 운영 API `/ops/*`

| 분류 | endpoint (요지) |
|---|---|
| 읽기 | dashboard, incident 상세, Issue 후보, notifications, case note |
| Issue | GitHub sync 1회, incident ↔ Issue 명시 연결 |
| work | approve(정확한 Issue snapshot 승인), cancel, retry(새 generation) |
| 알림·실행 | notification reconcile, execution reconcile(읽기·기록만, 재실행 금지) |
| 릴리스 | releases(정확한 최종 SHA 승인) |
| 기억 | 파생 검색 인덱스 재구축 |
| run | 새 run, archive(삭제 아님), incident escalate |

전체 경로·최소 body: [docs/04 §4](docs/04-api-reference.md). URL·원격 저장소·이미지 태그는 서버 catalog에서 정하고 요청으로 받지 않는다.

### 3.5 오류 코드

HTTP 401/403/404/409/413/422/429/503에 대응하는 코드와 자동 재시도 규칙은 [spec 03 §6](spec/docs/03-api-contracts.md)과 [docs/03 §5](docs/03-domain-model.md)에 있다. 409 계열(`STATE_CONFLICT`, `SOURCE_CHANGED`, `ISSUE_SCOPE_CHANGED`, `START_NOTICE_UNCONFIRMED` 등)은 원본 상태를 확인하기 전에 재시도하지 않는다.

## 4. 데이터 모델

정본 DDL: [spec 04 §5](spec/docs/04-data-state.md). 구현 시 `migrations/0001_init.sql`에 **원문 그대로** 옮긴다(D45).

| 테이블 | 역할 |
|---|---|
| `demo_runs` | 활성 run(동시에 하나), config·memory snapshot manifest |
| `incidents`, `evidence` | 관찰된 장애와 그 증거(과거 사례의 history projection 포함) |
| `proposals`, `executions`, `verifications`, `audit_events` | 제안·외부 실행 intent/결과·업무 검증·감사 원본 |
| `github_issues`, `issue_bindings` | Issue mirror와 승인된 연결 |
| `work_items` | Issue 단위 승인·시작 게이트·중복 선점 |
| `integration_state` | sync checkpoint·ETag·조회 완전성 |
| `api_requests` | 변경 요청의 scope·멱등키·본문 hash |
| `notifications` | 알림 outbox·provider receipt·실패 사유 |
| `case_notes`, `case_retrievals` | 결과별 사례 revision, 검색·인용 기록 |
| `case_search` | 재구축 가능한 FTS5 파생 인덱스(원본 아님) |

| 항목 | 규칙 |
|---|---|
| 식별자 | run_id `r-YYYYMMDD-HHMMSS-xxxx`, 엔티티 ID는 접두사 + 대문자 16진 12자(D50) |
| routing_scope | `live` 또는 `eval:<run_id>`. 평가 격리 수단이며 fingerprint에는 run을 넣지 않는다(D29) |
| problem_fingerprint | 정규화 signature의 SHA-256. 중복 후보 탐색 키이지 같은 원인의 증명이 아니다 |
| 동시성 제약 | Issue당 활성 work 하나, 전역 RUNNING work 하나 |
| 시각 | UTC 마이크로초 `Z`, 시간 판정은 주입 가능한 Clock(D51) |
| 트랜잭션 | `BEGIN IMMEDIATE`, 모든 상태 변경은 version CAS. 영향 0행이면 `STATE_CONFLICT` |

SQL이 강제하지 못하는 도메인 검사(run·repo·Issue 일치, 시작 알림 소속, `VERIFIED_SUCCESS` 조건 등): [spec 04 §6](spec/docs/04-data-state.md), [docs/03 §9](docs/03-domain-model.md).

## 5. 상태 기계

정본: [spec 04 §2~§4](spec/docs/04-data-state.md). 전이표 전체: [docs/03 §2·§3](docs/03-domain-model.md). **표에 없는 전이는 전부 거부**한다.

| 대상 | 상태 |
|---|---|
| incident | `NEW`, `INVESTIGATING`, `VALIDATING`, `PR_OPENED`, `DEPLOYING`, `VERIFYING`, `WORK_ORDER_DRAFTED`, `RESOLVED`, `ESCALATED`, `EXECUTION_UNKNOWN` |
| work item | `WAITING_APPROVAL`, `WAITING_NOTIFICATION`, `READY`, `RUNNING`, `WAITING_REVIEW`, `WAITING_VERIFICATION`, `HANDED_OFF`, `SUCCEEDED`, `BLOCKED`, `CANCELLED`, `EXECUTION_UNKNOWN` |

핵심 규칙:

1. `RESOLVED`(incident)와 `SUCCEEDED`(work)는 **verifier 내부 경로만** 쓴다.
2. `READY → RUNNING`은 시작 알림 `ACCEPTED`·Issue scope·취소 flag를 재확인하고, incident `NEW → INVESTIGATING`과 attempt 발급을 **한 트랜잭션**에서 한다.
3. `HANDED_OFF`·`SUCCEEDED`·`BLOCKED`·`CANCELLED`는 terminal이다. 재시도는 운영자 승인으로 새 incident + 새 generation + 새 시작 알림 + 새 attempt다.
4. incident와 work는 결합 전이로 같이 바뀌고, 결과 알림 intent도 같은 트랜잭션에 기록한다. **알림 상태는 결합 전이와 독립**이다. 알림이 실패해도 RESOLVED는 유지된다(D39).
5. 늦게 도착한 receipt가 BLOCKED work를 되살리지 않는다.

## 6. 처리 흐름과 트랜잭션 경계

정본: [spec 04 §7](spec/docs/04-data-state.md). 흐름별 의사코드: [docs/05](docs/05-workflows.md).

**공통 패턴**: `TX{ 검사·CAS·intent 기록·audit }` → `EXT: 외부 호출` → `TX{ 결과 기록 }`. GitHub·모델·SMTP·Docker 호출을 트랜잭션 안에 두지 않는다. 외부 응답이 불명이면 `UNKNOWN`으로 기록하고 재조회(reconcile)만 한다.

| # | 흐름 | 요지 |
|---|---|---|
| ① | 로그 → incident | signature → fingerprint, 같은 fingerprint 반복 시 사건 후보, 활성 incident가 있으면 증거만 추가 |
| ② | incident → Issue | 기존 binding → 우리 생성 기록 → 승인된 구조화 일치 → 모호함(생성 안 함) → 완전 조회 후 없음(생성). 조회 불완전이면 `LOOKUP_INCOMPLETE`로 정지 |
| ③ | Issue polling → work | 최초 import는 관찰만, 이후 새 Issue를 작성자 ID allowlist·범위로 검사. 댓글은 트리거가 아님 |
| ④ | claim → 시작 게이트 → attempt | work 선점과 `WORK_STARTING` outbox를 한 TX, provider receipt 후 READY, 대기 한도 초과 시 `START_NOTICE_UNCONFIRMED` |
| ⑤ | 제안 → 브로커 | `create_pr`·`create_work_order_draft`·`escalate` 3분기 |
| ⑥ | 리뷰·머지 → 릴리스 → 검증 | 사람 머지(G7), 사람 배포 승인(G8), exact SHA 재검사·빌드·기동, verifier 판정 |
| ⑦ | UNKNOWN 조정 | 정확한 identity로 외부 상태 조회 후 기록. core는 운영자 CLI, 자동 재조회는 H04 |
| ⑧ | 재시도·취소 | 새 generation, 실행 중 취소는 안전 경계에서 정지 |
| ⑨ | run 생성·reset·archive | 원격 이력·Issue·PR·사례 보존, run ID가 붙은 자원만 정리 |

외부 재수행 방지 논리 키(CREATE_ISSUE·CREATE_PR·DEPLOY·DRAFT_WORK_ORDER·알림)는 서버가 만든다: [docs/03 §8](docs/03-domain-model.md).

## 7. 불변식

정본: [spec 01 §3](spec/docs/01-requirements.md). 강제 위치와 시험 ID: [docs/06 §1](docs/06-invariants.md). 불변식은 프롬프트 약속이 아니라 **코드로** 강제한다.

| ID | 불변식 |
|---|---|
| INV-01 | `RESOLVED` 작성은 verifier 내부 경로만 |
| INV-02 | 사람 승인 없는 외부 배포 금지 |
| INV-03 | runtime 에이전트는 Control Plane·테스트 기준·배포 권한을 바꿀 수 없음 |
| INV-04 | 에이전트 출력과 생성 코드는 비신뢰 |
| INV-05 | 원인 분류(category)와 조치(action)가 대응해야 함 |
| INV-06 | 실행 결과 불명 시 재조회 전 중복 실행 금지 |
| INV-07 | 증거 ID·모델 confidence는 정확성 보증이 아님 |
| INV-08 | `WORK_ORDER_DRAFTED`·`ESCALATED`는 미복구 |
| INV-09 | 평가 정답·holdout은 항상 비공개, cold_start는 이전 해답 제외, memory_assisted는 사전 고정 사례만 |
| INV-10 | 실제 PLC·설비 제어·현장 지시 자동 발송 금지 |
| INV-11 | Issue 생성/closed/PR merge는 업무 복구 판정이 아님 |
| INV-12 | 시작 알림 receipt 전 코드 수정·agent attempt 금지 |
| INV-13 | Issue 본문·댓글의 승인 주장이나 작성자 문자열로 실행 권한을 주지 않음 |
| INV-14 | 사례 유사도·과거 성공은 현재 실행 승인·정답이 아님 |
| INV-15 | 로그/Issue/사례에서 받은 수신자·URL로 알림을 보내지 않음 |
| INV-16 | 외부 API 불명·조회 누락은 "없는 Issue"·"발송 완료"가 아님 |

## 8. 보안 경계

정본: [spec 07](spec/docs/07-security.md). 요약: [docs/06 §3·§4](docs/06-invariants.md).

- **신뢰**: 호스트 운영자, 고정 실행기 코드, 보호 계약·테스트, 서버 action catalog, 승인된 빌드 레시피.
- **비신뢰**: 로그 memo, 도구 반환 자연어, 모델 판단, 생성 diff·테스트, 그 코드를 실행하는 runner와 MES, Issue 본문·댓글, 과거 사례 텍스트.
- 에이전트는 OpenShell sandbox 안에서 자기 code copy, 자기 사건의 `/tools/*`, 승인된 추론 경로만 쓴다. `/ops/*`·DB·GitHub·Docker·평가 기대값에 접근하지 않는다.
- runner는 네트워크 없이 고정 image와 자원 상한으로 실행한다. `subprocess`는 argv 리스트만 쓴다.
- 보호 profile을 검증하지 못하면 새 외부 작업을 하지 않는다(`PROTECTION_UNAVAILABLE`).
- S3는 서로 다른 세 시험이다: **A** 로그 인젝션에 대한 에이전트 반응, **B** 브로커 거절, **C** sandbox 대조 프로브. 접속 불가를 곧바로 정책 차단으로 판정하지 않는다.

## 9. 브로커·릴리스·검증

정본: [spec 06](spec/docs/06-broker-runner.md), [spec 08](spec/docs/08-release-verification.md).

| 단계 | 계약 요지 |
|---|---|
| 패치 정책 | 업무 파일 1개 수정 + 새 재현 테스트 1개. 경로·크기·형태·보호 파일 hash 검사 |
| R0 | base 회귀가 정상인지 확인 |
| R1 | base + 새 테스트만: 재현 테스트가 **실패**해야 한다 |
| R2 | candidate: 새 테스트와 보호 회귀가 모두 통과 |
| PR | 봇이 `baseline/<run_id>` 기준으로 생성, 본문은 `Related to #n`(closing keyword 없음), 자동 머지 없음 |
| identity chain | base → patch hash → candidate → PR head → approved merge → tree → image → container → contract·fixture hash. PR head = candidate, 최종 tree = candidate tree를 요구 |
| 릴리스 | 사람이 exact SHA를 승인하면 사전 검사 후 재현·회귀 재실행, 신뢰 레시피로 빌드, image ID 대조 |
| 업무 검증 | `defect-summary-v1` 계약으로 정해진 시점에 표본 호출, 관찰 구간이 끝나기 전 PASS 금지 |
| 판정 | `PASS` → RESOLVED, `FAIL`(실제 반증) → ESCALATED, `INCONCLUSIVE`(수집 중단·identity 변경 등) → ESCALATED |

자동 rollback·자동 재조사·추가 패치는 없다. 로그 관찰 가능성을 확인하지 못하면 PASS가 아니다.

## 10. 알림과 사례 기억

### 10.1 알림 — 정본 [spec 16](spec/docs/16-notifications.md)

- 채널: 기본 `github_comment` 1개. SMTP는 G12에서 선택할 때만 **교체**한다(동시 발송은 P1).
- 이벤트 7종: `WORK_STARTING`, `WORK_BLOCKED`, `PR_READY`, `HANDOFF_DRAFTED`, `RECOVERY_VERIFIED`, `RECOVERY_NOT_VERIFIED`, `WORK_CANCELLED`.
- outbox 상태: `PENDING → SENDING → ACCEPTED / FAILED / UNKNOWN`. `DELIVERED_TO_HUMAN`·`READ` 상태는 만들지 않는다. ACCEPTED는 "공급자가 접수했다"는 뜻이다.
- 수신자·route는 host config의 catalog에서만 정한다(INV-15).
- blocker report 필드와 blocker_code 14종: [docs/03 §5](docs/03-domain-model.md), 양식 [spec templates/blocker-report.md](spec/templates/blocker-report.md).

### 10.2 사례 기억 — 정본 [spec 17](spec/docs/17-case-memory.md)

| outcome | 생성 조건 |
|---|---|
| `VERIFIED_SUCCESS` | verifier PASS + 정확한 image·contract·관찰 범위 연결 |
| `VERIFIED_FAILURE` | 실제 업무 검사 FAIL |
| `UNVERIFIED` | PR·테스트는 준비됐지만 업무 검증 없음 |
| `BLOCKED` | 권한·범위·자료 부족·정책 거부·source 충돌·예산 |
| `INCONCLUSIVE` | timeout·수집 공백·대상 불명 |
| `HANDOFF` | 정비 초안 생성 |

- outcome은 DB 원본으로 서버가 정한다. 에이전트가 직접 쓰지 않고, 모델 자기보고로 분류하지 않는다.
- 검색: 필터(ACL·repo·서비스·publish·snapshot) → exact fingerprint + FTS5(없으면 `keyword_fallback`) → top_k 상한 → 현재 incident의 history projection evidence로 인용(D54).
- 검색 상태 `OK`·`NO_HIT`·`DISABLED`·`UNAVAILABLE`을 구분한다. 오류를 NO_HIT로 바꾸지 않는다.
- `cold_start`는 검색을 끄고(DISABLED), `memory_assisted`는 사전에 고정한 snapshot manifest의 사례만 쓴다(D38).

## 11. 설정과 환경

| 종류 | 위치 | 정본 |
|---|---|---|
| 비밀 아닌 설정 | `config/linemedic.toml`(repo·service·route·equipment catalog, 한도, 예산). `tomllib` → pydantic strict·extra forbid로 검증하고 모르는 키를 거부한다. 미확정 값은 키 생략. 병합한 설정의 canonical JSON SHA-256을 run manifest에 기록 | [D52·D60](ADR.md) |
| 정책·계약·평가 기대값 | `broker_policy.toml`, `manual_templates.toml`, `defect-summary-v1.toml`, `scenario_expectations.toml`(에이전트 비노출). OpenShell 정책만 외부 schema라 YAML | [D60](ADR.md) |
| 비밀·run별 값 | 환경 변수. `.env.example`에는 **이름만** 둔다 | [spec 11 §2](spec/docs/11-runbook.md) |
| 상수·한도·fixture | 설계값 | [docs/07](docs/07-constants.md) |

에이전트와 runner에는 환경 파일 전체를 넘기지 않고 필요한 최소 변수만 전달한다. 모델 ID·endpoint·runtime은 스파이크로 확인하기 전까지 코드에 고정하지 않는다.

## 12. 테스트와 명령

정본: [spec 09 §6·§11](spec/docs/09-scenarios-evaluation.md). 배치·ID: [docs/09](docs/09-test-matrix.md).

| 계층 | 위치·마커 | 외부 의존 | 인정 상태 |
|---|---|---|---|
| unit | `linemedic/tests/unit/` | 없음 | `UNIT_TESTED` |
| integration | `linemedic/tests/integration/` — 실제 SQLite + Fake GitHub·Docker·Clock | 없음 | `UNIT_TESTED` |
| docker | 마커 `docker` | 로컬 Docker | 실제 Docker로 확인하고 증거가 있으면 `LIVE_VERIFIED` ([AGENTS 규칙 13](AGENTS.md)) |
| live | 마커 `live_github`·`live_model`·`live_sandbox`·`live_smtp` | 게이트 필요 | 증거가 있으면 `LIVE_VERIFIED` |

목표 명령(B00 이후 생성): `make setup`, `make test`, `make test-docker`, `make test-live`, `make lint`, `make doctor`. 운영 CLI 목표 이름은 [spec 11 §3](spec/docs/11-runbook.md), 명령별 구현 카드는 [docs/08 §8](docs/08-task-plan.md)에 있다. 실패 테스트를 지우거나 기대값을 약하게 만들어 통과시키지 않는다.
