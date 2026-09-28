# LineMedic v4 (lineMedic)

LineMedic은 합성 공장 환경(가상 L3 라인)에서 서버 로그나 등록된 GitHub Issue를 받아 한 Issue·한 작업으로 연결하고, 작업 시작을 먼저 알린 뒤 코드 수정 제안(사람 리뷰·배포 승인·업무 검증을 거침) 또는 설비 점검 요청 초안을 만들고, 결과를 검증 수준별 사례로 남겨 다음 조사에 참고하는 에이전트 시스템이다. Korea Agentic AI Hackathon(패스트캠퍼스 × NVIDIA) 온라인 예선용이다.

> **현재 상태 (2026-09-28): 구현 중, live 검증 전.** 아래 기능은 코드와 자동 시험이 있고 로컬 fake·Docker 시험으로 확인했다. 실제 GitHub·NVIDIA 모델·OpenShell sandbox로 확인한 기능은 아직 없다(목표가 `LIVE_VERIFIED`인 카드는 모두 사람 게이트 대기). 카드별 상태·증거·대기 이유는 [STATUS.md](STATUS.md)가 유일한 원본이다.

모든 환경은 합성이다. 회사·고객 데이터, 실제 PLC·설비, 실제 수신자를 쓰지 않는다.

## 구현 범위와 확인 수준

"확인"은 [docs/11 §1](docs/11-definition-of-done.md)의 `UNIT_TESTED`(fake 또는 로컬 Docker)다. live는 실제 외부 경로로 한 확인이다.

| 영역 | 구현한 것 | 확인 | live |
|---|---|---|---|
| 사건 감지 (W07·W08) | MES 로그 → signature·fingerprint → 사건, 카메라 합성 지표 → 설비 사건 | 실제 MES 컨테이너 로그(Docker) | 필요 없음 |
| 업무 검증 (W05) | 업무 계약 판정, 잘못된 200(S1b) 거절 | 실제 컨테이너(Docker) | 필요 없음 |
| Issue 연결 (W22~W24) | GitHub 포트, 60초 주기 polling, 로그 → 기존 Issue 연결/새 Issue 생성, 애매하면 멈춤 | FakeGitHub·MockTransport | G2·G10 |
| 작업·알림 (W25·W26) | work 선점·승인·재시도·취소, 시작 댓글 receipt 뒤에만 실행, 결과 댓글·차단 보고 | fake | G2·G10 |
| 제안·브로커 (W09·W10) | 제안 schema, 증거 범위·민감 값 검사, 패치 정책, 격리 runner R0~R2 | 실제 runner 컨테이너(Docker) | 필요 없음 |
| 봇 PR (W11) | candidate push·PR 생성·결과 불명 기록·조정 | fake | G2·G10 |
| 배포 (W12) | 사람이 승인한 exact SHA만 빌드·기동, identity chain, 업무 검증 연결 | 실제 배포 1회(Docker) | 사람 G7·G8 |
| 사례 기억 (W27·W28) | case note(성공·실패·미확인·차단 구분), 고정 snapshot, SQLite FTS5 lexical 검색, attempt 문맥의 초기 검색 | fake(실제 SQLite) | G3~G5 |
| 에이전트 준비 (W13·W14) | 사람 제안 경로 전체(fake E2E), 규칙 묶음·도구 client·서버 측 도구 예산·trace·workspace 금지 자료 검사 | fake | runtime adapter·실제 모델은 G3·G4 |
| sandbox 준비 (W15·W16) | sandbox port·기록·`sandbox_verified` 판정, S2-lite fake 경로 | fake | OpenShell 정책·구현은 G5 |
| 보안 시험 (W17) | S3-B 결정론 표, S3-C 판정·호스트 대조, 공격 memo·mock sink | 결정론 시험 | S3-A는 G3·G5, S3-C sandbox 쪽은 G5 |
| 화면·run·평가 (W18~W20·W29) | 읽기 전용 대시보드, run-new·export·reset, 평가 하네스·집계, 문서 정합성 점검 | fake | 평가 실행은 전체 게이트 |

## 아직 없는 것

- 실제 모델 호출과 runtime adapter: runtime 하나를 고르는 G4 결정 뒤에 만든다. 지금 에이전트 자리에는 사람이 미리 쓴 제안(`manual_integration`)만 돈다
- OpenShell 정책 파일과 sandbox 구현: 설치 버전의 schema를 확인하기 전에는 두지 않는다(G5)
- S3-A용 시나리오 옵션(공격 memo를 S1 요청에 넣기)
- 메일 알림 경로: G12에서 필요하다고 정할 때만
- hardening(H03~H07): core 전체 경로가 실제로 통과한 뒤에만 착수한다
- 평가 run: 아직 한 번도 돌리지 않았다([evidence/eval-summary.md](evidence/eval-summary.md)는 전부 `NOT_RUN`)

## 재현: API 키·리뷰어 없이 할 수 있는 단계

아래는 로컬 개발 Mac에서 실제로 실행한 명령과 출력이다(데모 호스트가 아니다).

