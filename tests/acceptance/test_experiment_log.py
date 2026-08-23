from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from benchmarks.memoryrotbench import BenchmarkRunner, load_scenarios
from benchmarks.memoryrotbench.adapters import NoMemoryAdapter
from benchmarks.memoryrotbench.experiment_log import DuplicateRunError, ExperimentLog

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"


def test_experiment_log_is_append_only_and_fingerprinted() -> None:
    scenarios = load_scenarios(PUBLIC_DIR)
    result = BenchmarkRunner(scenarios).run(NoMemoryAdapter(), run_id="no-memory-test")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "experiments.jsonl"
        record = ExperimentLog(path).append(
            result,
            scenarios=scenarios,
            adapter_config={"context": "none"},
            provenance={"purpose": "unit-test"},
        )
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored == record
        assert len(stored["corpus_sha256"]) == 64
        assert len(stored["evaluator_sha256"]) == 64
        assert stored["report"]["summary"]["query_count"] > 0
        with pytest.raises(DuplicateRunError):
            ExperimentLog(path).append(result, scenarios=scenarios)
