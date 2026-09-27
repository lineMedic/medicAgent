# 02. 저장소 구조와 모듈 책임

> 원본: [spec 02 §2·§6](../spec/docs/02-architecture.md). 배치 결정은 [DECISIONS.md](../DECISIONS.md) D42.
> 각 경로 옆 `[Wxx]`는 그 파일을 처음 만드는 카드다. 카드는 자기 범위 밖 파일을 만들지 않는다.

## 1. 최상위

```text
lineMedic/                    # git 저장소 루트 (문서 + 코드)
├── AGENTS.md CLAUDE.md README.md STATUS.md DECISIONS.md  # 진행 상태·완료 보고는 STATUS.md 직접 편집 (D62)
├── PRD.md SPEC.md ARCHITECTURE.md ADR.md   # 개요·결정 색인 (정본 아님)
├── spec/ docs/ tasks/        # 문서 (spec/은 수정 금지)
├── pyproject.toml            # [B00] 패키지·ruff·pytest 설정, 마커 정의
├── requirements.lock         # [B00] pip freeze 결과
├── Makefile                  # [B00] setup/test/lint/doctor + 카드별 운영 타깃
├── .env.example .gitignore   # [B00] 이름만, runs/·.env·*.db 무시
├── config/linemedic.toml     # [B00 골격, 카드별 항목 추가] 비밀 아닌 catalog (D60)
├── evidence/                 # [B00] 스파이크·live 실행·주최 측 답변의 실제 기록
├── linemedic/                # Control Plane 패키지 (아래 §2)
└── l3-mes-api-seed/          # 데모 대상 repo 시드 (아래 §3)
```

`RUNS_DIR`(기본 `./runs/`, git 제외)는 run별 manifest·원본 증거·workspace·trusted mirror를 담는다.

## 2. `linemedic/` 패키지