- 환경: macOS 26.6.2 arm64, Python 3.12.2, SQLite 3.46.0(FTS5 있음), Docker 28.1.1, git 2.55.0
- 의존성: `requirements.lock`(해시 고정), 설정: `config/linemedic.toml`, 환경 변수 이름: `.env.example`(값 없음)
- MES image: `linemedic/runner/mes.Dockerfile`(신뢰 레시피)로 `make mes-image` → `sha256:425755201561179ca1cf1ee1eccf03ef2559a8d556a9ce0b36b4a32968d5bce0`, base `python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`
- 데모 대상 repo 시드: `l3-mes-api-seed/`에서 결정적으로 만든 커밋 `19045b62f292dedff24529cab505e6d86a91ed8c`

```text
$ make setup
$ make test
1761 passed, 21 deselected
$ make lint
All checks passed!
187 files already formatted
$ make test-docker            # Docker 필요
13 passed
$ make test-live              # 게이트 전: 실행이 아니라 전부 skip
8 skipped
$ make doctor                 # 종료 코드 1
NOT_CONFIGURED  env          [필수] 미설정 변수: LINE_MEDIC_ENV, AGENT_MODE, ...
NOT_CONFIGURED  github       [필수] 미설정 변수: GITHUB_REPOSITORY, GITHUB_REPOSITORY_ID, GITHUB_BROKER_CREDENTIAL
NOT_CONFIGURED  runner_image [필수] 미설정 변수: RUNNER_IMAGE_ID (make runner-image의 runner_image_id)
NOT_CONFIGURED  openshell    [필수] 미설정 변수: AGENT_MODE (local 또는 sandbox)
doctor: 필수 항목 4개가 OK 아님 (env, github, runner_image, openshell)
$ make evaluate SUITE=s1      # 종료 코드 2, run을 만들지 않는다
"status": "NOT_CONFIGURED"    # 없는 조건 6개: sandbox 모드, runtime·모델, sandbox 구현, 데모 repo, 쓰기 허락, 코드 동결
```

`make test`의 fake E2E는 `make start`와 같은 조립으로 S1(사람 제안 → 게이트 → 봇 PR → 사람 머지·배포 승인 흉내 → 업무 검증 PASS), S2-lite(정비 요청 초안, 코드 변경 0건), S5-new(로그 없는 새 Issue)를 지난다. 이 경로의 제안은 사람이 미리 쓴 것이라 에이전트 성과가 아니다.

## 외부 서비스가 필요한 단계 (사람 게이트)

코딩 에이전트가 할 수 없거나 해서는 안 되는 일이다. 절차는 [docs/10-human-gates.md](docs/10-human-gates.md)에 있다.

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

## NVIDIA 기술

아직 실제로 호출한 NVIDIA 기술은 없다. 계획은 다음과 같고, 실제로 쓴 뒤에 모델 ID·endpoint·runtime·버전을 여기에 적는다.

- Nemotron 모델(NVIDIA API endpoint, G3): 도구 선택 시험 스크립트(N01)만 있고 실행하지 않았다
- runtime 하나(OpenClaw/NemoClaw 또는 NAT, G4): 아직 고르지 않았다
- OpenShell sandbox(G5): 설치·정책 전이다. 추론을 사내에서 돌리는 구성은 후속 계획이다

## 평가·시험 결과

| 기록 | 내용 | 한계 |
|---|---|---|
| [evidence/eval-summary.md](evidence/eval-summary.md) | 평가 run 집계(분모·origin·조건 집합) | 평가 run 없음, 전부 `NOT_RUN` |
| [evidence/S3-B-broker.md](evidence/S3-B-broker.md) | 브로커 거절 결정론 표 14행 PASS | 테스트 client의 요청 판정이다. 모델 반응·sandbox 대조가 아니다 |
| [evidence/N13-case-search.md](evidence/N13-case-search.md) | SQLite FTS5 한국어·오류 토큰 검색 확인 | 검색 품질은 측정하지 않았다 |
| [evidence/N06-runner-isolation.md](evidence/N06-runner-isolation.md) | runner 컨테이너 격리 확인 | 로컬 개발 Mac에서만 확인했다 |

몇 회의 반복 결과는 작은 시험이다. 성공률 일반화나 다른 방식과의 우열 비교로 쓰지 않는다.

## 문서 안내

| 경로 | 내용 |
|---|---|
| [spec/](spec/README.md) | 원본 설계 명세 `LineMedic_Development_Pack_v4` 26개(수정하지 않음) |
| [PRD.md](PRD.md) · [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) | 제품 요구·기술 계약·구조의 개요(spec 절로 링크) |
| [ADR.md](ADR.md) · [DECISIONS.md](DECISIONS.md) | 결정 색인과 spec이 비워 둔 기술 선택(D42~) |
| [AGENTS.md](AGENTS.md) · [CLAUDE.md](CLAUDE.md) | 코딩 에이전트 진입점과 절대 규칙 |
| [STATUS.md](STATUS.md) | 진행 상태·게이트·완료 보고의 유일한 원본 |
| [docs/](docs/00-mission-scope.md) · [tasks/](tasks/README.md) | 통합 참조 00~14, 작업 카드 B00·W00~W29·H03~H07 |
| [docs/14-submission-draft.md](docs/14-submission-draft.md) | 신청서·영상 구성 초안과 제출 전 점검 결과(사람이 확정·제출) |

원본 패키지는 `LineMedic_Development_Pack_v4.zip`(문서 26개)이고, 요약·재구성과 원본이 충돌하면 원본이 우선한다([AGENTS.md §8](AGENTS.md)).
