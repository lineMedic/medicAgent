# N01 — 모델 접근·도구 호출 형식

| 항목 | 값 |
|---|---|
| 날짜 (UTC) | 2026-09-28T08:07Z |
| 실행자 | 에이전트(Claude Code 세션, 사용자 요청: 해커톤 제출 전 live 재현 시험) |
| 환경 | 개발 Mac(Darwin 25.6.0, arm64, Python 3.14.7) — 데모 호스트 아님(G1 미확정). runtime 없음(직접 HTTP), sandbox 없음(local). 모델 `nvidia/nemotron-3.5-lightning-30b-a3b`, base URL `https://integrate.api.nvidia.com/v1` |
| 명령 | `.venv/bin/python -m linemedic.scripts.spikes.n01_model_tool_call --output evidence/spikes/N01-model-tool-call.json` → 종료 코드 0. 이어서 `.venv/bin/pytest -m live_model -q` → 1 passed (22.73s) |
| 출력 요약 | 1차 요청: `finish_reason=tool_calls`, `get_incident(incident_id=INC-000000000001)` 선택(200, 3191ms, 578 tokens, request `6a4703f3-5eff-4972-b61a-9549a76035ac`). 2차(도구 결과 재입력): `finish_reason=stop`, 구조화 제안(category `code_bug`, evidence 2개, action `escalate`/`INSUFFICIENT_EVIDENCE`, 열린 질문 2개)(200, 27356ms, 1466 tokens, request `1c209941-eb90-4849-98dc-0a081ea5c50d`). 응답 모델 = 요청 모델 |
| 원본 경로 | `evidence/spikes/N01-model-tool-call.json` |
| 판정 | PASS |
| 다음 행동 | 확인한 모델 ID로 `NVIDIA_MODEL_ID` 확정(G3). 도구는 가짜 `get_incident` 1개이며 LineMedic 도구 9개·sandbox 안 실행은 확인하지 않았다(N02·G4 runtime 선택, G5 OpenShell 필요) |
