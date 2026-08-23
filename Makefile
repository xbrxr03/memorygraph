.PHONY: test lint format build package-check doctor demo dogfood-fixture

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
