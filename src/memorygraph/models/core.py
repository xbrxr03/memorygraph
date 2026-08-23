from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class TriState(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ClaimObjectKind(StrEnum):
    ENTITY = "entity"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"


class ClaimPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class ClaimLifecycle(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"


class ClaimOrigin(StrEnum):
    EXPLICIT = "explicit"
    EXTRACTED = "extracted"
    IMPORTED = "imported"
    DERIVED = "derived"


class PredicateCardinality(StrEnum):
    ONE = "one"
    MANY = "many"
    EVENT = "event"


class PredicateVolatility(StrEnum):
    IMMUTABLE = "immutable"
    DURABLE = "durable"
    VOLATILE = "volatile"
    EPHEMERAL = "ephemeral"


class ConflictPolicy(StrEnum):
    CONSERVATIVE = "conservative"
    LATEST_EQUAL_AUTHORITY = "latest_equal_authority"
    AUTHORITATIVE_SOURCE = "authoritative_source"
    MANUAL_ONLY = "manual_only"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    MENTIONS = "mentions"


class EvidenceExplicitness(StrEnum):
    EXPLICIT = "explicit"
    STRONGLY_IMPLIED = "strongly_implied"
    INFERRED = "inferred"


class ClaimRelationKind(StrEnum):
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    REFINES = "refines"
    DUPLICATES = "duplicates"
    DERIVED_FROM = "derived_from"


class DecisionMethod(StrEnum):
    EXPLICIT = "explicit"
    RULE = "rule"
    MODEL_PROPOSAL = "model_proposal"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True, slots=True)
class HalfOpenInterval:
    start: datetime | None
    end: datetime | None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("half-open intervals require end > start")


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    name: str
    cardinality: PredicateCardinality = PredicateCardinality.MANY
    volatility: PredicateVolatility = PredicateVolatility.DURABLE
    conflict_policy: ConflictPolicy = ConflictPolicy.CONSERVATIVE
    bank_id: str | None = None
    subject_type: str | None = None
    object_type: str | None = None
    default_validity_seconds: int | None = None
    sensitivity: str = "normal"
    created_at: datetime | None = None

    @classmethod
    def unknown(cls, name: str, *, bank_id: str | None = None) -> PredicateDefinition:
        return cls(name=name, bank_id=bank_id)


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
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
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        HalfOpenInterval(self.valid_from, self.valid_to)
        HalfOpenInterval(self.system_from, self.system_to)
        if self.object_kind is ClaimObjectKind.ENTITY:
            if self.object_entity_id is None or self.object_value_json is not None:
                raise ValueError(
                    "entity-valued claims require object_entity_id and no object_value_json"
                )
        elif self.object_entity_id is not None or self.object_value_json is None:
            raise ValueError("non-entity claims require object_value_json and no object_entity_id")

    @property
    def valid_interval(self) -> HalfOpenInterval:
        return HalfOpenInterval(self.valid_from, self.valid_to)

    @property
    def system_interval(self) -> HalfOpenInterval:
        return HalfOpenInterval(self.system_from, self.system_to)

    @property
    def slot_key(self) -> tuple[str, str, str]:
        return (self.bank_id, self.subject_entity_id, self.predicate)

    @property
    def object_signature(self) -> tuple[str, str]:
        if self.object_kind is ClaimObjectKind.ENTITY:
            assert self.object_entity_id is not None
            return (self.object_kind.value, self.object_entity_id)
        assert self.object_value_json is not None
        return (self.object_kind.value, self.object_value_json)

    @property
    def content_signature(self) -> tuple[Any, ...]:
        return (
            self.bank_id,
            self.subject_entity_id,
            self.predicate,
            self.object_signature,
            self.polarity.value,
            self.valid_from,
            self.valid_to,
            self.origin.value,
            self.importance,
        )


@dataclass(frozen=True, slots=True)
class ClaimEvidence:
    id: str
    bank_id: str
    claim_id: str
    observation_id: str
    excerpt: str
    stance: EvidenceStance
    explicitness: EvidenceExplicitness
    source_reliability: float
    extraction_confidence: float
    extractor_name: str
    extractor_version: str
    created_at: datetime
    chunk_id: str | None = None
    start_offset: int = 0
    end_offset: int = 0

    def __post_init__(self) -> None:
        if self.end_offset < self.start_offset:
            raise ValueError("evidence end_offset must be >= start_offset")
        if not 0.0 <= self.source_reliability <= 1.0:
            raise ValueError("source_reliability must be between 0 and 1")
        if not 0.0 <= self.extraction_confidence <= 1.0:
            raise ValueError("extraction_confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ClaimRelation:
    id: str
    bank_id: str
    from_claim_id: str
    to_claim_id: str
    relation: ClaimRelationKind
    rationale: str
    decision_method: DecisionMethod
    decision_confidence: float
    created_at: datetime
    dream_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.from_claim_id == self.to_claim_id:
            raise ValueError("claim relations cannot self-reference")
        if not 0.0 <= self.decision_confidence <= 1.0:
            raise ValueError("decision_confidence must be between 0 and 1")
