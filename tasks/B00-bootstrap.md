# B00 — 저장소 골격과 개발 기반

| 항목 | 값 |
|---|---|
| 등급 | core 준비 작업 (원본 W에 없음) |
| 자율성 | A |
| 선행 | — |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 02 §6](../spec/docs/02-architecture.md), [spec 11 §1·§2·§3](../spec/docs/11-runbook.md), [spec 01 §5](../spec/docs/01-requirements.md) (FR-16 host manifest) |
| 참조 | [docs/01](../docs/01-tech-stack.md), [docs/02](../docs/02-repo-layout.md), [docs/07](../docs/07-constants.md), DECISIONS D42~D52·D55·D56 |

## 목표

`make setup && make test && make lint && make doctor`가 새 clone에서 동작하고, 이후 카드가 공통으로 쓰는 clock·ID·canonical JSON·config 로더가 테스트된 상태로 존재한다.

## 만들 파일

- `pyproject.toml` — 패키지 `linemedic`, `requires-python = ">=3.12"`, 의존성(docs/01 §1의 허용 목록), `[project.optional-dependencies] dev = ["pytest", "ruff"]`, ruff 설정, pytest 설정(`testpaths = ["linemedic/tests"]`, 마커 `docker`, `live_github`, `live_model`, `live_sandbox`, `live_smtp` 등록)
- `Makefile` — `setup`, `lock`, `test`, `test-docker`, `test-live`, `lint`, `fmt`, `doctor`. 개발 진행 기록은 STATUS.md를 직접 편집한다(D62).
- `.gitignore` — `.venv/`, `.env`, `runs/`, `*.db`, `*.db-wal`, `*.db-shm`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`
- `.env.example` — [spec 11 §2](../spec/docs/11-runbook.md)의 모든 변수 이름, 값 없음. 비밀 변수 옆에 `# secret` 주석
- `config/linemedic.toml` (D60) — [docs/07](../docs/07-constants.md)의 모든 값을 기본값으로. 섹션: `services`(mes-api), `repository`(id·full_name은 미확정이므로 키 생략), `issue_intake`, `notifications`(routes: github-issue-primary), `agent`, `tools`, `proposal`, `patch`, `runner`, `verifier`, `memory`, `detector`
- `linemedic/__init__.py`, `linemedic/cli.py` — argparse, subcommand `version`, `doctor`, `host-manifest`
- `linemedic/common/clock.py` — `Clock` 프로토콜(`utc_now()`, `monotonic()`, `sleep()`), `SystemClock`, 테스트용 `FakeClock`(수동 advance), `to_rfc3339()`(`...ffffffZ`)
- `linemedic/common/ids.py` — `new_run_id(clock)` → `r-YYYYMMDD-HHMMSS-xxxx`, `new_id(prefix)` → `PREFIX-` + 대문자 16진 12자, 형식 검증 함수
- `linemedic/common/canonical_json.py` — `loads_strict(bytes, max_bytes)`(중복 key·NaN·비UTF-8 거부), `canonical_dumps(obj)`(키 정렬, 구분자 `,` `:`, `ensure_ascii=False`), `sha256_hex(obj)`
- `linemedic/common/config.py` — TOML(stdlib `tomllib`, 바이트 크기 상한) → pydantic v2 `strict`·`extra="forbid"` 모델 검증 → env 병합, 비밀 변수는 별도 객체(repr에 값 비노출), `config_hash()` = 비밀 제외 canonical JSON의 SHA-256
- `linemedic/scripts/__init__.py`, `linemedic/scripts/host_manifest.py` — OS·kernel·arch·CPU·메모리·디스크·Python·SQLite(+FTS5 여부)·git·Docker 버전, OpenShell·runtime은 발견 못 하면 `null`. 환경변수 전체를 덤프하지 않는다
- `linemedic/scripts/doctor.py` — 점검 항목 레지스트리. 각 항목은 `OK / MISSING / FAIL / NOT_CONFIGURED`. 필수 항목이 OK가 아니면 종료 코드 1. 이후 카드가 항목을 추가한다(github, model, runtime, openshell, fixtures, runner image)
- `linemedic/tests/conftest.py` — `fake_clock`, `tmp_runs_dir` fixture
- `linemedic/tests/unit/test_common.py`
- `evidence/README.md` — 이 폴더에는 실제 실행 결과만 둔다는 규칙, 파일 이름 규칙(`N01-model-tool-call.md`, `host-manifest.json` 등)

