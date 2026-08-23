from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from memorygraph.models import Claim, ClaimLifecycle, HalfOpenInterval, TriState


@dataclass(frozen=True, slots=True)
class ClaimSelection:
    claim: Claim
    valid_time_match: TriState
    warnings: tuple[str, ...] = ()


def valid_time_contains(interval: HalfOpenInterval, point: datetime) -> TriState:
    if interval.start is not None and point < interval.start:
        return TriState.NO
    if interval.end is not None and point >= interval.end:
        return TriState.NO
    if interval.start is not None and interval.end is not None:
        return TriState.YES
    return TriState.UNKNOWN


def valid_time_overlap(left: HalfOpenInterval, right: HalfOpenInterval) -> TriState:
    if left.end is not None and right.start is not None and left.end <= right.start:
        return TriState.NO
    if right.end is not None and left.start is not None and right.end <= left.start:
        return TriState.NO
    if None not in (left.start, left.end, right.start, right.end):
        return TriState.YES
    return TriState.UNKNOWN


def system_time_contains(interval: HalfOpenInterval, point: datetime) -> bool:
    if interval.start is not None and point < interval.start:
        return False
    return not (interval.end is not None and point >= interval.end)


def claim_visible_at_system_time(claim: Claim, known_at: datetime) -> bool:
    return system_time_contains(claim.system_interval, known_at)


def claim_valid_at(claim: Claim, valid_at: datetime) -> TriState:
    return valid_time_contains(claim.valid_interval, valid_at)


def unknown_validity_warning(claim: Claim, valid_at: datetime) -> str | None:
    if claim_valid_at(claim, valid_at) is not TriState.UNKNOWN:
        return None
    if claim.valid_from is None and claim.valid_to is None:
        return "claim validity bounds are unknown"
    if claim.valid_from is None:
        return "claim has unknown valid_from bound"
    return "claim has unknown valid_to bound"


def select_claims_as_of(
    claims: Iterable[Claim],
    *,
    known_at: datetime,
    valid_at: datetime,
    allowed_lifecycles: Sequence[ClaimLifecycle] | None = None,
    include_unknown_validity: bool = True,
) -> tuple[ClaimSelection, ...]:
    allowed = set(allowed_lifecycles) if allowed_lifecycles is not None else None
    selections: list[ClaimSelection] = []
    for claim in claims:
        if allowed is not None and claim.lifecycle not in allowed:
            continue
        if not claim_visible_at_system_time(claim, known_at):
            continue
        validity = claim_valid_at(claim, valid_at)
        if validity is TriState.NO:
            continue
        if validity is TriState.UNKNOWN and not include_unknown_validity:
            continue
        warning = unknown_validity_warning(claim, valid_at)
        warnings = (warning,) if warning is not None else ()
        selections.append(ClaimSelection(claim=claim, valid_time_match=validity, warnings=warnings))
    selections.sort(key=_selection_sort_key)
    return tuple(selections)


def _selection_sort_key(selection: ClaimSelection) -> tuple[datetime, datetime, datetime, str]:
    claim = selection.claim
    valid_from = claim.valid_from or datetime.min.replace(tzinfo=claim.system_from.tzinfo)
    valid_rank = 0 if selection.valid_time_match is TriState.YES else 1
    return (claim.system_from, valid_from, claim.created_at, f"{valid_rank}:{claim.id}")
