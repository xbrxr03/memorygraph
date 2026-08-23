#!/usr/bin/env python3
"""Run the public MemoryRotBench seed set with the flat-context baseline."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from benchmarks.memoryrotbench import BenchmarkRunner, load_scenarios
    from benchmarks.memoryrotbench.adapters import FlatContextAdapter

    public_dir = REPO_ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"
    runner = BenchmarkRunner(load_scenarios(public_dir))
    result = runner.run(FlatContextAdapter(), run_id="example-flat-context")
    print(result.summary().to_text())


if __name__ == "__main__":
    main()
