# 08. 작업 계획 — 순서·의존·자율성·체크포인트

> 원본: [spec 10 개발 계획](../spec/docs/10-delivery-plan.md), [spec 09](../spec/docs/09-scenarios-evaluation.md), [spec 11 §3](../spec/docs/11-runbook.md).
> 코딩 에이전트 1개가 아래 **실행 순서표**를 위에서부터 처리한다. 사람은 게이트를 연다. 원본의 W ID를 그대로 쓴다(새 ID를 만들지 않는다). 원본에 없는 준비 작업만 `B00`으로 둔다.

## 1. 원칙

1. 수정 순서는 **기존 verifier 보존 → Issue 연결·단일 선점 → 시작 댓글 → 사람 제안 통합 → 실제 agent → 차단·결과 알림 → 사례 저장·검색 → 반복 평가**다([spec 00 §8](../spec/00-MASTER-PLAN.md)).
2. live PR 경로(W11·W13·W15)는 W24~W26이 준비된 뒤에 실행한다. 그 전에는 unit mock이나 읽기 전용 smoke만 한다. 시작 알림 게이트를 시험 편의로 생략한 실행을 v4 E2E라고 부르지 않는다.
3. 게이트가 필요한 카드도 fake로 할 수 있는 부분은 먼저 끝낸다. live 검증은 게이트가 열린 뒤 돌아와서 한다.
4. core 실제 실행과 평가 증거가 확보되면 새 기능을 멈춘다. hardening은 그 뒤다.

## 2. 첫 세션에서 사람에게 먼저 요청할 것

게이트는 준비 시간이 걸린다. 에이전트는 B00을 시작하기 전에 아래 요청을 [10-human-gates.md](10-human-gates.md)의 형식으로 사용자에게 한 번에 전달하고 STATUS.md 사람 게이트 표에 요청 내용·시각을 직접 기록한다.

- G1 데모 호스트 확정, G2 GitHub 조직·`l3-mes-api`·봇·리뷰어·보호 규칙, G3 NVIDIA 키·모델, G5 OpenShell 설치, G6 대회 조건 문의 발송, G11 체크포인트 KST 시각.
- G4(runtime 선택)는 W02 스파이크 결과를 보고 사람이 정한다.

## 3. 실행 순서표

자율성: **A** = AUTONOMOUS(fake로 목표 상태까지 가능), **C(Gx)** = 구현은 가능하나 live 검증에 게이트 필요, **H** = HUMAN_ONLY(에이전트는 스크립트·초안·체크리스트만).

