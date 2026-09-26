# 구현 결정 기록 (D42~)

> 원본 결정 D01~D41은 [spec 14](spec/docs/14-decisions-sources.md)에 있다. 이 파일은 spec이 비워 둔 기술 선택과 구현 중 생긴 결정을 D42부터 이어서 기록한다.
> 전체 결정 색인과 상태는 [ADR.md](ADR.md)에 있다. 새 결정을 추가하거나 대체하면 같은 변경에서 ADR.md 색인도 고친다.
> 형식: **결정 / 이유 / 영향 문서 / 필요한 재시험**. 결정을 바꾸면 기존 행을 지우지 말고 새 번호로 "Dxx를 대체"라고 적는다.
> 아래 D42~D58은 문서 작성 시점의 **기본값**이다. 게이트 결과(G1 호스트 등)와 충돌하면 새 결정으로 바꾼다.

## 1. 저장소·코드 배치

| ID | 결정 | 이유 | 영향 문서 | 재시험 |
|---|---|---|---|---|
| D42 | `lineMedic/`를 하나의 git 저장소로 쓴다(저장소 이름 표기는 D63 반영). Control Plane은 `linemedic/`, 데모 대상 저장소의 시드는 `l3-mes-api-seed/`(일반 파일)에 둔다. 원격 `l3-mes-api`의 git 이력(버그 base 커밋)은 `scripts/seed_demo_repo.py`가 시드에서 만들어 G2 이후 push한다 | git 안에 git을 두지 않고, 버그 base를 매 run 재현 가능하게 만들기 위해 | docs/02, tasks/B00·W03·W04 | W04 시드 재생성 테스트 |
| D43 | Python **3.12 이상**. 실제 버전은 호스트 manifest에 기록한다. 의존성은 `pyproject.toml` + `python -m venv .venv` + `pip install -e ".[dev]"`, 잠금 파일은 `requirements.lock`(`pip freeze`). `uv`가 있으면 써도 되지만 lock 파일 형식은 같게 유지한다 | spec 11 §1이 lockfile을 요구하고, 호스트에 uv가 없을 수 있음 | docs/01, tasks/B00 | `make setup` 새 venv 설치 |
| D44 | HTTP 서버는 FastAPI + uvicorn. 요청 모델은 pydantic v2 `ConfigDict(extra="forbid", strict=True)`. 변경 요청은 원시 body를 직접 읽어 크기 제한 → 중복 key 거부(`json.loads(object_pairs_hook=...)`) → 모델 검증 순서로 처리한다 | spec 03 §1의 "알 수 없는 필드·중복 key·잘못된 타입 거부" | docs/04, tasks/W06·W09 | T-AUTH-03, T-V4-01 |
| D45 | DB는 stdlib `sqlite3`(ORM 없음). 연결 시 `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`, `isolation_level=None`에 명시적 `BEGIN IMMEDIATE`. 스키마는 `linemedic/control_plane/migrations/NNNN_*.sql` + `schema_migrations` 테이블. `0001_init.sql` = spec 04 §5 DDL 원문, `0002_case_search_fts5.sql` = FTS5(지원 시) | spec 04 §7의 짧은 트랜잭션·CAS, fresh 개발이지만 이후 변경을 versioned로 관리 | docs/03, tasks/W06·W27 | DDL 제약 테스트 20건 |
| D46 | GitHub 연동은 `httpx` 동기 클라이언트로 REST를 직접 호출한다. `GitHubPort` 프로토콜과 테스트용 `FakeGitHub`(메모리)를 둔다. `X-GitHub-Api-Version` 값은 N11 스파이크에서 확정해 config에 고정한다. 봇(broker) credential과 setup credential을 별도 env로 둔다 | 외부 호출을 fake로 대체해 대부분의 카드를 게이트 없이 진행 | docs/01·02, tasks/W11·W22~W26 | T-ISS-*, T-NOT-* |
| D47 | Docker는 `docker` CLI를 `subprocess.run([...], shell=False, timeout=...)`로 호출한다(SDK 미사용). `DockerPort` + fake를 둔다 | 고정 argv 원칙(spec 06 §3), 의존성 최소화 | docs/01, tasks/W10·W12 | T-REPRO-*, `make test-docker` |
| D48 | 운영 CLI는 `python -m linemedic.cli <subcommand>`(argparse). Makefile 타깃 이름은 spec 11 §3을 따른다. `/ops/*`에 대응하는 명령은 CLI가 operator token으로 HTTP 호출한다. 시나리오 주입·reset·run-new 같은 demo 전용 명령은 공개 endpoint 없이 host 함수를 직접 호출한다 | spec 03 §5 "시나리오 주입·reset은 호스트 demo 전용 CLI" | docs/04·08, tasks/W11·W19·W25 | CLI 인자 검증 테스트 |
| D49 | 대시보드는 별도 프로세스 `python -m linemedic.dashboard`가 `127.0.0.1`에만 bind하고, SQLite를 읽기 전용(`mode=ro` URI)으로 연다. Jinja2 autoescape로 서버 렌더링하며 쓰기 route와 JS 토큰 저장이 없다 | spec 07 §3 "UI에 operator token 하드코딩·localStorage 금지" | docs/02, tasks/W18 | T-UI-01 |
| D50 | run_id 형식은 `r-YYYYMMDD-HHMMSS-xxxx`(소문자·숫자·하이픈, 정규식 `^[a-z0-9-]+$`). 엔티티 ID는 서버가 `접두사-` + 대문자 16진 12자로 발급한다(`INC-`, `WORK-`, `ATT-`, `PROP-`, `EXE-`, `EV-`, `NOT-`, `VER-`, `CASE-`, `RET-`, `MEM-`, `REQ-`) | `baseline/*` 보호 패턴이 `/`와 불일치(spec 11 §6), 전역 PK 충돌 방지 | docs/03, tasks/W06·W19 | ID 형식 테스트 |
| D51 | 시각은 `linemedic/common/clock.py`의 `Clock`(utc_now, monotonic, sleep)을 주입해 쓴다. 저장 형식은 `YYYY-MM-DDTHH:MM:SS.ffffffZ` | verifier t0~t60·polling·알림 60초 대기를 테스트에서 가짜 시계로 검증 | docs/07, tasks/W05·W23·W26 | T-VERIFY-*, T-NOT-01 |
| D52 | 비밀이 아닌 설정은 `config/linemedic.yaml`(repo·service catalog, issue_intake, notifications routes, equipment·manual catalog, runner limits, budgets)에 둔다. 비밀과 run별 값은 env([spec 11 §2](spec/docs/11-runbook.md) 이름). config hash = 병합한 비밀 아닌 설정의 canonical JSON SHA-256이고 run manifest에 기록한다 | spec 01 §5 "수치 변경 시 configuration hash를 바꾸고 평가를 구분" | docs/01·07, tasks/B00 | config hash 테스트 |
| D53 | runtime 에이전트는 `AgentAdapter` 프로토콜 `run_agent(run_id, incident_id, work_id, attempt_id, deadline, workspace_ref, context_ref) -> AttemptResult` 뒤에 둔다. G4 전에는 테스트용 `ScriptedAdapter`만 둔다(origin=`manual_integration`). 실제 adapter(OpenClaw 또는 NAT)는 G4 결정 후 한 가지만 구현한다 | spec 05 §1, 12 §4 "런타임 하나만 구현" | docs/02, tasks/W13·W14 | adapter 계약 테스트 |
| D54 | 사례 검색 엔진 이름은 `sqlite_fts5`(tokenize=`unicode61`) 또는 FTS5가 없을 때 `keyword_fallback`. 질의는 `[0-9A-Za-z_가-힣]+` 토큰을 최대 16개 뽑아 각각 큰따옴표로 감싸 OR로 잇고, 파라미터 바인딩으로 넘긴다 | spec 17 §4 "raw 입력을 연산자로 해석하지 않음, fallback을 FTS5라 부르지 않음" | docs/03·05, tasks/W27 | T-MEM-04 |
| D55 | 테스트는 `linemedic/tests/unit`, `integration`(실제 SQLite + fake 외부), `live`로 나눈다. pytest 마커 `docker`, `live_github`, `live_model`, `live_sandbox`, `live_smtp`. `make test`는 마커가 붙은 테스트를 제외한다 | mock 성공과 live 성공을 분리(spec 09 §6) | docs/09, tasks/B00 | — |
| D56 | 린트·포맷은 ruff 하나만 쓴다. 타입 힌트는 쓰되 타입 검사기는 필수로 두지 않는다 | 도구 최소화 | docs/01 | `make lint` |
| D57 | MES 로그는 stdout JSON Lines: `ts, level, service, event, request_id, lot_id, path, status, error_type, error_field, top_frame, stack`. detector는 demo에서 `docker logs --follow`, 테스트에서 파일·메모리 스트림을 읽는다 | fingerprint 입력(spec 15 §3.1)과 verifier observer(spec 08 §5)가 같은 스트림을 쓰기 위해 | docs/05, tasks/W04·W07 | W07 fingerprint 테스트 |
| D58 | 정비 초안의 안내 문구는 `linemedic/policies/manual_templates.yaml`(팀이 만든 가상 매뉴얼의 승인 템플릿)에서 브로커가 채운다 | spec 03 §3.B "자유 생성 절차 금지" | tasks/W08·W09 | S2-lite 테스트 |
| D59 | S2-lite 사건은 service catalog의 `vision-inspection`(line L3, 설비 L3-CAM-1~3, **코드 경로 없음**, Issue는 같은 `l3-mes-api` repo로 추적)으로 만든다. fingerprint의 `endpoint_or_metric`은 `metric:<equipment_id>:<metric>` 형식이다. 배포 기록은 `audit_events(event_type='DEPLOY_OBSERVED')`에 남기고 `get_deploys`가 이것과 DEPLOY execution을 함께 읽는다 | spec이 "한 서비스 매핑"과 설비 사건의 service 값을 함께 정하지 않았고, 배포 기록 테이블이 DDL에 없음 | docs/03·05, tasks/W07·W08 | W08 테스트 |

