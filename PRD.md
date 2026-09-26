# PRD — LineMedic v4 제품 요구 개요

> **문서 지위**: 제품 요구를 한 파일로 읽기 위한 개요이며 정본이 아니다. 요구의 정본은 [spec 00](spec/00-MASTER-PLAN.md)과 [spec 01](spec/docs/01-requirements.md)이다. 충돌하면 spec → [DECISIONS.md](DECISIONS.md) → 이 문서 순으로 따른다([AGENTS §8](AGENTS.md)).
> **현재 상태**: 코드 없음. 아래 기능은 모두 **구현 목표**이며 동작하는 기능이 아니다. 진행 상황은 [STATUS.md](STATUS.md)에 있다.
> 관련 개요: [SPEC.md](SPEC.md)(계약) · [ARCHITECTURE.md](ARCHITECTURE.md)(구조) · [ADR.md](ADR.md)(결정 색인)

## 1. 한 문장 정의

LineMedic은 **서버 로그 또는 등록된 GitHub Issue를 받아, 같은 작업을 중복으로 만들지 않고 담당 Issue에 연결하고, 작업 시작을 먼저 알린 뒤 코드 수정 제안 또는 설비 점검 요청 초안을 만들며, 결과와 실패 원인을 검증 수준과 함께 저장해 다음 조사에 재사용하는** 공장 IT 에이전트다.

- 대상 행사: Korea Agentic AI Hackathon(패스트캠퍼스 × NVIDIA) 온라인 예선.
- 환경: 가상 3라인(L3), 합성 MES 로그, 가짜 카메라 지표, 팀 소유 데모 저장소 `l3-mes-api` 하나.
- "대응 이력 재사용"은 모델 재학습이 아니다. 검색한 사례를 근거로 읽고 **현재 상황에서 다시 검증**하는 것이다.

## 2. 문제 정의

공장 IT 장애 대응에서 로그, GitHub Issue, 수정 이력이 따로 있으면 다음 문제가 생긴다([spec 13 §5](spec/docs/13-demo-submission.md)).

1. **중복과 충돌**: 같은 오류를 여러 번 조사하거나, 이미 사람이 진행 중인 작업과 부딪힌다.
2. **분기 판단**: AI가 수정안을 내도, 코드를 고칠 일인지 설비를 점검할 일인지 구분해 전달해야 한다.
3. **복구 판정**: PR이 생기거나 머지됐다고 실제 업무가 복구된 것은 아니다.
4. **불가 사유 전달**: 해결하지 못했을 때 이유·근거·해 본 일·필요한 조치가 사람에게 가야 한다.
5. **사례의 오염**: 과거의 실패나 미검증 시도를 성공 사례처럼 재사용하면 안 된다.

LineMedic은 이 문제를 합성 MES·설비 시뮬레이터에서 다룬다. 실제 공장 성능으로 확대 해석하지 않는다.

## 3. 사용자와 사용 범위

| 사용자 | 필요한 결과 | 제품이 대신하지 않는 일 |
|---|---|---|
| 개발자·IT 당직 | 증거, 재현 테스트, 최소 패치, 검사 결과 | 변경 승인과 원인 해석의 최종 책임 |
| 정비 담당 | 설비 ID·관찰 지표·미확인 원인이 있는 요청 초안 | 실제 설비 점검·작업 안전 절차 |
| 데모 운영자 | 장애 주입, 정확한 릴리스 선택, 반복 평가 | 모델 답변을 사후 편집해 성공 만들기 |
| 평가자(심사) | 원본 실행, 실패, 코드·이미지·정책 버전 | 실제 공장 성능·보안 인증으로 확대 해석 |

입력은 합성 로그·fixture·가짜 카메라 지표와 팀 소유 GitHub 저장소의 승인된 Issue뿐이다. 회사 시스템, 비밀, 개인정보에 연결하지 않는다([spec 01 §1](spec/docs/01-requirements.md)).

## 4. 목표와 비목표

### 4.1 core-v4 (반드시 완료)

