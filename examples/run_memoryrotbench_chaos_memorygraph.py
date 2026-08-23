#!/usr/bin/env python3
"""Run the supported chaos corpus cases against the real MemoryGraph engine."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for candidate in (str(REPO_ROOT), str(SRC_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from benchmarks.memoryrotbench.chaos_loader import load_chaos_cases  # noqa: E402
from benchmarks.memoryrotbench.chaos_runner import ChaosRunner  # noqa: E402
from benchmarks.memoryrotbench.production_runtime import (  # noqa: E402
    ProductionDreamRuntime,
    support_matrix,
)


def main() -> None:
    root = REPO_ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "development"
    cases = load_chaos_cases(root)
    matrix = support_matrix(cases)
    supported_ids = [row.case_id for row in matrix if row.supported]
    print("Support matrix:")
    for row in matrix:
        status = "supported" if row.supported else "unsupported"
        print(f"- case {row.acceptance_case}: {row.case_id} -> {status} ({row.reason})")
    print()
    result = ChaosRunner(ProductionDreamRuntime).run(
        [case for case in cases if case.case_id in supported_ids],
        run_id="example-chaos-memorygraph",
    )
    print(result.to_text())


if __name__ == "__main__":
    main()
