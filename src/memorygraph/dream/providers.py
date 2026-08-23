from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from memorygraph.dream.protocols import DreamProvider
from memorygraph.dream.schemas import (
    ChallengeRequest,
    ChallengeResult,
    ExtractionResult,
    SourceBundle,
)


@dataclass(slots=True)
class DeterministicDreamProvider(DreamProvider):
    extraction_results_by_bundle: Mapping[str, ExtractionResult]
    challenge_results_by_proposal: Mapping[str, ChallengeResult] = field(default_factory=dict)
    fail_extract_bundle_ids: Collection[str] = field(default_factory=tuple)
    fail_challenge_proposal_ids: Collection[str] = field(default_factory=tuple)
    call_log: list[tuple[str, str]] = field(default_factory=list)

    def extract(self, source_bundle: SourceBundle) -> ExtractionResult:
        self.call_log.append(("extract", source_bundle.bundle_id))
        if source_bundle.bundle_id in self.fail_extract_bundle_ids:
            raise RuntimeError(f"deterministic extract failure for {source_bundle.bundle_id}")
        try:
            return self.extraction_results_by_bundle[source_bundle.bundle_id]
        except KeyError as exc:
            raise KeyError(
                f"no extraction result configured for bundle {source_bundle.bundle_id}"
            ) from exc

    def challenge(self, request: ChallengeRequest) -> ChallengeResult:
        self.call_log.append(("challenge", request.proposal_id))
        if request.proposal_id in self.fail_challenge_proposal_ids:
            raise RuntimeError(f"deterministic challenge failure for {request.proposal_id}")
        try:
            return self.challenge_results_by_proposal[request.proposal_id]
        except KeyError as exc:
            raise KeyError(
                f"no challenge result configured for proposal {request.proposal_id}"
            ) from exc
