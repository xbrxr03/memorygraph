from __future__ import annotations

import tempfile
from pathlib import Path

from memorygraph import MemoryGraph
from memorygraph.adapters import StorageDomainReader
from memorygraph.domain import ExplanationService
from memorygraph.models import ClaimLifecycle, ClaimObjectKind


def test_storage_reader_drives_pure_explanation_service() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:reader")
        observation = memory.observe(
            "Reader uses SQLite.",
            bank=bank.id,
            source_key="event:sqlite",
        )
        claim = memory.assert_claim(
            bank=bank.id,
            subject="Reader",
            predicate="uses_database",
            object="SQLite",
            object_kind="string",
            observation_id=observation.id,
        )
        reader = StorageDomainReader(memory.connection)

        domain_claim = reader.get_claim(bank.id, claim.id)
        explanation = ExplanationService(reader).explain(bank_id=bank.id, claim_id=claim.id)

        assert domain_claim is not None
        assert domain_claim.object_kind is ClaimObjectKind.STRING
        assert domain_claim.lifecycle is ClaimLifecycle.ACTIVE
        assert explanation.supporting_evidence[0].excerpt == "Reader uses SQLite."
        assert reader.event_watermark(bank.id) == 2
