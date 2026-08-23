from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from memorygraph.models import (
    Claim,
    ClaimEvidence,
    ClaimLifecycle,
    ClaimRelation,
    EvidenceStance,
    PredicateDefinition,
    TriState,
)

from .protocols import ClaimReader
from .temporal import select_claims_as_of


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    claim: Claim
    valid_time_match: TriState
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExplainedRelation:
    relation: ClaimRelation
    other_claim: Claim | None


@dataclass(frozen=True, slots=True)
class ClaimExplanation:
    claim: Claim
    predicate_definition: PredicateDefinition
    supporting_evidence: tuple[ClaimEvidence, ...]
    contradicting_evidence: tuple[ClaimEvidence, ...]
    mentioning_evidence: tuple[ClaimEvidence, ...]
    relations: tuple[ExplainedRelation, ...]
    warnings: tuple[str, ...]


class HistoryQueryService:
    def __init__(self, reader: ClaimReader) -> None:
        self._reader = reader

    def history_for_slot(
        self,
        *,
        bank_id: str,
        subject_entity_id: str,
        predicate: str,
        known_at: datetime,
        valid_at: datetime,
        allowed_lifecycles: tuple[ClaimLifecycle, ...] | None = None,
        include_unknown_validity: bool = True,
    ) -> tuple[HistoryEntry, ...]:
        claims = self._reader.list_claims_for_slot(bank_id, subject_entity_id, predicate)
        selections = select_claims_as_of(
            claims,
            known_at=known_at,
            valid_at=valid_at,
            allowed_lifecycles=allowed_lifecycles,
            include_unknown_validity=include_unknown_validity,
        )
        return tuple(
            HistoryEntry(
                claim=selection.claim,
                valid_time_match=selection.valid_time_match,
                warnings=selection.warnings,
            )
            for selection in selections
        )


class ExplanationService:
    def __init__(self, reader: ClaimReader) -> None:
        self._reader = reader

    def explain(self, *, bank_id: str, claim_id: str) -> ClaimExplanation:
        claim = self._reader.get_claim(bank_id, claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        predicate_definition = self._reader.get_predicate_definition(bank_id, claim.predicate)
        if predicate_definition is None:
            predicate_definition = PredicateDefinition.unknown(claim.predicate, bank_id=bank_id)

        evidence = tuple(
            sorted(
                self._reader.list_evidence_for_claim(bank_id, claim_id),
                key=lambda item: (
                    item.stance.value,
                    -item.source_reliability,
                    -item.extraction_confidence,
                    item.created_at,
                    item.id,
                ),
            )
        )
        relations = tuple(
            sorted(
                self._reader.list_relations_for_claim(bank_id, claim_id),
                key=lambda item: (item.created_at, item.relation.value, item.id),
            )
        )
        related_ids = {
            relation.to_claim_id if relation.from_claim_id == claim_id else relation.from_claim_id
            for relation in relations
        }
        related_claims = self._reader.get_claims(bank_id, tuple(sorted(related_ids)))
        relation_views = tuple(
            ExplainedRelation(
                relation=relation,
                other_claim=related_claims.get(
                    relation.to_claim_id
                    if relation.from_claim_id == claim_id
                    else relation.from_claim_id
                ),
            )
            for relation in relations
        )

        warnings: list[str] = []
        if claim.lifecycle is ClaimLifecycle.CONTESTED:
            warnings.append("claim is currently contested")
        if claim.lifecycle is ClaimLifecycle.RETRACTED:
            warnings.append("claim has been retracted")
        if claim.lifecycle is ClaimLifecycle.SUPERSEDED:
            warnings.append("claim has been superseded")
        if not any(item.stance is EvidenceStance.SUPPORTS for item in evidence):
            warnings.append("claim has no supporting evidence")
        if claim.valid_from is None or claim.valid_to is None:
            warnings.append("claim has an unknown valid-time bound")

        return ClaimExplanation(
            claim=claim,
            predicate_definition=predicate_definition,
            supporting_evidence=tuple(
                item for item in evidence if item.stance is EvidenceStance.SUPPORTS
            ),
            contradicting_evidence=tuple(
                item for item in evidence if item.stance is EvidenceStance.CONTRADICTS
            ),
            mentioning_evidence=tuple(
                item for item in evidence if item.stance is EvidenceStance.MENTIONS
            ),
            relations=relation_views,
            warnings=tuple(warnings),
        )
