from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from memorygraph.dream.models import DreamProposal, ProposalDisposition, ProposalValidation
from memorygraph.dream.schemas import ProviderCallTrace


class DreamRunMode(StrEnum):
    APPLY = "apply"
    DRY_RUN = "dry_run"
    REVIEW_ONLY = "review_only"


class DreamRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class ProposalCommitStatus(StrEnum):
    COMMITTED = "committed"
    REPLAYED = "replayed"
    STALE = "stale"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DreamTask:
    run_id: str
    task_id: str
    bank_id: str
    trigger: str
    mode: DreamRunMode
    input_watermark: int
    reason: str
    priority: int = 0
    observation_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    artifact_keys: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.input_watermark < 0:
            raise ValueError("input_watermark must be non-negative")


@dataclass(frozen=True, slots=True)
class ValidatedProposal:
    proposal: DreamProposal
    validation: ProposalValidation


@dataclass(frozen=True, slots=True)
class ProposalCommitOutcome:
    proposal_id: str
    status: ProposalCommitStatus
    message: str = ""


@dataclass(frozen=True, slots=True)
class BatchCommitResult:
    outcomes: tuple[ProposalCommitOutcome, ...]
    committed_event_range: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class ProposalRunResult:
    proposal: DreamProposal
    validation: ProposalValidation
    commit_outcome: ProposalCommitOutcome | None = None

    @property
    def final_disposition(self) -> str:
        if self.commit_outcome is not None:
            return self.commit_outcome.status.value
        return self.validation.disposition.value


@dataclass(frozen=True, slots=True)
class DreamRunMetrics:
    selected_observations: int
    extracted_entities: int
    extracted_claims: int
    proposals_total: int
    auto_eligible: int
    review_required: int
    rejected: int
    stale: int
    committed: int
    replayed: int
    provider_calls: int

    @classmethod
    def from_results(
        cls,
        *,
        selected_observations: int,
        extracted_entities: int,
        extracted_claims: int,
        proposal_results: tuple[ProposalRunResult, ...],
        provider_calls: tuple[ProviderCallTrace, ...],
    ) -> DreamRunMetrics:
        auto_eligible = sum(
            1
            for item in proposal_results
            if item.validation.disposition is ProposalDisposition.AUTO_ELIGIBLE
        )
        review_required = sum(
            1
            for item in proposal_results
            if item.validation.disposition is ProposalDisposition.REVIEW_REQUIRED
        )
        rejected = sum(
            1
            for item in proposal_results
            if item.validation.disposition is ProposalDisposition.REJECTED
        )
        stale = sum(
            1
            for item in proposal_results
            if item.validation.disposition is ProposalDisposition.STALE
        )
        committed = sum(
            1
            for item in proposal_results
            if (
                item.commit_outcome is not None
                and item.commit_outcome.status is ProposalCommitStatus.COMMITTED
            )
        )
        replayed = sum(
            1
            for item in proposal_results
            if (
                item.commit_outcome is not None
                and item.commit_outcome.status is ProposalCommitStatus.REPLAYED
            )
        )
        return cls(
            selected_observations=selected_observations,
            extracted_entities=extracted_entities,
            extracted_claims=extracted_claims,
            proposals_total=len(proposal_results),
            auto_eligible=auto_eligible,
            review_required=review_required,
            rejected=rejected,
            stale=stale,
            committed=committed,
            replayed=replayed,
            provider_calls=len(provider_calls),
        )


@dataclass(frozen=True, slots=True)
class DreamRunReport:
    task: DreamTask
    status: DreamRunStatus
    source_bundle_id: str | None
    provider_calls: tuple[ProviderCallTrace, ...]
    proposal_results: tuple[ProposalRunResult, ...]
    metrics: DreamRunMetrics
    commit_result: BatchCommitResult | None = None
    error_message: str | None = None
    failure_stage: str | None = None
