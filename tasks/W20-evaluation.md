# W20 — 반복 평가와 실패 포함 보고

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(전체 게이트) — 평가 하네스와 집계는 A, 실행은 게이트 필요, 사람 승인 포함 |
| 선행 | W15, W16, W17, W19 |
| 목표 상태 | LIVE_VERIFIED (V4-CP5) |
| 원본 근거 | [spec 09 §7·§8·§9·§12·§13](../spec/docs/09-scenarios-evaluation.md), [spec templates/run-record.md](../spec/templates/run-record.md) |
| 참조 | [docs/09 §7](../docs/09-test-matrix.md), [docs/11](../docs/11-definition-of-done.md) |
| 요구 | FR-12, FR-14, V4-CP5 |

## 목표

코드 동결 뒤, 정해진 시나리오를 새 run으로 반복 실행해 실패·수동 개입·미실행을 포함한 결과를 분모와 origin을 밝혀 보고한다.

## 만들 파일

- `linemedic/eval/scenario_expectations.toml` (D60) — 시나리오별 기대 category·action·도착 상태(에이전트 비노출)
- `linemedic/eval/harness.py` + `make evaluate SUITE=<s1|s2-lite|s2-recent-deploy|s1b|s3|s4|s5|s6|s7-cold|s7-memory>` — 새 run 준비 → 시나리오 주입 → **사람 승인 대기 지점에서 멈추고 표시**(승인 우회 없음) → 결과 수집 → `runs/<run_id>/run-record.md` 작성
- `evidence/eval-summary.md` — 집계 표(아래)

## 반복 목표 (동결 후)

| 그룹 | 목표 | 주의 |
|---|---:|---|
| S1 실제 agent 전체 경로 | 3 | 사람 리뷰·승인 시각 기록 |
| S2-lite 기본 / recent-deploy | 2 / 1 | 변형별 분리 |
| S1b | ≥1 | agent 분모 제외 |
| S3-A | ≥1 | 업무 완주 여부 별도 |
| S3-B/C | 필수 항목 각 1세트 | 모델 성공률과 합산 안 함 |

S4~S7 실제 실행은 W29에서 한다.

## 지표 ([spec 09 §8](../spec/docs/09-scenarios-evaluation.md))

S1 PR 도달 수, S1 업무 복구 수, S2 적절한 이관 수, 거짓 완료 수, 실제 금지 행동 수(관측 범위가 충분할 때만 0), 변경 보존(identity chain 연결 수), 시간(모델·broker·사람 대기·배포·검증 분리), 사용량(token·도구·HTTP 호출, 불명은 null/partial). 모든 지표에 분모·origin·관측 범위를 적는다.

## 수용 기준

- 매 run이 새 run·사건·workspace·baseline 브랜치를 쓰고 model ID·runtime·policy/prompt/contract/config hash가 기록된다.
- run 시작 뒤의 API 오류·timeout·오판이 모두 행으로 남는다. 재시도는 같은 예산 안의 일부로 기록된다.
- 모델·정책을 바꾼 run은 다른 집합으로 분리된다.
- 목표 횟수를 못 채우면 실제 횟수와 미실행을 적는다.

## 금지·함정

- 3/3을 산업적 성공률·MTTR 개선·수상 확률로 확장하지 않는다.
- 사람 패치·S1b를 agent 성공 분자에 넣지 않는다.
- 동일 조건 비교 없이 "규칙 기반보다 우수"라고 쓰지 않는다(규칙 baseline은 P1).
