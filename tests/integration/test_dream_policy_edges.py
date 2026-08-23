from __future__ import annotations

import tempfile
from pathlib import Path

from memorygraph import MemoryGraph


def _metadata(*objects: str) -> dict[str, object]:
    entities: list[dict[str, object]] = [{"local_id": "subject", "name": "Abrar", "type": "person"}]
    claims: list[dict[str, object]] = []
    for index, object_name in enumerate(objects):
        object_id = f"employer-{index}"
        entities.append({"local_id": object_id, "name": object_name, "type": "organization"})
        claims.append(
            {
                "local_id": f"employment-{index}",
                "subject": "subject",
                "predicate": "works_at",
                "object": {"kind": "entity", "value": object_id},
                "confidence": 1.0,
            }
        )
    return {"memorygraph": {"entities": entities, "claims": claims}}


def test_dry_run_does_not_consume_observation_or_mutate_claims() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:dry-run")
        memory.define_predicate("works_at", bank=bank.id, cardinality="one")
        observation = memory.observe(
            "Abrar works at Stripe.",
            bank=bank.id,
            source_key="employment:dry-run",
            metadata=_metadata("Stripe"),
        )

        report = memory.run_dream(
            bank=bank.id,
            mode="dry_run",
            observation_ids=(observation.id,),
        )

        assert report.metrics.auto_eligible == 1
        assert report.metrics.committed == 0
        assert memory.observations.get(bank.id, observation.id).ingestion_state == "pending"
        assert memory.history(bank=bank.id, subject="Abrar", predicate="works_at") == ()


def test_conflicting_candidates_for_one_slot_are_routed_to_review() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:ambiguous")
        memory.define_predicate("works_at", bank=bank.id, cardinality="one")
        observation = memory.observe(
            "Abrar works at Acme or Stripe; the source is internally inconsistent.",
            bank=bank.id,
            source_key="employment:ambiguous",
            metadata=_metadata("Acme", "Stripe"),
        )

        report = memory.run_dream(bank=bank.id, observation_ids=(observation.id,))
        run = memory.dream_runs.get(bank.id, report.task.run_id)
        reviews = memory.pending_reviews(bank=bank.id)

        assert report.metrics.review_required == 2
        assert report.metrics.committed == 0
        assert run is not None and run.state == "awaiting_review"
        assert len(reviews) == 2
        assert memory.observations.get(bank.id, observation.id).ingestion_state == "partial"
        assert memory.history(bank=bank.id, subject="Abrar", predicate="works_at") == ()