## 2. 게이트에 따라 확정할 결정 (현재 미정)

| 항목 | 결정 주체·게이트 | 기본값 / 확정 전 처리 |
|---|---|---|
| 데모 호스트(OS·arch) | 사람, G1 | 미정. 로컬 개발은 가능하나 평가 run은 확정 호스트에서만 |
| agent runtime (OpenClaw/NemoClaw vs NAT) | 사람, G4 (V4-CP0 스파이크 N01·N02) | 미정. `ScriptedAdapter`까지만 구현 |
| NVIDIA 모델 ID·endpoint | 사람, G3 (N01) | 원안 후보 `nvidia/nemotron-3.5-lightning-30b-a3b`는 **실측 전 후보명**. 확인 전 코드에 고정하지 않음 |
| OpenShell 정책 schema·버전 | 사람, G5 (N03·N04·N09) | 미확인 YAML을 실행 설정으로 넣지 않음 |
| GitHub API 버전 헤더 | 에이전트, N11 (G2 후) | config 키만 만들고 값은 확인 후 기입 |
| 알림 채널 | 사람, G12 | `github_comment` 1개. SMTP는 G12에서 선택할 때만 구현 |
| NeMo Guardrails 사용 여부 | 사람, G6 (R2 답변) | 사용하지 않음. R2가 필수로 확인되면 새 결정 추가 |

