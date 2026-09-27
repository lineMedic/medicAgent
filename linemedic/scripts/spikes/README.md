# 스파이크 절차 (W02)

V4-CP0에서 runtime을 **하나** 고르기 위한 실측 절차다. 원본: [tasks/W02](../../../tasks/W02-runtime-spikes.md), [spec 12 §3~§6](../../../spec/docs/12-nvidia-requirements.md).

- 실행한 스파이크만 `evidence/spikes/Nxx-<주제>.md`로 기록한다. 실행하지 않은 스파이크의 파일을 미리 만들지 않는다.
- 판정은 `PASS / FAIL / INCONCLUSIVE`다. 확인하지 못한 값은 `미확인`, 실행하지 않은 항목은 `NOT_RUN`으로 둔다.
- 키·토큰은 출력·evidence에 남기지 않는다. secret은 이름·보관 위치·접근 주체만 적는다.
- local 성공과 sandbox 성공은 **별도 행**으로 기록한다.
- N06은 W10·W12, N07은 W03, N11~N15는 W22~W29에서 한다.

## evidence 기록 양식

```markdown
# Nxx — <주제>

| 항목 | 값 |
|---|---|
| 날짜 (UTC) | 2026-09-27T00:00Z |
| 실행자 | <사람 또는 에이전트> |
| 환경 | 호스트·OS·arch, 관련 버전 (runtime·OpenShell·모델 ID) |
| 명령 | 실행한 명령 그대로 (비밀 제외) |
| 출력 요약 | 핵심 결과 몇 줄 |
| 원본 경로 | evidence/spikes/... 또는 runs/<run_id>/... |
| 판정 | PASS / FAIL / INCONCLUSIVE |
| 다음 행동 | 예: G4 결정 요청, 다른 모델 후보로 1회 교체 |
```

## N01 — 모델 접근·도구 호출 형식 (G3, 자동 스크립트)

```bash
# .env에 NVIDIA_BASE_URL, NVIDIA_MODEL_ID, NVIDIA_API_KEY를 사람이 넣은 뒤
.venv/bin/python -m linemedic.scripts.spikes.n01_model_tool_call \
    --output evidence/spikes/N01-model-tool-call.json
```

- 스크립트는 가짜 `get_incident` 도구 1개로 tool call → 결과 재입력 → 구조화 제안까지 확인하고, 모델 ID·request ID·지연·token(없으면 null)을 JSON으로 남긴다.
- 종료 코드: PASS 0, FAIL·INCONCLUSIVE 1, 필수 env 없음 2(`NOT_CONFIGURED`).
- 통과 증거: tool → 결과 재입력 → 제안 성공 1회. 결과 JSON과 함께 `evidence/spikes/N01-model-tool-call.md`를 위 양식으로 쓴다.
- 미달 시: 접근 가능한 다른 Nemotron 후보로 **한 번** 교체하고 변경을 기록한다. `NVIDIA_MODEL_ID`는 N01에서 실제 확인한 값으로만 확정한다.
- live 시험: `make test-live`의 `linemedic/tests/live/test_model_toolcall.py`가 같은 스크립트를 실행한다.

## N02 — runtime의 로컬 코드·테스트 지원 (G4)

OpenClaw/NemoClaw를 먼저 시도하고, G11에서 정한 제한 시간 안에 통과하지 못하면 NAT로 같은 체크리스트를 한 번 수행한다. 교육 자료의 gateway·SDK 문법을 그대로 믿지 말고 설치된 버전에서 확인한다.

- [ ] 설치한 runtime 이름·버전·설치 경로 기록
- [ ] 실제 모델이 도구를 선택하고 결과를 다시 읽음
- [ ] 고정된 repo 사본에서 파일 읽기
- [ ] 허용된 로컬 파일 수정
- [ ] 사전 설치된 pytest 실행
- [ ] 유효한 제안을 로컬 fake `/tools`에 제출하고 결과 조회. fake `/tools`는 W06~W09에서 생긴다. 아직 없으면 이 항목은 `NOT_RUN`으로 두고 나머지만 판정한다
- [ ] host 쪽에서 deadline·실행 종료·trace 회수 가능 여부
- 결과를 사람에게 보여 주고 G4 결정을 받는다. DECISIONS.md에 runtime·버전·이유를 새 결정으로 추가하고 `AGENT_RUNTIME`을 확정한다. 선택하지 않은 쪽은 "사용"이라고 적지 않는다.

## N03 — sandbox에서 Control API 접근·인가 (G5)

- [ ] sandbox 안에서 허용된 `/tools/*` 조회·제안 제출 성공
- [ ] 같은 sandbox에서 `/ops/*` 요청이 거절됨 (W06의 인증 구현 필요)
- [ ] 실패 시 주소·effective policy·API 인증을 각각 조사해 원인을 분리

## N04 — 실제 policy 문법·적용·거절 로그 (G5)

- [ ] 설치 버전의 실제 policy schema로 정책 파일 작성, 파일 hash 기록
- [ ] effective policy 확인 방법과 출력 보존
- [ ] 허용 요청과 금지 요청을 같은 sandbox에서 대조하고 거절 로그 확보
- 문서만 있고 실제 적용을 확인하지 못했으면 "문서만 있음"으로 구분하고 차단을 주장하지 않는다. 검증하지 않은 YAML을 실행 설정으로 커밋하지 않는다.

## N05 — NVIDIA 키·agent token 전달 위치 (G3·G5)

- [ ] secret inventory 작성: 이름, 권한 범위, 보관 위치, 접근 주체 (값 없음)
- [ ] 추론 키가 sandbox 안에 있으면 그대로 적고 공개 범위를 설명한다

## N08 — OS·Python·패키지·image 호환 (G1)

- [ ] 데모 호스트에서 `make setup`, `make doctor`, `make host-manifest > evidence/host-manifest.json`
- [ ] 원안 버전을 강제하지 않고 호환되는 버전 하나로 고정해 기록

## N09 — 데모 호스트의 OpenShell·runtime·Docker 조합 (G1·G5)

- [ ] OpenShell Support Matrix와 호스트 대조
- [ ] 실제 sandbox 기동 기록과 host manifest 재생성 (OpenShell·runtime 버전이 채워져야 함)
- 안 되면 다른 머신·VM으로 교체한다. 교체는 평가 시작 전에만 한다.

## N10 — (OpenClaw 선택 시) workspace·skill 파일 쓰기 가능 여부 (G5)

- [ ] 에이전트로 AGENTS.md·skill 파일 쓰기 시도, 결과 기록
- [ ] 쓰기 가능하면 attempt 시작·종료 시 파일 hash를 기록하도록 W14·W15에 반영 ([spec 05 §3](../../../spec/docs/05-agent-spec.md))
