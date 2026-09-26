# W02 — 모델·런타임·샌드박스 스파이크 (N01~N05, N08~N10)

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G3·G4·G5) — 스크립트 작성은 A, 실행·판정은 게이트 필요 |
| 선행 | B00 |
| 목표 상태 | LIVE_VERIFIED (각 스파이크의 실제 결과가 evidence에 있음, G4 결정 완료) |
| 원본 근거 | [spec 12 §3·§4·§5·§6](../spec/docs/12-nvidia-requirements.md), [spec 05 §1·§3](../spec/docs/05-agent-spec.md), [spec 07 §4](../spec/docs/07-security.md) |
| 요구 | FR-03, FR-14, FR-15 (준비) |

## 목표

V4-CP0에서 runtime을 **하나** 고를 수 있을 만큼의 실측 결과를 만든다. OpenClaw/NemoClaw 경로가 제한 시간 안에 N01·N02를 통과하지 못하면 NAT로 한 번 전환한다.

## 만들 파일

- `linemedic/scripts/spikes/n01_model_tool_call.py` — `NVIDIA_BASE_URL`의 chat completions에 도구 1개(`get_incident` 모양의 가짜 도구)를 정의해 호출 → 모델이 tool call을 내는지 → 가짜 결과를 다시 넣고 → 최종 JSON 제안을 받는지. 모델 ID, request ID, 지연, `usage` token(없으면 null)을 기록. `httpx`만 사용. endpoint가 OpenAI 호환 형식인지부터 실제 응답으로 확인한다
- `linemedic/scripts/spikes/README.md` — N02~N05·N08~N10 수동 절차 체크리스트
- `evidence/spikes/Nxx-*.md` — 스파이크마다 한 파일: 날짜(UTC), 실행자, 버전, 명령, 출력 요약과 원본 경로, 판정(PASS/FAIL/INCONCLUSIVE), 다음 행동

## 스파이크 목록

| ID | 확인 | 통과 증거 | 미달 시 | 게이트 |
|---|---|---|---|---|
| N01 | Nemotron 접근·tool 호출 형식 | tool → 결과 재입력 → 제안 성공 1회 | 접근 가능한 다른 Nemotron 후보로 한 번 교체, 변경 기록 | G3 |
| N02 | runtime의 로컬 코드/테스트 지원 | pinned repo 사본에서 읽기·파일 수정·pytest 실행·제안 제출(로컬 fake `/tools`) | NAT 단일 adapter로 전환 | G4 |
| N03 | sandbox → Control API 접근·인가 | 허용 조회/제안 성공, `/ops/*` 거절 | 주소·effective policy·API auth를 각각 조사 | G5 |
| N04 | 실제 policy 문법·적용·거절 로그 | 고정된 policy 파일·hash·대조 결과 | 문서만 있는 상태로 구분, 차단 주장 보류 | G5 |
| N05 | NVIDIA 키·agent token 전달 위치 | 이름·권한·위치만 적은 secret inventory | 추론 키가 sandbox 안에 있으면 그대로 공개 범위 설명 | G3·G5 |
| N08 | OS·Python·패키지·image 호환 | 재현 환경 manifest | 원안 버전 강제 대신 호환 버전 하나로 고정 | G1 |
| N09 | 데모 호스트에서 OpenShell·runtime·Docker 조합 | host manifest, sandbox 기동 기록 | 다른 머신·VM으로 교체(평가 시작 전만) | G1·G5 |
| N10 | (OpenClaw 선택 시) workspace·skill 파일 쓰기 가능 여부 | 쓰기 시도 결과 | 쓰기 가능하면 attempt 전후 hash 기록([spec 05 §3](../spec/docs/05-agent-spec.md)) | G5 |

N06(runner·MES 네트워크)은 W10·W12, N07(GitHub 보호)은 W03, N11~N15는 W22~W29에서 한다.

## 구현 단계

1. n01 스크립트를 먼저 작성하고, 키 없이 실행하면 `NOT_CONFIGURED`로 끝나는지 확인한다(단위 테스트 1개).
2. G3 요청을 보낸다. 키가 들어오면 N01을 실행하고 결과를 기록한다.
3. N02: 사람과 함께 OpenClaw/NemoClaw 설치 경로를 시도한다. 교육 자료의 gateway·SDK 문법을 그대로 믿지 말고 설치된 버전에서 확인한다. 제한 시간(G11에서 정함)을 넘기면 NAT로 같은 체크리스트를 수행한다.
4. N02 결과를 사람에게 보여 주고 G4 결정을 받는다. DECISIONS.md에 새 결정(runtime, 버전, 이유)을 추가한다. `AGENT_RUNTIME` 값을 확정한다.
5. G5 이후 N03·N04·N09·N10을 수행한다. 정책 YAML은 설치 버전의 실제 schema로 작성한다.
6. N05 secret inventory를 작성한다(값 없이 이름·보관 위치·접근 주체).

## 수용 기준

- N01·N02 결과가 evidence에 있고 G4가 결정됐다.
- local 성공과 sandbox 성공이 별도 행으로 기록돼 있다.
- `NVIDIA_MODEL_ID`가 원안 후보명이 아니라 N01에서 실제 확인한 값이다.

## 금지·함정

- 두 runtime을 동시에 완성하지 않는다. 선택하지 않은 쪽은 설치만 해 두고 "사용"이라고 적지 않는다.
- 검증하지 않은 OpenShell YAML을 실행 설정으로 커밋하지 않는다.
- 키를 스크립트 출력·evidence에 남기지 않는다.
