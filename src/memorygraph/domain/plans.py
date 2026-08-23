from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    ClaimRelationKind,
    DecisionMethod,
    HalfOpenInterval,
    PredicateCardinality,
    PredicateDefinition,
)


@dataclass(frozen=True, slots=True)
class ClaimTemplate:
    bank_id: str
    subject_entity_id: str
    predicate: str
    object_kind: ClaimObjectKind
    object_entity_id: str | None
    object_value_json: str | None
    polarity: ClaimPolarity
    valid_from: datetime | None
    valid_to: datetime | None
    origin: ClaimOrigin
    importance: float
    created_by_run_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        HalfOpenInterval(self.valid_from, self.valid_to)
        if self.object_kind is ClaimObjectKind.ENTITY:
            if self.object_entity_id is None or self.object_value_json is not None:
                raise ValueError(
                    "entity-valued templates require object_entity_id and no object_value_json"
                )
        elif self.object_entity_id is not None or self.object_value_json is None:
            raise ValueError(
                "non-entity templates require object_value_json and no object_entity_id"
            )


@dataclass(frozen=True, slots=True)
class DraftClaim:
    ref: str
    bank_id: str
    subject_entity_id: str
    predicate: str
    object_kind: ClaimObjectKind
    object_entity_id: str | None
    object_value_json: str | None
    polarity: ClaimPolarity
    valid_from: datetime | None
    valid_to: datetime | None
    system_from: datetime
    system_to: datetime | None
    lifecycle: ClaimLifecycle
    origin: ClaimOrigin
    importance: float
    created_at: datetime
    created_by_run_id: str | None = None

    def __post_init__(self) -> None:
        HalfOpenInterval(self.valid_from, self.valid_to)
        HalfOpenInterval(self.system_from, self.system_to)


@dataclass(frozen=True, slots=True)
class ClaimHandle:
    claim_id: str | None = None
    draft_ref: str | None = None

    def __post_init__(self) -> None:
        selected = int(self.claim_id is not None) + int(self.draft_ref is not None)
        if selected != 1:
            raise ValueError("claim handles must reference exactly one existing id or draft ref")


@dataclass(frozen=True, slots=True)
class ClaimClosure:
    claim_id: str
    system_to: datetime


@dataclass(frozen=True, slots=True)
class PlannedEvidenceAttachment:
    target: ClaimHandle
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannedRelation:
    from_claim: ClaimHandle
    to_claim: ClaimHandle
    relation: ClaimRelationKind
    rationale: str
    decision_method: DecisionMethod
    decision_confidence: float


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    operation: str
    closures: tuple[ClaimClosure, ...]
    draft_claims: tuple[DraftClaim, ...]
    evidence_attachments: tuple[PlannedEvidenceAttachment, ...]
    relations: tuple[PlannedRelation, ...]
    warnings: tuple[str, ...] = ()


def plan_confirm(
    target: Claim,
    *,
    commit_time: datetime,
    evidence_ids: Sequence[str],
    reactivate_contested: bool = False,
    successor_ref: str = "confirmed",
) -> TransitionPlan:
    if target.lifecycle in {ClaimLifecycle.RETRACTED, ClaimLifecycle.SUPERSEDED}:
        raise ValueError("only active or contested claims can be confirmed")

    evidence_tuple = tuple(evidence_ids)
    if reactivate_contested and target.lifecycle is ClaimLifecycle.CONTESTED:
        successor = _copy_claim_to_draft(
            target,
            ref=successor_ref,
            lifecycle=ClaimLifecycle.ACTIVE,
            system_from=commit_time,
            system_to=None,
            created_at=commit_time,
        )
        return TransitionPlan(
            operation="confirm",
            closures=(ClaimClosure(claim_id=target.id, system_to=commit_time),),
            draft_claims=(successor,),
            evidence_attachments=(
                PlannedEvidenceAttachment(
                    target=ClaimHandle(draft_ref=successor.ref),
                    evidence_ids=evidence_tuple,
                ),
            ),
            relations=(),
        )

    return TransitionPlan(
        operation="confirm",
        closures=(),
        draft_claims=(),
        evidence_attachments=(
            PlannedEvidenceAttachment(
                target=ClaimHandle(claim_id=target.id), evidence_ids=evidence_tuple
            ),
        ),
        relations=(),
    )


