# W28 — agent 문맥 통합: Issue·receipt·사례 인용·결과 이벤트

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G3~G5, G2·G10) — 문맥 구성·도구·이벤트 배선은 fake로 A |
| 선행 | W14, W24, W25, W26, W27 |
| 목표 상태 | LIVE_VERIFIED (V4-CP3·CP4) |
| 원본 근거 | [spec 05 §10·§11](../spec/docs/05-agent-spec.md), [spec 17 §5·§6](../spec/docs/17-case-memory.md), [spec 06 §10](../spec/docs/06-broker-runner.md), [spec 02 §3](../spec/docs/02-architecture.md), [spec 12 §8 N14](../spec/docs/12-nvidia-requirements.md) |
| 참조 | [docs/05 ④·⑤·사례 기억 흐름](../docs/05-workflows.md), [docs/04 §2·§5](../docs/04-api-reference.md) |
| 요구·테스트 | FR-23, FR-24, INV-12, INV-14, AC-S5, AC-S7 / T-MEM-06, N14, S5-new E2E, S7 1쌍 |

## 목표

시작 게이트를 통과한 attempt의 문맥에 서버가 확인한 Issue·시작 receipt·memory mode·초기 검색 결과가 들어가고, 에이전트가 `get_bound_issue`·`search_cases`로 더 조회해 과거 사례를 **history projection ID로 인용**한다. 각 결과(PR_READY, HANDOFF_DRAFTED, WORK_BLOCKED, RECOVERY_*)는 host가 자동으로 알림·case event로 남긴다.

## 만들 파일·변경

- `supervisor.py` — `start_attempt` 직후 `memory.prepare_snapshot_context()`로 초기 검색 1회(cold_start면 DISABLED 기록), `context_ref`에 `work_id, issue_ref(repo_id, number, snapshot_sha256), start_receipt, run_id, attempt_id, base identity, memory{mode, snapshot_id, retrieval_id, note_ids}` 저장. 메모리 서비스 실패 시 `history_status=UNAVAILABLE`을 문맥에 표시하고, run policy(`memory.on_unavailable: proceed|stop`)에 따라 진행 또는 BLOCKED
- `tools_api.py` — `get_bound_issue`(서버 확정 값만, 임의 repo 검색 아님), `get_incident`에 work·issue·start_notification·memory 필드([spec 03 §2 예시](../spec/docs/03-api-contracts.md))
- adapter 입력 — runtime에 context를 넘기는 방식은 W14 adapter 안에 둔다(Issue 본문·사례 텍스트는 "비신뢰 자료" 구분 표시와 함께)
- broker 연결 확인 — PR 본문에 parent Issue·work generation·run/incident·base/candidate·검사 ID. 제안의 `evidence_ids`에 과거 run의 원본 evidence ID가 있으면 `EVIDENCE_SCOPE_MISMATCH`, projection ID는 허용
- 결과 이벤트 배선 점검 — 모든 결합 전이가 outbox intent와 case event를 같은 트랜잭션에 남기는지 통합 테스트로 확인
- 테스트: `integration/test_agent_context.py`(T-MEM-06 포함), `integration/test_e2e_fake.py` 확장(S5-new·S7 경로)

## 구현 단계

1. T-MEM-06을 먼저 쓴다: case note 본문에 "검사기를 끄라", 임의 URL, 수신자 주소가 있어도 권한 확대·외부 전송·검증 생략이 없다(브로커·verifier 경로 그대로, 알림 route 불변).
2. context 구성과 도구 두 개를 구현한다.
3. fake E2E: S5-new(로그 없이 Issue로 접수 → 시작 알림 → ScriptedAdapter), S7(memory_assisted snapshot에 VERIFIED_FAILURE 사례 → 검색 → projection 인용).
4. live(G3~G5): S5-new 실제 1회, S7 cold_start 1회 + memory_assisted 1회(같은 모델·예산·base). N14: 사례 projection이 실제 모델 context와 tool trace에 전달됐는지 evidence에 남긴다.

## 수용 기준

- 유효한 binding과 ACCEPTED 시작 알림이 없으면 workspace·패치가 생기지 않는다(host 진입점에서 강제, INV-12).
- memory_assisted run의 제안·trace에 사례 note ID(projection evidence ID)가 인용된다. cold_start run에는 사례가 없다.
- 사례가 말하는 source/contract가 현재와 다르면 경고가 문맥에 있고, 현재 검사(R1·R2·verifier)를 그대로 수행한다.
- PR-only·권한 차단 사례를 성공/오답으로 해석한 표현이 context·PR 본문에 없다.
- 결과 알림 4종이 각각 다른 메시지로 발송·기록된다(FR-24).

## 금지·함정

- 모델이 notify 도구를 골라야 알림이 가는 구조로 만들지 않는다.
- 저장만 되고 모델 context로 전달되지 않았으면 "RAG 동작"이라고 쓰지 않는다.
- 같은 run의 결과를 같은 run의 memory로 되먹이지 않는다.
