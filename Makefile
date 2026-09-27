# LineMedic v4 개발 명령 (tasks/B00-bootstrap.md, D43·D55·D56).
# 개발 진행 기록은 STATUS.md를 직접 편집한다(D62). 이 Makefile은 진행 상태를 관리하지 않는다.

PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

MARKED := docker or live_github or live_model or live_sandbox or live_smtp
LIVE := live_github or live_model or live_sandbox or live_smtp

# 버그 base MES 이미지 (신뢰 레시피 linemedic/runner/mes.Dockerfile, W04)
MES_IMAGE ?= linemedic-mes:base
MES_BASE_PYTHON ?= python:3.12-slim
# 패치 검사 runner 이미지 (신뢰 레시피 linemedic/runner/runner.Dockerfile, W10)
RUNNER_IMAGE ?= linemedic-runner:v1

.PHONY: setup lock test test-docker test-live lint fmt doctor host-manifest mes-image runner-image scenario-s1 verify-negative run-new detect-once scenario-s2-lite api-schema issue-sync issue-bind approve-work retry-work cancel-work notification-reconcile reconcile approve-release start stop rebuild-case-index memory-snapshot dashboard export-run reset

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	@if [ -f requirements.lock ]; then $(PIP) install -r requirements.lock; fi
	$(PIP) install -e ".[dev]"

lock:
	$(PIP) freeze --exclude-editable > requirements.lock

# 마커가 붙은 docker·live 테스트는 제외한다(D55).
test:
	$(PYTEST) -m "not ($(MARKED))"

# 선택된 테스트가 하나도 없으면(pytest 종료 코드 5) 실패가 아니라 "해당 테스트 없음"으로 알린다.
test-docker:
	@$(PYTEST) -m docker; status=$$?; \
	if [ $$status -eq 5 ]; then echo "docker 마커 테스트 없음 (NOT_RUN)"; else exit $$status; fi

test-live:
	@$(PYTEST) -m "$(LIVE)"; status=$$?; \
	if [ $$status -eq 5 ]; then echo "live 마커 테스트 없음 (NOT_RUN)"; else exit $$status; fi

lint:
	$(RUFF) check .
	$(RUFF) format --check .

fmt:
	$(RUFF) format .
	$(RUFF) check --fix .

doctor:
	$(PY) -m linemedic.cli doctor

# 명령줄을 출력하지 않는다(@). `make host-manifest > evidence/host-manifest.json`이 순수 JSON이 되게 한다.
host-manifest:
	@$(PY) -m linemedic.cli host-manifest

# 시드(버그 base)로 MES 이미지를 만든다. 빌드 뒤 image ID와 base image digest를 출력해 기록한다.
# base image를 먼저 받아 두어야 로컬에서 digest를 조회할 수 있다(BuildKit은 빌드 중 받은 base를 목록에 남기지 않는다).
mes-image:
	docker pull --quiet $(MES_BASE_PYTHON)
	docker build --build-arg PYTHON_IMAGE=$(MES_BASE_PYTHON) -f linemedic/runner/mes.Dockerfile -t $(MES_IMAGE) l3-mes-api-seed
	@docker image inspect --format 'mes_image_id={{.Id}}' $(MES_IMAGE)
	@docker image inspect --format 'base_repo_digests={{json .RepoDigests}}' $(MES_BASE_PYTHON)

# 패치 검사 runner 이미지 (W10): 출력한 runner_image_id를 .env의 RUNNER_IMAGE_ID에 넣는다(태그가 아니라 ID로 고정).
# 검사할 코드는 넣지 않는다. 보호 pytest 설정(linemedic/runner/pytest-protected.ini)을 image에 넣는다.
runner-image:
	docker pull --quiet $(MES_BASE_PYTHON)
	docker build --build-arg PYTHON_IMAGE=$(MES_BASE_PYTHON) -f linemedic/runner/runner.Dockerfile -t $(RUNNER_IMAGE) linemedic/runner
	@docker image inspect --format 'runner_image_id={{.Id}}' $(RUNNER_IMAGE)
	@docker image inspect --format 'base_repo_digests={{json .RepoDigests}}' $(MES_BASE_PYTHON)

scenario-s1:
	@test -n "$(RUN_ID)" || { echo "사용법: make scenario-s1 RUN_ID=r-YYYYMMDD-HHMMSS-xxxx"; exit 2; }
	$(PY) -m linemedic.cli scenario-s1 --run-id "$(RUN_ID)"