def plan_supersede(
    current: Claim,
    replacement: ClaimTemplate,
    *,
    predicate_definition: PredicateDefinition,
    commit_time: datetime,
    rationale: str,
    evidence_ids: Sequence[str],
    retired_ref: str = "retired",
    replacement_ref: str = "replacement",
    decision_method: DecisionMethod = DecisionMethod.EXPLICIT,
    decision_confidence: float = 1.0,
) -> TransitionPlan:
    _require_transitionable(current, "superseded")
    _require_same_slot(current, replacement)
    if current.content_signature[:5] == _template_content_signature(replacement)[:5]:
        raise ValueError("supersession requires materially different claim content")

    warnings: list[str] = []
    if predicate_definition.cardinality is PredicateCardinality.MANY:
        warnings.append(
            "multi-valued predicates do not auto-supersede; this plan requires explicit approval"
        )

    retired_valid_to = current.valid_to
    if replacement.valid_from is not None:
        retired_valid_to = replacement.valid_from
    elif current.valid_to is None:
        warnings.append("replacement has unknown valid_from; retiring claim keeps unknown valid_to")

    retired = _copy_claim_to_draft(
        current,
        ref=retired_ref,
        lifecycle=ClaimLifecycle.SUPERSEDED,
        system_from=commit_time,
        system_to=None,
        created_at=commit_time,
        valid_to=retired_valid_to,
    )
    new_claim = _template_to_draft(
        replacement,
        ref=replacement_ref,
        lifecycle=ClaimLifecycle.ACTIVE,
        system_from=commit_time,
        created_at=commit_time,
    )
    return TransitionPlan(
        operation="supersede",
        closures=(ClaimClosure(claim_id=current.id, system_to=commit_time),),
        draft_claims=(retired, new_claim),
        evidence_attachments=(
            PlannedEvidenceAttachment(
                target=ClaimHandle(draft_ref=new_claim.ref),
                evidence_ids=tuple(evidence_ids),
            ),
        ),
        relations=(
            PlannedRelation(
                from_claim=ClaimHandle(draft_ref=new_claim.ref),
                to_claim=ClaimHandle(draft_ref=retired.ref),
                relation=ClaimRelationKind.SUPERSEDES,
                rationale=rationale,
                decision_method=decision_method,
                decision_confidence=decision_confidence,
            ),
        ),
        warnings=tuple(warnings),
    )


def plan_contradict(
    current: Claim,
    contradiction: ClaimTemplate,
    *,
    commit_time: datetime,
    rationale: str,
    evidence_ids: Sequence[str],
    existing_ref: str = "existing_contested",
    contradiction_ref: str = "contradiction",
    decision_method: DecisionMethod = DecisionMethod.EXPLICIT,
    decision_confidence: float = 1.0,
) -> TransitionPlan:
    _require_transitionable(current, "contested")
    _require_same_slot(current, contradiction)
    if (
        current.object_signature == _template_object_signature(contradiction)
        and current.polarity == contradiction.polarity
    ):
        raise ValueError("contradiction requires different object and/or polarity")

    closures: list[ClaimClosure] = []
    draft_claims: list[DraftClaim] = []
    if current.lifecycle is ClaimLifecycle.ACTIVE:
        closures.append(ClaimClosure(claim_id=current.id, system_to=commit_time))
        existing_target = ClaimHandle(draft_ref=existing_ref)
        draft_claims.append(
            _copy_claim_to_draft(
                current,
                ref=existing_ref,
                lifecycle=ClaimLifecycle.CONTESTED,
                system_from=commit_time,
                system_to=None,
                created_at=commit_time,
            )
        )
    else:
        existing_target = ClaimHandle(claim_id=current.id)

    contradictory_claim = _template_to_draft(
        contradiction,
        ref=contradiction_ref,
        lifecycle=ClaimLifecycle.CONTESTED,
        system_from=commit_time,
        created_at=commit_time,
    )
    draft_claims.append(contradictory_claim)
    return TransitionPlan(
        operation="contradict",
        closures=tuple(closures),
        draft_claims=tuple(draft_claims),
        evidence_attachments=(
            PlannedEvidenceAttachment(
                target=ClaimHandle(draft_ref=contradictory_claim.ref),
                evidence_ids=tuple(evidence_ids),
            ),
        ),
        relations=(
            PlannedRelation(
                from_claim=ClaimHandle(draft_ref=contradictory_claim.ref),
                to_claim=existing_target,
                relation=ClaimRelationKind.CONTRADICTS,
                rationale=rationale,
                decision_method=decision_method,
                decision_confidence=decision_confidence,
            ),
        ),
    )


