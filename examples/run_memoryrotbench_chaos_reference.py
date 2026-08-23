#!/usr/bin/env python3
"""Run the development chaos corpus against the reference DreamRuntime."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.memoryrotbench.chaos_loader import load_chaos_cases  # noqa: E402
from benchmarks.memoryrotbench.chaos_runner import ChaosRunner  # noqa: E402
from benchmarks.memoryrotbench.reference_runtime import FakeDreamRuntime  # noqa: E402


def main() -> None:
    root = REPO_ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "development"
    cases = load_chaos_cases(root)
    result = ChaosRunner(FakeDreamRuntime).run(cases, run_id="example-chaos-reference")
    print(result.to_text())


if __name__ == "__main__":
    main()