- 한 repo, 서비스 매핑 하나, 동시 에이전트 1개.
- 새 Issue는 **60초 주기 polling**으로 접수한다. 알림 채널은 GitHub Issue 댓글 1개다. SMTP는 G12에서 선택할 때만 교체한다.
- FastAPI + SQLite. 사례 검색은 exact fingerprint + SQLite FTS5 lexical 검색이다. 임베딩 검색이라고 부르지 않는다.
- 두 입력의 중복 claim 방지(H01)와 같은 멱등키·다른 본문 409(H02)는 core다([D35](ADR.md)).
- 시작 알림 확인 전 수정 금지, 결과 불명 시 재조회만, 사례 신뢰 수준 구분, 로그 관찰 불가 시 PASS 금지도 core다.

### 4.2 hardening (core 이후, H03~H07)

로그 heartbeat·cursor(H03), 자동 bounded reconcile(H04), 빌드 레시피 hash(H05), 추가 테스트 조합(H06), 상세 화면(H07). 미완료면 "설계됨/미구현"으로 공개한다. **미완료 core를 hardening으로 재분류해 숨기지 않는다.** 상세: [tasks/H03-H07-hardening.md](tasks/H03-H07-hardening.md).

### 4.3 비목표 (P1, 만들지 않음)

공개 HTTPS webhook, 다중 저장소, 알림 채널 동시 발송, 임베딩·vector DB·reranker, 자동 정비 완료 수집, CMMS, 자동 재조사·머지 감시, 온프레미스 NIM, 비평가(critic) 에이전트, 두 번째 버그 유형.

### 4.4 계속 금지

자동 머지, 사람 승인 없는 배포, 모델이 지정한 수신자·웹훅 URL, 타인 작업 강탈, 실제 PLC 접속·제어, 회사·고객 데이터, 보호 테스트 수정, 무한 repair loop, 기존 Harness Toolkit 재설계.

## 5. 핵심 사용자 흐름

```text
로그 이상 → 기존 Issue 연결 / 없으면 생성 ┐
승인된 새 GitHub Issue → 60초 polling 접수 ├→ 단일 work 선점 → 시작 알림(접수 확인)
                                        ┘  → 허용된 사례 조회 → 에이전트 조사(sandbox)
                                           ├ 코드: 보호된 검사 → 봇 PR → 사람 리뷰·머지
                                           │        → exact SHA 배포 승인 → 독립 업무 검증
                                           ├ 설비: 정비 요청 초안 (서버 변경 없음)
                                           └ 불가: 구조화된 차단 보고
                                           → 결과·차단 알림 → case note 저장
```

- 로그에서 원인 조사를 위한 **읽기 전용 수집**은 Issue 연결 전에도 할 수 있다. writable 작업 공간과 패치 작성은 시작 알림 receipt 이후다.
- 두 입구(로그·Issue)는 에이전트를 따로 호출하지 않고 **같은 work 생성 경로**로 들어온다.
- 에이전트 액션은 `create_pr`, `create_work_order_draft`, `escalate` 세 개뿐이다. Issue 검색·생성, 선점, 알림, 결과 기록은 Control Plane이 한다. 모델에게 GitHub·메일 쓰기 권한을 주지 않는다.

단계별 트랜잭션 경계는 [docs/05-workflows.md](docs/05-workflows.md)에 있다.

## 6. 시나리오와 수용 기준

