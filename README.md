# LineMedic v4 (lineMedic) — 구현 준비 중

> **현재 상태: 코드 없음.** 이 저장소에는 설계 명세와 코딩 에이전트용 작업 문서만 있다. 모든 작업은 `NOT_CHECKED`이며, 아래에 적힌 기능은 **구현 목표**이지 동작하는 기능이 아니다. 진행 상황은 [STATUS.md](STATUS.md)를 본다.

LineMedic은 합성 공장 환경(가상 L3 라인)에서 서버 로그나 등록된 GitHub Issue를 받아 중복 없이 한 Issue에 연결하고, 작업 시작을 먼저 알린 뒤 코드 수정 제안(사람 리뷰·배포 승인·업무 검증 포함) 또는 설비 점검 요청 초안을 만들고, 결과를 검증 수준별 사례로 남겨 다음 조사에 재사용하는 에이전트다. Korea Agentic AI Hackathon(패스트캠퍼스 × NVIDIA) 온라인 예선용이다.

## 구성

| 경로 | 내용 |
|---|---|
| [spec/](spec/README.md) | 원본 설계 명세 `LineMedic_Development_Pack_v4` 26개 (수정하지 않음) |
| [PRD.md](PRD.md) · [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) | 제품 요구·기술 계약·구조의 개요. 정본이 아니며 spec 절로 링크 |
| [ADR.md](ADR.md) | 결정 D01~ 통합 색인 (상세는 spec 14와 DECISIONS.md) |
| [AGENTS.md](AGENTS.md) · [CLAUDE.md](CLAUDE.md) | 코딩 에이전트 진입점: 역할, 작업 루프, 절대 규칙, 게이트 프로토콜 |
| [STATUS.md](STATUS.md) | 직접 편집하는 개발 진행 상태·게이트·체크포인트·완료 보고의 유일한 원본 |
| [DECISIONS.md](DECISIONS.md) | spec이 비워 둔 기술 선택(D42~) |
| [docs/](docs/00-mission-scope.md) | 에이전트용 통합 참조 00~13 |
| [tasks/](tasks/README.md) | 작업 카드 B00, W00~W29, H03~H07 |

## 코딩 에이전트로 시작하기

`lineMedic/`에서 Claude Code 같은 코딩 에이전트를 열고 [docs/12-kickoff-prompts.md](docs/12-kickoff-prompts.md)의 "처음 시작" 프롬프트를 붙여 넣는다. 에이전트는 먼저 사람이 준비할 게이트 목록을 보여 주고, [tasks/B00](tasks/B00-bootstrap.md)부터 순서대로 구현한다.

## 사람이 해야 하는 일

코딩 에이전트가 할 수 없거나 해서는 안 되는 일이다. 자세한 절차는 [docs/10-human-gates.md](docs/10-human-gates.md)에 있다.

| 게이트 | 할 일 | 필요한 시점 |
|---|---|---|
| G1 | 데모 호스트 1대 확정 | 평가 전 |
| G2 | GitHub 조직·`l3-mes-api` repo·봇 계정(또는 App)·리뷰어·`baseline/*` 보호·squash 설정, 자동 처리 작성자 숫자 ID | Issue·PR live 검증 전 |
| G3 | NVIDIA API 키, Nemotron 모델 후보 | 실제 agent 전 |
| G4 | runtime 선택(OpenClaw/NemoClaw 또는 NAT) | 스파이크 후 |
| G5 | OpenShell 설치 | sandbox 실행 전 |
| G6 | 주최 측에 참가 조건(R1~R5) 문의 | 가능한 빨리 |
| G7 · G8 | run마다 PR 리뷰·머지, 배포 승인 명령 실행 | S1 run마다 |
| G9 | memory 비교에 쓸 사례 snapshot 선택 | S7 전 |
| G10 | GitHub 쓰기 활성화 | live 검증 전 |
| G11 | 체크포인트 KST 시각 | 즉시 |
| G12 | 메일 채널 필요 여부 | 알림 구현 전 |

비밀값(토큰·키·비밀번호)은 `.env`에 직접 넣고 대화나 문서에 붙여 넣지 않는다.

## 원본 패키지

- 출처: `LineMedic_Development_Pack_v4.zip` (문서 26개). `spec/`은 zip의 `LineMedic_Development_Pack_v4/` 폴더 내용을 그대로 옮긴 것이다.
- 원본의 요약·재구성은 `docs/`와 `tasks/`에 있고, 충돌 시 원본이 우선한다([AGENTS.md §8](AGENTS.md)).
