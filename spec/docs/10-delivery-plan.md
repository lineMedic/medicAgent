# 10. v4 개발 TODO·선행 관계·남은 시간 계획

> v3는 9/27~9/28 KST 전일 작업을 가정한 일정이었다. v4는 **새로운 48시간을 부여하지 않는다.** 실제 코드와 완료 증거가 전달되지 않았으므로 기존 W00~W21도 NOT_CHECKED로 둔다. 기능 추가를 기존 일정에 공짜로 끼워 넣지 않는다.

## 1. 먼저 20분 동안 확인

현재 완료된 W 작업, 실제 호스트·모델·repo, 월요일 인원별 가용 시간, 제출 시각, blocker를 적는다. v3의 월요일 휴가·4인·20~24시간은 문서 가정이지 v4가 확인한 개인 일정이 아니다. 완료 확률 표는 삭제하고 아래 체크포인트를 사용한다.

| 항목 | 실제 값 |
|---|---|
| v4 작업 시작 KST | 미입력 |
| 코드 동결·평가 시작 KST | 미입력 |
| 목표 제출·공식 마감 KST | 미확인 — 주최 측 기준 사용 |
| 담당별 남은 시간 | A / B / C / D 각각 미입력 |
| v3 core 완료 증거 | W별 확인 필요 |
| 이번 run의 알림 채널 | github_comment 기본 / SMTP 선택 시 실제 시험 |
| Issue 등록 repo·author 승인 | 미확인 |
| 과거 case 원본·snapshot | 없으면 없음/seed로 표시 |

## 2. 기존 작업 ID 보존

원래 W00~W21을 새 ID로 다시 만들어 중복 구현하지 않는다. 아래는 v3에서 계승한 작업이며, 기존 코드가 있다면 영향 부분만 바꾼다.

| ID | 원래 작업 | v4 추가·변경 | 실제 상태 |
|---|---|---|---|
| W00 | 데모 호스트 확정·OpenShell 지원 조합 확인·Docker 준비 | 기존 확정 호스트 유지, 실제 manifest 확인 | NOT_CHECKED |
| W01 | R1~R5 참가 조건 원문 캡처·주최 측 문의 발송 | NVIDIA 조건과 확정 마감 다시 확인 | NOT_CHECKED |
| W02 | 모델·런타임·샌드박스 스파이크 (N01~N05) | 런타임 1개 유지, 새 도구 scope까지 smoke | NOT_CHECKED |
| W03 | 조직·`l3-mes-api`·봇 PR 권한·`baseline/*` 보호·squash 단일 머지·bypass 시험 | Issue 읽기/쓰기·댓글 권한을 W22에서 추가 | NOT_CHECKED |
| W04 | MES 버그·정상·거짓 정상 fixture, holdout, 실제 API | v3의 보호·검증 기준 유지 | NOT_CHECKED |
| W05 | **독립 업무 검증기 + S1b부터** | case 판정은 verifier 결과를 근거로 함 | NOT_CHECKED |
| W06 | DB·상태 전이 함수·audit·인증(`/tools`·`/ops` 분리) | v4 DDL·work state·outbox·API idem 원장 | NOT_CHECKED |
| W07 | 감지·증거·API 실제 자료 연결 | problem fingerprint와 routing_scope 분리 | NOT_CHECKED |
| W08 | S2-lite 지표·매뉴얼 fixture·혼동 사례 | v3의 보호·검증 기준 유지 | NOT_CHECKED |
| W09 | proposal schema·DB unique 논리 키·정비 요청 초안 | 모델 action 3개는 유지; Issue는 lifecycle | NOT_CHECKED |
| W10 | 패치 경로 재확인·runner(network none)·R0/R1/R2 | v3의 보호·검증 기준 유지 | NOT_CHECKED |
| W11 | 봇 계정 PR 생성·결과 불명 기록·**사람이 쓰는 reconcile CLI** | parent Issue·work·시작 receipt 확인 후 PR | NOT_CHECKED |
| W12 | 명시적 exact SHA 승인 배포 | work+Issue를 exact SHA 승인에 연결 | NOT_CHECKED |
| W13 | **사람 제안으로 통합 경로** | live 통합은 W24~W26 gate 포함 | NOT_CHECKED |
| W14 | 실제 agent S1·S2-lite 제안 (local 모드) | 실제 agent 실행 전 시작 알림 필요; unit stub과 구분 | NOT_CHECKED |
| W15 | **샌드박스 안 실제 agent S1 전체 경로** | S1 전체 경로에 Issue·결과 알림·case 저장 포함 | NOT_CHECKED |
| W16 | 샌드박스 안 실제 agent S2-lite (기본·혼동) | 정비 초안 알림과 실제 정비 완료 구분 | NOT_CHECKED |
| W17 | S3-A/B/C (C는 호스트 대조 + 샌드박스 비교 + 거절 로그) | v3의 보호·검증 기준 유지 | NOT_CHECKED |
| W18 | 최소 화면 1개·모든 실패 상태 연결 | Issue/work·알림·history 상태 한 화면 | NOT_CHECKED |
| W19 | 초기화·export·새 run smoke | Issue/PR/case 원본 보존, memory snapshot 초기화 | NOT_CHECKED |
| W20 | 반복 평가·실패 포함 보고 | S4~S7 및 cold/memory 비교 추가 | NOT_CHECKED |
| W21 | README·영상·주장 점검·**전원 개별 제출** | 실제 구현 범위와 필수 조건으로 문안 확정 | NOT_CHECKED |

