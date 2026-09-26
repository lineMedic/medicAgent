# W01 — 대회 참가 조건 R1~R5 확인

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | H(G6) — 에이전트는 기록 양식과 문안 준비만 |
| 선행 | — |
| 목표 상태 | LIVE_VERIFIED (답변 원문이 기록됨) / 답이 없으면 BLOCKED, R* = UNCONFIRMED 유지 |
| 원본 근거 | [spec 12 §1·§2·§7](../spec/docs/12-nvidia-requirements.md), [spec 13 §8](../spec/docs/13-demo-submission.md) |
| 요구 | FR-14 |

## 목표

R1(Skill API), R2(NeMo Framework/Microservices), R3(심사 항목), R4(데모·코드·개별 신청), R5(마감)의 현재 상태가 원문·답변과 함께 기록된다.

## 만들 파일

- `evidence/contest-conditions.md` — 아래 두 표

| ID | 원안·제공 자료의 내용 | 필요한 확인 | 상태 | 근거(확인일·출처·확인자) |
|---|---|---|---|---|
| R1 ~ R5 | [spec 12 §1](../spec/docs/12-nvidia-requirements.md) 표를 옮김 | | UNCONFIRMED | |

| 확인일 | 질문 원문 | 답변 원문 | 출처 | 확인자 | 영향받는 작업 |
|---|---|---|---|---|---|

## 사람이 할 일 (G6)

- [spec 12 §2](../spec/docs/12-nvidia-requirements.md)의 문의 문안을 주최 측에 보낸다.
- 답변을 받으면 원문 그대로 에이전트에게 주거나 위 표에 직접 적는다.
- R5 공식 마감 시각을 확인해 G11 체크포인트에 반영한다.

## 에이전트가 할 일

1. 기록 파일을 만들고 모든 R을 `UNCONFIRMED`로 둔다.
2. 답변이 들어오면 원문을 표에 옮기고, 영향을 받는 카드(W14 runtime, W21 제출, Guardrails 사용 여부)를 STATUS.md의 해당 카드 메모에 적는다. 대회 조건 표의 상태·근거도 함께 갱신한다.
3. R2가 "NeMo 필수"로 확인되면 DECISIONS.md에 새 결정을 추가하고 사람과 범위를 정한다(기술을 그림에만 추가하지 않는다).

## 금지·함정

- 에이전트가 문의를 보내거나 신청서를 제출하지 않는다.
- "NAT를 썼으니 R2 충족", "SKILL.md가 있으니 R1 충족"으로 스스로 판정하지 않는다.
- 답이 없는 조건을 `확인 완료`로 바꾸지 않는다.
