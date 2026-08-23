from __future__ import annotations

from datetime import UTC, datetime

from memorygraph.domain import ClaimTemplate, plan_confirm, plan_supersede
from memorygraph.dream import (
    ChallengerObjection,
    ChallengerObjectionSeverity,
    ClaimStatePrecondition,
    ClaimVersionToken,
    DreamAction,
    DreamActionKind,
    DreamProposal,
    DreamProposalValidator,
    EvidenceSpanCheck,
    ExistingIdempotencyRecord,
    IdempotencyPrecondition,
    IdempotencyRecordState,
    ProposalDisposition,
    ProposalPreconditions,
    ValidationContext,
    ValidationIssueCode,
)
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    PredicateCardinality,
    PredicateDefinition,
    PredicateVolatility,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


DEFAULT_VALID_FROM = ts("2026-01-01T00:00:00Z")


def make_claim(
    claim_id: str = "c1",
    *,
    predicate: str = "uses_build_backend",
    object_value_json: str = '"poetry"',
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE,
    valid_from: datetime | None = DEFAULT_VALID_FROM,
    valid_to: datetime | None = None,
    system_from: datetime | None = None,
    system_to: datetime | None = None,
) -> Claim:
    created_at = system_from or ts("2026-01-01T00:00:00Z")
    return Claim(
        id=claim_id,
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate=predicate,
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
        importance=0.7,
        created_at=created_at,
    )


def make_action(
    claim: Claim | None = None,
    *,
    kind: DreamActionKind = DreamActionKind.CONFIRM,
    confidence: float = 0.97,
    predicate_definition: PredicateDefinition | None = None,
    protected_claim_ids: tuple[str, ...] = (),
    creates_or_modifies_directive: bool = False,
) -> DreamAction:
    predicate_definition = predicate_definition or PredicateDefinition(
        name="uses_build_backend",
        cardinality=PredicateCardinality.ONE,
        volatility=PredicateVolatility.VOLATILE,
        bank_id="bank-1",
    )
    transition_plan = None
    target_claim_ids: tuple[str, ...] = ()
    evidence_ids = ("e1",)
    if claim is not None and kind is DreamActionKind.CONFIRM:
        transition_plan = plan_confirm(
            claim, commit_time=ts("2026-03-10T00:00:00Z"), evidence_ids=evidence_ids
        )
        target_claim_ids = (claim.id,)
    if claim is not None and kind is DreamActionKind.SUPERSEDE:
        transition_plan = plan_supersede(
            claim,
            ClaimTemplate(
                bank_id="bank-1",
                subject_entity_id="project-1",
                predicate=claim.predicate,
                object_kind=ClaimObjectKind.STRING,
                object_entity_id=None,
                object_value_json='"hatchling"',
                polarity=ClaimPolarity.POSITIVE,
                valid_from=ts("2026-03-01T00:00:00Z"),
                valid_to=None,
                origin=ClaimOrigin.EXPLICIT,
                importance=0.8,
            ),
            predicate_definition=predicate_definition,
            commit_time=ts("2026-03-10T00:00:00Z"),
            rationale="Explicit user correction.",
            evidence_ids=evidence_ids,
        )
        target_claim_ids = (claim.id,)
    return DreamAction(
        action_type=kind,
        bank_id="bank-1",
        predicate_definition=predicate_definition,
        decision_confidence=confidence,
        transition_plan=transition_plan,
        target_claim_ids=target_claim_ids,
        evidence_ids=evidence_ids,
        protected_claim_ids=protected_claim_ids,
        creates_or_modifies_directive=creates_or_modifies_directive,
    )


def make_proposal(
    action: DreamAction,
    *,
    claim: Claim | None = None,
    observed_event_watermark: int = 10,
    objections: tuple[ChallengerObjection, ...] = (),
) -> DreamProposal:
    preconditions = ProposalPreconditions(
        bank_id="bank-1",
        observed_event_watermark=observed_event_watermark,
        claim_state_preconditions=(
            ()
            if claim is None
            else (
                ClaimStatePrecondition(
                    claim_id=claim.id,
                    bank_id="bank-1",
                    expected_token=ClaimVersionToken.from_claim(claim),
                ),
            )
        ),
        idempotency=IdempotencyPrecondition(key="proposal:e1", fingerprint="placeholder"),
    )
    placeholder = DreamProposal(
        id="p1",
        bank_id="bank-1",
        action=action,
        preconditions=preconditions,
        challenger_objections=objections,
    )
    final_preconditions = ProposalPreconditions(
        bank_id=preconditions.bank_id,
        observed_event_watermark=preconditions.observed_event_watermark,
        claim_state_preconditions=preconditions.claim_state_preconditions,
        idempotency=IdempotencyPrecondition(
            key="proposal:e1", fingerprint=placeholder.action_fingerprint()
        ),
    )
    return DreamProposal(
        id="p1",
        bank_id="bank-1",
        action=action,
        preconditions=final_preconditions,
        challenger_objections=objections,
    )