## 3. v4 추가 작업

| 완료 | ID | 담당 | 할 일 | 선행 | 완료 증거 |
|---|---|---|---|---|---|
| [ ] | W22 | C+D | repo 숫자 ID·작성자 승인·Issue 권한·알림 route catalog | W03·W06 | 전용 repo 조회·생성·댓글 1회 smoke, 최소 권한 |
| [ ] | W23 | C | Issue mirror·bounded polling·checkpoint | W22 | backlog 미실행, 새 승인 Issue 감지, page 누락·PR 제외 |
| [ ] | W24 | C+A | 로그 signature와 기존 Issue 연결/신규 생성 | W07·W23 | S4-new/existing/ambiguous/incomplete |
| [ ] | W25 | C | work 상태·단일 claim·body hash409·retry/cancel | W06·W22 | T-ISS-04·T-V4-02, 활성 work 하나 |
| [ ] | W26 | D+C | 실제 채널 하나·outbox·시작 gate·blocker 보고 | W22·W25 | receipt 전 attempt 없음, S6 actual notification |
| [ ] | W27 | A+B | case builder·outcome·exact/FTS 검색·snapshot | W05·W06·W07 | PR-only·blocked 오분류 없음, 검색·current evidence |
| [ ] | W28 | B+C | agent context·Issue 연결 PR·case 인용·결과 이벤트 | W14·W24·W25·W26·W27 | S1/S2 end-to-end와 S5/S7 |
| [ ] | W29 | 전원 | 새 회귀·live API·cold/memory 비교·문서 정합성 | W28·기존 W20 | actual 결과·실패·미실행·origin 구분 |

W26은 D가 선택한 채널 어댑터를 맡고, C는 상태/게이트만 맡아 C 병목을 줄인다. W27은 A가 사실·검증 outcome, B가 retrieval/context를 맡는다. 이것을 위해 다른 프레임워크나 서비스 서버를 만들지 않는다.

## 4. 실제 개발 순서와 통과점

```text
기존 W05 verifier와 보호 경계 유지 ─────────────────────────────┐
W22 등록/권한 → W23 Issue 관찰 → W24 신규/재사용 ────────────────┤
W06 store → W25 단일 work/409 → W26 시작 알림·차단 보고 ─────────┼→ W28 실제 agent 기존 경로
W05·W07 → W27 case 생성·검색 ──────────────────────────────────┘  → W29 평가
```

live PR 경로(W11/W13/W15)는 v4에서는 W24~W26이 준비된 이후에 실행한다. 그전에는 unit mock 또는 읽기 전용 smoke만 한다. 실제 수정 시작의 알림 gate를 시험 편의로 생략하고 v4 E2E라고 부르지 않는다.

