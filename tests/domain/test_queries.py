from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from memorygraph.domain import ExplanationService, HistoryQueryService
from memorygraph.models import (
    Claim,
    ClaimEvidence,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    ClaimRelation,
    ClaimRelationKind,
    DecisionMethod,
    EvidenceExplicitness,
    EvidenceStance,
    PredicateCardinality,
    PredicateDefinition,
    PredicateVolatility,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class InMemoryReader:
    def __init__(
        self,
        *,
        claims: Sequence[Claim],
        evidence: Sequence[ClaimEvidence],
        relations: Sequence[ClaimRelation],
        predicates: Mapping[str, PredicateDefinition],
    ) -> None:
        self._claims = {claim.id: claim for claim in claims}
        self._evidence = tuple(evidence)
        self._relations = tuple(relations)
        self._predicates = dict(predicates)

    def get_predicate_definition(self, bank_id: str, predicate: str) -> PredicateDefinition | None:
        return self._predicates.get(predicate)

    def get_claim(self, bank_id: str, claim_id: str) -> Claim | None:
        claim = self._claims.get(claim_id)
        if claim is None or claim.bank_id != bank_id:
            return None
        return claim

    def get_claims(self, bank_id: str, claim_ids: Sequence[str]) -> Mapping[str, Claim]:
        return {
            claim_id: claim
            for claim_id in claim_ids
            if (claim := self.get_claim(bank_id, claim_id)) is not None
        }

    def list_claims_for_slot(
        self, bank_id: str, subject_entity_id: str, predicate: str
    ) -> Sequence[Claim]:
        return tuple(
            claim
            for claim in self._claims.values()
            if claim.bank_id == bank_id
            and claim.subject_entity_id == subject_entity_id
            and claim.predicate == predicate
        )

    def list_evidence_for_claim(self, bank_id: str, claim_id: str) -> Sequence[ClaimEvidence]:
        return tuple(
            item for item in self._evidence if item.bank_id == bank_id and item.claim_id == claim_id
        )

    def list_relations_for_claim(self, bank_id: str, claim_id: str) -> Sequence[ClaimRelation]:
        return tuple(
            item
            for item in self._relations
            if item.bank_id == bank_id and claim_id in {item.from_claim_id, item.to_claim_id}
        )


def make_claim(
    claim_id: str,
    *,
    object_value_json: str,
    valid_from: datetime | None,
    valid_to: datetime | None = None,
    system_from: datetime | None = None,
    system_to: datetime | None = None,
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE,
) -> Claim:
    created_at = system_from or ts("2026-01-01T00:00:00Z")
    return Claim(
        id=claim_id,
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate="uses_build_backend",
        object_kind=ClaimObjectKind.STRING,
        object_entity_id=None,
        object_value_json=object_value_json,
        polarity=ClaimPolarity.POSITIVE,
        valid_from=valid_from,
        valid_to=valid_to,
        system_from=created_at,
        system_to=system_to,
        lifecycle=lifecycle,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.5,
        created_at=created_at,
    )


def test_history_service_returns_as_of_timeline_entries() -> None:
    before = make_claim(
        "c1",
        object_value_json='"poetry"',
        valid_from=ts("2026-01-01T00:00:00Z"),
        valid_to=ts("2026-03-01T00:00:00Z"),
        system_from=ts("2026-01-01T00:00:00Z"),
        system_to=ts("2026-03-10T00:00:00Z"),
    )
    after = make_claim(
        "c2",
        object_value_json='"hatchling"',
        valid_from=ts("2026-03-01T00:00:00Z"),
        system_from=ts("2026-03-10T00:00:00Z"),
    )
    reader = InMemoryReader(claims=(before, after), evidence=(), relations=(), predicates={})
    history = HistoryQueryService(reader).history_for_slot(
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate="uses_build_backend",
        known_at=ts("2026-03-15T00:00:00Z"),
        valid_at=ts("2026-03-15T00:00:00Z"),
        allowed_lifecycles=(ClaimLifecycle.ACTIVE,),
    )
    assert [entry.claim.id for entry in history] == ["c2"]


def test_explanation_service_groups_evidence_and_relations() -> None:
    current = make_claim(
        "c1",
        object_value_json='"hatchling"',
        valid_from=ts("2026-03-01T00:00:00Z"),
    )
    previous = make_claim(
        "c0",
        object_value_json='"poetry"',
        valid_from=ts("2026-01-01T00:00:00Z"),
        valid_to=ts("2026-03-01T00:00:00Z"),
        lifecycle=ClaimLifecycle.SUPERSEDED,
    )
    evidence = (
        ClaimEvidence(
            id="e1",
            bank_id="bank-1",
            claim_id="c1",
            observation_id="o1",
            excerpt="We switched to hatchling.",
            stance=EvidenceStance.SUPPORTS,
            explicitness=EvidenceExplicitness.EXPLICIT,
            source_reliability=1.0,
            extraction_confidence=1.0,
            extractor_name="manual",
            extractor_version="1",
            created_at=ts("2026-03-10T00:00:00Z"),
            start_offset=0,
            end_offset=24,
        ),
        ClaimEvidence(
            id="e2",
            bank_id="bank-1",
            claim_id="c1",
            observation_id="o2",
            excerpt="Old README still mentions poetry.",
            stance=EvidenceStance.CONTRADICTS,
            explicitness=EvidenceExplicitness.STRONGLY_IMPLIED,
            source_reliability=0.4,
            extraction_confidence=0.8,
            extractor_name="manual",
            extractor_version="1",
            created_at=ts("2026-03-11T00:00:00Z"),
            start_offset=0,
            end_offset=32,
        ),
    )
    relations = (
        ClaimRelation(
            id="r1",
            bank_id="bank-1",
            from_claim_id="c1",
            to_claim_id="c0",
            relation=ClaimRelationKind.SUPERSEDES,
            rationale="User explicitly switched backends.",
            decision_method=DecisionMethod.EXPLICIT,
            decision_confidence=1.0,
            created_at=ts("2026-03-10T00:00:00Z"),
        ),
    )
    reader = InMemoryReader(
        claims=(current, previous),
        evidence=evidence,
        relations=relations,
        predicates={
            "uses_build_backend": PredicateDefinition(
                name="uses_build_backend",
                cardinality=PredicateCardinality.ONE,
                volatility=PredicateVolatility.VOLATILE,
            )
        },
    )
    explanation = ExplanationService(reader).explain(bank_id="bank-1", claim_id="c1")
    assert explanation.claim.id == "c1"
    assert [item.id for item in explanation.supporting_evidence] == ["e1"]
    assert [item.id for item in explanation.contradicting_evidence] == ["e2"]
    assert explanation.relations[0].other_claim is not None
    assert explanation.relations[0].other_claim.id == "c0"
    assert "claim has an unknown valid-time bound" in explanation.warnings
