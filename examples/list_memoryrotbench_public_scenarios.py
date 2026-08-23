#!/usr/bin/env python3
"""Print a compact summary of the public MemoryRotBench seed scenarios."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from benchmarks.memoryrotbench import load_scenarios

    root = REPO_ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"
    scenarios = load_scenarios(root)
    print(f"Loaded {len(scenarios)} public scenarios")
    for scenario in scenarios:
        print(
            f"- {scenario.scenario_id} | {scenario.category} | {scenario.bank_id} | "
            f"{len(scenario.events)} events | {len(scenario.queries)} queries"
        )


if __name__ == "__main__":
    main()