```text
linemedic/
├── __init__.py
├── cli.py                         # [B00] argparse 진입점. 카드마다 subcommand 추가
├── common/
│   ├── clock.py                   # [B00] Clock 주입 (D51)
│   ├── ids.py                     # [B00] run_id·엔티티 ID 발급 (D50)
│   ├── canonical_json.py          # [B00] canonical JSON·SHA-256·중복 key 거부 파서 (D44)
│   ├── config.py                  # [B00] toml+env 로드·strict 검증, config hash (D52·D60)
│   └── sanitize.py                # [W06] 비밀 패턴·멘션·URL·HTML 정제
├── integrations/
│   ├── docker.py                  # [W05] DockerPort, CliDocker, FakeDocker (D47)
│   ├── github.py                  # [W22] GitHubPort, HttpGitHub, FakeGitHub (D46)
│   └── git_push.py                # [W11] candidate push(GitPusher·FakePusher, force·hook 없음)
├── control_plane/
│   ├── app.py                     # [W06] FastAPI app factory, 라우터 등록, body 파서
│   ├── auth.py                    # [W06] token → principal(agent/operator), scope 검사
│   ├── errors.py                  # [W06] 오류 외피·코드·HTTP 매핑
│   ├── store.py                   # [W06] 연결·BEGIN IMMEDIATE·CAS·bounded SQLITE_BUSY retry
│   ├── migrations/0001_init.sql   # [W06] spec 04 §5 DDL 원문 + schema_migrations
│   ├── migrations/0002_case_search_fts5.sql  # [W27] FTS5 (지원 시)
│   ├── state.py                   # [W06] incident·work 전이 표, 주체 권한, 결합 전이
│   ├── idempotency.py             # [W06] api_requests, body hash 409
│   ├── audit.py                   # [W06] audit_events INSERT, [W19] JSONL export
│   ├── detector.py                # [W07] 로그 → signature → problem_fingerprint → incident
│   ├── evidence.py                # [W07] evidence 저장·정제·조회
│   ├── tools_api.py               # [W07] /tools/* 라우터 (W08·W09·W27·W28에서 도구 추가)
│   ├── ops_api.py                 # [W06] /ops/* 라우터 (카드별 endpoint 추가)
│   ├── runs.py                    # [W06] demo_runs, run manifest, [W19] run-new/archive/reset
│   ├── catalog.py                 # [W22] repo·service·route·equipment catalog 로더
│   ├── main.py                    # [W13] make start 진입점: API·supervisor·poll·outbox·broker 루프
│   ├── security_probe.py          # [W17] S3-C 대조 절차·판정
│   ├── issue_sync.py              # [W23] polling·mirror·checkpoint·snapshot hash
│   ├── issue_router.py            # [W24] 매칭 1~5·CREATE_ISSUE·binding
│   ├── supervisor.py              # [W25] work claim·approve·retry·cancel, [W26] start gate, [W28] attempt 시작
│   ├── broker/
│   │   ├── proposals.py           # [W09] 제안 pydantic 모델(union 3종)
│   │   ├── intake.py              # [W09] B01~B06, 202 접수·백그라운드 검사
│   │   ├── work_order.py          # [W09] 정비 요청 초안
│   │   ├── patch_policy.py        # [W10] 경로·크기·파일 형태·보호 hash
│   │   ├── candidate.py           # [W10] disposable checkout, git apply, candidate commit
│   │   ├── runner.py              # [W10] R0/R1/R2, junitxml 판정
│   │   ├── patch_gate.py          # [W10] 정책 → 기준 base → candidate → R0/R1/R2 순서·거절 사유
│   │   ├── github_pr.py           # [W11] 브랜치 push·PR 생성·재사용·본문 템플릿
│   │   └── reconcile.py           # [W11] execution UNKNOWN 재조회
│   ├── notifications/
│   │   ├── outbox.py              # [W26] enqueue·상태 전이·재시도
│   │   ├── templates.py           # [W26] 이벤트 7종 본문, blocker report
│   │   ├── github_comment.py      # [W26] send/reconcile adapter
│   │   └── smtp.py                # [W26, G12 선택 시만]
│   ├── memory/
│   │   ├── builder.py             # [W27] event → case note
│   │   ├── search.py              # [W27] exact + FTS5/fallback, 필터 후 top_k
│   │   ├── snapshot.py            # [W27] 불변 manifest
│   │   └── projection.py          # [W27] history evidence projection
│   ├── release.py                 # [W12] exact SHA 릴리스
│   ├── observer.py                # [W05] 로그 스트림 연속성·container/image 불변 관찰
│   └── verifier.py                # [W05] 업무 계약 판정
├── agent/                         # 제품 runtime 에이전트 쪽 (개발 지침을 넣지 않는다)
│   ├── adapter.py                 # [W13] AgentAdapter, ScriptedAdapter (D53)
│   ├── tools_client.py            # [W14] /tools HTTP client (scope token)
│   ├── runtime_<openclaw|nat>.py  # [W14, G4 후] 선택한 하나만
│   ├── trace.py                   # [W14] model ID·prompt hash·tool trace·token
│   ├── prompts/system.md          # [W14] spec 05 §5 템플릿
│   └── skills/{code-exception,vision-quality-drop}/SKILL.md  # [W14] spec 05 §6
├── policies/
│   ├── broker_policy.toml         # [W10] 허용 경로·상한
│   ├── manual_templates.toml      # [W08] 가상 매뉴얼 승인 템플릿 (D58)
│   └── openshell/                 # [W15, G5 후] 실제 schema로 작성한 정책·hash
├── contracts/
│   ├── defect-summary-v1.toml     # [W05] 업무 계약 (보호)
│   └── api/                       # [W09] 제안·응답 JSON Schema (pydantic에서 생성)
├── runner/
│   ├── runner.Dockerfile          # [W10] 고정 runner image 레시피
│   ├── mes.Dockerfile             # [W04] 신뢰된 MES 빌드 레시피 (repo Dockerfile 미사용)
│   └── pytest-protected.ini       # [W10] repo 설정·plugin 자동 로드 차단 profile
├── factory_sim/
│   ├── fixtures/                  # [W04] 공개 로트 입력(118, 101)
│   ├── scenarios.py               # [W04] S1 주입, [W08] S2-lite 주입
│   ├── camera_metrics.py          # [W08] 합성 카메라 지표
│   ├── manuals/                   # [W08] 가상 매뉴얼 절
│   ├── negative/                  # [W05] S1b 잘못된 200 구현 (trusted harness 전용)
│   ├── attacks/s3a_memo.txt       # [W17] S3-A 공격 문장 (canary만)
│   └── sinks/mock_ot_sink.py      # [W17] S3-C 팀 소유 수신 서버
├── eval/                          # runtime 에이전트 비노출
│   ├── holdout-defects-v1.json    # [W04] holdout 입력+기대값 (verifier만 읽음)
│   ├── scenario_expectations.toml # [W20] 시나리오별 기대 category/action
│   ├── harness.py                 # [W20] make evaluate
│   ├── manual_proposals/          # [W13] 사람이 작성한 제안 (origin=manual_integration)
│   └── snapshots/                 # [W27] memory snapshot manifest
├── dashboard/
│   ├── __main__.py                # [W18] 127.0.0.1 읽기 전용 서버 (D49)
│   ├── readmodel.py               # [W18] 화면·/ops/dashboard 공용 읽기 모델
│   └── templates/index.html       # [W18]
├── scripts/
│   ├── host_manifest.py           # [B00]
│   ├── doctor.py                  # [B00, 카드별 점검 추가]
│   ├── seed_demo_repo.py          # [W04] 시드 → 버그 base git 이력, [W03] push(G2 후)
│   ├── github_setup_check.py      # [W03] 보호 규칙·권한 점검 (N07·N11)
│   ├── github_protection_probe.py # [W03] 보호 규칙 쓰기 시험 (G10·허락 후)
│   └── spikes/                    # [W02] n01_model_tool_call.py 등
└── tests/
    ├── conftest.py                # [B00] 임시 DB, FakeClock, FakeGitHub, FakeDocker fixture
    ├── unit/  integration/  live/ # 배치는 docs/09-test-matrix.md
    ├── helpers/demo_states.py     # [W05] 테스트·demo 전용 상태 준비 helper (운영 API 아님)
    ├── helpers/runner.py          # [W10] docker 옵션→inspect 흉내, 로컬 pytest runner (테스트 전용)
    ├── helpers/pr_world.py        # [W11] 제안→게이트→봇 PR·조정 시험 world (테스트 전용)
    └── fixtures/                  # [W10] 테스트용 patch·junit XML (에이전트 workspace에 넣지 않음)
```

