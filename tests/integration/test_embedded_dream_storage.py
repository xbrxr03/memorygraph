from __future__ import annotations

import tempfile
from pathlib import Path

from memorygraph import MemoryGraph
from memorygraph.application import EmbeddedDreamService


def test_embedded_dream_service_persists_run_state_and_replay_keeps_one_current_claim() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:dream-storage")
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
            source_key="dream:employment:stripe",
            observed_at="2026-08-21T12:00:00Z",
            metadata={
                "memorygraph": {
                    "entities": [
                        {"local_id": "subject", "name": "Abrar", "type": "person"},
                        {"local_id": "employer", "name": "Stripe", "type": "organization"},
                    ],
                    "claims": [
                        {
                            "local_id": "claim-1",
                            "subject": "subject",
                            "predicate": "works_at",
                            "object": {"kind": "entity", "value": "employer"},
                            "confidence": 1.0,
                        }
                    ],
                }
            },
        )

        service = EmbeddedDreamService(memory)
        first_report = service.run(bank=bank.id, observation_ids=(observation.id,))

        first_run = memory.dream_runs.get(bank.id, first_report.task.run_id)
        first_task = memory.dream_tasks.get(bank.id, first_report.task.task_id)
        first_proposals = memory.dream_proposals.list_for_run(bank.id, first_report.task.run_id)
        current_after_first = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        assert len(current_after_first) == 1
        current_claim_id = current_after_first[0].claim.id
        first_explanation = memory.explain(current_claim_id, bank=bank.id)

        assert first_report.status.value == "completed"
        assert first_report.metrics.selected_observations == 1
        assert first_report.metrics.extracted_entities == 2
        assert first_report.metrics.extracted_claims == 1
        assert first_report.metrics.committed == 1
        assert first_run is not None and first_run.state == "completed"
        assert first_run.usage["committed"] == 1
        assert first_task is not None and first_task.state == "completed"
        assert first_task.output["committed"] == 1
        assert len(first_proposals) == 1
        assert first_proposals[0].disposition == "committed"
        assert memory.observations.get(bank.id, observation.id).ingestion_state == "processed"
        assert current_after_first[0].object == "Stripe"
        assert len(first_explanation.evidence) == 1
        assert first_explanation.evidence[0].excerpt == "Abrar now works at Stripe."

        second_report = service.run(bank=bank.id, observation_ids=(observation.id,))

        second_run = memory.dream_runs.get(bank.id, second_report.task.run_id)
        second_task = memory.dream_tasks.get(bank.id, second_report.task.task_id)
        second_proposals = memory.dream_proposals.list_for_run(bank.id, second_report.task.run_id)
        current_after_replay = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        replay_explanation = memory.explain(current_claim_id, bank=bank.id)
        events = memory.events.list_after(bank.id, sequence_exclusive=0, limit=100)
        claim_events = [event for event in events if event.event_type.startswith("claim.")]

        assert second_report.status.value == "completed"
        assert second_report.metrics.selected_observations == 0
        assert second_report.metrics.proposals_total == 0
        assert second_run is not None and second_run.state == "completed"
        assert second_task is not None and second_task.state == "completed"
        assert second_run.usage["committed"] == 0
        assert second_task.output["committed"] == 0
        assert second_proposals == ()
        assert len(current_after_replay) == 1
        assert current_after_replay[0].claim.id == current_claim_id
        assert current_after_replay[0].object == "Stripe"
        assert len(replay_explanation.evidence) == 1
        assert [event.event_type for event in claim_events] == ["claim.asserted"]