| # | 카드 | 내용 | 자율성 | 선행 | 목표 상태 | CP |
|---|---|---|---|---|---|---|
| 1 | [B00](../tasks/B00-bootstrap.md) | 저장소 골격, 설정, 테스트 기반, host manifest·doctor | A | — | UNIT_TESTED | — |
| 2 | [W00](../tasks/W00-demo-host.md) | 데모 호스트 확정·manifest | H(G1) | B00 | LIVE_VERIFIED | CP0 |
| 3 | [W01](../tasks/W01-contest-conditions.md) | R1~R5 참가 조건 확인 | H(G6) | — | LIVE_VERIFIED | CP0 |
| 4 | [W02](../tasks/W02-runtime-spikes.md) | 모델·런타임·샌드박스 스파이크 N01~N05·N08~N10 | C(G3·G4·G5) | B00 | LIVE_VERIFIED | CP0 |
| 5 | [W03](../tasks/W03-github-setup.md) | 조직·repo·봇·`baseline/*` 보호·squash (N07) | C(G2) | B00 | LIVE_VERIFIED | CP0 |
| 6 | [W04](../tasks/W04-mes-fixtures.md) | MES 서비스(버그 base)·fixture·holdout·scenario-s1 | A | B00 | UNIT_TESTED | — |
| 7 | [W05](../tasks/W05-verifier.md) 1부 | 업무 verifier 판정 엔진·observer·S1b 이미지 | A (docker) | W04 | UNIT_TESTED | — |
| 8 | [W06](../tasks/W06-store-auth.md) | DDL·전이·CAS·audit·인증·멱등 409·오류 외피 | A | B00 | UNIT_TESTED | — |
| 9 | [W05](../tasks/W05-verifier.md) 2부 | verification 저장·S1b 전이(ESCALATED) | A | W06 | UNIT_TESTED | — |
| 10 | [W07](../tasks/W07-detector-evidence.md) | 감지·fingerprint·evidence·조회 도구 | A | W04·W06 | UNIT_TESTED | — |
| 11 | [W08](../tasks/W08-s2-lite-fixtures.md) | 카메라 지표·설비·매뉴얼·scenario-s2-lite | A | W06·W07 | UNIT_TESTED | — |
| 12 | [W09](../tasks/W09-proposal-schema.md) | 제안 schema·접수 B01~B06·정비 초안·escalate | A | W06·W07·W08 | UNIT_TESTED | — |
| 13 | [W22](../tasks/W22-github-registration.md) | GitHubPort·FakeGitHub·repo/author/route catalog·smoke | C(G2·G10) | W06 | LIVE_VERIFIED | CP0 |
| 14 | [W23](../tasks/W23-issue-polling.md) | Issue mirror·bounded polling·checkpoint | C(G2) | W22 | LIVE_VERIFIED | CP1 |
| 15 | [W24](../tasks/W24-issue-matching.md) | 로그 → 기존 Issue 연결/신규 생성 | C(G2·G10) | W07·W23 | LIVE_VERIFIED | CP1 |
| 16 | [W25](../tasks/W25-work-lifecycle.md) | work 상태·단일 claim·409·approve/retry/cancel | A | W06·W22 | UNIT_TESTED | CP1 |
| 17 | [W26](../tasks/W26-notifications.md) | outbox·GitHub 댓글·시작 게이트·차단 보고 | C(G2·G10) | W22·W25 | LIVE_VERIFIED | CP2 |
| 18 | [W10](../tasks/W10-patch-gate-runner.md) | 패치 정책·candidate·runner R0/R1/R2 | A (docker) | W04·W09 | UNIT_TESTED | — |
| 19 | [W11](../tasks/W11-bot-pr-reconcile.md) | 봇 PR 생성·UNKNOWN·reconcile CLI | C(G2·G10) | W10·W24~W26 | LIVE_VERIFIED | CP2 |
| 20 | [W12](../tasks/W12-exact-release.md) | exact SHA 승인 배포·검증 연결 | C(G7·G8) | W05·W11 | LIVE_VERIFIED | CP2 |
| 21 | [W13](../tasks/W13-manual-integration.md) | 사람 제안으로 전체 경로 통합 (`manual_integration`) | C(G2·G7·G8·G10) | W12·W26 | LIVE_VERIFIED | CP2 |
| 22 | [W27](../tasks/W27-case-memory.md) | case builder·outcome·FTS5·snapshot | A | W05·W06·W07 | UNIT_TESTED | CP4 |
| 23 | [W14](../tasks/W14-local-agent.md) | 실제 agent local 모드 S1·S2-lite 제안 | C(G3·G4) | W02·W09·W13 | LIVE_VERIFIED | — |
| 24 | [W15](../tasks/W15-sandbox-s1.md) | sandbox 안 실제 agent S1 전체 경로 | C(G5·G7·G8) | W14·W13 | LIVE_VERIFIED | CP3 |
| 25 | [W16](../tasks/W16-sandbox-s2-lite.md) | sandbox 안 실제 agent S2-lite (기본·혼동) | C(G5) | W14·W08 | LIVE_VERIFIED | CP3 |
| 26 | [W28](../tasks/W28-agent-context-integration.md) | agent context·Issue 연결 PR·사례 인용·결과 이벤트 | C(G3~G5) | W14·W24~W27 | LIVE_VERIFIED | CP3·CP4 |
| 27 | [W17](../tasks/W17-security-s3.md) | S3-A/B/C | C(G5) | W10·W15 | LIVE_VERIFIED | CP5 |
| 28 | [W18](../tasks/W18-dashboard.md) | 최소 화면 1개 | A | W06 (표시할 데이터는 누적) | UNIT_TESTED | — |
| 29 | [W19](../tasks/W19-reset-export.md) | run-new·reset·export·archive | C(G2) | W06·W11 | LIVE_VERIFIED | — |
| 30 | [W20](../tasks/W20-evaluation.md) | 반복 평가·실패 포함 보고 | C(전체) | W15~W17·W19 | LIVE_VERIFIED | CP5 |
| 31 | [W29](../tasks/W29-v4-regression.md) | v4 회귀·live API·cold/memory 비교·문서 정합성 | C(전체) | W28·W20 | LIVE_VERIFIED | CP5 |
| 32 | [W21](../tasks/W21-readme-submission.md) | README·영상·주장 점검·전원 개별 제출 | H | W20·W29 | LIVE_VERIFIED | — |
| 33 | [H03~H07](../tasks/H03-H07-hardening.md) | hardening (core 완료 후만) | A/C | core 전체 | UNIT_TESTED | — |

