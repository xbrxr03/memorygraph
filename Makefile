.PHONY: test lint format build package-check doctor demo dogfood-fixture dogfood-live dogfood-beta

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check .

format:
	uv run --extra dev ruff format .

build:
	uv run --extra dev python -m build

package-check: build
	uv run --extra dev twine check dist/*

doctor:
	uv run memorygraph doctor

demo:
	PYTHONPATH=src:. uv run python examples/run_memoryrotbench_memorygraph.py

dogfood-fixture:
	PYTHONPATH=src:. uv run python examples/run_dogfood_fixture_matrix.py

dogfood-live:
	uv run memorygraph dogfood evaluate-live \
		--ledger .memorygraph/dogfood/live-sessions.jsonl \
		--output .memorygraph/dogfood/live-report.json

dogfood-beta:
	PYTHONPATH=src:. uv run python examples/run_dogfood_beta.py
