from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memorygraph.domain import (
    ClaimTemplate,
    plan_confirm,
    plan_contradict,
    plan_retract,
    plan_supersede,
)
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    DecisionMethod,
    HalfOpenInterval,
    PredicateCardinality,
    PredicateDefinition,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


DEFAULT_VALID_FROM = ts("2026-01-01T00:00:00Z")


def existing_claim(
    claim_id: str = "c1",
    *,
    object_value_json: str = '"poetry"',
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE,
    valid_from: datetime | None = DEFAULT_VALID_FROM,
    valid_to: datetime | None = None,
) -> Claim:
    created_at = ts("2026-01-01T00:00:00Z")
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
        system_to=None,
        lifecycle=lifecycle,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.7,
        created_at=created_at,
    )


def replacement_template(
    *, object_value_json: str = '"hatchling"', valid_from: datetime | None = None
) -> ClaimTemplate:
    return ClaimTemplate(
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate="uses_build_backend",
        object_kind=ClaimObjectKind.STRING,
        object_entity_id=None,
        object_value_json=object_value_json,
        polarity=ClaimPolarity.POSITIVE,
        valid_from=valid_from,
        valid_to=None,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.8,
    )


def test_confirm_existing_active_claim_only_attaches_evidence() -> None:
    plan = plan_confirm(
        existing_claim(), commit_time=ts("2026-03-01T00:00:00Z"), evidence_ids=("e1",)
    )
    assert plan.operation == "confirm"
    assert plan.closures == ()
    assert plan.draft_claims == ()
    assert plan.evidence_attachments[0].target.claim_id == "c1"


def test_confirm_can_reactivate_contested_claim() -> None:
    plan = plan_confirm(
        existing_claim(lifecycle=ClaimLifecycle.CONTESTED),
        commit_time=ts("2026-03-01T00:00:00Z"),
        evidence_ids=("e1",),
        reactivate_contested=True,
    )
    assert plan.closures[0].claim_id == "c1"
    assert plan.draft_claims[0].lifecycle is ClaimLifecycle.ACTIVE
    assert plan.evidence_attachments[0].target.draft_ref == "confirmed"


def test_supersede_closes_current_version_and_creates_retired_and_replacement_versions() -> None:
    current = existing_claim()
    plan = plan_supersede(
        current,
        replacement_template(
            object_value_json='"hatchling"', valid_from=ts("2026-03-01T00:00:00Z")
        ),
        predicate_definition=PredicateDefinition(
            name="uses_build_backend", cardinality=PredicateCardinality.ONE
        ),
        commit_time=ts("2026-03-10T00:00:00Z"),
        rationale="User explicitly switched the backend.",
        evidence_ids=("e2",),
    )

    retired, replacement = plan.draft_claims
    assert plan.closures == (plan.closures[0],)
    assert retired.lifecycle is ClaimLifecycle.SUPERSEDED
    assert retired.valid_to == ts("2026-03-01T00:00:00Z")
    assert replacement.lifecycle is ClaimLifecycle.ACTIVE
    assert replacement.object_value_json == '"hatchling"'
    assert plan.relations[0].relation.value == "supersedes"


def test_supersede_warns_for_multi_value_predicates() -> None:
    plan = plan_supersede(
        existing_claim(),
        replacement_template(object_value_json='"hatchling"'),
        predicate_definition=PredicateDefinition(
            name="uses_build_backend", cardinality=PredicateCardinality.MANY
        ),
        commit_time=ts("2026-03-10T00:00:00Z"),
        rationale="Explicit override.",
        evidence_ids=(),
    )
    assert plan.warnings == (
        "multi-valued predicates do not auto-supersede; this plan requires explicit approval",
        "replacement has unknown valid_from; retiring claim keeps unknown valid_to",
    )


def test_contradict_marks_existing_active_claim_contested_and_adds_new_contested_claim() -> None:
    plan = plan_contradict(
        existing_claim(),
        replacement_template(object_value_json='"uv"'),
        commit_time=ts("2026-03-10T00:00:00Z"),
        rationale="Two conflicting user statements remain unresolved.",
        evidence_ids=("e3",),
        decision_method=DecisionMethod.RULE,
    )
    assert plan.closures[0].claim_id == "c1"
    assert [draft.lifecycle for draft in plan.draft_claims] == [
        ClaimLifecycle.CONTESTED,
        ClaimLifecycle.CONTESTED,
    ]
    assert plan.relations[0].to_claim.draft_ref == "existing_contested"


def test_retract_preserves_world_history_by_default() -> None:
    target = existing_claim(
        valid_from=ts("2026-01-01T00:00:00Z"), valid_to=ts("2026-04-01T00:00:00Z")
    )
    plan = plan_retract(target, commit_time=ts("2026-03-10T00:00:00Z"), evidence_ids=("e4",))
    assert plan.draft_claims[0].lifecycle is ClaimLifecycle.RETRACTED
    assert plan.draft_claims[0].valid_from == ts("2026-01-01T00:00:00Z")
    assert plan.draft_claims[0].valid_to == ts("2026-04-01T00:00:00Z")


def test_retract_can_replace_valid_interval_when_claim_was_never_true() -> None:
    plan = plan_retract(
        existing_claim(),
        commit_time=ts("2026-03-10T00:00:00Z"),
        evidence_ids=("e4",),
        replacement_valid_interval=HalfOpenInterval(None, ts("2026-02-01T00:00:00Z")),
    )
    assert plan.draft_claims[0].valid_from is None
    assert plan.draft_claims[0].valid_to == ts("2026-02-01T00:00:00Z")


def test_invalid_transitions_raise() -> None:
    with pytest.raises(ValueError):
        plan_confirm(
            existing_claim(lifecycle=ClaimLifecycle.RETRACTED),
            commit_time=ts("2026-03-01T00:00:00Z"),
            evidence_ids=("e1",),
        )