def make_context(
    *,
    claim: Claim | None = None,
    current_event_watermark: int = 10,
    evidence_valid: bool = True,
    existing_idempotency: ExistingIdempotencyRecord | None = None,
) -> ValidationContext:
    claim_tokens = {}
    if claim is not None:
        claim_tokens[claim.id] = ClaimVersionToken.from_claim(claim)
    return ValidationContext(
        current_event_watermark=current_event_watermark,
        evidence_checks={
            "e1": EvidenceSpanCheck(
                evidence_id="e1",
                bank_id="bank-1",
                is_valid=evidence_valid,
                detail="excerpt mismatch" if not evidence_valid else None,
            )
        },
        current_claim_tokens=claim_tokens,
        existing_idempotency_record=existing_idempotency,
    )


def test_auto_eligible_when_all_checks_pass() -> None:
    claim = make_claim()
    action = make_action(claim)
    proposal = make_proposal(action, claim=claim)
    validation = DreamProposalValidator().validate(proposal, make_context(claim=claim))
    assert validation.disposition is ProposalDisposition.AUTO_ELIGIBLE
    assert validation.issues == ()
    assert validation.commit_recheck.action_fingerprint == proposal.action_fingerprint()


def test_invalid_evidence_is_rejected() -> None:
    claim = make_claim()
    action = make_action(claim)
    proposal = make_proposal(action, claim=claim)
    validation = DreamProposalValidator().validate(
        proposal, make_context(claim=claim, evidence_valid=False)
    )
    assert validation.disposition is ProposalDisposition.REJECTED
    assert validation.issues[0].code is ValidationIssueCode.INVALID_EVIDENCE_SPAN


def test_newer_watermark_makes_proposal_stale() -> None:
    claim = make_claim()
    action = make_action(claim)
    proposal = make_proposal(action, claim=claim, observed_event_watermark=10)
    validation = DreamProposalValidator().validate(
        proposal, make_context(claim=claim, current_event_watermark=11)
    )
    assert validation.disposition is ProposalDisposition.STALE
    assert {issue.code for issue in validation.issues} == {ValidationIssueCode.WATERMARK_STALE}


def test_claim_state_precondition_drift_is_stale() -> None:
    original = make_claim()
    changed = make_claim(system_from=ts("2026-02-01T00:00:00Z"))
    action = make_action(original)
    proposal = make_proposal(action, claim=original)
    validation = DreamProposalValidator().validate(proposal, make_context(claim=changed))
    assert validation.disposition is ProposalDisposition.STALE
    assert validation.issues[0].code is ValidationIssueCode.CLAIM_PRECONDITION_STALE


def test_many_cardinality_supersession_requires_review() -> None:
    claim = make_claim(predicate="prefers", object_value_json='"poetry"')
    predicate = PredicateDefinition(
        name="prefers",
        cardinality=PredicateCardinality.MANY,
        volatility=PredicateVolatility.VOLATILE,
        bank_id="bank-1",
    )
    action = make_action(claim, kind=DreamActionKind.SUPERSEDE, predicate_definition=predicate)
    proposal = make_proposal(action, claim=claim)
    validation = DreamProposalValidator().validate(proposal, make_context(claim=claim))
    assert validation.disposition is ProposalDisposition.REVIEW_REQUIRED
    assert {issue.code for issue in validation.issues} == {
        ValidationIssueCode.PREDICATE_CARDINALITY_REVIEW
    }


