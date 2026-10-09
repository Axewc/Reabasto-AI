.PHONY: setup check lint test fmt unpack slice clean-data data export-demo

setup:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q

check: lint test

fmt:
	uv run ruff check --fix .
	uv run ruff format .

# --- data pipeline (run in this order, or just `make data`) ---
unpack:
	@mkdir -p data/csv
	@for f in items stores holidays_events oil transactions; do \
		7zz e -y -bso0 -bsp0 -odata/csv data/raw/$$f.csv.7z; done

slice:
	uv run python scripts/run_slice.py

clean-data:
	uv run python scripts/run_clean.py

data: unpack slice clean-data

export-demo:
	uv run python -m reabasto.ingest.sap_export --date 2017-05-24
	uv run python -m reabasto.ingest.sap_export --date 2017-05-25 --inject-errors
