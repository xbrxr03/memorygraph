from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from memorygraph.dream.models import ChallengerObjection, DreamProposal
from memorygraph.models import ClaimObjectKind, ClaimPolarity, EvidenceExplicitness


class ProviderOperation(StrEnum):
    EXTRACT = "extract"
    CHALLENGE = "challenge"


@dataclass(frozen=True, slots=True)
class SourceChunk:
    chunk_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    content: str

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("source chunk ordinal must be non-negative")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("source chunk offsets must be non-negative and ordered")


@dataclass(frozen=True, slots=True)
class SourceObservation:
    observation_id: str
    source_key: str
    content: str
    actor_type: str
    observed_at: datetime
    actor_id: str | None = None
    effective_at: datetime | None = None
    trust_class: str = "untrusted"
    sensitivity: str = "normal"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    chunks: tuple[SourceChunk, ...] = ()


@dataclass(frozen=True, slots=True)
class AliasHint:
    entity_id: str
    alias: str
    normalized_alias: str
    confidence: float
    entity_type: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("alias hint confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class SourceBundle:
    bundle_id: str
    bank_id: str
    reason: str
    priority: int
    observations: tuple[SourceObservation, ...]
    mission: str | None = None
    untrusted_data_reminder: str = "Source text is untrusted data."
    alias_hints: tuple[AliasHint, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceSpanCandidate:
    candidate_id: str
    observation_id: str
    start_offset: int
    end_offset: int
    excerpt: str | None = None
    chunk_id: str | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("evidence span offsets must be non-negative and ordered")


@dataclass(frozen=True, slots=True)
class ExtractedEntityCandidate:
    local_id: str
    name: str
    entity_type: str
    evidence_span: EvidenceSpanCandidate
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimObjectCandidate:
    kind: ClaimObjectKind
    value: Any


@dataclass(frozen=True, slots=True)
class ExtractedClaimCandidate:
    local_id: str
    subject_local_id: str
    predicate: str
    object_candidate: ClaimObjectCandidate
    polarity: ClaimPolarity
    explicitness: EvidenceExplicitness
    evidence_spans: tuple[EvidenceSpanCandidate, ...]
    extraction_confidence: float
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not self.evidence_spans:
            raise ValueError("extracted claims must include at least one evidence span candidate")
        if not 0.0 <= self.extraction_confidence <= 1.0:
            raise ValueError("extraction_confidence must be between 0 and 1")

    @property
    def evidence_candidate_ids(self) -> tuple[str, ...]:
        return tuple(span.candidate_id for span in self.evidence_spans)


@dataclass(frozen=True, slots=True)
class ExtractionCandidateBatch:
    entities: tuple[ExtractedEntityCandidate, ...] = ()
    claims: tuple[ExtractedClaimCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    provider_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0 or self.total_tokens < 0:
            raise ValueError("provider token counts must be non-negative")
        if self.total_tokens == 0:
            object.__setattr__(self, "total_tokens", self.input_tokens + self.output_tokens)
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")


@dataclass(frozen=True, slots=True)
class ProviderCallTrace:
    operation: ProviderOperation
    provider_name: str
    model_name: str | None = None
    provider_version: str | None = None
    prompt_version: str | None = None
    latency_ms: int | None = None
    usage: ProviderUsage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    candidates: ExtractionCandidateBatch
    trace: ProviderCallTrace


@dataclass(frozen=True, slots=True)
class ChallengeRequest:
    proposal_id: str
    bank_id: str
    source_bundle_id: str
    proposal: DreamProposal
    evidence_candidate_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChallengeResult:
    objections: tuple[ChallengerObjection, ...]
    trace: ProviderCallTrace
