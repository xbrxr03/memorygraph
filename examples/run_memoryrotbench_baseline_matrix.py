#!/usr/bin/env python3
"""Run the reproducible public baseline matrix and append fingerprinted results."""

from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from benchmarks.memoryrotbench import BenchmarkRunner, load_scenarios
    from benchmarks.memoryrotbench.adapters import (
        BM25Adapter,
        ExternalCommandAdapter,
        FlatContextAdapter,
        LatestNAdapter,
        MarkdownRunbookAdapter,
        MemoryGraphAdapter,
        NoMemoryAdapter,
    )
    from benchmarks.memoryrotbench.experiment_log import ExperimentLog
    from benchmarks.memoryrotbench.memorygraph_loader import MemoryGraphScenarioLoader
    from benchmarks.memoryrotbench.results import make_run_id
    from memorygraph import MemoryGraph

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("benchmarks/reports/public-baseline-matrix.jsonl"),
    )
    parser.add_argument(
        "--graphify-command",
        help="Optional quoted command implementing memoryrotbench.external/v1.",
    )
    args = parser.parse_args()

    scenarios = load_scenarios(REPO_ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public")
    runner = BenchmarkRunner(scenarios)
    adapters = [
        NoMemoryAdapter(),
        LatestNAdapter(limit=3),
        MarkdownRunbookAdapter(max_tokens=512),
        BM25Adapter(max_items=10, max_tokens=512),
        FlatContextAdapter(),
    ]
    if args.graphify_command:
        adapters.append(
            ExternalCommandAdapter(
                shlex.split(args.graphify_command),
                adapter_name="graphify",
            )
        )

    log = ExperimentLog(args.log)
    for adapter in adapters:
        result = runner.run(adapter, run_id=make_run_id(adapter.adapter_name))
        log.append(result, scenarios=scenarios)
        print(result.summary().to_text(), end="\n\n")

    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memoryrotbench.db") as memory,
    ):
        MemoryGraphScenarioLoader(memory).seed_all(scenarios)
        result = runner.run(
            MemoryGraphAdapter(memory, max_items=10, max_tokens=512),
            run_id=make_run_id("memorygraph"),
        )
    log.append(
        result,
        scenarios=scenarios,
        adapter_config={"max_items": 10, "max_tokens": 512},
        provenance={"runtime": "embedded-memorygraph"},
    )
    print(result.summary().to_text())


if __name__ == "__main__":
    main()
