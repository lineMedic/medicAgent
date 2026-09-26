# 작업 카드

> 한 파일 = 한 작업이다. 원본 W00~W29 ID를 그대로 쓰고, 원본에 없는 준비 작업만 `B00`으로 둔다. 순서·의존·체크포인트의 근거는 [docs/08-task-plan.md](../docs/08-task-plan.md)에 있다.

## 사용법

1. [STATUS.md](../STATUS.md)를 읽고 아래 실행 순서와 카드의 선행·게이트를 확인해 다음 카드를 고른다([AGENTS.md §2](../AGENTS.md)).
2. 카드의 `원본 근거`에 적힌 spec 절만 읽는다.
3. `수용 기준`의 테스트를 먼저 쓰고, `만들 파일` 범위 안에서 구현한다.
4. [STATUS.md](../STATUS.md)에 상태·완료 증거·완료 보고를 직접 남기고 현재 작업·다음 작업을 갱신한다. [docs/11 §1](../docs/11-definition-of-done.md)의 기록 점검을 마치면 커밋한다([D62](../DECISIONS.md)).
5. 게이트가 필요한 단계는 `BLOCKED_ON_HUMAN`과 재개 조건을 적는다. 게이트 없이 가능한 부분을 끝냈으면 현재 작업을 비우고 선행 조건을 충족하는 다음 카드로 간다.

카드의 **자율성** 표기: `A` 게이트 없이 목표 상태까지 가능 / `C(Gx)` 구현은 가능, live 검증에 게이트 필요 / `H` 사람만 가능(에이전트는 준비물만).

## 실행 순서

| # | 카드 | 자율성 | 목표 상태 |
|---|---|---|---|
| 1 | [B00 저장소 골격](B00-bootstrap.md) | A | UNIT_TESTED |
| 2 | [W00 데모 호스트](W00-demo-host.md) | H(G1) | LIVE_VERIFIED |
| 3 | [W01 대회 조건](W01-contest-conditions.md) | H(G6) | LIVE_VERIFIED |
| 4 | [W02 런타임 스파이크](W02-runtime-spikes.md) | C(G3·G4·G5) | LIVE_VERIFIED |
| 5 | [W03 GitHub 설정](W03-github-setup.md) | C(G2) | LIVE_VERIFIED |
| 6 | [W04 MES·fixture](W04-mes-fixtures.md) | A | UNIT_TESTED |
| 7 | [W05 verifier 1부](W05-verifier.md) | A | UNIT_TESTED |
| 8 | [W06 저장소·인증](W06-store-auth.md) | A | UNIT_TESTED |
| 9 | [W05 verifier 2부](W05-verifier.md) | A | UNIT_TESTED |
| 10 | [W07 감지·증거](W07-detector-evidence.md) | A | UNIT_TESTED |
| 11 | [W08 S2-lite fixture](W08-s2-lite-fixtures.md) | A | UNIT_TESTED |
| 12 | [W09 제안 schema](W09-proposal-schema.md) | A | UNIT_TESTED |
| 13 | [W22 GitHub 등록](W22-github-registration.md) | C(G2·G10) | LIVE_VERIFIED |
| 14 | [W23 Issue polling](W23-issue-polling.md) | C(G2) | LIVE_VERIFIED |
| 15 | [W24 Issue 연결·생성](W24-issue-matching.md) | C(G2·G10) | LIVE_VERIFIED |
| 16 | [W25 work lifecycle](W25-work-lifecycle.md) | A | UNIT_TESTED |
| 17 | [W26 알림·시작 게이트](W26-notifications.md) | C(G2·G10) | LIVE_VERIFIED |
| 18 | [W10 패치 게이트·runner](W10-patch-gate-runner.md) | A | UNIT_TESTED |
| 19 | [W11 봇 PR·reconcile](W11-bot-pr-reconcile.md) | C(G2·G10) | LIVE_VERIFIED |
| 20 | [W12 exact 릴리스](W12-exact-release.md) | C(G7·G8) | LIVE_VERIFIED |
| 21 | [W13 사람 제안 통합](W13-manual-integration.md) | C(G2·G7·G8·G10) | LIVE_VERIFIED |
| 22 | [W27 사례 기억](W27-case-memory.md) | A | UNIT_TESTED |
| 23 | [W14 local agent](W14-local-agent.md) | C(G3·G4) | LIVE_VERIFIED |
| 24 | [W15 sandbox S1](W15-sandbox-s1.md) | C(G5·G7·G8) | LIVE_VERIFIED |
| 25 | [W16 sandbox S2-lite](W16-sandbox-s2-lite.md) | C(G5) | LIVE_VERIFIED |
| 26 | [W28 agent 문맥 통합](W28-agent-context-integration.md) | C(G3~G5) | LIVE_VERIFIED |
| 27 | [W17 보안 S3](W17-security-s3.md) | C(G5) | LIVE_VERIFIED |
| 28 | [W18 대시보드](W18-dashboard.md) | A | UNIT_TESTED |
| 29 | [W19 reset·export](W19-reset-export.md) | C(G2) | LIVE_VERIFIED |
| 30 | [W20 반복 평가](W20-evaluation.md) | C(전체) | LIVE_VERIFIED |
| 31 | [W29 v4 회귀·비교](W29-v4-regression.md) | C(전체) | LIVE_VERIFIED |
| 32 | [W21 README·제출](W21-readme-submission.md) | H | LIVE_VERIFIED |
| 33 | [H03~H07 hardening](H03-H07-hardening.md) | A/C | UNIT_TESTED |

## 모든 카드에 공통으로 적용하는 제약

[spec templates/implementation-handoff.md §3](../spec/templates/implementation-handoff.md)에서 가져왔다.

- 설계 문서의 목표 명령이 이미 구현됐다고 가정하지 않는다. 실제 SDK·API 문법과 버전은 설치 환경과 공식 문서로 확인한다.
- 키·비공개 로그·runtime state를 커밋하지 않는다.
- 외부 쓰기(Issue 생성, 댓글, PR, 메일)는 명시적으로 허용된 전용 테스트 자원에서만 한다. 일반 개발 요청을 운영 변경 승인으로 해석하지 않는다.
- 실패하는 테스트를 지우거나 검증 조건을 약하게 만들어 통과시키지 않는다.
- 실제 agent 작업은 binding·승인·시작 알림 receipt 뒤에만 시작한다.
- provider timeout을 성공이나 실패로 추정해 재전송하지 않는다.
- PR 생성·권한 부족·미확인 결과를 검증된 정답/오답으로 기록하지 않는다.
- 막히면 전체 아키텍처를 다시 만들지 말고, 최소 차단 요인과 대안을 보고한다.
