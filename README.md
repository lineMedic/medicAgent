# LineMedic v4 (lineMedic)

LineMedic은 합성 공장 환경(가상 L3 라인)에서 서버 로그나 등록된 GitHub Issue를 받아 한 Issue·한 작업으로 연결하고, 작업 시작을 먼저 알린 뒤 코드 수정 제안(사람 리뷰·배포 승인·업무 검증을 거침) 또는 설비 점검 요청 초안을 만들고, 결과를 검증 수준별 사례로 남겨 다음 조사에 참고하는 에이전트 시스템이다. Korea Agentic AI Hackathon(패스트캠퍼스 × NVIDIA) 온라인 예선용이다.

> **현재 상태 (2026-09-28):** 로그 감지 → GitHub Issue → 시작 댓글 → 검사한 봇 PR → 사람 리뷰·머지 → 사람 배포 승인 → 업무 검증 PASS까지의 경로를 **실제 GitHub·Docker로 한 번 끝까지 실행했다**(S1 live run 2). 이 run의 수정 제안은 **사람이 미리 쓴 것**(`manual_integration`)이다. **Nemotron 에이전트가 제안을 만드는 runtime adapter와 OpenShell sandbox는 아직 없다.** Nemotron은 도구 호출 왕복 시험(N01)으로만 실제 호출했다. 카드별 상태·증거·대기 이유는 [STATUS.md](STATUS.md)가 유일한 원본이다.

모든 환경은 합성이다. 회사·고객 데이터, 실제 PLC·설비, 실제 수신자를 쓰지 않는다.

## 구현 범위와 확인 수준

"확인"은 [docs/11 §1](docs/11-definition-of-done.md)의 `UNIT_TESTED`(fake 또는 로컬 Docker)다. live는 실제 외부 경로(GitHub·NVIDIA API)로 한 확인이며 증거는 `evidence/`에 있다.

| 영역 | 구현한 것 | 확인 | live (2026-09-28) |
|---|---|---|---|
| 사건 감지 (W07·W08) | MES 로그 → signature·fingerprint → 사건, 카메라 합성 지표 → 설비 사건 | 실제 MES 컨테이너 로그(Docker) | S1 live run에서 실제 로그로 사건 생성 |
| 업무 검증 (W05) | 업무 계약 판정, 잘못된 200(S1b) 거절 | 실제 컨테이너(Docker) | S1b 재확인, run 2 배포 뒤 PASS |
| Issue 연결 (W22~W24) | GitHub 포트, 60초 주기 polling, 로그 → 기존 Issue 연결/새 Issue 생성, 애매하면 멈춤 | FakeGitHub·MockTransport | **LIVE_VERIFIED**: 조회·생성·댓글, 새 Issue 감지, 신규·기존·모호 연결 |
| 작업·알림 (W25·W26) | work 선점·승인·재시도·취소, 시작 댓글 receipt 뒤에만 실행, 결과 댓글·차단 보고 | fake | **LIVE_VERIFIED**(W26): 시작·차단·결과 댓글 등록, 결과 불명 뒤 재발송 없이 조회 |
| 제안·브로커 (W09·W10) | 제안 schema, 증거 범위·민감 값 검사, 패치 정책, 격리 runner R0~R2 | 실제 runner 컨테이너(Docker) | S1 live run에서 검사 13개 PASS |
| 봇 PR (W11) | candidate push·PR 생성·결과 불명 기록·조정 | fake | **LIVE_VERIFIED**: PR head = 검사한 candidate, 봇이 아닌 리뷰어 승인 |
| 배포 (W12) | 사람이 승인한 exact SHA만 빌드·기동, identity chain, 업무 검증 연결 | 실제 배포(Docker) | **LIVE_VERIFIED**: 사람 머지 SHA → image → 검증 PASS, identity chain 기록 |
| 사람 제안 통합 (W13) | 사람 제안 경로 전체와 run-record 연결 | fake E2E | **LIVE_VERIFIED**: Issue·시작 댓글·PR·merge SHA·image·검증·결과 댓글 연결 |
| 사례 기억 (W27·W28) | case note(성공·실패·미확인·차단 구분), 고정 snapshot, SQLite FTS5 lexical 검색, attempt 문맥의 초기 검색 | fake(실제 SQLite) | 없음(실제 모델 문맥은 G3~G5 뒤) |
| 에이전트 준비 (W14) | 규칙 묶음·도구 client·서버 측 도구 예산·trace·workspace 금지 자료 검사 | fake | runtime adapter 없음(G4) |
| sandbox 준비 (W15·W16) | sandbox port·기록·`sandbox_verified` 판정, S2-lite fake 경로 | fake | OpenShell 정책·구현 없음(G5) |
| 보안 시험 (W17) | S3-B 결정론 표, S3-C 판정·호스트 대조, 공격 memo·mock sink | 결정론 시험 | S3-A·S3-C sandbox 쪽은 G3·G5 |
| 화면·run·평가 (W18~W20·W29) | 읽기 전용 대시보드, run-new·export·reset, 평가 하네스·집계, 문서 정합성 점검 | fake | run-new·baseline 브랜치·export는 live로 사용. 평가 run은 없음 |