| ID | 장면 | 도착점 | 성공으로 오해하면 안 되는 것 | 수용 기준 |
|---|---|---|---|---|
| S1 | 코드 오류 | Issue → 시작 알림 → 실제 agent 패치 → PR → 사람 승인 → 업무 검증 PASS | PR 생성·머지·test PASS만으로는 복구가 아님 | AC-S1, AC-SBX |
| S2-lite | 설비 점검 | 같은 Issue에 정비 요청 초안·확인 사항, 서버 변경 0건 | 초안 작성 ≠ 설비 복구 | AC-S2, AC-SBX |
| S1b | 거짓 정상 | HTTP 200이지만 틀린 집계를 verifier가 `content_mismatch`로 거절 | 사람이 주입한 결함. agent 성과에 넣지 않음 | AC-S1B |
| S3 | 안전성 | agent 반응(A)·broker 거절(B)·sandbox 프로브(C)를 분리 기록 | 연결 실패 전체가 정책 차단은 아님 | AC-S3 |
| S4 | Issue 연결 | 신규 생성, 기존 재사용, 모호함·조회 불완전 시 안전 정지 | 제목이 비슷하다고 같은 원인으로 확정하지 않음 | AC-S4 |
| S5 | Issue 입력 | 승인된 새 Issue를 접수, 시작 알림보다 빠른 수정 0건 | 공개 Issue를 무조건 실행하지 않음 | AC-S5 |
| S6 | 불가 보고 | 이유 코드·근거·해 본 일·필요 조치가 있는 외부 알림 | 로컬 이벤트 저장만으로는 발송이 아님 | AC-S6 |
| S7 | 사례 재사용 | 검증된 성공·실패 사례를 찾아 현재 판단에 인용 | 과거 정답이 현재도 맞는다는 보장 없음 | AC-S7 |

S4~S7은 새 서비스가 아니라 S1·S2에 붙는 입구·출구다. 각 AC의 Given/When/Then 원문은 [spec 01 §4](spec/docs/01-requirements.md), fixture와 평가 절차는 [spec 09](spec/docs/09-scenarios-evaluation.md)에 있다.

## 7. 기능 요구 요약

모두 core다(HR 제외). 수용 기준 원문은 [spec 01 §2](spec/docs/01-requirements.md), 카드 대응은 [docs/08 §7](docs/08-task-plan.md)에 있다.

### 7.1 감지·증거

| ID | 요구 | 카드 |
|---|---|---|
| FR-01 | 장애를 감지하고 같은 run·fingerprint의 미해결 사건을 하나로 묶는다 | W07 |
| FR-02 | 시간·서비스·run이 연결된 증거를 ID·관찰 시각·출처와 함께 저장한다 | W07 |

### 7.2 Issue 입력과 work

| ID | 요구 | 카드 |
|---|---|---|
| FR-17 | 로그를 등록 repo의 기존 Issue에 연결하고, 조회가 완전하고 후보가 없을 때만 새로 만든다 | W24 |
| FR-18 | opt-in repo·승인된 작성자·범위 검사를 통과한 새 Issue를 주기적으로 접수한다. 기존 backlog는 자동 시작하지 않는다 | W23 |
| FR-19 | `(routing_scope, repo, issue)`당 활성 work 하나. 두 입력이 같은 작업을 중복 시작하지 않는다 (HR-01 승격) | W25 |
| FR-25 | 명시적 재시도·취소·재발 정책. 닫힌 Issue·사람 작업을 강탈하지 않고, 재시도는 새 generation | W25 |

### 7.3 알림

| ID | 요구 | 카드 |
|---|---|---|
| FR-20 | 시작 알림의 provider receipt를 저장한 뒤에만 writable workspace와 agent attempt를 시작한다 | W26 |
| FR-21 | 실행 불가 사유를 구조화된 blocker report로 만들어 실제 채널로 보낸다. 모델 실패 때도 고정 템플릿 사용 | W26 |
| FR-24 | PR_READY·WORK_BLOCKED·HANDOFF_DRAFTED·RECOVERY_VERIFIED를 서로 다른 메시지로 알린다. 알림 성공과 복구 성공은 독립 | W26, W28 |

### 7.4 에이전트·제안

| ID | 요구 | 카드 |
|---|---|---|
| FR-03 | 모델이 조회 도구를 선택해 쓰고 구조화된 제안을 낸다(실제 tool-call 왕복 최소 1건) | W14, W15 |
| FR-04 | 코드 문제의 패치와 재현 테스트를 실제 에이전트가 만든다 | W14, W15 |
| FR-09 | 설비 문제는 등록 설비·증거·가설·승인된 참조가 있는 정비 요청 초안을 만든다. 코드 변경 없음 | W08, W09, W16 |
| FR-15 | 에이전트를 OpenShell sandbox 안에서 실행하고 정책 hash·sandbox identity를 기록한다 | W15, W16 |

### 7.5 브로커·PR·릴리스·검증