| CP | 완료 조건 | 미달 시 |
|---|---|---|
| V4-CP0 | 현재 W 상태·남은 시간·repo·author·channel 확정 | 구현 시작 전에 범위를 결정, 무작정 전체 재개발 금지 |
| V4-CP1 | 모델 없이 Issue 신규/기존 연결·중복 work 방지 | memory UI·embedding·새 provider 중단 |
| V4-CP2 | 실제 시작 알림 receipt → 사람 제안 통합, blocker 알림 | agent 수정부터 먼저 실행하는 우회 금지 |
| V4-CP3 | 실제 sandbox agent가 Issue 단위 작업·PR/초안 생성 | 사례 요약 품질 튜닝보다 통합 경로 수정 |
| V4-CP4 | 결과 저장·정답/오답/차단 분리·검색 결과가 model context로 전달 | 원본만 저장하면 기억 기능 미구현으로 표시 |
| V4-CP5 | 실제 검증·S4~S7 회귀·cold/memory 구분·증거 보존 | 실제 완료 범위로 발표 축소 |

각 CP에 팀이 실제 KST 목표시각을 적는다. v3의 9/28 16시 동결/22시30분 제출 목표를 유지할지는 남은 시간과 공식 마감을 확인해 결정한다. 이미 지난 시각을 미래 마감으로 제시하지 않는다.

## 5. core와 hardening 재분류

| 기존 ID | v4 처리 |
|---|---|
| H01 claim 경합 | **W25 core**: log·Issue 두 입력이므로 필수 |
| H02 같은 key 다른 body | **W25 core**: 요청/생성/알림 중복 의미를 분명하게 함 |
| H03 고급 heartbeat 진단 | hardening 유지. 기본 관찰 불가에서 INCONCLUSIVE는 core |
| H04 자동 reconcile | hardening 유지. 운영자 수동 reconcile과 UNKNOWN 차단은 core |
| H05 recipe hash 보강 | hardening 유지. exact source/image·trusted recipe 고정은 core |
| H06 전체 추가 시험 | 운영 조합 확장만 hardening. 09의 v4 필수 correctness 시험은 core |
| H07 상세 화면 | hardening 유지, 기본 상태·미전송·case evidence 표시는 core |

## 6. 시간 부족 시 무엇을 줄이나

**먼저 줄인다:** 공개 webhook, 다중 채널, SMTP+메신저 동시 지원, vector DB/embedding/reranker, 새 runtime, 자동 Issue close, 자동 재조사, 장식 dashboard, 새로운 장애 유형.

**줄이면 요청 미충족이 되는 것:** 기존 Issue 재사용/신규 연결, 승인된 새 Issue 입력, 시작 알림 선행, 중복 claim 방지, 불가 보고의 실제 채널, 성공·실패·차단 이력 구분, 최소 검색→agent context. 이 항목을 빼면 ‘v4 전체 완료’가 아니라 실제 부분 완료라고 적는다.

**절대 약화하지 않는다:** 사람 리뷰·배포 승인, exact SHA binding, 보호 테스트·독립 업무 검증, 비신뢰 코드 격리, 권한·정제·고정 수신자, UNKNOWN을 중복 실행하지 않는 원칙, 실제 증거 보존.

새 요구는 제품에 자연스럽지만 범위는 확장됐다. 인원이 부족하면 v3 실제 core를 먼저 보존하고 **Issue 연결+시작 알림 한 경로**부터 내보낸다. 남은 기능을 자동으로 만들어졌다고 설명하지 않는다.

## 7. 완료 보고

각 W는 `NOT_CHECKED / NOT_STARTED / IMPLEMENTED / UNIT_TESTED / LIVE_VERIFIED / BLOCKED` 중 현재 상태와 evidence를 남긴다. 문서를 작성한 이번 작업이 제품 구현 W를 완료하지 않는다. 작업 단위 인계는 [템플릿](../templates/implementation-handoff.md), 평가 기록은 [실행 기록](../templates/run-record.md)을 쓴다.
