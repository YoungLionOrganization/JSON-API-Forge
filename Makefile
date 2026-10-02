.PHONY: setup setup-dev lint test check build

VENV_PYTHON := .venv/bin/python

setup:
	./scripts/install.sh

setup-dev:
	./scripts/install.sh --dev

lint:
	$(VENV_PYTHON) -m ruff format --check framework tests scripts forge.py main.py run.py passenger_wsgi.py
	$(VENV_PYTHON) -m ruff check framework tests scripts forge.py main.py run.py passenger_wsgi.py

test:
	$(VENV_PYTHON) -m pytest -q

check: lint test
	$(VENV_PYTHON) -m pip check
	$(VENV_PYTHON) scripts/check_manifest.py

build:
	$(VENV_PYTHON) -m build
	$(VENV_PYTHON) -m twine check dist/*
