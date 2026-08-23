from __future__ import annotations

from pathlib import Path

from benchmarks.dogfood import DogfoodExperimentLog, DogfoodRunner, RunConfig, load_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "dogfood" / "fixtures" / "offline-mvp.json"
BRIDGE = ROOT / "tests" / "dogfood" / "fixtures" / "graphify_bridge.py"
REPORT = ROOT / "benchmarks" / "reports" / "dogfood-offline-mvp.json"
LEDGER = ROOT / "benchmarks" / "reports" / "dogfood-offline-mvp.jsonl"


def main() -> None:
    manifest = load_manifest(MANIFEST)
    config = RunConfig(
        arms=(
            "no_memory",
            "markdown",
            "memorygraph_graph_only",
            "memorygraph_gated_dream",
            "memorygraph_always_dream",
            "graphify_compatible",
        ),
        external_command=("python3", str(BRIDGE)),
    )
    result = DogfoodRunner(manifest).run(config)
    result.write_json_report(REPORT)
    DogfoodExperimentLog(LEDGER).append(
        result,
        manifest=manifest,
        config=config,
        provenance={"fixture": "offline-mvp"},
    )
    print(REPORT)
    print(LEDGER)


if __name__ == "__main__":
    main()
