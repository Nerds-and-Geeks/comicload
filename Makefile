.PHONY: help setup install test lint typecheck check run clean

PYTHON := .venv/bin/python
UV := uv

help:
	@echo "comicload — development commands"
	@echo ""
	@echo "  make setup      create .venv and install everything (run this first)"
	@echo "  make install    reinstall the package in editable mode"
	@echo "  make test       run the test suite"
	@echo "  make lint       check formatting and style with ruff"
	@echo "  make typecheck  check types with mypy"
	@echo "  make check      lint + typecheck + test (run before every commit)"
	@echo "  make run        run the CLI, e.g. make run ARGS='scan ./photos'"
	@echo "  make clean      remove caches and build artifacts"

setup:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install --python $(PYTHON) -e ".[dev]"
	@echo ""
	@echo "Ready. Barcode reading also needs the zbar library: brew install zbar"

install:
	$(UV) pip install --python $(PYTHON) -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

typecheck:
	$(PYTHON) -m mypy

check: lint typecheck test

run:
	$(PYTHON) -m comicload.adapters.cli.app $(ARGS)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
