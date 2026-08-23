from __future__ import annotations

from datetime import UTC, datetime

from memorygraph.domain import plan_confirm
from memorygraph.dream import (
    ClaimStatePrecondition,
    ClaimVersionToken,
    DreamAction,
    DreamActionKind,
    DreamProposal,
    IdempotencyPrecondition,
    ProposalPreconditions,
    fingerprint_for_value,
)
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    PredicateCardinality,
    PredicateDefinition,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def make_claim() -> Claim:
    created_at = ts("2026-01-01T00:00:00Z")
    return Claim(
        id="c1",
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate="uses_build_backend",
        object_kind=ClaimObjectKind.STRING,
        object_entity_id=None,
        object_value_json='"poetry"',
        polarity=ClaimPolarity.POSITIVE,
        valid_from=created_at,
        valid_to=None,
        system_from=created_at,
        system_to=None,
        lifecycle=ClaimLifecycle.ACTIVE,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.5,
        created_at=created_at,
    )


def test_action_referenced_ids_include_transition_plan_references() -> None:
    claim = make_claim()
    action = DreamAction(
        action_type=DreamActionKind.CONFIRM,
        bank_id="bank-1",
        predicate_definition=PredicateDefinition(
            name="uses_build_backend", cardinality=PredicateCardinality.ONE
        ),
        decision_confidence=0.95,
        transition_plan=plan_confirm(
            claim, commit_time=ts("2026-03-01T00:00:00Z"), evidence_ids=("e1", "e2")
        ),
        target_claim_ids=(claim.id,),
    )
    assert action.referenced_claim_ids() == ("c1",)
    assert action.referenced_evidence_ids() == ("e1", "e2")


def test_proposal_action_fingerprint_is_stable() -> None:
    claim = make_claim()
    action = DreamAction(
        action_type=DreamActionKind.CONFIRM,
        bank_id="bank-1",
        predicate_definition=PredicateDefinition(
            name="uses_build_backend", cardinality=PredicateCardinality.ONE
        ),
        decision_confidence=0.95,
        transition_plan=plan_confirm(
            claim, commit_time=ts("2026-03-01T00:00:00Z"), evidence_ids=("e1",)
        ),
        target_claim_ids=(claim.id,),
    )
    token = ClaimVersionToken.from_claim(claim)
    proposal = DreamProposal(
        id="p1",
        bank_id="bank-1",
        action=action,
        preconditions=ProposalPreconditions(
            bank_id="bank-1",
            observed_event_watermark=1,
            claim_state_preconditions=(
                ClaimStatePrecondition(claim_id="c1", bank_id="bank-1", expected_token=token),
            ),
            idempotency=IdempotencyPrecondition(key="proposal:e1", fingerprint="placeholder"),
        ),
    )
    assert proposal.action_fingerprint() == proposal.action_fingerprint()


def test_fingerprint_normalizes_unordered_sets() -> None:
    forward = {"alpha", "beta", "gamma"}
    reverse = set(reversed(("alpha", "beta", "gamma")))
    assert fingerprint_for_value(forward) == fingerprint_for_value(reverse)
