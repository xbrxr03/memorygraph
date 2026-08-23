from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from memorygraph.models import Claim, PredicateCardinality, PredicateDefinition, TriState

from .temporal import valid_time_overlap


class ConflictReason(StrEnum):
    CARDINALITY_ONE = "cardinality_one_overlap"
    EXPLICIT_NEGATION = "explicit_negation"


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    left_claim_id: str
    right_claim_id: str
    overlap: TriState
    reason: ConflictReason


def claims_materially_differ(left: Claim, right: Claim) -> bool:
    return left.object_signature != right.object_signature or left.polarity != right.polarity


def claims_are_explicit_negation_pair(left: Claim, right: Claim) -> bool:
    return left.object_signature == right.object_signature and left.polarity != right.polarity


def detect_conflict_candidate(
    left: Claim,
    right: Claim,
    predicate_definition: PredicateDefinition,
) -> ConflictCandidate | None:
    if left.bank_id != right.bank_id:
        return None
    if left.subject_entity_id != right.subject_entity_id or left.predicate != right.predicate:
        return None
    if not claims_materially_differ(left, right):
        return None

    overlap = valid_time_overlap(left.valid_interval, right.valid_interval)
    if overlap is TriState.NO:
        return None

    if claims_are_explicit_negation_pair(left, right):
        return ConflictCandidate(
            left_claim_id=left.id,
            right_claim_id=right.id,
            overlap=overlap,
            reason=ConflictReason.EXPLICIT_NEGATION,
        )

    if predicate_definition.cardinality is not PredicateCardinality.ONE:
        return None

    return ConflictCandidate(
        left_claim_id=left.id,
        right_claim_id=right.id,
        overlap=overlap,
        reason=ConflictReason.CARDINALITY_ONE,
    )


def find_conflict_candidates(
    claims: Iterable[Claim],
    predicate_definition: PredicateDefinition,
) -> tuple[ConflictCandidate, ...]:
    claim_list = list(claims)
    conflicts: list[ConflictCandidate] = []
    for index, left in enumerate(claim_list):
        for right in claim_list[index + 1 :]:
            candidate = detect_conflict_candidate(left, right, predicate_definition)
            if candidate is not None:
                conflicts.append(candidate)
    return tuple(conflicts)
