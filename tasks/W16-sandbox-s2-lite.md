# W16 — sandbox 안 실제 agent S2-lite (기본·recent-deploy 혼동)

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G5, 그리고 G2·G3·G10) |
| 선행 | W14, W08 (W15의 sandbox 준비) |
| 목표 상태 | LIVE_VERIFIED (V4-CP3) |
| 원본 근거 | [spec 09 §3](../spec/docs/09-scenarios-evaluation.md), [spec 01 AC-S2·FR-09](../spec/docs/01-requirements.md), [spec 06 §7](../spec/docs/06-broker-runner.md), [spec 05 §4·§6](../spec/docs/05-agent-spec.md) |
| 참조 | [docs/07 §4](../docs/07-constants.md), DECISIONS D58·D59 |
| 요구 | FR-09, FR-15, AC-S2, INV-08, INV-10 |

## 목표

카메라 2번 이상에서 sandbox 안 실제 에이전트가 지표·배포·매뉴얼을 조회하고 `equipment + create_work_order_draft`를 제안해, 코드 변경·PR·배포 없이 정비 요청 초안과 `HANDOFF_DRAFTED` Issue 댓글이 남는다. recent-deploy 변형에서도 시간상 동시 발생만으로 코드 수정에 뛰어들지 않는지 기록한다.

## 할 일

1. 기본 변형 2회, recent-deploy 변형 1회를 각각 새 run으로 실행한다(`make scenario-s2-lite RUN_ID= [RECENT_DEPLOY=1]`).
2. 각 run에서 확인·기록: 에이전트가 조회한 도구(지표 비교, 배포 기록, 매뉴얼), 제안 category·action, 초안 필드(`equipment_id`, 관찰 지표, 가설 표시, `open_questions`, 허용된 `manual_ref_id`, `review_required: true`, `delivery_status: not_sent`), 코드 변경·PR·배포 0건, incident `WORK_ORDER_DRAFTED`·work `HANDED_OFF`, 알림 receipt, case note `HANDOFF`.
3. 에이전트가 PR을 고르면 그대로 **오답으로** 기록한다(사후 수정 금지).
4. run-record의 해당 칸을 채우고 코드·배포 칸은 `N/A`로 둔다.

## 수용 기준 (AC-S2)

- 정비 요청 초안이 남고 서버 패치·재시작·배포·정비 완료 처리가 없다.
- 렌즈 오염 등 세부 원인은 가설로 표시된다.
- `delivery_status=not_sent`(실제 CMMS·현장 지시)와 GitHub `HANDOFF_DRAFTED` 알림 상태가 따로 기록된다.
- 변형별 결과가 분리돼 있다.

## 금지·함정

- 초안을 실제 정비 지시로 보내지 않는다. "정비 완료"로 표시하지 않는다.
- 지표 숫자만으로 원인을 확정했다고 점수화하지 않는다.
