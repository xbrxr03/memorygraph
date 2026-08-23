from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memorygraph.domain import select_claims_as_of, valid_time_contains, valid_time_overlap
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    HalfOpenInterval,
    TriState,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def make_claim(
    claim_id: str,
    *,
    object_value_json: str = '"poetry"',
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    system_from: datetime | None = None,
    system_to: datetime | None = None,
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE,
) -> Claim:
    base_system_from = system_from or ts("2026-01-01T00:00:00Z")
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
        system_from=base_system_from,
        system_to=system_to,
        lifecycle=lifecycle,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.5,
        created_at=base_system_from,
    )


@pytest.mark.parametrize(
    ("interval", "point", "expected"),
    [
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), ts("2026-02-01T00:00:00Z")),
            ts("2026-01-01T00:00:00Z"),
            TriState.YES,
        ),
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), ts("2026-02-01T00:00:00Z")),
            ts("2026-02-01T00:00:00Z"),
            TriState.NO,
        ),
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), None),
            ts("2026-03-01T00:00:00Z"),
            TriState.UNKNOWN,
        ),
        (
            HalfOpenInterval(None, ts("2026-03-01T00:00:00Z")),
            ts("2026-02-01T00:00:00Z"),
            TriState.UNKNOWN,
        ),
        (HalfOpenInterval(None, None), ts("2026-02-01T00:00:00Z"), TriState.UNKNOWN),
        (
            HalfOpenInterval(ts("2026-03-01T00:00:00Z"), None),
            ts("2026-02-01T00:00:00Z"),
            TriState.NO,
        ),
    ],
)
def test_valid_time_contains_uses_conservative_unknown_semantics(
    interval: HalfOpenInterval,
    point: datetime,
    expected: TriState,
) -> None:
    assert valid_time_contains(interval, point) is expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), ts("2026-03-01T00:00:00Z")),
            HalfOpenInterval(ts("2026-02-01T00:00:00Z"), ts("2026-04-01T00:00:00Z")),
            TriState.YES,
        ),
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), ts("2026-02-01T00:00:00Z")),
            HalfOpenInterval(ts("2026-02-01T00:00:00Z"), ts("2026-03-01T00:00:00Z")),
            TriState.NO,
        ),
        (
            HalfOpenInterval(ts("2026-01-01T00:00:00Z"), None),
            HalfOpenInterval(ts("2026-02-01T00:00:00Z"), ts("2026-03-01T00:00:00Z")),
            TriState.UNKNOWN,
        ),
        (
            HalfOpenInterval(None, ts("2026-01-15T00:00:00Z")),
            HalfOpenInterval(ts("2026-02-01T00:00:00Z"), ts("2026-03-01T00:00:00Z")),
            TriState.NO,
        ),
    ],
)
def test_valid_time_overlap_is_half_open_and_conservative(
    left: HalfOpenInterval,
    right: HalfOpenInterval,
    expected: TriState,
) -> None:
    assert valid_time_overlap(left, right) is expected


def test_select_claims_as_of_filters_by_system_and_valid_time() -> None:
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
        object_value_json='"poetry"',
        valid_from=ts("2026-01-01T00:00:00Z"),
        valid_to=ts("2026-03-01T00:00:00Z"),
        system_from=ts("2026-03-10T00:00:00Z"),
        lifecycle=ClaimLifecycle.SUPERSEDED,
    )
    replacement = make_claim(
        "c3",
        object_value_json='"hatchling"',
        valid_from=ts("2026-03-01T00:00:00Z"),
        system_from=ts("2026-03-10T00:00:00Z"),
    )

    old_view = select_claims_as_of(
        (before, after, replacement),
        known_at=ts("2026-03-09T00:00:00Z"),
        valid_at=ts("2026-02-15T00:00:00Z"),
    )
    assert [item.claim.id for item in old_view] == ["c1"]
    assert old_view[0].valid_time_match is TriState.YES

    new_view = select_claims_as_of(
        (before, after, replacement),
        known_at=ts("2026-03-11T00:00:00Z"),
        valid_at=ts("2026-03-15T00:00:00Z"),
        allowed_lifecycles=(ClaimLifecycle.ACTIVE, ClaimLifecycle.CONTESTED),
    )
    assert [item.claim.id for item in new_view] == ["c3"]


def test_select_claims_as_of_can_surface_unknown_validity_with_warning() -> None:
    claim = make_claim(
        "c1",
        valid_from=ts("2026-01-01T00:00:00Z"),
        valid_to=None,
    )

    matches = select_claims_as_of(
        (claim,),
        known_at=ts("2026-04-01T00:00:00Z"),
        valid_at=ts("2026-04-01T00:00:00Z"),
    )
    assert matches[0].valid_time_match is TriState.UNKNOWN
    assert matches[0].warnings == ("claim has unknown valid_to bound",)
