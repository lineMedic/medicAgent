# W29 — v4 회귀·live API·cold/memory 비교·문서 정합성

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(전체 게이트, G9 포함) |
| 선행 | W28, W20 |
| 목표 상태 | LIVE_VERIFIED (V4-CP5) |
| 원본 근거 | [spec 09 §10·§11·§12](../spec/docs/09-scenarios-evaluation.md), [spec 17 §6](../spec/docs/17-case-memory.md), [spec 12 §8 N15](../spec/docs/12-nvidia-requirements.md), [spec 00 §8](../spec/00-MASTER-PLAN.md) |
| 참조 | [docs/09](../docs/09-test-matrix.md), [docs/11 §6](../docs/11-definition-of-done.md) |
| 요구 | FR-17~FR-26 전체, AC-S4~AC-S7 |

## 목표

v4 추가 기능(S4~S7)을 실제 API로 최소 1회씩 확인하고, cold_start와 memory_assisted를 분리 비교하며, 문서·STATUS·README가 실제 구현과 일치하는지 점검한다.

## 할 일

1. **전체 자동 회귀**: `make test`, `make test-docker`, 게이트가 열린 범위의 `make test-live`. T-ISS·T-NOT·T-MEM·T-V4·T-STATE-01·T-IDEM-02 결과를 STATUS.md에 기록.
2. **live API** (G2·G10): S4-new 1, S4-existing 1, S4-ambiguous 1, S5-new 1(실제 polling 감지), S5-duplicate 1(같은 Issue의 poll 반복·bot 댓글·동시 로그), S6-blocked 1(지원 밖 DB 변경 요구 또는 권한 부족 → 외부 알림 receipt). N15(동시 입력·bot 댓글 반복·409)를 evidence에 기록.
3. **S7 비교** (G3~G5, G9): 사람이 고른 snapshot(성공·업무 실패·권한 차단 사례 포함)으로, 같은 모델·예산·권한·base에서 cold_start와 memory_assisted를 **최소 1쌍, 목표 3쌍** 실행. 확인: 관련 정답·오답 인용, PR-only가 성공으로 승격되지 않음, 권한 차단이 오답으로 분류되지 않음, 다른 repo 기록 비노출, RETRACTED·미래 노트 제외, stale source 재검증, 주입 지시 무시. 1쌍이면 시연으로만 쓰고 통계 효과를 주장하지 않는다. 같은 patch를 읽고 성공했다면 "기록 재사용 시험"이라고 쓴다.
4. **추가 지표**: Issue 신규/재사용 정확성, duplicate work 수, 시작 알림보다 이른 수정 수(0이어야 함), blocker 보고 완성도·provider 접수 수, 잘못 SUCCESS로 승격한 note 수, 관련 실패 사례 검색·인용률, 사례 참조 후 실제 업무 계약 결과. 분모·origin·관측 범위를 함께.
5. **문서 정합성**: [docs/11 §1](../docs/11-definition-of-done.md)의 기록 점검으로 STATUS.md를 확인한다. `LIVE_VERIFIED` 항목의 `evidence/` 또는 `runs/<run_id>/` 원본 경로를 열어 내용·실행 환경·사람 승인 근거가 주장과 맞는지 확인한다. docs·tasks와 구현이 다른 곳은 고치고 DECISIONS.md에 기록. README 초안에 [docs/11 §4](../docs/11-definition-of-done.md) 금지 표현이 없는지 검사.

## 수용 기준

- S4~S7 각 항목이 실제 실행 기록(run-record·evidence)과 연결되거나, 미실행 사유가 적혀 있다.
- cold_start와 memory_assisted 결과가 별도 표이고, memory run을 cold_start 성공률에 합산하지 않았다.
- v4 완료 주장 조건([docs/11 §6](../docs/11-definition-of-done.md))을 충족했는지, 부분 완료인지 명시했다.

## 금지·함정

- 실행 실패·no-hit run을 집계에서 빼지 않는다.
- 이전 사례를 사용한 run을 cold_start 성공률에 넣지 않는다.
