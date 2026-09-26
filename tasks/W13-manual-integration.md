# W13 — 사람 제안으로 전체 경로 통합 (`manual_integration`)

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2·G7·G8·G10) — `make start`·adapter·ScriptedAdapter는 A, 실제 통합 run은 게이트 후 |
| 선행 | W12, W26 (W24·W25 포함) |
| 목표 상태 | LIVE_VERIFIED (V4-CP2) |
| 원본 근거 | [spec 11 §4 첫 smoke 1~2단계](../spec/docs/11-runbook.md), [spec 10 §4 V4-CP2](../spec/docs/10-delivery-plan.md), [spec 05 §1 adapter](../spec/docs/05-agent-spec.md), [spec 09 §2](../spec/docs/09-scenarios-evaluation.md) |
| 참조 | [docs/05 전체](../docs/05-workflows.md), DECISIONS D53 |
| 요구 | V4-CP2, FR-12(일부) |

## 목표

모델 없이, 사람이 미리 작성한 제안(`origin=manual_integration`)으로 **실제** 경로를 한 번 끝까지 통과시킨다: S1 주입 → 감지 → Issue 연결/생성 → work 승인 → 시작 댓글 receipt → attempt → 제안 → 브로커 검사 → 봇 PR → 사람 리뷰·머지 → exact 배포 승인 → verifier PASS → RESOLVED → 결과 댓글.

## 만들 파일

- `linemedic/agent/adapter.py` — `AgentAdapter` 프로토콜: `run_agent(run_id, incident_id, work_id, attempt_id, deadline, workspace_ref, context_ref) -> AttemptResult`. `ScriptedAdapter`: 파일에서 제안을 읽어 attempt token으로 `/tools/proposals`에 제출하고 결과를 조회. attempt 기록에 `adapter=scripted`, `origin=manual_integration`
- `linemedic/eval/manual_proposals/s1_manual.json` — 사람이 작성한 S1 제안(재현 테스트 + 최소 수정 diff). **eval 폴더에만 두고** 에이전트 workspace·시드에 넣지 않는다
- `linemedic/control_plane/main.py` — `make start RUN_ID=`의 진입점: API 서버(uvicorn), supervisor 루프(READY work → `start_attempt` → adapter), issue poll 루프(W23), outbox worker(W26), broker worker(W09). 한 프로세스, 한 활성 run
- `supervisor.py` 추가 — attempt 시작 시 workspace 생성(`RUNS_DIR/<run>/workspaces/<attempt>/repo` = base 사본), attempt token 발급·종료 시 폐기, adapter 호출, deadline 초과 시 중단 → BLOCKED(`BUDGET_EXCEEDED`)
- Makefile — `make start RUN_ID=`, `make stop RUN_ID=`
- `runs/<run_id>/run-record.md` — 이 통합 run의 기록([spec templates/run-record.md](../spec/templates/run-record.md))
- 테스트: `integration/test_e2e_fake.py` — FakeGitHub·FakeDocker·ScriptedAdapter로 같은 경로 전체(사람 머지·승인은 테스트가 흉내)

## 구현 단계

1. fake E2E 테스트를 먼저 통과시킨다(모든 상태 전이·outbox·case event가 순서대로 기록되는지).
2. 실제 run: `make run-new` → `make start` → `make scenario-s1` → Issue 연결(S4-new 또는 existing) → `make approve-work`(또는 trusted 자동 승인) → 시작 댓글 확인 → ScriptedAdapter 제출 → PR 확인 → **사람**이 리뷰·머지(G7) → **사람**이 `make approve-release`(G8) → verifier 결과 → 결과 댓글.
3. run-record를 채운다. 사람 개입 시각과 대기 시간을 모두 적는다.

## 수용 기준

- fake E2E에서 시작 receipt 시각 < attempt 시작 시각, 모든 결합 전이가 docs/03 §3 표와 일치.
- 실제 run에서 Issue 번호, 시작 comment ID, PR 번호, merge SHA, image ID, verification ID, 결과 comment ID가 run-record에 연결된다.
- verification·case의 origin이 `manual_integration`이다. agent 성과 통계에 들어가지 않는다.

## 금지·함정

- 시작 알림 게이트를 시험 편의로 끄지 않는다. 끄고 돌린 결과를 v4 E2E라고 부르지 않는다.
- 사람 제안을 에이전트 산출물처럼 표시하지 않는다.
- 이 카드의 사람 제안 diff를 case memory의 정답 사례로 memory_assisted snapshot에 넣으려면 G9에서 사람이 명시적으로 고르고 origin을 유지한다.