def plan_retract(
    target: Claim,
    *,
    commit_time: datetime,
    evidence_ids: Sequence[str],
    successor_ref: str = "retracted",
    replacement_valid_interval: HalfOpenInterval | None = None,
) -> TransitionPlan:
    _require_transitionable(target, "retracted")
    valid_from = (
        replacement_valid_interval.start
        if replacement_valid_interval is not None
        else target.valid_from
    )
    valid_to = (
        replacement_valid_interval.end
        if replacement_valid_interval is not None
        else target.valid_to
    )
    successor = _copy_claim_to_draft(
        target,
        ref=successor_ref,
        lifecycle=ClaimLifecycle.RETRACTED,
        system_from=commit_time,
        system_to=None,
        created_at=commit_time,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    return TransitionPlan(
        operation="retract",
        closures=(ClaimClosure(claim_id=target.id, system_to=commit_time),),
        draft_claims=(successor,),
        evidence_attachments=(
            PlannedEvidenceAttachment(
                target=ClaimHandle(draft_ref=successor.ref),
                evidence_ids=tuple(evidence_ids),
            ),
        ),
        relations=(),
    )


def _require_transitionable(claim: Claim, target_lifecycle: str) -> None:
    if claim.lifecycle not in {ClaimLifecycle.ACTIVE, ClaimLifecycle.CONTESTED}:
        raise ValueError(f"cannot transition {claim.lifecycle.value} claims to {target_lifecycle}")


def _require_same_slot(claim: Claim, template: ClaimTemplate) -> None:
    if claim.bank_id != template.bank_id:
        raise ValueError("claims must remain in the same bank")
    if (
        claim.subject_entity_id != template.subject_entity_id
        or claim.predicate != template.predicate
    ):
        raise ValueError("transitions require the same subject/predicate slot")


def _copy_claim_to_draft(
    claim: Claim,
    *,
    ref: str,
    lifecycle: ClaimLifecycle,
    system_from: datetime,
    system_to: datetime | None,
    created_at: datetime,
    valid_from: datetime | None | object = ...,
    valid_to: datetime | None | object = ...,
) -> DraftClaim:
    new_valid_from = claim.valid_from if valid_from is ... else valid_from
    new_valid_to = claim.valid_to if valid_to is ... else valid_to
    return DraftClaim(
        ref=ref,
        bank_id=claim.bank_id,
        subject_entity_id=claim.subject_entity_id,
        predicate=claim.predicate,
        object_kind=claim.object_kind,
        object_entity_id=claim.object_entity_id,
        object_value_json=claim.object_value_json,
        polarity=claim.polarity,
        valid_from=new_valid_from,
        valid_to=new_valid_to,
        system_from=system_from,
        system_to=system_to,
        lifecycle=lifecycle,
        origin=claim.origin,
        importance=claim.importance,
        created_at=created_at,
        created_by_run_id=claim.created_by_run_id,
    )


def _template_to_draft(
    template: ClaimTemplate,
    *,
    ref: str,
    lifecycle: ClaimLifecycle,
    system_from: datetime,
    created_at: datetime,
) -> DraftClaim:
    return DraftClaim(
        ref=ref,
        bank_id=template.bank_id,
        subject_entity_id=template.subject_entity_id,
        predicate=template.predicate,
        object_kind=template.object_kind,
        object_entity_id=template.object_entity_id,
        object_value_json=template.object_value_json,
        polarity=template.polarity,
        valid_from=template.valid_from,
        valid_to=template.valid_to,
        system_from=system_from,
        system_to=None,
        lifecycle=lifecycle,
        origin=template.origin,
        importance=template.importance,
        created_at=created_at,
        created_by_run_id=template.created_by_run_id,
    )


def _template_object_signature(template: ClaimTemplate) -> tuple[str, str]:
    if template.object_kind is ClaimObjectKind.ENTITY:
        assert template.object_entity_id is not None
        return (template.object_kind.value, template.object_entity_id)
    assert template.object_value_json is not None
    return (template.object_kind.value, template.object_value_json)


def _template_content_signature(template: ClaimTemplate) -> tuple[object, ...]:
    return (
        template.bank_id,
        template.subject_entity_id,
        template.predicate,
        _template_object_signature(template),
        template.polarity.value,
        template.valid_from,
        template.valid_to,
        template.origin.value,
        template.importance,
    )