| ID | 요구 | 카드 |
|---|---|---|
| FR-05 | 브로커가 경로·내용·테스트·기준 커밋·권한을 검사해야 PR로 갈 수 있다 | W10 |
| FR-06 | 봇 계정이 지정 저장소·기준 브랜치에만 PR을 만들고, 다른 사람이 리뷰한다. 자동 머지 없음 | W11 |
| FR-07 | 사람이 승인한 정확한 merge SHA만 배포하고, 이미지 ID와 실제 실행 이미지를 대조한다 | W12 |
| FR-08 | 업무 결과로 복구를 판정한다. verifier만 `RESOLVED`를 쓴다 | W05, W12 |
| FR-10 | 거짓 정상(HTTP 200 + 틀린 집계)을 `content_mismatch`로 거절한다 | W05, W12 |

### 7.6 사례 기억

| ID | 요구 | 카드 |
|---|---|---|
| FR-22 | 시도·오류·변경·결과를 case note로 저장하고, verifier PASS·의미적 실패·권한 차단·미검증을 구분한다 | W27, W28 |
| FR-23 | 필터 후 exact + 키워드로 이전 사례를 찾아 ID로 인용한다. no-hit와 검색 불가를 구분한다 | W27, W28 |
| FR-26 | 같은 멱등키·다른 본문 409, UNKNOWN 재실행 금지, cold_start/memory_assisted 분리 (HR-02 승격) | W06, W25, W27, W29 |

### 7.7 운영·보존·표시·환경

| ID | 요구 | 카드 |
|---|---|---|
| FR-11 | 금지 조치·권한·timeout(`EXECUTION_UNKNOWN`)·중복 논리 키·S3 결과를 각각 기록한다 | W06, W11, W17 |
| FR-12 | 모든 실행을 재현·보존한다. 초기화가 원격 이력·기존 평가를 지우지 않는다 | W19 |
| FR-13 | 상태·근거·변경·검사 결과를 DB에서 읽어 표시한다. 모르는 값은 미확인 | W18 |
| FR-14 | 실제 NVIDIA 사용 버전·성공 호출과 대회 조건 R1~R5 확인 상태를 기록한다 | W01, W02, W21 |
| FR-16 | 모든 평가를 한 데모 호스트에서 실행하고 호스트 manifest를 run에 연결한다 | B00, W00 |

### 7.8 hardening 요구

| ID | 요구 | 카드 | 미완료 시 공개 문구 |
|---|---|---|---|
| HR-01 | FR-19로 core 승격 | W25 | Issue/로그 동시 입력 완료를 주장하지 않음 |
| HR-02 | FR-26으로 core 승격 | W25 | 외부 생성 자동화 완료를 주장하지 않음 |
| HR-03 | 로그 수집 heartbeat·cursor | H03 | core의 "스트림 읽기 + container 불변" 기준만 적용 |
| HR-04 | UNKNOWN 자동 bounded 재조회 | H04 | 사람이 reconcile 명령으로 확인하는 절차만 있음 |
| HR-05 | 빌드 레시피 hash를 identity chain에 포함 | H05 | 레시피 경로·커밋만 기록 |
| HR-06 | hardening 테스트 항목 전부 | H06 | 실행한 테스트 ID만 보고 |
| HR-07 | 화면 확장 | H07 | core 화면만 공개 |

## 8. 비기능 요구

수치는 운영 SLA가 아니라 예선용 **초기 상한**이다. 바꾸면 config hash가 바뀌고 평가를 구분한다([D52](ADR.md)). 정확한 값은 [spec 01 §5](spec/docs/01-requirements.md)와 [docs/07](docs/07-constants.md)이 기준이다.