# S1b 거짓 정상 시험 (W05, trusted harness 전용). make run-new가 만든 활성 run에서 S1b 이미지로
# verifier를 돌리고 제어 DB(verifications·incident ESCALATED)와 runs/<RUN_ID>/verifications/에 기록한다.
# FAIL/content_mismatch이고 사건이 ESCALATED일 때만 종료 코드 0.
verify-negative:
	@test -n "$(RUN_ID)" || { echo "사용법: make verify-negative RUN_ID=<make run-new가 만든 활성 run>"; exit 2; }
	$(PY) -m linemedic.cli verify-negative --run-id "$(RUN_ID)" --mes-image "$(MES_IMAGE)"

# 새 run (W06·W19): 제어 DB migration, demo_runs 활성 전환, manifest(config·모델·runtime·정책·prompt·계약 hash,
# memory mode·snapshot, host manifest 경로·SHA-256). CREATE_BASELINE=1이면 먼저 setup credential로
# baseline/<run_id> 브랜치를 BASELINE_COMMIT에 만든다(G2·G10 뒤. 등록 repo ID 확인, 다른 SHA면 옮기지 않고 멈춤).
run-new:
	$(PY) -m linemedic.cli run-new $(if $(wildcard evidence/host-manifest.json),--host-manifest evidence/host-manifest.json,) $(if $(CREATE_BASELINE),--create-baseline,)

# 감지 1회 (W07): run의 S1 MES 컨테이너 로그를 지금까지 한 번 읽어 감지기에 넣는다.
# 같은 fingerprint가 60초 안 3회면 사건 NEW. 상시 감시(docker logs --follow)는 W13의 make start가 한다.
detect-once:
	@test -n "$(RUN_ID)" || { echo "사용법: make detect-once RUN_ID=<make run-new가 만든 활성 run>"; exit 2; }
	$(PY) -m linemedic.cli detect-once --run-id "$(RUN_ID)"

# S2-lite (W08): L3 카메라 합성 지표를 run에 쓰고 설비 이상을 감지한다(이상은 L3-CAM-2).
# RECENT_DEPLOY=1이면 이상 시작 전 mes-api 배포 기록을 더한다(혼동 사례).
scenario-s2-lite:
	@test -n "$(RUN_ID)" || { echo "사용법: make scenario-s2-lite RUN_ID=<활성 run> [RECENT_DEPLOY=1]"; exit 2; }
	$(PY) -m linemedic.cli scenario-s2-lite --run-id "$(RUN_ID)" $(if $(RECENT_DEPLOY),--recent-deploy,)

# 제안·응답 JSON Schema (W09): pydantic 모델에서 linemedic/contracts/api/*.schema.json을 다시 쓴다.
# 파일이 모델과 다르면 make test의 schema 비교 테스트가 실패한다.
api-schema:
	$(PY) -m linemedic.scripts.api_schema

# Issue 조회 1회 (W23): 등록 repo Issue를 mirror·checkpoint에 반영한다. 처음이면 관찰만(backlog 실행 없음).
# issue_intake.enabled=false(G10 전)면 만들 work를 planned로만 보고한다. G2 env가 없으면 NOT_CONFIGURED(종료 코드 2).
issue-sync:
	@test -n "$(RUN_ID)" || { echo "사용법: make issue-sync RUN_ID=<make run-new가 만든 활성 run>"; exit 2; }
	$(PY) -m linemedic.cli issue-sync --run-id "$(RUN_ID)"

# 운영자 Issue 연결 (W24): incident를 등록 repo의 Issue 번호에 명시적으로 연결한다(basis OPERATOR).
# Control API(make start, W13)가 떠 있어야 하고 CONTROL_OPERATOR_TOKEN을 쓴다. 같은 명령은 멱등 재전송이다.
issue-bind:
	@test -n "$(INCIDENT_ID)" -a -n "$(ISSUE_NUMBER)" || { echo "사용법: make issue-bind INCIDENT_ID=INC-... ISSUE_NUMBER=<번호> [EXPECTED_VERSION=] [NOTE=]"; exit 2; }
	$(PY) -m linemedic.cli issue-bind --incident-id "$(INCIDENT_ID)" --issue-number "$(ISSUE_NUMBER)" $(if $(EXPECTED_VERSION),--expected-version "$(EXPECTED_VERSION)",) $(if $(NOTE),--note "$(NOTE)",)

