.PHONY: setup data snapshots eval test lint app all

UV := uv run

setup:
	uv sync --all-extras

data:
	$(UV) python -m calib.ingest_kalshi
	$(UV) python -m calib.ingest_polymarket
	$(UV) python -m calib.quality

snapshots:
	$(UV) python -m calib.snapshots

eval:
	$(UV) python -m calib.report

test:
	$(UV) pytest

lint:
	$(UV) ruff check src tests app
	$(UV) ruff format --check src tests app
	$(UV) mypy

app:
	$(UV) streamlit run app/app.py

all: data snapshots eval