W18은 순서상 뒤에 있지만 core 최소판(FR-13)이다. 시연 준비에 화면이 먼저 필요하면 W26 이후 언제든 앞당겨도 된다.

## 4. 의존 그래프

```text
B00 ─┬─ W04 ─ W05(1부) ─────────────────────────────┐
     ├─ W06 ─┬─ W05(2부)                            │
     │       ├─ W07 ─┬─ W08 ─ W09 ─ W10 ─┐           │
     │       │       └──────────────┐   │           │
     │       ├─ W22 ─ W23 ─ W24 ◄───┘   │           │
     │       ├─ W25 (W22 필요) ─ W26 ───┼─ W11 ─ W12 ─ W13
     │       ├─ W27 (W05·W07)           │                │
     │       └─ W18                      │                │
     ├─ W02 ─────────────────── W14 ◄────┴────────────────┘
     └─ W03 (G2)                  ├─ W15 ─┬─ W17 ─┐
                                  ├─ W16  │       ├─ W20 ─ W29 ─ W21
                                  └─ W28 ◄┘(W24~W27)
W19 (W06·W11) ────────────────────────────────────┘
```

## 5. 체크포인트 V4-CP0~CP5

목표 KST 시각은 사람이 G11에서 정해 STATUS.md 체크포인트 표의 `목표 KST` 칸에 적는다. 이미 지난 시각을 미래 마감처럼 쓰지 않는다.

| CP | 완료 조건 | 미달 시 |
|---|---|---|
| V4-CP0 | W 상태·남은 시간·repo·author·channel 확정 (W00~W03, W22 게이트) | 구현 범위를 먼저 결정. 무작정 전체 재개발 금지 |
| V4-CP1 | 모델 없이 Issue 신규/기존 연결·중복 work 방지 (W23~W25) | memory UI·embedding·새 provider 중단 |
| V4-CP2 | 실제 시작 알림 receipt → 사람 제안 통합, blocker 알림 (W26, W13) | agent 수정부터 먼저 실행하는 우회 금지 |
| V4-CP3 | 실제 sandbox agent가 Issue 단위 작업·PR/초안 생성 (W15, W16, W28) | 사례 요약 품질보다 통합 경로 수정 |
| V4-CP4 | 결과 저장·정답/오답/차단 분리·검색 결과가 model context로 전달 (W27, W28) | 원본만 저장했다면 기억 기능 미구현으로 표시 |
| V4-CP5 | 실제 검증·S4~S7 회귀·cold/memory 구분·증거 보존 (W17, W20, W29) | 실제 완료 범위로 발표 축소 |

## 6. 시간이 부족할 때

**먼저 줄인다**: 공개 webhook, 다중 채널, SMTP+메신저 동시, vector DB·embedding·reranker, 새 runtime, 자동 Issue close, 자동 재조사, 장식 dashboard, 새 장애 유형, hardening H03~H07.