## live 재현 결과 (2026-09-28, 개발 Mac)

데모 호스트가 아닌 개발 Mac(macOS arm64, Docker 29.8.0, Python 3.14.7)에서 `AGENT_MODE=local`(sandbox 없음)로 실행했다. GitHub 쓰기는 등록된 합성 데모 repo `lineMedic/l3-mes-api`에만 했다.

| 시험 | 결과 | 증거 |
|---|---|---|
| N01 Nemotron 도구 호출 | PASS — `nvidia/nemotron-3.5-lightning-30b-a3b`(NVIDIA API cloud endpoint)가 도구 선택 → 결과 재입력 → 구조화 제안. 가짜 도구 1개로 한 형식 시험이다 | [evidence/spikes/N01-model-tool-call.md](evidence/spikes/N01-model-tool-call.md) |
| GitHub 조회·Issue·댓글 (N11) | PASS | [evidence/N11-github-smoke.md](evidence/N11-github-smoke.md) |
| 로그 → Issue 신규·기존·모호 (S4) | PASS — 새 Issue 생성, 같은 문제는 같은 번호 재사용, 후보가 둘이면 만들지 않고 멈춤 | [evidence/S4-issue-live.md](evidence/S4-issue-live.md) |
| 새 Issue 감지 (S5-new) | PASS — 승인된 작성자의 새 Issue를 60초 주기 polling이 찾아 승인 대기 작업 생성 | [evidence/S5-new-issue-detect.md](evidence/S5-new-issue-detect.md) |
| 알림 경로 (N12) | PASS — 시작 댓글 receipt가 attempt 시작보다 먼저, 결과 불명 뒤 재발송 0 | [evidence/N12-notification-route.md](evidence/N12-notification-route.md) |
| S1 live run 1 | 봇 PR 생성까지. 그 PR은 리뷰 없이 머지돼 배포 승인 대상이 아니었다 | [evidence/W13-s1-live-run1.md](evidence/W13-s1-live-run1.md) |
| **S1 live run 2** | **실제 로그 → Issue #9 → 시작 댓글 → 검사 → 봇 PR #10 → 팀원 리뷰·머지 → 사람 배포 승인 → exact image 배포 → 업무 검증 PASS → 결과 댓글.** 제안은 사람이 미리 쓴 것이다(`manual_integration`) | [evidence/W13-s1-live-run2.md](evidence/W13-s1-live-run2.md) |

알려 둘 한계:

- 봇 계정을 따로 만들지 못해 봇 credential이 팀원 개인 계정이다([DECISIONS.md D94](DECISIONS.md)). `baseline/*` 보호 규칙과 squash만 허용 설정이 없어 GitHub 설정 점검은 FAIL이다([evidence/github-setup-check.json](evidence/github-setup-check.json)).
- run 2의 work 승인은 사용자 지시로 코딩 에이전트가 실행했고, 머지는 squash가 아닌 merge commit이었다(서버는 merge tree = 검사한 candidate tree를 확인해 통과시켰다).
- 한 번의 run이다. 지정한 업무 계약과 관찰 범위를 통과했다는 뜻이며 성공률로 일반화하지 않는다.

## 아직 없는 것

- Nemotron 에이전트 runtime adapter: runtime 하나를 고르는 G4 결정 뒤에 만든다. 지금 에이전트 자리에는 사람이 미리 쓴 제안(`manual_integration`)만 돈다
- OpenShell 정책 파일과 sandbox 구현: 설치 버전의 schema를 확인하기 전에는 두지 않는다(G5)
- S3-A용 시나리오 옵션(공격 memo를 S1 요청에 넣기)
- 메일 알림 경로: G12에서 필요하다고 정할 때만
- hardening(H03~H07)
- 평가 run: 아직 한 번도 돌리지 않았다([evidence/eval-summary.md](evidence/eval-summary.md)는 전부 `NOT_RUN`)