# work 승인·재시도·취소 (W25): Control API(make start)를 operator token으로 부른다. 같은 명령은 멱등 재전송이다.
# approve-work는 기대 version이 필요하고, 기대 snapshot을 생략하면 work를 만든 때의 Issue snapshot으로 승인한다
# (그 뒤 Issue가 바뀌었으면 409 ISSUE_SCOPE_CHANGED: 바뀐 내용을 확인하고 EXPECTED_SNAPSHOT=으로 다시 승인한다).
approve-work:
	@test -n "$(WORK_ID)" -a -n "$(EXPECTED_VERSION)" || { echo "사용법: make approve-work WORK_ID=WORK-... EXPECTED_VERSION=<n> [EXPECTED_SNAPSHOT=] [NOTE=]"; exit 2; }
	$(PY) -m linemedic.cli approve-work --work-id "$(WORK_ID)" --expected-version "$(EXPECTED_VERSION)" $(if $(EXPECTED_SNAPSHOT),--expected-snapshot "$(EXPECTED_SNAPSHOT)",) $(if $(NOTE),--note "$(NOTE)",)

retry-work:
	@test -n "$(WORK_ID)" -a -n "$(REASON)" || { echo "사용법: make retry-work WORK_ID=WORK-... REASON=<blocker 해소 확인 메모> [EXPECTED_VERSION=]"; exit 2; }
	$(PY) -m linemedic.cli retry-work --work-id "$(WORK_ID)" --reason "$(REASON)" $(if $(EXPECTED_VERSION),--expected-version "$(EXPECTED_VERSION)",)

cancel-work:
	@test -n "$(WORK_ID)" || { echo "사용법: make cancel-work WORK_ID=WORK-... [EXPECTED_VERSION=] [NOTE=]"; exit 2; }
	$(PY) -m linemedic.cli cancel-work --work-id "$(WORK_ID)" $(if $(EXPECTED_VERSION),--expected-version "$(EXPECTED_VERSION)",) $(if $(NOTE),--note "$(NOTE)",)

# 알림 조정 (W26): 결과 불명(UNKNOWN) 알림을 bound Issue 댓글 조회로만 확인한다. 다시 보내지 않는다.
# 봇 작성자 + marker + 본문 hash가 모두 맞는 댓글이 정확히 1개일 때만 ACCEPTED로 기록한다.
notification-reconcile:
	@test -n "$(NOTIFICATION_ID)" || { echo "사용법: make notification-reconcile NOTIFICATION_ID=NOT-..."; exit 2; }
	$(PY) -m linemedic.cli notification-reconcile --notification-id "$(NOTIFICATION_ID)"

# 결과 불명 execution 조정 (W11): CREATE_PR(W11)·CREATE_ISSUE(W24)·DEPLOY(W12)를 외부 조회로만 확인한다.
# 새로 만들거나 다시 배포하지 않는다.
# CREATE_PR은 봇 작성·head 브랜치·candidate SHA·base·marker가 모두 맞는 PR 1개만 채택한다(PR_OPENED).
# PR·브랜치가 모두 없음이 확인되면 ESCALATED, 그 밖(충돌·불완전·브랜치만 남음)은 기록만 한다.
# DEPLOY는 이번 image ID·execution 라벨의 MES가 실행 중이면 새 업무 검증을 시작하고(VERIFYING),
# 없거나 다른 것이 실행 중이면 ESCALATED, docker 조회 실패는 기록만 한다.
reconcile:
	@test -n "$(RUN_ID)" -a -n "$(EXECUTION_ID)" || { echo "사용법: make reconcile RUN_ID=<run> EXECUTION_ID=EXE-..."; exit 2; }
	$(PY) -m linemedic.cli reconcile --run-id "$(RUN_ID)" --execution-id "$(EXECUTION_ID)"

# 배포 승인 (W12·G8): 사람이 GitHub에서 리뷰·머지한 PR의 최종 merge SHA를 지정해 배포를 승인한다.
# 에이전트는 이 명령을 실행하지 않는다. spec 11 §5 체크리스트를 보여 주고 터미널에서 approve를 입력해야 보낸다.
# 서버가 merged=true·최종 merge SHA·PR head·리뷰·tree·지금 MES image를 다시 확인하고, 통과하면 exact SHA를
# 재검사·빌드·기동한 뒤 업무 검증을 한다. 진행은 make reconcile·GET /ops/executions/{id}로 본다.
approve-release:
	@test -n "$(RUN_ID)" -a -n "$(INCIDENT_ID)" -a -n "$(WORK_ID)" -a -n "$(PR_NUMBER)" -a -n "$(MERGE_SHA)" -a -n "$(EXPECTED_IMAGE_ID)" || { echo "사용법: make approve-release RUN_ID= INCIDENT_ID= WORK_ID= PR_NUMBER= MERGE_SHA= EXPECTED_IMAGE_ID= [PROPOSAL_ID=] [NOTE=]"; exit 2; }
	$(PY) -m linemedic.cli approve-release --run-id "$(RUN_ID)" --incident-id "$(INCIDENT_ID)" --work-id "$(WORK_ID)" --pr-number "$(PR_NUMBER)" --merge-sha "$(MERGE_SHA)" --expected-image-id "$(EXPECTED_IMAGE_ID)" $(if $(PROPOSAL_ID),--proposal-id "$(PROPOSAL_ID)",) $(if $(NOTE),--note "$(NOTE)",)

