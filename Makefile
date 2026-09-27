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

.PHONY: setup lock test test-docker test-live lint fmt doctor host-manifest mes-image scenario-s1 verify-negative run-new

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