## 3. 구현 중 추가 결정

현재 개발 진행 관리에는 **D62**를 적용한다. D61은 대체된 결정으로, 당시의 경로·명령은 이력으로만 보존한다.

| ID | 결정 | 이유 | 영향 문서 | 재시험 |
|---|---|---|---|---|
| D60 | **D52·D58의 파일 형식을 대체.** 비밀이 아닌 설정·정책·계약·평가 기대값 파일은 TOML로 쓰고 stdlib `tomllib`로 읽는다: `config/linemedic.toml`, `linemedic/policies/broker_policy.toml`, `linemedic/policies/manual_templates.toml`, `linemedic/contracts/defect-summary-v1.toml`, `linemedic/eval/scenario_expectations.toml`. `pyyaml`은 허용 의존성에서 뺀다. 로더는 바이트 크기 상한 → `tomllib.loads` → pydantic v2 `strict`·`extra="forbid"` 모델 순으로 검증하고 모르는 키를 거부한다. TOML에는 null이 없으므로 미확정 값은 **키를 생략**한다(빈 문자열·0 금지). TOML 날짜·시각 타입은 쓰지 않고 시각은 `...Z` RFC3339 문자열로만 받는다. `config_hash`(병합 설정의 canonical JSON SHA-256)와 `contract_sha256`(파일 바이트 SHA-256) 정의는 그대로다. OpenShell 정책은 외부 schema라 YAML 그대로 두고 LineMedic은 바이트 해시·전달만 한다. spec 08 §4·15 §2·16 §1의 YAML 예시는 같은 필드·값의 TOML로 옮긴다 | stdlib만으로 읽어 의존성을 줄이고, YAML의 암묵 형변환(`yes`/`on`/8진수)과 파서별 객체 생성 위험을 없앤다. 코드가 없는 지금 바꾸는 비용이 가장 작다. 대가: 깊은 중첩 catalog는 TOML이 덜 읽기 쉽다 | docs/01·02·07, tasks/B00·W05·W08·W10·W20·W22, SPEC.md, ARCHITECTURE.md | B00 config 로더 테스트(모르는 키·local datetime 거부), W05 계약 필드 동일성 테스트 |
| D61 | 카드·게이트·체크포인트·대회 조건의 **진행 상태 원본은 `flow/state.toml`**, 카드 구조(선행·게이트·목표 상태)는 `flow/cards.toml`, 완료 보고는 `flow/reports.md`다. `STATUS.md`는 `python3 flow/flowctl.py render`가 만드는 **생성 파일**이며 직접 고치지 않는다. `flowctl check`가 상태 enum·목표 상한·선행·게이트·증거 경로·시각 형식·비밀 패턴·STATUS.md 동기화를 검사한다. `flow/`는 개발 과정 전용이며 runtime 에이전트의 prompt·workspace·도구 응답에 넣지 않는다. 검사기는 **기록**의 일관성을 검사할 뿐 실제 외부 쓰기·배포를 막지 않는다(그 강제는 제품 코드 몫) | 손으로 고치는 markdown 표로는 선행 미충족 착수, 증거 없는 `LIVE_VERIFIED`, 닫힌 게이트의 live 완료, 비밀값 기입을 기계적으로 막을 수 없다 | AGENTS.md, CLAUDE.md, README.md, docs/02·08·09·10·11·12, tasks/README·B00·W00·W01·W29 | `python3 -m unittest discover -s flow`, `python3 flow/flowctl.py check` |
| D62 | **D61을 대체.** 사용자 요청에 따라 `flowctl`과 `flow/`의 상태·카드 구조·보고 파일 및 전용 테스트를 삭제한다. 개발 진행 상태와 완료 보고의 유일한 원본은 직접 편집하는 `STATUS.md`다. 작업 범위·선행·게이트·수용 기준은 `tasks/` 카드에서 확인한다. 착수·완료·차단·재개 때 현재 작업, 다음 작업, 작업표, 게이트, 체크포인트와 완료 보고를 함께 갱신한다. 별도 진행 관리 명령은 만들지 않는다. 제품의 상태 enum·테스트·승인·원본 증거·runtime 비노출 규칙은 유지한다 | 단일 에이전트 순차 작업에서 카드와 TOML의 중복 및 진행 도구 유지 비용을 없앤다. 대가: 상태 형식·선행·증거 경로는 docs/11의 기록 점검으로 확인해야 한다 | AGENTS.md, CLAUDE.md, STATUS.md, README.md, ADR.md, ARCHITECTURE.md, docs/01·02·08·09·10·11·12, tasks/README·B00·W00·W01·W29 | 삭제 경로·현행 문서의 명령 참조·상대 링크 검사, 기존 상태·게이트·체크포인트·대회 조건 및 spec 원문 보존 확인 |
| D63 | **D42의 저장소 이름 표기를 대체.** 프로젝트 저장소와 문서의 루트 경로 표기를 `lineMedic`으로 통일한다. 사용자 요청에 따른 GitHub 생성 목표는 조직 `lineMedic`, 공개 저장소 `lineMedic/lineMedic`이다. D42의 코드 배치와 Python 패키지 `linemedic`, 데모 대상 저장소 `l3-mes-api`는 유지한다. 원격 생성·push 완료 여부는 STATUS.md에 실제 결과로 기록하며, 이 명명 결정으로 G2·G10을 완료 처리하지 않는다 | 사용자가 지정한 프로젝트 이름을 문서와 원격 저장소에 일관되게 적용 | README.md, CLAUDE.md, ARCHITECTURE.md, docs/02·12, ADR.md | 이전 저장소 이름 잔여 검색, 상대 링크 대상 검사 |
| D64 | **D63의 GitHub 게시 대상을 대체.** 사용자 지정 원격 저장소는 `https://github.com/lineMedic/medicAgent`다. 로컬 루트 `lineMedic/`, 제품 이름과 Python 패키지 `linemedic`, 데모 대상 저장소 `l3-mes-api`는 유지한다. 게시 결과는 STATUS.md에 실제 결과로 기록하며, 개발 문서 게시 권한을 제품 runtime의 G2·G10 완료로 간주하지 않는다 | 사용자가 생성한 실제 원격 저장소에 현재 프로젝트를 게시 | ADR.md, STATUS.md | 원격 저장소 메타데이터·쓰기 권한 확인, push 후 로컬·원격 SHA 비교 |
| (D65~) | | | | |