**줄이면 요청 미충족이 되는 것** (빼면 "v4 부분 완료"라고 적는다): 기존 Issue 재사용·신규 연결, 승인된 새 Issue 입력, 시작 알림 선행, 중복 claim 방지, 불가 보고의 실제 채널, 성공·실패·차단 이력 구분, 최소 검색 → agent context.

**절대 약화하지 않는다**: 사람 리뷰·배포 승인, exact SHA binding, 보호 테스트·독립 업무 검증, 비신뢰 코드 격리, 권한·정제·고정 수신자, UNKNOWN 재실행 금지, 실제 증거 보존.

**최소 완주 경로**: 시간이 아주 적으면 `B00 → W04 → W05 → W06 → W22 → W25 → W26`으로 "Issue 연결 + 시작 알림 한 경로"를 먼저 실제로 보인다. 나머지는 구현된 것과 설계만 된 것을 구분해 공개한다.

## 7. 요구사항 → 카드 대응

| 요구 | 카드 |
|---|---|
| FR-01 사건 묶기 · FR-02 증거 | W07 |
| FR-03 모델 tool-call 왕복 · FR-04 패치·재현 테스트 | W14, W15 |
| FR-05 브로커 검사 | W10 |
| FR-06 봇 PR | W11 |
| FR-07 exact 배포 | W12 |
| FR-08 업무 판정 · FR-10 거짓 정상 거절 | W05, W12 |
| FR-09 설비 초안 | W08, W09, W16 |
| FR-11 안전 경계·실패 처리 | W06, W11, W17 |
| FR-12 재현·보존 | W19 |
| FR-13 상태 표시 | W18 |
| FR-14 NVIDIA 사용·대회 인정 | W01, W02, W21 |
| FR-15 sandbox 실행 · FR-16 한 호스트 | W15, W16 · B00, W00 |
| FR-17 로그 → Issue | W24 |
| FR-18 새 Issue 접수 | W23 |
| FR-19 중복 방지 (HR-01) | W25 |
| FR-20 시작 알림 · FR-21 불가 보고 | W26 |
| FR-22 case note · FR-23 사례 검색 | W27, W28 |
| FR-24 단계별 결과 알림 | W26, W28 |
| FR-25 재시도·취소·재발 | W25 |
| FR-26 멱등 409·UNKNOWN·cold/memory 분리 (HR-02) | W06, W25, W27, W29 |
| HR-03~HR-07 | H03~H07 |
| AC-S1 · AC-S2 · AC-S1B · AC-SBX | W15 · W16 · W05 · W15, W16 |
| AC-S3 · AC-S4 · AC-S5 · AC-S6 · AC-S7 | W17 · W24 · W23, W25, W26 · W26 · W27, W28, W29 |

## 8. 운영 명령 → 카드

| 명령 ([spec 11 §3](../spec/docs/11-runbook.md)) | 카드 |
|---|---|
| `make setup`, `make doctor` | B00 (점검 항목은 카드마다 추가) |
| `make run-new` | W06 (DB 기본), W19 (baseline 브랜치·manifest 완성) |
| `make start RUN_ID=` | W13 |
| `make scenario-s1 RUN_ID=` | W04 |
| `make scenario-s2-lite RUN_ID=` | W08 |
| `make verify-negative RUN_ID=` | W05 |
| `make issue-sync RUN_ID=` | W23 |
| `make issue-bind INCIDENT_ID= ISSUE_NUMBER=` | W24 |
| `make approve-work WORK_ID= EXPECTED_VERSION=` · `make retry-work WORK_ID= REASON=` | W25 |
| `make notification-reconcile NOTIFICATION_ID=` | W26 |
| `make reconcile RUN_ID= EXECUTION_ID=` | W11 |
| `make approve-release RUN_ID= INCIDENT_ID= WORK_ID= PR_NUMBER= MERGE_SHA= EXPECTED_IMAGE_ID=` | W12 |
| `make rebuild-case-index` · `make memory-snapshot RUN_ID= (LIST=1 \| NOTES=)` | W27 |
| `make security-test RUN_ID=` | W17 |
| `make export-run RUN_ID=` · `make reset RUN_ID=` | W19 |
| `make evaluate SUITE=` | W20 |
