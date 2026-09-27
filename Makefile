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

.PHONY: setup lock test test-docker test-live lint fmt doctor host-manifest

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

host-manifest:
	$(PY) -m linemedic.cli host-manifest
