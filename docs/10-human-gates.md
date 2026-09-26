# 10. 사람 게이트 G1~G12

> 원본: [spec 10 §1](../spec/docs/10-delivery-plan.md), [spec 11 §1·§5](../spec/docs/11-runbook.md), [spec 12](../spec/docs/12-nvidia-requirements.md), [spec 06 §6](../spec/docs/06-broker-runner.md).
> 게이트는 코딩 에이전트가 할 수 없거나 해서는 안 되는 결정·권한·행동이다. 에이전트는 게이트를 우회하거나 흉내 내지 않는다. 비밀값은 사람이 `.env`에 직접 넣고, 대화나 문서에 붙여 넣지 않는다.

## 1. 요청 형식

에이전트는 게이트가 필요할 때 STATUS.md 사람 게이트 표의 해당 행을 `REQUESTED`로 갱신하고 요청 시각(UTC)·절차를 적은 뒤 사용자에게 아래 형식으로 알린다. `OPEN`은 아직 준비·승인이 완료되지 않은 상태다. 사람이 완료를 알려 주면 `DONE`과 완료 시각·승인 범위·확인 근거(비밀 제외)를 기록한다. G7·G8은 해당 run의 사람 행동을 확인하며 이전 run의 완료를 새 run 승인으로 쓰지 않는다.

```text
[게이트 요청] G2 — GitHub 데모 저장소·봇 준비
필요한 이유: W22~W26의 live 검증(Issue 조회·생성·댓글)에 필요
해 주실 일:
  1. ...
  2. ...
알려 주실 값(비밀 아님): GITHUB_REPOSITORY, GITHUB_REPOSITORY_ID, 리뷰어 계정명
.env에 직접 넣을 값(비밀): GITHUB_BROKER_CREDENTIAL, GITHUB_SETUP_CREDENTIAL
확인 방법: make doctor 의 github 항목, scripts/github_setup_check.py 출력
그동안 에이전트는: FakeGitHub로 W23~W26 구현을 계속함
```

## 2. 게이트 목록

