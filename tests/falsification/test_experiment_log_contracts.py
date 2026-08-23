from __future__ import annotations

import json
from pathlib import Path

from benchmarks.memoryrotbench import BenchmarkRunner, load_scenarios
from benchmarks.memoryrotbench.adapters import NoMemoryAdapter
from benchmarks.memoryrotbench.experiment_log import ExperimentLog

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"


def test_append_preserves_valid_jsonl_when_existing_file_lacks_trailing_newline(tmp_path) -> None:
    path = tmp_path / "experiments.jsonl"
    path.write_text('{"schema":"memoryrotbench.experiment/v1","run_id":"prior"}', encoding="utf-8")
    scenarios = load_scenarios(PUBLIC_DIR)
    result = BenchmarkRunner(scenarios).run(NoMemoryAdapter(), run_id="newline-falsification")

    ExperimentLog(path).append(result, scenarios=scenarios)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "prior"
    assert json.loads(lines[1])["run_id"] == "newline-falsification"