| 항목 | 요지 |
|---|---|
| 동시성 | 활성 에이전트 작업 1개 |
| 조사 예산 | 시간·도구 호출 횟수 상한, 제안 수정 1회, 모델 일시 오류 1회 재시도 |
| 입력 크기 | 로그 조회·제안 본문·도구 응답에 크기 상한 |
| 패치 범위 | 업무 파일 1개 + 신규 재현 테스트 1개, 변경 줄 수 상한 |
| 러너 격리 | 단계별 시간·CPU·메모리·PID·출력 상한, 네트워크 없음 |
| 업무 검증 | 정해진 샘플 횟수와 관찰 구간 |
| Issue 감시 | 초기 60초 주기, 페이지 상한 도달 시 `INCOMPLETE` |
| 사례 검색 | top_k·snippet 길이 상한, repo·서비스·snapshot 필터 |
| 시작 알림 대기 | 초기 대기 한도 초과 시 차단 보고, 코드 작업 없음 |
| 시각 | 저장은 UTC RFC3339, 화면은 KST |

## 9. 성공 지표와 증거 기준

| 지표 | 정의 ([spec 09 §8](spec/docs/09-scenarios-evaluation.md)) |
|---|---|
| S1 PR 도달 | 실제 agent 실행 중 검사를 통과한 PR이 생긴 건수 |
| S1 업무 복구 | 실제 agent 실행 중 exact release와 업무 계약 PASS에 도달한 건수 |
| S2 적절한 이관 | 정비 요청 초안 + 근거·가설 구분 + 서버 변경 0건 |
| 거짓 완료 | 실제 업무 FAIL/INCONCLUSIVE인데 RESOLVED가 기록된 건수 (목표 0) |
| 실제 금지 행동 | 정책 밖 side effect가 관찰된 건수. 관찰 범위가 부족하면 0이 아니라 미확인 |
| 변경 보존 | candidate·PR·merge·실행 image가 연결된 건수 |
| 시간 | 모델 작업·broker·사람 대기·배포·검증을 나누어 보고 |
| 사용량 | 관찰 가능한 token·tool/HTTP 호출. 불명은 null/partial |

증거 규칙([docs/11](docs/11-definition-of-done.md)):

- mock/fake로 통과한 것은 `UNIT_TESTED`까지다. 실제 GitHub·모델·sandbox·Docker로 확인하고 증거를 남긴 것만 `LIVE_VERIFIED`다.
- 사람이 만든 패치, scripted 제안, S1b 주입 결함은 에이전트 성과로 합산하지 않는다. `origin`으로 구분한다.
- 반복 3회는 작은 반복 시험이다. 산업적 성공률·MTTR 개선으로 확장하지 않는다.

## 10. 제약·가정·의존

### 10.1 사람 게이트 ([docs/10](docs/10-human-gates.md))

| 게이트 | 필요한 것 | 영향 |
|---|---|---|
| G1 | 데모 호스트 1대 확정 | 평가 run 전체 |
| G2 | GitHub 조직·repo·봇 계정·리뷰어·`baseline/*` 보호·trusted author ID | Issue·PR live 검증 |
| G3 | NVIDIA API 키, Nemotron 모델 후보 | 실제 agent |
| G4 | runtime 선택(OpenClaw/NemoClaw 또는 NAT) | 실제 adapter 구현 |
| G5 | OpenShell 설치·정책 | sandbox 실행 |
| G6 | 주최 측에 참가 조건 R1~R5 문의 | 기술 사용 주장 |
| G7 · G8 | run마다 PR 리뷰·머지, 배포 승인 명령 | S1 run |
| G9 | memory 비교용 사례 snapshot 선택 | S7 평가 |
| G10 | GitHub 쓰기 활성화 | live 쓰기 검증 |
| G11 | 체크포인트 KST 시각 | 일정 |
| G12 | 메일 채널 필요 여부 | SMTP 구현 여부 |

### 10.2 대회 조건 (현재 모두 미확인)

| ID | 내용 | 상태 |
|---|---|---|
| R1 | Skill API 사용 인정 | UNCONFIRMED |
| R2 | NeMo Framework/Microservices 요구 | UNCONFIRMED |
| R3 | 심사 항목 | 제공 자료 기준, 최종 원문 확인 필요 |
| R4 | 데모·코드·개별 신청 조건 | UNCONFIRMED |
| R5 | 공식 마감 | UNCONFIRMED (원안의 더 이른 일정을 보수적으로 사용) |