## 재현: API 키·리뷰어 없이 할 수 있는 단계

```text
$ make setup
$ make test                   # unit + integration(fake)
1785 passed, 21 deselected
$ make lint
All checks passed!
$ make test-docker            # Docker 필요
13 passed
$ make doctor                 # .env가 비어 있으면 NOT_CONFIGURED 항목과 종료 코드 1
```

- 의존성: `requirements.lock`(해시 고정), 설정: `config/linemedic.toml`(기본은 GitHub 쓰기 꺼짐 shadow 모드), 환경 변수 이름: `.env.example`(값 없음)
- 데모 대상 repo 시드: `l3-mes-api-seed/`에서 결정적으로 만든 커밋 `19045b62f292dedff24529cab505e6d86a91ed8c`

`make test`의 fake E2E는 `make start`와 같은 조립으로 S1(사람 제안 → 게이트 → 봇 PR → 사람 머지·배포 승인 흉내 → 업무 검증 PASS), S2-lite(정비 요청 초안, 코드 변경 0건), S5-new(로그 없는 새 Issue)를 지난다.

## live 재현 절차 (키·데모 repo가 있을 때)

`.env`에 [`.env.example`](.env.example)의 변수를 채운 뒤, live 동안만 `config/linemedic.toml`의 `github.write_enabled = true`로 켠다.

```text
$ make mes-image && make runner-image          # 출력 image ID를 .env에 넣는다
$ python -m linemedic.scripts.spikes.n01_model_tool_call --output evidence/spikes/N01-model-tool-call.json
$ python -m linemedic.scripts.seed_demo_repo --push --confirm-write --record evidence/W03-seed-push.json
$ make run-new CREATE_BASELINE=1
$ make start RUN_ID=<run>                      # 별도 터미널
$ make scenario-s1 RUN_ID=<run>                # 사건 → Issue → 승인 대기 work
$ make approve-work WORK_ID=<work> EXPECTED_VERSION=0
#   → 시작 댓글 → 검사 → 봇 PR. 봇이 아닌 리뷰어가 승인·머지(G7)
$ make approve-release RUN_ID=... PR_NUMBER=... MERGE_SHA=... ...   # 사람이 직접(G8)
$ make export-run RUN_ID=<run>                 # run-record
```

## 외부 서비스가 필요한 단계 (사람 게이트)

절차는 [docs/10-human-gates.md](docs/10-human-gates.md)에 있다.

| 게이트 | 할 일 | 상태 (2026-09-28) |
|---|---|---|
| G1 | 데모 호스트 1대 확정 | 미정(개발 Mac에서 실행) |
| G2 | GitHub repo·봇·리뷰어·보호 규칙·작성자 ID | 부분: repo·credential·작성자 ID만, 봇 분리·보호 없음(D94) |
| G3 | NVIDIA API 키, Nemotron 모델 | 완료(N01 PASS) |
| G4 | runtime 선택(OpenClaw/NemoClaw 또는 NAT) | 미정 |
| G5 | OpenShell 설치 | 미설치 |
| G6 | 주최 측 참가 조건(R1~R5) 문의 | 답변 없음(UNCONFIRMED) |
| G7 · G8 | run마다 PR 리뷰·머지, 배포 승인 | S1 live run 2에서 완료 |
| G10 | GitHub 쓰기 활성화 | 데모 repo에만 허락 |

비밀값(토큰·키·비밀번호)은 `.env`에 직접 넣고 대화나 문서에 붙여 넣지 않는다.

## NVIDIA 기술

- **Nemotron (실제 호출함)**: `nvidia/nemotron-3.5-lightning-30b-a3b`, NVIDIA API cloud endpoint(`integrate.api.nvidia.com/v1`, OpenAI 호환). N01 도구 호출 왕복 시험에서만 호출했다. LineMedic 작업 경로의 수정 제안은 아직 이 모델이 만들지 않는다
- runtime(OpenClaw/NemoClaw 또는 NAT, G4): 아직 고르지 않았다
- OpenShell sandbox(G5): 설치·정책 전이다. 온프레미스 NIM 추론은 후속 계획이다

## 평가·시험 결과

| 기록 | 내용 | 한계 |
|---|---|---|
| [evidence/W13-s1-live-run2.md](evidence/W13-s1-live-run2.md) | S1 live run 2의 run-record 연결과 identity chain | 사람 제안(`manual_integration`), 1회, local 모드 |
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
