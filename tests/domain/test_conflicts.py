from __future__ import annotations

from datetime import UTC, datetime

from memorygraph.domain import ConflictReason, detect_conflict_candidate, find_conflict_candidates
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    PredicateCardinality,
    PredicateDefinition,
    PredicateVolatility,
    TriState,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def make_claim(
    claim_id: str,
    *,
    object_value_json: str,
    polarity: ClaimPolarity = ClaimPolarity.POSITIVE,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> Claim:
    created_at = ts("2026-01-01T00:00:00Z")
    return Claim(
        id=claim_id,
        bank_id="bank-1",
        subject_entity_id="person-1",
        predicate="works_at",
        object_kind=ClaimObjectKind.STRING,
        object_entity_id=None,
        object_value_json=object_value_json,
        polarity=polarity,
        valid_from=valid_from,
        valid_to=valid_to,
        system_from=created_at,
        system_to=None,
        lifecycle=ClaimLifecycle.ACTIVE,
        origin=ClaimOrigin.EXTRACTED,
        importance=0.5,
        created_at=created_at,
    )


def test_cardinality_one_overlapping_distinct_objects_conflict() -> None:
    predicate = PredicateDefinition(
        name="works_at",
        cardinality=PredicateCardinality.ONE,
        volatility=PredicateVolatility.VOLATILE,
    )
    first = make_claim("c1", object_value_json='"Acme"', valid_from=ts("2026-01-01T00:00:00Z"))
    second = make_claim("c2", object_value_json='"Stripe"', valid_from=ts("2026-02-01T00:00:00Z"))

    candidate = detect_conflict_candidate(first, second, predicate)
    assert candidate is not None
    assert candidate.reason is ConflictReason.CARDINALITY_ONE
    assert candidate.overlap is TriState.UNKNOWN


def test_multi_value_predicate_coexists_without_conflict() -> None:
    predicate = PredicateDefinition(name="prefers", cardinality=PredicateCardinality.MANY)
    first = make_claim("c1", object_value_json='"vim"')
    second = make_claim("c2", object_value_json='"tmux"')
    assert detect_conflict_candidate(first, second, predicate) is None


def test_explicit_negation_conflicts_even_for_many_predicate() -> None:
    predicate = PredicateDefinition(name="prefers", cardinality=PredicateCardinality.MANY)
    first = make_claim("c1", object_value_json='"vim"', polarity=ClaimPolarity.POSITIVE)
    second = make_claim("c2", object_value_json='"vim"', polarity=ClaimPolarity.NEGATIVE)

    candidate = detect_conflict_candidate(first, second, predicate)
    assert candidate is not None
    assert candidate.reason is ConflictReason.EXPLICIT_NEGATION


def test_known_disjoint_valid_intervals_do_not_conflict() -> None:
    predicate = PredicateDefinition(name="works_at", cardinality=PredicateCardinality.ONE)
    first = make_claim(
        "c1",
        object_value_json='"Acme"',
        valid_from=ts("2026-01-01T00:00:00Z"),
        valid_to=ts("2026-03-01T00:00:00Z"),
    )
    second = make_claim(
        "c2",
        object_value_json='"Stripe"',
        valid_from=ts("2026-03-01T00:00:00Z"),
        valid_to=ts("2026-04-01T00:00:00Z"),
    )
    assert detect_conflict_candidate(first, second, predicate) is None


def test_find_conflict_candidates_collects_pairwise_matches() -> None:
    predicate = PredicateDefinition(name="works_at", cardinality=PredicateCardinality.ONE)
    claims = (
        make_claim("c1", object_value_json='"Acme"', valid_from=ts("2026-01-01T00:00:00Z")),
        make_claim("c2", object_value_json='"Stripe"', valid_from=ts("2026-02-01T00:00:00Z")),
        make_claim("c3", object_value_json='"Stripe"', valid_from=ts("2026-04-01T00:00:00Z")),
    )
    conflicts = find_conflict_candidates(claims, predicate)
    assert [(item.left_claim_id, item.right_claim_id) for item in conflicts] == [
        ("c1", "c2"),
        ("c1", "c3"),
    ]
