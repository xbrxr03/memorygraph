"""Run the embedded deterministic dream cycle end to end."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memorygraph import MemoryGraph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".memorygraph/dream-demo.db"))
    args = parser.parse_args()

    with MemoryGraph.open(args.database) as memory:
        bank = memory.create_bank(
            "personal:founder",
            mission="Maintain an evidence-backed, current model of the founder's world.",
        )
        memory.define_predicate(
            "works_at",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
            subject_type="person",
            object_type="organization",
        )
        observation = memory.observe(
            "Abrar now works at Stripe.",
            bank=bank.id,
            source_key="demo:employment:stripe",
            observed_at="2026-08-21T12:00:00Z",
            metadata={
                "memorygraph": {
                    "entities": [
                        {"local_id": "person", "name": "Abrar", "type": "person"},
                        {
                            "local_id": "employer",
                            "name": "Stripe",
                            "type": "organization",
                        },
                    ],
                    "claims": [
                        {
                            "local_id": "employment",
                            "subject": "person",
                            "predicate": "works_at",
                            "object": {"kind": "entity", "value": "employer"},
                            "confidence": 1.0,
                        }
                    ],
                }
            },
        )
        report = memory.run_dream(bank=bank.id, observation_ids=(observation.id,))
        history = memory.history(bank=bank.id, subject="Abrar", predicate="works_at")

    print(
        json.dumps(
            {
                "run_id": report.task.run_id,
                "status": report.status.value,
                "selected_observations": report.metrics.selected_observations,
                "committed": report.metrics.committed,
                "current_beliefs": [
                    {
                        "subject": item.subject,
                        "predicate": item.claim.predicate,
                        "object": item.object,
                        "lifecycle": item.claim.lifecycle,
                    }
                    for item in history
                    if item.claim.system_to is None
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
