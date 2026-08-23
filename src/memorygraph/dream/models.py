from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from memorygraph.domain import TransitionPlan
from memorygraph.models import Claim, ClaimLifecycle, PredicateDefinition


class DreamActionKind(StrEnum):
    ASSERT = "assert"
    DUPLICATE = "duplicate"
    CONFIRM = "confirm"
    SUPERSEDE = "supersede"
    CONTRADICT = "contradict"
    HISTORICAL_BACKFILL = "historical_backfill"
    RETRACT = "retract"


class ProposalDisposition(StrEnum):
    PENDING = "pending"
    AUTO_ELIGIBLE = "auto_eligible"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMMITTED = "committed"
    STALE = "stale"


class ChallengerObjectionSeverity(StrEnum):
    WARNING = "warning"
    REVIEW_REQUIRED = "review_required"
    BLOCKING = "blocking"


class IdempotencyRecordState(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"


class ValidationIssueSeverity(StrEnum):
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"
    STALE = "stale"


class ValidationIssueCode(StrEnum):
    BANK_SCOPE_MISMATCH = "bank_scope_mismatch"
    MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"
    MISSING_CLAIM_PRECONDITION = "missing_claim_precondition"
    INVALID_EVIDENCE_SPAN = "invalid_evidence_span"
    MISSING_EVIDENCE_CHECK = "missing_evidence_check"
    PREDICATE_CARDINALITY_REVIEW = "predicate_cardinality_review"
    PROTECTED_CLAIM_REVIEW = "protected_claim_review"
    DIRECTIVE_MUTATION_PROHIBITED = "directive_mutation_prohibited"
    CONFIDENCE_BELOW_REVIEW_FLOOR = "confidence_below_review_floor"
    CONFIDENCE_BELOW_AUTO_THRESHOLD = "confidence_below_auto_threshold"
    CHALLENGER_REVIEW = "challenger_review"
    CHALLENGER_BLOCKING = "challenger_blocking"
    WATERMARK_STALE = "watermark_stale"
    WATERMARK_REGRESSION = "watermark_regression"
    CLAIM_PRECONDITION_STALE = "claim_precondition_stale"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_RESERVED = "idempotency_reserved"


@dataclass(frozen=True, slots=True)
class ClaimVersionToken:
    claim_id: str
    bank_id: str
    subject_entity_id: str
    predicate: str
    lifecycle: ClaimLifecycle
    system_from: datetime
    system_to: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    content_signature: tuple[Any, ...]

    @classmethod
    def from_claim(cls, claim: Claim) -> ClaimVersionToken:
        return cls(
            claim_id=claim.id,
            bank_id=claim.bank_id,
            subject_entity_id=claim.subject_entity_id,
            predicate=claim.predicate,
            lifecycle=claim.lifecycle,
            system_from=claim.system_from,
            system_to=claim.system_to,
            valid_from=claim.valid_from,
            valid_to=claim.valid_to,
            content_signature=claim.content_signature,
        )


@dataclass(frozen=True, slots=True)
class ClaimStatePrecondition:
    claim_id: str
    bank_id: str
    expected_token: ClaimVersionToken | None
    must_exist: bool = True

    def __post_init__(self) -> None:
        if self.expected_token is not None:
            if self.expected_token.claim_id != self.claim_id:
                raise ValueError("claim state precondition claim_id must match expected token")
            if self.expected_token.bank_id != self.bank_id:
                raise ValueError("claim state precondition bank_id must match expected token")


@dataclass(frozen=True, slots=True)
class IdempotencyPrecondition:
    key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ProposalPreconditions:
    bank_id: str
    observed_event_watermark: int
    claim_state_preconditions: tuple[ClaimStatePrecondition, ...] = ()
    idempotency: IdempotencyPrecondition | None = None

    def __post_init__(self) -> None:
        if self.observed_event_watermark < 0:
            raise ValueError("observed_event_watermark must be non-negative")

    def claim_preconditions_by_id(self) -> dict[str, ClaimStatePrecondition]:
        return {item.claim_id: item for item in self.claim_state_preconditions}


@dataclass(frozen=True, slots=True)
class EvidenceSpanCheck:
    evidence_id: str
    bank_id: str
    is_valid: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ChallengerObjection:
    code: str
    severity: ChallengerObjectionSeverity
    detail: str


@dataclass(frozen=True, slots=True)
class ExistingIdempotencyRecord:
    bank_id: str
    key: str
    fingerprint: str
    state: IdempotencyRecordState


@dataclass(frozen=True, slots=True)
class DreamAction:
    action_type: DreamActionKind
    bank_id: str
    predicate_definition: PredicateDefinition
    decision_confidence: float
    transition_plan: TransitionPlan | None = None
    target_claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    protected_claim_ids: tuple[str, ...] = ()
    rationale: str = ""
    creates_or_modifies_directive: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.decision_confidence <= 1.0:
            raise ValueError("decision_confidence must be between 0 and 1")

    def referenced_claim_ids(self) -> tuple[str, ...]:
        claim_ids = list(self.target_claim_ids)
        if self.transition_plan is not None:
            claim_ids.extend(closure.claim_id for closure in self.transition_plan.closures)
            for attachment in self.transition_plan.evidence_attachments:
                if attachment.target.claim_id is not None:
                    claim_ids.append(attachment.target.claim_id)
            for relation in self.transition_plan.relations:
                if relation.from_claim.claim_id is not None:
                    claim_ids.append(relation.from_claim.claim_id)
                if relation.to_claim.claim_id is not None:
                    claim_ids.append(relation.to_claim.claim_id)
        return tuple(dict.fromkeys(claim_ids))

    def referenced_evidence_ids(self) -> tuple[str, ...]:
        evidence_ids = list(self.evidence_ids)
        if self.transition_plan is not None:
            for attachment in self.transition_plan.evidence_attachments:
                evidence_ids.extend(attachment.evidence_ids)
        return tuple(dict.fromkeys(evidence_ids))


@dataclass(frozen=True, slots=True)
class DreamProposal:
    id: str
    bank_id: str
    action: DreamAction
    preconditions: ProposalPreconditions
    challenger_objections: tuple[ChallengerObjection, ...] = ()
    disposition: ProposalDisposition = ProposalDisposition.PENDING
    created_at: datetime | None = None

    def action_fingerprint(self) -> str:
        return fingerprint_for_value(self.action)


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    auto_commit_min_confidence: float = 0.9
    review_confidence_floor: float = 0.5
    require_idempotency: bool = True
    stale_on_watermark_advance: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.review_confidence_floor <= 1.0:
            raise ValueError("review_confidence_floor must be between 0 and 1")
        if not 0.0 <= self.auto_commit_min_confidence <= 1.0:
            raise ValueError("auto_commit_min_confidence must be between 0 and 1")
        if self.review_confidence_floor > self.auto_commit_min_confidence:
            raise ValueError("review_confidence_floor cannot exceed auto_commit_min_confidence")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ValidationIssueCode
    severity: ValidationIssueSeverity
    message: str
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommitRecheckContract:
    bank_id: str
    observed_event_watermark: int
    claim_state_preconditions: tuple[ClaimStatePrecondition, ...]
    idempotency: IdempotencyPrecondition | None
    action_fingerprint: str


@dataclass(frozen=True, slots=True)
class ValidationContext:
    current_event_watermark: int
    evidence_checks: Mapping[str, EvidenceSpanCheck]
    current_claim_tokens: Mapping[str, ClaimVersionToken]
    existing_idempotency_record: ExistingIdempotencyRecord | None = None

    def __post_init__(self) -> None:
        if self.current_event_watermark < 0:
            raise ValueError("current_event_watermark must be non-negative")


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    proposal_id: str
    disposition: ProposalDisposition
    issues: tuple[ValidationIssue, ...]
    commit_recheck: CommitRecheckContract


def build_commit_recheck_contract(proposal: DreamProposal) -> CommitRecheckContract:
    return CommitRecheckContract(
        bank_id=proposal.bank_id,
        observed_event_watermark=proposal.preconditions.observed_event_watermark,
        claim_state_preconditions=proposal.preconditions.claim_state_preconditions,
        idempotency=proposal.preconditions.idempotency,
        action_fingerprint=proposal.action_fingerprint(),
    )


def fingerprint_for_value(value: Any) -> str:
    encoded = json.dumps(_normalize_for_json(value), sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_for_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        normalized: dict[str, Any] = {}
        for field in fields(value):
            normalized[field.name] = _normalize_for_json(getattr(value, field.name))
        return normalized
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_for_json(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, set):
        normalized_items = [_normalize_for_json(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, (tuple, list)):
        return [_normalize_for_json(item) for item in value]
    return value