| ID | 무엇 | 사람이 할 일 | 에이전트에게 줄 값 (비밀은 `.env`에만) | 게이트 전 에이전트가 할 수 있는 일 | 확인 | 카드 |
|---|---|---|---|---|---|---|
| G1 | 데모 호스트 확정 | 평가·영상에 쓸 호스트 1대 결정(OS·arch·메모리·디스크, 다른 운영 workload 없음). OpenShell Support Matrix와 대조 | `DEMO_HOST_ID`, 호스트 접근 방법 | 로컬에서 개발·fake 테스트 | `python -m linemedic.scripts.host_manifest` 결과를 `evidence/host-manifest.json`에 저장 | W00, B00 |
| G2 | GitHub 준비 | ① 팀 조직에 `l3-mes-api` repo 생성 ② 봇 계정 또는 GitHub App 생성, 해당 repo에만 metadata·Issues·Pull requests·필요한 contents 권한 ③ 봇이 아닌 리뷰어 계정 지정 ④ `baseline/*` 패턴 보호(필수 리뷰 1, 최신 변경 승인, 봇 직접 push 금지) ⑤ squash 머지만 허용 ⑥ 자동 처리할 작성자의 **숫자 user ID** 목록 확정 | `GITHUB_REPOSITORY`, `GITHUB_REPOSITORY_ID`, 리뷰어 계정명, `ISSUE_TRUSTED_AUTHOR_IDS` / 비밀: `GITHUB_BROKER_CREDENTIAL`, `GITHUB_SETUP_CREDENTIAL` | `FakeGitHub`로 W22~W26·W11 구현, `github_setup_check.py`·`seed_demo_repo.py` 작성 | `scripts/github_setup_check.py`: repo ID 일치, 봇 권한 범위, 보호 규칙, 리뷰 없는 머지·봇 직접 push 거절 (N07·N11) | W03, W22 |
| G3 | NVIDIA 추론 | build.nvidia.com 키 발급, 사용할 Nemotron 모델 후보 선택 | `NVIDIA_BASE_URL`, `NVIDIA_MODEL_ID` 후보 / 비밀: `NVIDIA_API_KEY` | N01 스파이크 스크립트 작성 | `scripts/spikes/n01_model_tool_call.py`: tool 선택 → 결과 재입력 → 구조화 제안 1회 | W02, W14 |
| G4 | runtime 선택 | W02의 N01·N02 결과를 보고 OpenClaw/NemoClaw 또는 NAT **하나**를 결정 | `AGENT_RUNTIME` 값과 버전 | `AgentAdapter` 인터페이스·`ScriptedAdapter`까지 | DECISIONS.md에 새 결정으로 기록 | W02, W14 |
| G5 | OpenShell | 데모 호스트에 OpenShell 설치, 버전 고정, effective policy 확인 방법 확보 | OpenShell 버전, 정책 schema 문서 위치 | 정책 초안의 **경계 목록**만(실행 YAML은 실제 schema 확인 후) | N03·N04·N09·N10 결과를 `evidence/`에 저장 | W02, W15~W17 |
| G6 | 대회 조건 | [spec 12 §2](../spec/docs/12-nvidia-requirements.md)의 문의 문안을 주최 측에 발송하고 답변을 받음 | 답변 원문(확인일·질문·답변·출처·확인자) | `evidence/contest-conditions.md` 기록 양식 준비 | R1~R5 표 갱신. 답이 없으면 UNCONFIRMED 유지 | W01, W21 |
| G7 | PR 리뷰·머지 | 봇 PR을 사람이 diff·근거·허용 파일 범위를 보고 리뷰, squash 머지 | PR 번호, 머지 여부 | PR 본문·검사 기록 준비 | GitHub `merged=true`, reviewer ≠ bot | W12, W13, W15 |
| G8 | 배포 승인 | [spec 11 §5](../spec/docs/11-runbook.md) 체크리스트 확인 후 `make approve-release ...` **사람이 직접 실행** | — | 체크리스트 출력, 명령 인자 준비 | release execution 기록의 operator principal | W12, W13, W15 |
| G9 | memory snapshot 선택 | memory_assisted 평가에 넣을 과거 note(성공·실패·차단)를 골라 승인 | snapshot에 넣을 note ID 목록 | `make memory-snapshot` 구현, 후보 목록 출력 | `eval/snapshots/MEM-*.json` manifest | W27, W29 |
| G10 | 쓰기 활성화 | shadow 모드에서 match/work 계획을 검토한 뒤 GitHub 쓰기(Issue 생성·댓글·PR)를 켬 | `ISSUE_INTAKE_ENABLED=true`, write 플래그 | shadow 모드 출력(생성할 Issue·댓글 계획) | STATUS.md의 G10 완료 시각·승인 범위 기록 | W22~W26, W11 |
| G11 | 일정 | V4-CP0~CP5 목표 KST, 코드 동결·평가 시작·내부 제출 시각 결정 | 각 시각 | — | STATUS.md 체크포인트 표의 목표 KST | 전체 |
| G12 | 메일 채널 (선택) | 메일이 필수인지 결정. 필수면 SMTP 서버·팀 소유 수신자 확정 | 선택 여부 / 비밀: `SMTP_CREDENTIAL`, `LINEMEDIC_OPS_RECIPIENT` | 결정 전에는 `github_comment`만 | SMTP 선택 시 서버 접수 receipt 1회 (사람 inbox 도착과 구분) | W26 |

## 3. 에이전트가 절대 하지 않는 것 (게이트 대신)

- 봇 계정·GitHub App·토큰 생성, 브랜치 보호 규칙 변경, 조직 설정 변경.
- PR 승인·머지, `make approve-release` 실행(사람의 행동이며 무개입 지표에 포함하지 않는다).
- 주최 측 문의 발송, 신청서 제출, 영상 게시.
- NVIDIA·GitHub 약관 동의, 결제, 크레딧 신청.
- 대화에 붙여 넣은 비밀값을 파일에 저장하는 일. 비밀은 사람이 `.env`에 직접 넣도록 안내한다.

## 4. 게이트가 끝내 열리지 않을 때

| 게이트 | 대응 |
|---|---|
| G2 없음 | Issue·알림·PR 기능은 fake로 `UNIT_TESTED`까지. 발표·README에 "GitHub 실연동 미검증" 표시 |
| G3·G4 없음 | 실제 agent 경로 없음. 사람 제안(`manual_integration`) 통합까지만 보이고 agent E2E를 주장하지 않음 |
| G5 없음 | local 모드만. "sandbox 통합 미완료"로 공개, local 결과와 프로브를 구분 |
| G6 없음 | R1~R5 `UNCONFIRMED` 유지. 필수 조건 불충족은 제출 리스크로 남김 |
| G7·G8 없음 | PR까지만. 업무 복구(RESOLVED) 주장 없음 |
