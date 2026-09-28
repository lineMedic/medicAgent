# 평가 요약 (eval-summary)

- 생성 시각(UTC): 2026-09-28T04:04:45.306011Z
- 범위: run manifest에 `evaluation`이 있는 평가 run(`make evaluate`가 만든 run)만. 개발 중 run은 넣지 않는다
- agent 분모: origin `agent_release`이고 sandbox 모드인 실행. 사람 제안(`manual_integration`)·S1b(`human_injected_negative`)는 분모 밖 칸에 따로 적는다. 사람이 고친 패치(human-edited)는 기록 필드가 없어 run-record에서 확인한다
- 조건(model·runtime·agent_mode·sandbox_verified·policy/prompt/contract hash)이 다른 run은 다른 집합이다. 금지 행동은 관측 범위 기록이 없어 미확인이다(0이 아니다)
- 몇 회의 결과는 작은 반복 시험이다. 산업적 성공률·MTTR 개선·수상 확률로 확장하지 않는다. 동일 조건 비교가 없으므로 규칙 기반과 비교하지 않는다

## 목표 대비 실행

실행: agent 분모 그룹은 agent 실행 수, 분모 밖 그룹(S1b·S4)은 run 수다.

| 그룹 | 목표 | 실행 | 상태 |
|---|---|---|---|
| S1 실제 agent 전체 경로 | 3 | 0 | NOT_RUN (0/3) |
| S2-lite 기본 | 2 | 0 | NOT_RUN (0/2) |
| S2-lite recent-deploy | 1 | 0 | NOT_RUN (0/1) |
| S1b 거짓 정상 부정 시험 | 1 | 0 | NOT_RUN (0/1) |
| S3-A 공격 memo가 섞인 S1 | 1 | 0 | NOT_RUN (0/1) |
| S4 Issue 연결(new·existing·ambiguous) | 3 | 0 | NOT_RUN (0/3) |
| S5-new 로그 없는 새 Issue | 1 | 0 | NOT_RUN (0/1) |
| S6-blocked 지원 밖 요청 | 1 | 0 | NOT_RUN (0/1) |
| S7 cold_start | 3 | 0 | NOT_RUN (0/3) |
| S7 memory_assisted | 3 | 0 | NOT_RUN (0/3) |

## 행 (run·사건마다, 실패·사건 없음 포함)

| run | suite | 사건 | origin | mode | category/action(첫 → 최종) | 사건 | work | 검증 | 비고 |
|---|---|---|---|---|---|---|---|---|---|
| (평가 run 없음) | | | | | | | | | |

## 제외

- 제외한 run 없음
