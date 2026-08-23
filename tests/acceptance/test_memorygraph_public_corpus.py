from __future__ import annotations

import tempfile
from pathlib import Path

from benchmarks.memoryrotbench.adapters import MemoryGraphAdapter
from benchmarks.memoryrotbench.memorygraph_loader import MemoryGraphScenarioLoader
from benchmarks.memoryrotbench.runner import BenchmarkRunner
from benchmarks.memoryrotbench.scenario_loader import load_scenarios
from memorygraph import MemoryGraph


def test_real_engine_passes_the_public_retrieval_corpus() -> None:
    corpus = Path("benchmarks/memoryrotbench/scenarios/public")
    scenarios = load_scenarios(corpus)
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memoryrotbench.db") as memory,
    ):
        MemoryGraphScenarioLoader(memory).seed_all(scenarios)
        result = BenchmarkRunner(scenarios).run(
            MemoryGraphAdapter(memory, max_items=10, max_tokens=512),
            run_id="memorygraph-public-acceptance",
        )

    summary = result.summary()
    assert summary.query_count == 12
    assert summary.passed_queries == 12
    assert summary.failed_queries == 0
    assert summary.average_required_evidence_recall == 1.0
    assert summary.leaked_query_count == 0
    assert summary.forbidden_evidence_query_count == 0
    assert summary.forbidden_fragment_query_count == 0
