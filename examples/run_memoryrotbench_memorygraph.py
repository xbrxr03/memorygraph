"""Run the public MemoryRotBench corpus against the real embedded engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

from benchmarks.memoryrotbench.adapters import MemoryGraphAdapter
from benchmarks.memoryrotbench.memorygraph_loader import MemoryGraphScenarioLoader
from benchmarks.memoryrotbench.runner import BenchmarkRunner
from benchmarks.memoryrotbench.scenario_loader import load_scenarios
from memorygraph import MemoryGraph


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scenarios = load_scenarios(
        project_root / "benchmarks" / "memoryrotbench" / "scenarios" / "public"
    )
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memoryrotbench.db") as memory,
    ):
        MemoryGraphScenarioLoader(memory).seed_all(scenarios)
        result = BenchmarkRunner(scenarios).run(
            MemoryGraphAdapter(memory, max_items=10, max_tokens=512)
        )
    print(result.summary().to_text())


if __name__ == "__main__":
    main()