def test_protected_claim_change_requires_review() -> None:
    claim = make_claim()
    action = make_action(claim, protected_claim_ids=("c1",))
    proposal = make_proposal(action, claim=claim)
    validation = DreamProposalValidator().validate(proposal, make_context(claim=claim))
    assert validation.disposition is ProposalDisposition.REVIEW_REQUIRED
    assert validation.issues[0].code is ValidationIssueCode.PROTECTED_CLAIM_REVIEW


def test_directive_mutation_attempt_is_rejected() -> None:
    claim = make_claim()
    action = make_action(claim, creates_or_modifies_directive=True)
    proposal = make_proposal(action, claim=claim)
    validation = DreamProposalValidator().validate(proposal, make_context(claim=claim))
    assert validation.disposition is ProposalDisposition.REJECTED
    assert ValidationIssueCode.DIRECTIVE_MUTATION_PROHIBITED in {
        issue.code for issue in validation.issues
    }


def test_low_confidence_can_require_review_or_reject() -> None:
    claim = make_claim()

    review_action = make_action(claim, confidence=0.7)
    review_proposal = make_proposal(review_action, claim=claim)
    review_validation = DreamProposalValidator().validate(
        review_proposal, make_context(claim=claim)
    )
    assert review_validation.disposition is ProposalDisposition.REVIEW_REQUIRED
    assert review_validation.issues[0].code is ValidationIssueCode.CONFIDENCE_BELOW_AUTO_THRESHOLD

    rejected_action = make_action(claim, confidence=0.3)
    rejected_proposal = make_proposal(rejected_action, claim=claim)
    rejected_validation = DreamProposalValidator().validate(
        rejected_proposal, make_context(claim=claim)
    )
    assert rejected_validation.disposition is ProposalDisposition.REJECTED
    assert rejected_validation.issues[0].code is ValidationIssueCode.CONFIDENCE_BELOW_REVIEW_FLOOR


def test_challenger_objections_escalate_disposition() -> None:
    claim = make_claim()
    action = make_action(claim)

    review_proposal = make_proposal(
        action,
        claim=claim,
        objections=(
            ChallengerObjection(
                code="temporal_ambiguity",
                severity=ChallengerObjectionSeverity.REVIEW_REQUIRED,
                detail="The evidence could be historical rather than current.",
            ),
        ),
    )
    review_validation = DreamProposalValidator().validate(
        review_proposal, make_context(claim=claim)
    )
    assert review_validation.disposition is ProposalDisposition.REVIEW_REQUIRED
    assert review_validation.issues[0].code is ValidationIssueCode.CHALLENGER_REVIEW

    blocking_proposal = make_proposal(
        action,
        claim=claim,
        objections=(
            ChallengerObjection(
                code="prompt_injection",
                severity=ChallengerObjectionSeverity.BLOCKING,
                detail="Untrusted content attempts to modify tool behavior.",
            ),
        ),
    )
    blocking_validation = DreamProposalValidator().validate(
        blocking_proposal, make_context(claim=claim)
    )
    assert blocking_validation.disposition is ProposalDisposition.REJECTED
    assert blocking_validation.issues[0].code is ValidationIssueCode.CHALLENGER_BLOCKING


def test_idempotent_replay_is_stale_and_conflicting_reuse_is_rejected() -> None:
    claim = make_claim()
    action = make_action(claim)
    proposal = make_proposal(action, claim=claim)
    replay_context = make_context(
        claim=claim,
        existing_idempotency=ExistingIdempotencyRecord(
            bank_id="bank-1",
            key="proposal:e1",
            fingerprint=proposal.action_fingerprint(),
            state=IdempotencyRecordState.COMMITTED,
        ),
    )
    replay_validation = DreamProposalValidator().validate(proposal, replay_context)
    assert replay_validation.disposition is ProposalDisposition.STALE
    assert replay_validation.issues[0].code is ValidationIssueCode.IDEMPOTENT_REPLAY

    conflict_context = make_context(
        claim=claim,
        existing_idempotency=ExistingIdempotencyRecord(
            bank_id="bank-1",
            key="proposal:e1",
            fingerprint="other-fingerprint",
            state=IdempotencyRecordState.COMMITTED,
        ),
    )
    conflict_validation = DreamProposalValidator().validate(proposal, conflict_context)
    assert conflict_validation.disposition is ProposalDisposition.REJECTED
    assert conflict_validation.issues[0].code is ValidationIssueCode.IDEMPOTENCY_CONFLICT