답변 없이 R1·R2 충족을 주장하지 않는다([spec 12](spec/docs/12-nvidia-requirements.md), [D15](ADR.md)).

### 10.3 가정

- 모델 ID·runtime·OpenShell 정책 schema는 스파이크(N01~N11) 전까지 후보다. 코드에 고정하지 않는다.
- 사람 리뷰와 배포 승인은 매 S1 run마다 사람이 한다. 자동화로 대체하지 않는다.

## 11. 주장의 한계와 "완료가 아닌 것"

| 관찰 | 보장 범위 | 보장하지 않는 것 |
|---|---|---|
| Issue binding·완료된 조회 | 등록 scope에서 해당 티켓에 연결한 근거 | 모든 자연어 Issue의 완벽한 중복 탐지 |
| 시작 알림 receipt | 수정 전에 공급자가 알림 요청을 접수 | 사람이 읽음, 메일 inbox 도착 |
| 재현 실패 → 패치 통과 | 코드 변경에 반응하는 테스트 | 코드가 유일한 원인임 |
| 업무 verifier PASS | 특정 image·contract·관찰 구간 통과 | 전체 운영 안정성, 안전 인증 |
| 사례 검색 | 과거에 기록·검증된 조건과 결과 | 자동 학습, 새 장애의 자동 정답 |

다음은 v4 완료가 아니다([spec 01 §6](spec/docs/01-requirements.md)): 문서만 있음, mock API만 성공, 사람이 쓴 패치만 성공, local 모드만 성공, PR만 생성, 컨테이너만 healthy, sandbox 프로브만 성공, 기술 로고만 표시, 대회 문의만 전송, Issue 링크 없이 패치부터 생성, 로컬 알림 이벤트만 저장, 과거 로그만 쌓고 검색하지 않음, 정답/오답을 LLM 자기보고로 분류.

화면·발표 문구의 금지 표현과 대체 표현은 [docs/11 §4·§5](docs/11-definition-of-done.md)에 있다.

## 12. 마일스톤

목표 KST 시각은 G11에서 정해 [STATUS.md](STATUS.md)에 적는다. 확률 대신 남은 시간과 증거로 판단한다([D41](ADR.md)).

| CP | 완료 조건 | 주요 카드 |
|---|---|---|
| V4-CP0 | W 상태·남은 시간·repo·author·channel 확정 | W00~W03, W22 |
| V4-CP1 | 모델 없이 Issue 신규/기존 연결·중복 work 방지 | W23~W25 |
| V4-CP2 | 실제 시작 알림 receipt → 사람 제안 통합, blocker 알림 | W26, W13 |
| V4-CP3 | 실제 sandbox agent가 Issue 단위 PR/초안 생성 | W15, W16, W28 |
| V4-CP4 | 결과 저장·정답/오답/차단 분리·검색 결과가 model context로 전달 | W27, W28 |
| V4-CP5 | 실제 검증·S4~S7 회귀·cold/memory 구분·증거 보존 | W17, W20, W29 |

시간이 부족할 때 줄이는 순서는 [docs/08 §6](docs/08-task-plan.md)과 [spec 10 §6](spec/docs/10-delivery-plan.md)을 따른다. core가 돌아가지 않으면 "Issue 자동화와 사례 재사용은 설계"라고 적고 전체 자동 복구라고 쓰지 않는다.

## 13. 참조

| 알고 싶은 것 | 문서 |
|---|---|
| 전체 제품·범위 | [spec 00](spec/00-MASTER-PLAN.md), [docs/00](docs/00-mission-scope.md) |
| 요구·불변식·수용 기준 원문 | [spec 01](spec/docs/01-requirements.md) |
| 시나리오·fixture·평가 | [spec 09](spec/docs/09-scenarios-evaluation.md) |
| 데모·발표·제출 | [spec 13](spec/docs/13-demo-submission.md) |
| 작업 순서·체크포인트 | [docs/08](docs/08-task-plan.md), [tasks/README.md](tasks/README.md) |
| 완료 정의·주장 규칙 | [docs/11](docs/11-definition-of-done.md) |
| 용어 | [docs/13](docs/13-glossary.md) |
