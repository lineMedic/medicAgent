# 01. 기술 스택과 기본값

> 원본: [spec 02 아키텍처](../spec/docs/02-architecture.md), [spec 11 Runbook](../spec/docs/11-runbook.md), [spec 12 NVIDIA 조건](../spec/docs/12-nvidia-requirements.md). 결정 근거는 [DECISIONS.md](../DECISIONS.md) D42~D62(D61은 D62로 대체).

## 1. 구현 스택 (에이전트가 바로 사용)

| 영역 | 선택 | 결정 |
|---|---|---|
| 언어 | Python 3.12 이상 (실제 버전은 host manifest에 기록) | D43 |
| 패키지 | `pyproject.toml`, `.venv`, `pip install -e ".[dev]"`, `requirements.lock` | D43 |
| HTTP API | FastAPI + uvicorn, pydantic v2 strict / extra=forbid | D44 |
| DB | stdlib `sqlite3`, WAL, `BEGIN IMMEDIATE`, versioned SQL migration | D45 |
| 검색 | SQLite FTS5 (`unicode61`), 없으면 `keyword_fallback` | D54 |
| GitHub | `httpx` REST 직접 호출 + `FakeGitHub` | D46 |
| 컨테이너 | `docker` CLI를 고정 argv subprocess로 호출 + fake | D47 |
| CLI | `python -m linemedic.cli` (argparse) + Makefile | D48 |
| 화면 | `python -m linemedic.dashboard`, Jinja2 autoescape, 127.0.0.1, DB 읽기 전용 | D49 |
| 설정 | `config/linemedic.toml`(stdlib `tomllib` + pydantic strict) + env(비밀). OpenShell 정책만 YAML | D52, D60 |
| 테스트 | pytest, 마커로 docker/live 분리 | D55 |
| 린트 | ruff | D56 |

### 허용 의존성 목록

런타임: `fastapi`, `uvicorn`, `pydantic`(v2), `httpx`, `jinja2`. 설정 파일은 stdlib `tomllib`로 읽으므로 `pyyaml`을 넣지 않는다(D60).
개발: `pytest`, `ruff`.
agent runtime 관련 패키지(예: NAT)는 G4 결정 후 그 한 가지만 추가한다.

그 밖의 의존성은 DECISIONS.md에 이유를 적은 뒤 추가한다. ORM, 작업 큐, 메시지 브로커, vector DB, LangChain류 프레임워크는 추가하지 않는다.

## 2. NVIDIA 스택 (제품이 "사용"한다고 말할 수 있는 조건)

| 기술 | LineMedic에서의 역할 | "사용"이라고 적을 최소 증거 | 상태 |
|---|---|---|---|
| Nemotron | runtime 에이전트의 조사·패치·제안 생성 | 모델 ID, provider request, tool trace, 원본 제안 | G3 전 미확인 |
| NemoClaw / OpenClaw | 우선 후보 runtime | 실제 session·도구·로컬 파일/테스트·제안 왕복 | G4 전 미정 |
| NeMo Agent Toolkit (NAT) | OpenClaw가 V4-CP0에서 안 되면 **단일 대안** | 선택한 버전·설정·해당 run의 trace | G4 전 미정 |
| OpenShell | 에이전트 실행 경계(sandbox) | effective policy·sandbox identity·허용/금지 대조·거절 로그 | G5 전 미확인 |
| NeMo Guardrails | R2가 필수로 확인될 때만 한 가지 보호 기능 | 실제 배포 형태·설정·정상/공격 시험 | 사용 안 함 (기본) |
| NIM 온프레미스 | 후속 설계 | 실제 배포 전까지 "계획" | P1 |
| Embedding / Retriever | P1 | — | 사용 안 함 |

규칙:
- runtime은 **하나만** 구현한다. 설치만 해 두고 두 개를 다 썼다고 적지 않는다([spec 12 §4](../spec/docs/12-nvidia-requirements.md)).
- "NAT를 썼으니 R2 충족", "SKILL.md가 있으니 R1 충족"처럼 대회 인정을 스스로 판정하지 않는다. R1~R5는 G6에서 주최 측 답변으로만 바꾼다.
- 원안의 모델명 `nvidia/nemotron-3.5-lightning-30b-a3b`는 실측 전 후보명이다. `NVIDIA_MODEL_ID` 값은 N01에서 tool calling 왕복을 확인한 모델로 넣는다.
- SQLite lexical 검색을 "NVIDIA embedding" 또는 "vector RAG"라고 쓰지 않는다.

## 3. 환경 변수

[spec 11 §2](../spec/docs/11-runbook.md)의 이름을 그대로 쓴다. `.env.example`에는 값 없이 이름만 둔다. agent·runner·MES에는 필요한 최소 변수만 넘기고 env 파일 전체를 mount하지 않는다.

| 변수 | 쓰는 프로세스 | 비밀 |
|---|---|---|
| `LINE_MEDIC_ENV`, `AGENT_MODE`(`local`/`sandbox`), `AGENT_RUNTIME` | control plane | 아니오 |
| `NVIDIA_MODEL_ID`, `NVIDIA_BASE_URL` | agent runtime | 아니오 |
| `NVIDIA_API_KEY` | agent runtime(추론 전용) | 예 |
| `GITHUB_REPOSITORY`, `GITHUB_REPOSITORY_ID` | control plane | 아니오 |
| `GITHUB_BROKER_CREDENTIAL` | control plane(broker·notifier·issue_sync) | 예 |
| `GITHUB_SETUP_CREDENTIAL` | trusted setup 스크립트(baseline 브랜치 생성) | 예 |
| `ISSUE_INTAKE_ENABLED`, `ISSUE_POLL_SECONDS`, `ISSUE_TRUSTED_AUTHOR_IDS`, `ROUTING_SCOPE` | control plane | 아니오 |
| `NOTIFICATION_ROUTE_ID` | control plane | 아니오 |
| `LINEMEDIC_OPS_RECIPIENT`, `SMTP_CREDENTIAL` | notifier(SMTP 선택 시만) | 예 |
| `MEMORY_MODE`(`cold_start`/`memory_assisted`), `MEMORY_SNAPSHOT_PATH`, `CASE_SEARCH_ENGINE` | control plane | 아니오 |
| `DEMO_HOST_ID` | 전체 manifest | 아니오 |
| `CONTROL_AGENT_TOKEN` | agent tool client(run·attempt 범위) | 예 |
| `CONTROL_OPERATOR_TOKEN` | CLI(호스트 전용) | 예 |
| `BASELINE_COMMIT`, `RUNNER_IMAGE_ID`, `MES_BASE_IMAGE_ID` | control plane | 아니오 |
| `RUNS_DIR` | control plane | 아니오 |

`CONTROL_AGENT_TOKEN`은 실제로는 attempt마다 host가 발급하고 attempt 종료 시 폐기한다. env 값은 로컬 개발용 기본값일 뿐이다.

## 4. 호스트 전제

- 데모 호스트 1대에서 모든 평가를 실행한다(G1). 호스트 manifest(OS·kernel·arch·Python·Docker·runtime·OpenShell 버전)를 모든 run에 연결한다(FR-16).
- OpenShell README는 macOS·WSL 2·Linux 호스트와 Docker/Podman/MicroVM 런타임을 적고 있다([spec 14 W10](../spec/docs/14-decisions-sources.md#w10)). 정확한 조건은 N09에서 실제 설치로 확인한다.
- GPU·클라우드 크레딧이 지급됐다고 가정하지 않는다. 예선 추론은 확인된 NVIDIA cloud endpoint다(오프라인 제품이 아님).
