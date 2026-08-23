from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from memorygraph.dream.models import DreamProposal, ValidationContext
from memorygraph.dream.runtime_models import BatchCommitResult, DreamTask, ValidatedProposal
from memorygraph.dream.schemas import (
    ChallengeRequest,
    ChallengeResult,
    ExtractionResult,
    SourceBundle,
)


class DreamProvider(Protocol):
    def extract(self, source_bundle: SourceBundle) -> ExtractionResult:
        ...

    def challenge(self, request: ChallengeRequest) -> ChallengeResult:
        ...


class DreamContextBuilder(Protocol):
    def build_source_bundle(self, task: DreamTask) -> SourceBundle:
        ...

    def build_validation_context(
        self,
        task: DreamTask,
        proposal: DreamProposal,
    ) -> ValidationContext:
        ...


class DreamProposalPipeline(Protocol):
    def proposals_from_extraction(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        extraction: ExtractionResult,
    ) -> Sequence[DreamProposal]:
        ...

    def should_challenge(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> bool:
        ...

    def build_challenge_request(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> ChallengeRequest:
        ...

    def apply_challenge_result(
        self,
        proposal: DreamProposal,
        challenge_result: ChallengeResult,
    ) -> DreamProposal:
        ...


class DreamProposalCommitter(Protocol):
    def commit_batch(
        self,
        task: DreamTask,
        proposals: Sequence[ValidatedProposal],
    ) -> BatchCommitResult:
        ...