## 구현 단계

1. `git init`(아직 없으면)을 하고 `.gitignore`부터 만든다. `spec/`·`docs/`·`tasks/`는 이미 있으니 그대로 커밋한다.
2. `pyproject.toml`과 `Makefile`을 만든다. `make setup`은 `python3 -m venv .venv` → `.venv/bin/pip install -e ".[dev]"`. `requirements.lock`이 있으면 `-r requirements.lock`으로 먼저 설치하고, `make lock`은 `pip freeze --exclude-editable > requirements.lock`.
3. `make test`는 `.venv/bin/pytest -m "not docker and not live_github and not live_model and not live_sandbox and not live_smtp"`. `make test-docker`는 `-m docker`, `make test-live`는 `-m "live_github or live_model or live_sandbox or live_smtp"`.
4. common 모듈 4개와 테스트를 작성한다.
5. host manifest·doctor·cli를 작성한다. doctor의 초기 항목: python 버전, sqlite FTS5, docker CLI 존재(없으면 MISSING, docker 필요 카드에서만 필수), config 로드, `.env`의 필수 변수 존재 여부(값은 출력하지 않음).
6. `make lock`으로 lock 파일을 만든다.
7. STATUS.md의 B00 상태·증거·완료 보고와 현재 작업·다음 작업을 갱신하고 [docs/11 §1](../docs/11-definition-of-done.md)의 기록 점검을 마친 뒤 커밋한다.

## 수용 기준

- `loads_strict(b'{"a":1,"a":2}')`가 오류를 낸다. 128 KiB 초과 입력을 거부한다.
- `canonical_dumps`가 키 순서와 공백에 무관하게 같은 문자열을 만든다. 같은 객체의 `sha256_hex`가 안정적이다.
- `new_run_id` 결과가 `^r-\d{8}-\d{6}-[0-9a-f]{4}$`이고 `/`가 없다. `new_id("INC")`가 `^INC-[0-9A-F]{12}$`다.
- `FakeClock.advance(10)` 후 `monotonic()`과 `utc_now()`가 정확히 10초 늘어난다.
- `config_hash()`가 비밀 env 값을 바꿔도 변하지 않고, `agent.deadline_seconds`를 바꾸면 변한다.
- config 로더가 모르는 키, 잘못된 타입, TOML 날짜·시각 타입(시각은 `...Z` 문자열만 허용), 크기 상한 초과를 거부한다. 생략한 선택 키는 `None`으로 읽힌다.
- `make doctor`가 설정이 없는 항목을 `NOT_CONFIGURED`로 보이고 종료 코드 1을 낸다. 어떤 출력에도 비밀 값이 없다.
- `make lint`가 통과한다.
- STATUS.md의 B00 상태·증거·완료 보고가 실제 검사 결과와 일치한다.

## 완료 증거

STATUS.md의 B00 증거·완료 보고에 `make setup`·`make test`·`make lint` 결과 줄, Python·SQLite 버전, 커밋 해시를 적는다. `make host-manifest` 출력은 W00에서 데모 호스트로 다시 만든다.

## 금지·함정

- 허용 목록 밖 의존성을 넣지 않는다(ORM, 작업 큐, agent 프레임워크 등).
- `.env` 실제 파일을 만들거나 커밋하지 않는다. 사람이 만든다.
- doctor가 실패를 경고로만 출력하고 "ready"라고 표시하지 않게 한다.
