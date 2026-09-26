# W14 — 실제 runtime 에이전트 (local 모드) S1·S2-lite 제안

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G3·G4) — tool client·prompt·예산·trace·workspace는 A, 실제 모델 실행은 게이트 후 |
| 선행 | W02(G4 결정), W09, W13 |
| 목표 상태 | LIVE_VERIFIED (local 모드 실제 모델 제안 1건 이상. 평가 집계에는 넣지 않음) |
| 원본 근거 | [spec 05 §1~§9](../spec/docs/05-agent-spec.md), [spec 12 §3·§3.1·§4·§6](../spec/docs/12-nvidia-requirements.md), [spec 01 FR-03·FR-04](../spec/docs/01-requirements.md) |
| 참조 | [docs/02 §5](../docs/02-repo-layout.md), [docs/04 §2·§3](../docs/04-api-reference.md), [docs/07 §1](../docs/07-constants.md), DECISIONS D53 |
| 요구 | FR-03, FR-04, FR-09 (제안 품질 맞추기), INV-09 |

## 목표

G4에서 고른 runtime **하나**로 실제 Nemotron 에이전트가 `/tools/*`를 스스로 골라 호출하고, workspace의 repo 사본을 읽고 재현 테스트·최소 패치를 만들어 schema에 맞는 제안을 제출한다. host가 deadline·도구 예산·trace를 강제·기록한다. 이 단계는 local 모드이며 평가·영상에는 쓰지 않는다.

## 만들 파일

- `linemedic/agent/runtime_<openclaw|nat>.py` — G4에서 고른 것 **하나만**. `AgentAdapter` 구현. 설치 버전에서 확인한 실제 API만 사용(교육 자료 문법을 그대로 복사하지 않음)
- `linemedic/agent/tools_client.py` — `/tools/*` 9개 HTTP client. token은 prompt가 아니라 client 설정으로 전달
- `linemedic/agent/prompts/system.md` — [spec 05 §5](../spec/docs/05-agent-spec.md) 템플릿(제품 runtime용). 개발 지침·시나리오 ID·정답·token·관리 주소를 넣지 않는다
- `linemedic/agent/skills/code-exception/SKILL.md`, `linemedic/agent/skills/vision-quality-drop/SKILL.md` — [spec 05 §6](../spec/docs/05-agent-spec.md) 내용. 완성 패치·holdout 기대값 없음. 업무 규칙(누락 검사자 `미지정`, 전체 건수 유지)은 제공 가능
- `linemedic/agent/trace.py` — attempt별 기록: runtime·version, model ID, prompt hash(렌더링된 system prompt + skills의 SHA-256), tool trace(서버 측 `/tools` 호출 로그 + runtime이 관측한 로컬 도구), token usage(관측 못 하면 `null`, 일부면 `partial`), 시간
- `supervisor.py` 변경 — workspace 구성([docs/02 §5](../docs/02-repo-layout.md)): `/sandbox/work/repo`(base 사본), `/sandbox/work/output`, `/agent_rules`(읽기 전용: prompt·skills·도구 설명). 넣지 않는 것 목록을 코드로 검사. `agent_mode=local` 기록. 예산: deadline 240초 강제 종료, `/tools` 호출 수 서버 측 계수(15 초과 → 429 또는 이관), 모델 일시 오류 1회 재시도(외부 변경 없는 단계만)
- `linemedic/tests/unit/test_agent_workspace.py` — workspace와 prompt·skills에 금지 자료(holdout 값, `S1`, `expected_category`, operator token 패턴, `eval/` 경로)가 없음을 검사
- `linemedic/tests/live/test_model_toolcall.py`(`live_model`)

## 구현 단계

1. workspace·prompt 검사 테스트와 예산 강제 테스트(가짜 runtime)를 먼저 쓴다.
2. tools client·trace를 구현한다.
3. G4 runtime adapter를 구현한다. 시나리오 ID를 입력으로 넘기지 않는다. context에는 서버가 확인한 work·Issue·receipt·memory mode만.
4. local 모드로 S1 incident에 대해 실제 실행 → 제안 제출 → broker 결과 확인. S2-lite도 1회.
5. 결과를 `evidence/W14-local-runs.md`에 기록(제안 원본 hash, broker decision, tool trace 위치). 사람이 diff를 고치지 않는다.

## 수용 기준

- 실제 모델 ↔ 도구 왕복 ↔ schema-valid 제안이 최소 1건 연결된다(FR-03).
- 범위 밖·시간 초과·도구 실패 → escalate 또는 host의 BLOCKED 처리.
- 접수(202)를 복구 성공으로 보고하지 않는다.
- model ID·runtime version·prompt hash·tool trace·token(또는 null/partial)이 attempt에 기록된다.
- local 결과는 `agent_mode=local`로 표시되고 평가 집계에 들어가지 않는다.

## 금지·함정

- 두 runtime을 구현하지 않는다.
- 에이전트에 GitHub·SMTP·operator credential, Docker socket을 주지 않는다.
- critic·전문가 pool·다중 에이전트를 만들지 않는다.
- 정답 category를 넣은 fixture 테스트는 adapter unit test로만 쓴다.
