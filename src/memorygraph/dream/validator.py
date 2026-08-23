from __future__ import annotations

from dataclasses import dataclass

from memorygraph.models import PredicateCardinality

from .models import (
    ChallengerObjectionSeverity,
    DreamAction,
    DreamActionKind,
    DreamProposal,
    ExistingIdempotencyRecord,
    ProposalDisposition,
    ProposalValidation,
    ValidationContext,
    ValidationIssue,
    ValidationIssueCode,
    ValidationIssueSeverity,
    ValidationPolicy,
    build_commit_recheck_contract,
)


@dataclass(slots=True)
class DreamProposalValidator:
    policy: ValidationPolicy = ValidationPolicy()

    def validate(self, proposal: DreamProposal, context: ValidationContext) -> ProposalValidation:
        issues: list[ValidationIssue] = []

        self._validate_bank_scope(proposal, context, issues)
        self._validate_precondition_contract(proposal, issues)
        self._validate_evidence_checks(proposal.action, proposal.bank_id, context, issues)
        self._validate_watermark(proposal, context, issues)
        self._validate_claim_preconditions(proposal, context, issues)
        self._validate_idempotency(proposal, context.existing_idempotency_record, issues)
        self._validate_predicate_cardinality(proposal.action, issues)
        self._validate_protected_and_directive_constraints(proposal.action, issues)
        self._validate_confidence(proposal.action, issues)
        self._validate_challenger_objections(proposal, issues)

        disposition = _choose_disposition(issues)
        return ProposalValidation(
            proposal_id=proposal.id,
            disposition=disposition,
            issues=tuple(issues),
            commit_recheck=build_commit_recheck_contract(proposal),
        )

    def _validate_bank_scope(
        self,
        proposal: DreamProposal,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        if proposal.action.bank_id != proposal.bank_id:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="proposal action bank_id must match proposal bank_id",
                )
            )
        if proposal.preconditions.bank_id != proposal.bank_id:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="proposal preconditions bank_id must match proposal bank_id",
                )
            )
        predicate_bank_id = proposal.action.predicate_definition.bank_id
        if predicate_bank_id is not None and predicate_bank_id != proposal.bank_id:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                    severity=ValidationIssueSeverity.REJECTED,
                    message=(
                        "predicate definition bank_id must be global or match the proposal bank"
                    ),
                )
            )
        for item in proposal.preconditions.claim_state_preconditions:
            if item.bank_id != proposal.bank_id:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                        severity=ValidationIssueSeverity.REJECTED,
                        message="claim state preconditions cannot cross banks",
                        related_ids=(item.claim_id,),
                    )
                )
        if (
            context.existing_idempotency_record is not None
            and context.existing_idempotency_record.bank_id != proposal.bank_id
        ):
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="idempotency records cannot cross banks",
                )
            )
        if proposal.action.transition_plan is not None:
            for draft_claim in proposal.action.transition_plan.draft_claims:
                if draft_claim.bank_id != proposal.bank_id:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                            severity=ValidationIssueSeverity.REJECTED,
                            message="transition plan draft claims cannot cross banks",
                            related_ids=(draft_claim.ref,),
                        )
                    )

    def _validate_precondition_contract(
        self, proposal: DreamProposal, issues: list[ValidationIssue]
    ) -> None:
        referenced_claim_ids = set(proposal.action.referenced_claim_ids())
        precondition_claim_ids = set(proposal.preconditions.claim_preconditions_by_id())
        missing_claim_ids = sorted(referenced_claim_ids - precondition_claim_ids)
        if missing_claim_ids:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.MISSING_CLAIM_PRECONDITION,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="every referenced persisted claim must carry a precondition token",
                    related_ids=tuple(missing_claim_ids),
                )
            )
        if self.policy.require_idempotency and proposal.preconditions.idempotency is None:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.MISSING_IDEMPOTENCY_KEY,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="dream proposals must carry an idempotency key and fingerprint",
                )
            )
        if proposal.preconditions.idempotency is not None:
            expected = proposal.action_fingerprint()
            actual = proposal.preconditions.idempotency.fingerprint
            if actual != expected:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.IDEMPOTENCY_CONFLICT,
                        severity=ValidationIssueSeverity.REJECTED,
                        message=(
                            "proposal idempotency fingerprint does not match the action fingerprint"
                        ),
                    )
                )

    def _validate_evidence_checks(
        self,
        action: DreamAction,
        bank_id: str,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        for evidence_id in action.referenced_evidence_ids():
            check = context.evidence_checks.get(evidence_id)
            if check is None:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.MISSING_EVIDENCE_CHECK,
                        severity=ValidationIssueSeverity.REJECTED,
                        message=(
                            "referenced evidence requires a deterministic integrity check result"
                        ),
                        related_ids=(evidence_id,),
                    )
                )
                continue
            if check.bank_id != bank_id:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.BANK_SCOPE_MISMATCH,
                        severity=ValidationIssueSeverity.REJECTED,
                        message="evidence integrity checks cannot cross banks",
                        related_ids=(evidence_id,),
                    )
                )
                continue
            if not check.is_valid:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.INVALID_EVIDENCE_SPAN,
                        severity=ValidationIssueSeverity.REJECTED,
                        message=check.detail or "evidence integrity validation failed",
                        related_ids=(evidence_id,),
                    )
                )

    def _validate_watermark(
        self,
        proposal: DreamProposal,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        observed = proposal.preconditions.observed_event_watermark
        current = context.current_event_watermark
        if current < observed:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.WATERMARK_REGRESSION,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="current event watermark cannot be earlier than the proposal watermark",
                )
            )
            return
        if self.policy.stale_on_watermark_advance and current > observed:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.WATERMARK_STALE,
                    severity=ValidationIssueSeverity.STALE,
                    message="newer committed events make the proposal stale",
                )
            )

    def _validate_claim_preconditions(
        self,
        proposal: DreamProposal,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        for precondition in proposal.preconditions.claim_state_preconditions:
            current = context.current_claim_tokens.get(precondition.claim_id)
            if precondition.must_exist:
                if current is None or current != precondition.expected_token:
                    issues.append(
                        ValidationIssue(
                            code=ValidationIssueCode.CLAIM_PRECONDITION_STALE,
                            severity=ValidationIssueSeverity.STALE,
                            message="claim state no longer matches the proposal precondition",
                            related_ids=(precondition.claim_id,),
                        )
                    )
            elif current is not None:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.CLAIM_PRECONDITION_STALE,
                        severity=ValidationIssueSeverity.STALE,
                        message="proposal expected a claim to remain absent, but it now exists",
                        related_ids=(precondition.claim_id,),
                    )
                )

    def _validate_idempotency(
        self,
        proposal: DreamProposal,
        record: ExistingIdempotencyRecord | None,
        issues: list[ValidationIssue],
    ) -> None:
        idempotency = proposal.preconditions.idempotency
        if idempotency is None or record is None:
            return
        if record.key != idempotency.key:
            return
        if record.fingerprint != idempotency.fingerprint:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.IDEMPOTENCY_CONFLICT,
                    severity=ValidationIssueSeverity.REJECTED,
                    message=(
                        "an existing idempotency key is bound to a different action fingerprint"
                    ),
                    related_ids=(record.key,),
                )
            )
            return
        if record.state.value == "committed":
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.IDEMPOTENT_REPLAY,
                    severity=ValidationIssueSeverity.STALE,
                    message="the same idempotent action has already been committed",
                    related_ids=(record.key,),
                )
            )
            return
        issues.append(
            ValidationIssue(
                code=ValidationIssueCode.IDEMPOTENCY_RESERVED,
                severity=ValidationIssueSeverity.STALE,
                message=(
                    "the same idempotency key is already reserved by another in-flight mutation"
                ),
                related_ids=(record.key,),
            )
        )

    def _validate_predicate_cardinality(
        self, action: DreamAction, issues: list[ValidationIssue]
    ) -> None:
        if (
            action.action_type is DreamActionKind.SUPERSEDE
            and action.predicate_definition.cardinality is not PredicateCardinality.ONE
        ):
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.PREDICATE_CARDINALITY_REVIEW,
                    severity=ValidationIssueSeverity.REVIEW_REQUIRED,
                    message=(
                        "only single-cardinality predicates are automatically eligible for "
                        "supersession"
                    ),
                )
            )

    def _validate_protected_and_directive_constraints(
        self,
        action: DreamAction,
        issues: list[ValidationIssue],
    ) -> None:
        if action.creates_or_modifies_directive:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.DIRECTIVE_MUTATION_PROHIBITED,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="dream proposals cannot create or modify privileged directives",
                )
            )
        if action.protected_claim_ids or action.predicate_definition.sensitivity == "protected":
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.PROTECTED_CLAIM_REVIEW,
                    severity=ValidationIssueSeverity.REVIEW_REQUIRED,
                    message="changes touching protected claims or predicates require review",
                    related_ids=tuple(action.protected_claim_ids),
                )
            )

    def _validate_confidence(self, action: DreamAction, issues: list[ValidationIssue]) -> None:
        if action.decision_confidence < self.policy.review_confidence_floor:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.CONFIDENCE_BELOW_REVIEW_FLOOR,
                    severity=ValidationIssueSeverity.REJECTED,
                    message="decision confidence is below the minimum review floor",
                )
            )
            return
        if action.decision_confidence < self.policy.auto_commit_min_confidence:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.CONFIDENCE_BELOW_AUTO_THRESHOLD,
                    severity=ValidationIssueSeverity.REVIEW_REQUIRED,
                    message="decision confidence is below the automatic commit threshold",
                )
            )

    def _validate_challenger_objections(
        self, proposal: DreamProposal, issues: list[ValidationIssue]
    ) -> None:
        for objection in proposal.challenger_objections:
            if objection.severity is ChallengerObjectionSeverity.WARNING:
                continue
            if objection.severity is ChallengerObjectionSeverity.BLOCKING:
                issues.append(
                    ValidationIssue(
                        code=ValidationIssueCode.CHALLENGER_BLOCKING,
                        severity=ValidationIssueSeverity.REJECTED,
                        message=objection.detail,
                    )
                )
                continue
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.CHALLENGER_REVIEW,
                    severity=ValidationIssueSeverity.REVIEW_REQUIRED,
                    message=objection.detail,
                )
            )


def _choose_disposition(issues: list[ValidationIssue]) -> ProposalDisposition:
    severities = {issue.severity for issue in issues}
    if ValidationIssueSeverity.REJECTED in severities:
        return ProposalDisposition.REJECTED
    if ValidationIssueSeverity.STALE in severities:
        return ProposalDisposition.STALE
    if ValidationIssueSeverity.REVIEW_REQUIRED in severities:
        return ProposalDisposition.REVIEW_REQUIRED
    return ProposalDisposition.AUTO_ELIGIBLE
