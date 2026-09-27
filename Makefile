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

.PHONY: setup lock test test-docker test-live lint fmt doctor host-manifest mes-image runner-image scenario-s1 verify-negative run-new detect-once scenario-s2-lite api-schema issue-sync issue-bind approve-work retry-work cancel-work notification-reconcile reconcile

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

# 새 run (W06: 제어 DB migration과 demo_runs 활성 전환·manifest 기록. baseline 브랜치 등은 W19).
# host manifest가 있으면 경로와 SHA-256을 run manifest에 남긴다.
run-new:
	$(PY) -m linemedic.cli run-new $(if $(wildcard evidence/host-manifest.json),--host-manifest evidence/host-manifest.json,)

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

# 결과 불명 execution 조정 (W11): CREATE_PR(W11)·CREATE_ISSUE(W24)를 외부 조회로만 확인한다. 새로 만들지 않는다.
# CREATE_PR은 봇 작성·head 브랜치·candidate SHA·base·marker가 모두 맞는 PR 1개만 채택한다(PR_OPENED).
# PR·브랜치가 모두 없음이 확인되면 ESCALATED, 그 밖(충돌·불완전·브랜치만 남음)은 기록만 한다.
reconcile:
	@test -n "$(RUN_ID)" -a -n "$(EXECUTION_ID)" || { echo "사용법: make reconcile RUN_ID=<run> EXECUTION_ID=EXE-..."; exit 2; }
	$(PY) -m linemedic.cli reconcile --run-id "$(RUN_ID)" --execution-id "$(EXECUTION_ID)"