## 3. `l3-mes-api-seed/` (데모 대상 repo)

PR 대상 저장소의 내용이다. runtime 에이전트는 이 코드의 사본(`/sandbox/work/repo/`)만 본다. 그래서 **정답 패치·기대값·평가 자료를 두지 않는다.**

```text
l3-mes-api-seed/
├── README.md                      # 서비스 설명과 업무 규칙(누락 검사자는 '미지정', 전체 건수 유지)
├── app/
│   ├── __init__.py
│   ├── main.py                    # [W04] FastAPI: GET /defects/summary?lot_id=, GET /healthz
│   ├── defects.py                 # [W04] 집계 함수. 버그 base는 row['inspector_id'] 직접 접근
│   ├── data.py                    # [W04] MES_DATA_DIR의 로트 JSON 로더
│   └── logging_json.py            # [W04] JSON Lines 로그 (D57)
├── data/lots/                     # [W04] L3-0927-118.json, L3-0927-101.json (공개 입력)
└── tests/
    ├── regression/test_summary_regression.py  # [W04] 보호 회귀 (정상 로트·빈 배열)
    └── repro/.gitkeep             # 에이전트가 새 재현 테스트 1개를 추가하는 위치
```

- 브로커가 허용하는 변경은 `app/defects.py` 수정과 `tests/repro/test_*.py` 신규 1개뿐이다([spec 06 §2](../spec/docs/06-broker-runner.md)).
- MES 이미지는 repo 안의 파일로 빌드하지 않고 `linemedic/runner/mes.Dockerfile`(신뢰 레시피)로 허용 파일만 복사해 빌드한다([spec 07 §5](../spec/docs/07-security.md)).
- holdout 로트 입력(L3-HOLDOUT-201)은 배포 시 MES 데이터 디렉터리에 읽기 전용으로 mount할 수 있다. 기대값은 `linemedic/eval/`에만 있다.

## 4. 모듈 책임 요약

| 모듈 | 입력 | 출력 | 소유하지 않는 것 |
|---|---|---|---|
| detector | 합성 서비스 로그·검사 이상 | 안정 signature·정제 증거 | Issue 자연어 동일성 확정 |
| issue_sync / issue_router | 등록 repo 목록·로그 후보 | Issue mirror·binding·work | 모델 조사·패치 |
| supervisor | 승인된 work·start receipt | 단일 attempt·예산·sandbox 작업 | 임의 운영 변경·자동 머지 |
| notifier | durable outbox | provider receipt/실패/UNKNOWN | 작업 성공 판정·수신자 선택 |
| agent | Issue·증거·허용 history·현재 코드 | 제안 1개 | GitHub/메일 쓰기·상태 변경 |
| broker / runner | 제안과 고정 source | 검증된 PR 또는 초안/차단 결과 | Issue closed를 복구로 번역 |
| release / verifier | 사람 승인 SHA·업무 계약 | exact image 배포·PASS/FAIL/INCONCLUSIVE | 자동 머지·repair loop |
| case builder / retriever | 기록된 관찰·검사·사유 | 검증 수준별 사례·조회 근거 | 자기보고 성공 승격·재학습 |
| UI / CLI | 저장된 상태 | 승인·현황·재조회 요청 | force-resolve |

DB, router, supervisor, notifier, memory builder는 모두 **같은 프로세스의 Python 모듈**이다. 공개 webhook listener, 메시지 브로커, vector DB, 멀티 에이전트 스케줄러는 만들지 않는다.

## 5. runtime 에이전트 workspace (sandbox 안)

```text
/sandbox/work/repo/     # 고정 base의 l3-mes-api 사본. 허용 경로만 제출 가능
/sandbox/work/output/   # 산출물·로컬 테스트 결과 (비신뢰)
/agent_rules/           # host가 관리하는 prompt·skill·도구 설명 (읽기 전용)
/tmp/                   # 제한된 임시 공간
```

넣지 않는 것: LineMedic 저장소 전체, `runs/`, operator·GitHub token, Docker socket, 보호 fixture 기대값, verifier 코드, cold_start에서 이전 정답 패치([spec 05 §3](../spec/docs/05-agent-spec.md)).
