from __future__ import annotations

from memorygraph import MemoryGraph
from memorygraph.dream import DreamRunMode


def test_apply_dream_uses_observation_time_for_historical_recall(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:falsify-dream")
        memory.define_predicate(
            "release_command",
            bank=bank.id,
            cardinality="one",
            volatility="durable",
            subject_type="project",
            object_type="value",
        )
        observation = memory.observe(
            "Deploy Delta with ./scripts/deploy.sh --prod.",
            bank=bank.id,
            source_key="release-note",
            observed_at="2026-04-01T14:00:00Z",
            metadata={
                "memorygraph": {
                    "entities": [{"local_id": "project", "name": "Delta", "type": "project"}],
                    "claims": [
                        {
                            "local_id": "release",
                            "subject": "project",
                            "predicate": "release_command",
                            "object": {
                                "kind": "string",
                                "value": "./scripts/deploy.sh --prod.",
                            },
                            "confidence": 1.0,
                        }
                    ],
                }
            },
        )

        report = memory.run_dream(
            bank=bank.id,
            mode=DreamRunMode.APPLY,
            observation_ids=(observation.id,),
        )
        hits = memory.recall(
            bank_id=bank.id,
            query_text="What is Delta's deploy command?",
            as_of="2026-04-02T00:00:00Z",
            max_items=5,
            max_tokens=128,
        )

    assert report.status.value == "completed"
    assert report.commit_result is not None
    assert [hit.event_id for hit in hits] == ["release-note"]
