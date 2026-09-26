# W15 — OpenShell sandbox 안 실제 agent S1 전체 경로

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G5·G7·G8, 그리고 G2·G3·G10) |
| 선행 | W14, W13 |
| 목표 상태 | LIVE_VERIFIED (V4-CP3) |
| 원본 근거 | [spec 05 §1 샌드박스 통합 순서·§3·§9·§10](../spec/docs/05-agent-spec.md), [spec 07 §4](../spec/docs/07-security.md), [spec 01 FR-15·AC-S1·AC-SBX](../spec/docs/01-requirements.md), [spec 12 §3.1·N03·N04·N10](../spec/docs/12-nvidia-requirements.md), [spec 14 W02](../spec/docs/14-decisions-sources.md#w02) |
| 참조 | [docs/05 ①~⑥](../docs/05-workflows.md), [docs/06 §3](../docs/06-invariants.md) |
| 요구 | FR-04, FR-15, FR-16, AC-S1, AC-SBX |

## 목표

W14의 같은 adapter가 데모 호스트의 OpenShell sandbox 안에서 실행되고, 실제 에이전트가 만든 패치가 Issue 연결·시작 알림·브로커·봇 PR·사람 리뷰·exact 배포·업무 검증을 거쳐 `RESOLVED`에 도달한다. run 기록에 `agent_mode=sandbox`, 정책 hash, sandbox identity가 남는다.

## 만들 파일

- `linemedic/policies/openshell/<policy file>` — 설치 버전의 **실제 schema**로 작성: egress는 승인된 추론 경로와 Control 도구 API만, `/ops/*`·GitHub·Docker API 불가, 파일 쓰기는 workspace·임시 공간만, `/agent_rules`·host 비밀 접근 불가, non-root, 불필요한 provider 연결 없음, 에이전트가 정책 예외를 승인할 수 없음
- policy hash 계산과 effective policy 스냅샷 저장(`runs/<run>/sandbox/effective-policy.*`)
- `supervisor.py` 변경 — `AGENT_MODE=sandbox`면 adapter를 sandbox 안에서 기동. attempt에 `agent_mode`, `policy_sha256`, `sandbox_identity`, `sandbox_verified`(필수 보호 적용 확인 여부) 기록. (OpenClaw면) attempt 전후 workspace 규칙 파일 hash 기록(N10)
- doctor 항목 `openshell`(버전, 정책 파일 hash, sandbox 기동 가능)
- `runs/<run_id>/run-record.md` — S1 실제 run 기록

## 구현 단계

1. N03(sandbox → tools API 허용, `/ops` 거절)과 N04(정책 적용·거절 로그)를 먼저 확인해 evidence에 남긴다.
2. sandbox 안에서 W14 local 실행과 같은 입력으로 1회 돌려 제안이 제출되는지 확인한다.
3. S1 전체 run: `make run-new` → `make start`(AGENT_MODE=sandbox) → `make scenario-s1` → Issue 연결 → 승인·시작 댓글 → sandbox agent → broker → PR → **사람 리뷰·머지(G7)** → **사람 배포 승인(G8)** → verifier → RESOLVED → 결과 댓글 → case note.
4. run-record의 §1~§6·§11~§13을 모두 채운다. 사람 개입과 대기 시간을 적는다.
5. V4-CP3 목표 시각까지 sandbox 통합이 안 되면 "sandbox 통합 미완료"로 공개하고 local 결과와 프로브를 구분해 기록한다.

## 수용 기준 (AC-S1, AC-SBX)

- 에이전트 프로세스가 sandbox 안에서 실행되고, 도구 API와 추론 경로만 성공한다.
- run 기록에 `agent_mode=sandbox`, 정책 hash, sandbox identity, host manifest가 있다.
- 사람이 패치를 대신 쓰거나 고치지 않았다(고쳤다면 `human-edited`로 분류하고 agent 성과에서 제외).
- 승인한 코드·실행 image·업무 검사 결과가 연결되고, 모든 계약·관찰 조건을 만족할 때만 RESOLVED.

## 금지·함정

- 평가 편의를 위해 평가용 sandbox 정책을 넓히지 않는다.
- local 모드 결과를 sandbox 결과와 합치지 않는다.
- 보호를 적용하지 못했으면 `sandbox_verified=false`로 기록한다.
