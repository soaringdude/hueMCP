.PHONY: install test test-all lint

install:
	uv sync --extra dev

test:
	uv run pytest

test-all:
	uv run pytest -m ""

lint:
	uv run python -m py_compile huemcp/*.py
