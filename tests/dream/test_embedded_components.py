from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from memorygraph import MemoryGraph
from memorygraph.application.dream_service import EmbeddedDreamService
from memorygraph.dream import (
    ChallengeResult,
    ClaimObjectCandidate,
    EvidenceSpanCandidate,
    ExtractedClaimCandidate,
    ExtractedEntityCandidate,
    ExtractionCandidateBatch,
    ExtractionResult,
    ProviderCallTrace,
    ProviderOperation,
)
from memorygraph.models import ClaimObjectKind, ClaimPolarity, EvidenceExplicitness


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ConfirmingProvider:
    def __init__(
        self,
        *,
        memory: MemoryGraph | None = None,
        invalid_excerpt: bool = False,
        bump_watermark: bool = False,
    ) -> None:
        self.memory = memory
        self.invalid_excerpt = invalid_excerpt
        self.bump_watermark = bump_watermark
        self._bumped = False

    def extract(self, source_bundle):
        if self.bump_watermark and self.memory is not None and not self._bumped:
            self.memory.events.append(
                event_id=str(uuid4()),
                bank_id=source_bundle.bank_id,
                event_type="test.concurrent",
                aggregate_type="bank",
                aggregate_id=source_bundle.bank_id,
                actor_type="system",
                payload={"note": "simulate concurrent write"},
                created_at=iso(ts("2026-08-21T12:00:00Z")),
            )
            self._bumped = True

        observation = source_bundle.observations[0]
        subject_text = "MemoryGraph"
        object_text = "poetry"
        subject_start = observation.content.index(subject_text)
        object_start = observation.content.index(object_text)
        evidence_excerpt = "wrong excerpt" if self.invalid_excerpt else object_text
        return ExtractionResult(
            candidates=ExtractionCandidateBatch(
                entities=(
                    ExtractedEntityCandidate(
                        local_id="subject-1",
                        name=subject_text,
                        entity_type="project",
                        evidence_span=EvidenceSpanCandidate(
                            candidate_id="entity-ev-1",
                            observation_id=observation.observation_id,
                            start_offset=subject_start,
                            end_offset=subject_start + len(subject_text),
                            excerpt=subject_text,
                        ),
                    ),
                ),
                claims=(
                    ExtractedClaimCandidate(
                        local_id="claim-1",
                        subject_local_id="subject-1",
                        predicate="uses_build_backend",
                        object_candidate=ClaimObjectCandidate(
                            kind=ClaimObjectKind.STRING,
                            value=object_text,
                        ),
                        polarity=ClaimPolarity.POSITIVE,
                        explicitness=EvidenceExplicitness.EXPLICIT,
                        evidence_spans=(
                            EvidenceSpanCandidate(
                                candidate_id="claim-ev-1",
                                observation_id=observation.observation_id,
                                start_offset=object_start,
                                end_offset=object_start + len(object_text),
                                excerpt=evidence_excerpt,
                            ),
                        ),
                        extraction_confidence=0.98,
                    ),
                ),
            ),
            trace=ProviderCallTrace(
                operation=ProviderOperation.EXTRACT,
                provider_name="test.confirming",
                provider_version="1",
            ),
        )

    def challenge(self, request) -> ChallengeResult:
        return ChallengeResult(
            objections=(),
            trace=ProviderCallTrace(
                operation=ProviderOperation.CHALLENGE,
                provider_name="test.confirming",
                provider_version="1",
            ),
        )


def seed_current_claim(memory: MemoryGraph, bank_id: str) -> tuple[str, str]:
    initial_observation = memory.observe(
        "MemoryGraph uses poetry today.",
        bank=bank_id,
        source_key="seed:current",
        actor_type="user",
        actor_id="abrar",
    )
    claim = memory.assert_claim(
        bank=bank_id,
        subject="MemoryGraph",
        subject_type="project",
        predicate="uses_build_backend",
        object="poetry",
        object_kind="string",
        object_type="tool",
        observation_id=initial_observation.id,
        excerpt="poetry",
        origin="explicit",
    )
    return initial_observation.id, claim.id


def make_dream_observation(memory: MemoryGraph, bank_id: str, *, source_key: str) -> str:
    observation = memory.observe(
        "MemoryGraph still uses poetry in CI.",
        bank=bank_id,
        source_key=source_key,
        actor_type="user",
        actor_id="abrar",
    )
    return observation.id


def test_embedded_components_valid_commit() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:embedded")
        memory.define_predicate(
            "uses_build_backend",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
        )
        _, claim_id = seed_current_claim(memory, bank.id)
        dream_observation_id = make_dream_observation(memory, bank.id, source_key="dream:valid")
        before = memory.events.current_watermark(bank.id)

        report = EmbeddedDreamService(memory).run(
            bank=bank.id,
            provider=ConfirmingProvider(),
            observation_ids=(dream_observation_id,),
        )

        assert report.status.value == "completed"
        assert report.proposal_results[0].commit_outcome is not None
        assert report.proposal_results[0].commit_outcome.status.value == "committed"
        assert len(memory.evidence.list_for_claim(bank.id, claim_id)) == 2
        events = memory.events.list_after(bank.id, sequence_exclusive=before)
        assert [event.event_type for event in events] == [
            "claim.confirmed",
            "dream.proposal.committed",
        ]
        proposal_records = memory.dream_proposals.list_for_run(bank.id, report.task.run_id)
        assert proposal_records[0].disposition == "committed"
        assert memory.observations.get(bank.id, dream_observation_id).ingestion_state == "processed"


def test_embedded_components_invalid_evidence_rejects_without_commit() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:embedded")
        memory.define_predicate(
            "uses_build_backend",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
        )
        _, claim_id = seed_current_claim(memory, bank.id)
        dream_observation_id = make_dream_observation(memory, bank.id, source_key="dream:invalid")
        before = memory.events.current_watermark(bank.id)

        report = EmbeddedDreamService(memory).run(
            bank=bank.id,
            provider=ConfirmingProvider(invalid_excerpt=True),
            observation_ids=(dream_observation_id,),
        )

        assert report.status.value == "completed"
        assert report.proposal_results[0].validation.disposition.value == "rejected"
        assert report.commit_result is None
        assert len(memory.evidence.list_for_claim(bank.id, claim_id)) == 1
        assert memory.events.list_after(bank.id, sequence_exclusive=before) == ()
        proposal_records = memory.dream_proposals.list_for_run(bank.id, report.task.run_id)
        assert proposal_records[0].disposition == "rejected"


def test_embedded_components_stale_watermark_skips_commit() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:embedded")
        memory.define_predicate(
            "uses_build_backend",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
        )
        _, claim_id = seed_current_claim(memory, bank.id)
        dream_observation_id = make_dream_observation(memory, bank.id, source_key="dream:stale")
        before = memory.events.current_watermark(bank.id)

        report = EmbeddedDreamService(memory).run(
            bank=bank.id,
            provider=ConfirmingProvider(memory=memory, bump_watermark=True),
            observation_ids=(dream_observation_id,),
        )

        assert report.status.value == "completed"
        assert report.proposal_results[0].validation.disposition.value == "stale"
        assert report.commit_result is None
        assert len(memory.evidence.list_for_claim(bank.id, claim_id)) == 1
        events = memory.events.list_after(bank.id, sequence_exclusive=before)
        assert [event.event_type for event in events] == ["test.concurrent"]
        proposal_records = memory.dream_proposals.list_for_run(bank.id, report.task.run_id)
        assert proposal_records[0].disposition == "stale"