# Control Plane 기동 (W13): Control API와 루프(로그 감지·Issue poll·Issue 연결·알림·supervisor·broker)를 한 프로세스로
# 띄운다. attempt는 사람이 미리 쓴 제안(ScriptedAdapter, origin=manual_integration)으로 돈다. 기동 때 결과를 모르는
# 외부 실행은 UNKNOWN으로 두고 다시 실행하지 않는다. 외부 연결(G2 GitHub·RUNNER_IMAGE_ID)이 없으면 그 기능만 꺼진다.
start:
	@test -n "$(RUN_ID)" || { echo "사용법: make start RUN_ID=<make run-new가 만든 활성 run> [MANUAL_PROPOSAL=]"; exit 2; }
	$(PY) -m linemedic.cli start --run-id "$(RUN_ID)" $(if $(MANUAL_PROPOSAL),--manual-proposal "$(MANUAL_PROPOSAL)",)

# make start로 띄운 프로세스 종료 (W13): pid 파일의 프로세스가 이 run의 LineMedic일 때만 SIGTERM을 보낸다.
stop:
	@test -n "$(RUN_ID)" || { echo "사용법: make stop RUN_ID=<run>"; exit 2; }
	$(PY) -m linemedic.cli stop --run-id "$(RUN_ID)"

# 사례 검색 색인 재구축 (W27): PUBLISHED 사례 노트 revision 전부로 FTS5 색인을 지우고 다시 만든다.
# 노트·outcome은 바꾸지 않는다. Control API(make start)와 maintenance 역할 operator token을 쓴다.
# RUN_ID를 생략하면 제어 DB의 활성 run. FTS5가 없는 SQLite면 keyword_fallback이라고만 답한다.
rebuild-case-index:
	$(PY) -m linemedic.cli rebuild-case-index $(if $(RUN_ID),--run-id "$(RUN_ID)",)

# memory snapshot (W27): memory_assisted 평가 run을 시작하기 전에 사례 corpus를 고정한다.
# series별 cutoff 이전 최신 PUBLISHED revision만 넣고, 이 run의 결과·미래 revision·평가 식별자가 든 노트는 뺀다.
# linemedic/eval/snapshots/MEM-*.json에 쓰고(덮어쓰지 않음) MEMORY_SNAPSHOT_PATH로 make start에 넘긴다.
memory-snapshot:
	@test -n "$(RUN_ID)" || { echo "사용법: make memory-snapshot RUN_ID=<평가 run> [CUTOFF=<UTC RFC3339>]"; exit 2; }
	$(PY) -m linemedic.cli memory-snapshot --run-id "$(RUN_ID)" $(if $(CUTOFF),--cutoff "$(CUTOFF)",)

# 읽기 전용 대시보드 (W18): 127.0.0.1에만 bind하고 제어 DB를 읽기 전용으로 연다(쓰기 route·JavaScript 없음).
# 모르는 값은 "미확인", 해당 없는 값은 "N/A". 같은 읽기 모델을 GET /ops/dashboard(operator read)로도 본다.
dashboard:
	$(PY) -m linemedic.dashboard $(if $(RUN_ID),--run-id "$(RUN_ID)",) $(if $(PORT),--port "$(PORT)",)

# run 증거 export (W19): runs/<RUN_ID>/export/<UTC 시각>/에 새로 쓴다(덮어쓰지 않음).
# private/는 DB 행 원본(0700), shared/는 비밀·평가 식별자·로컬 경로를 가린 공유본과 run-record.md.
export-run:
	@test -n "$(RUN_ID)" || { echo "사용법: make export-run RUN_ID=<run>"; exit 2; }
	$(PY) -m linemedic.cli export-run --run-id "$(RUN_ID)"

# run 초기화 (W19): 새 intake·dispatch 정지(run 비활성) → 미해결 외부 실행·알림 확인 → export →
# 이 run 라벨(linemedic.run_id·linemedic.run)이 붙은 컨테이너·network와 runs/<RUN_ID>/workspaces만 정확한 ID·경로로 정리.
# DB 기록·원격 브랜치·Issue·PR·case note는 그대로 둔다. prune·wildcard 삭제·force push 없음. 새 run은 make run-new.
reset:
	@test -n "$(RUN_ID)" || { echo "사용법: make reset RUN_ID=<run>"; exit 2; }
	$(PY) -m linemedic.cli reset --run-id "$(RUN_ID)"
