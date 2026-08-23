"""Read-only SQLite adapter for domain queries and dream preconditions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from memorygraph.models import (
    Claim,
    ClaimEvidence,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    ClaimRelation,
    ClaimRelationKind,
    ConflictPolicy,
    DecisionMethod,
    EvidenceExplicitness,
    EvidenceStance,
    PredicateCardinality,
    PredicateDefinition,
    PredicateVolatility,
)
from memorygraph.storage import (
    ClaimEvidenceRecord,
    ClaimEvidenceRepository,
    ClaimRecord,
    ClaimRelationRecord,
    ClaimRelationRepository,
    ClaimRepository,
    PredicateDefinitionRecord,
    PredicateDefinitionRepository,
)


class StorageDomainReader:
    """Implement the pure ``ClaimReader`` protocol over one SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.claims = ClaimRepository(connection)
        self.evidence = ClaimEvidenceRepository(connection)
        self.relations = ClaimRelationRepository(connection)
        self.predicates = PredicateDefinitionRepository(connection)

    def get_predicate_definition(
        self,
        bank_id: str,
        predicate: str,
    ) -> PredicateDefinition | None:
        record = self.predicates.resolve(bank_id, predicate)
        return None if record is None else predicate_from_record(record)

    def get_claim(self, bank_id: str, claim_id: str) -> Claim | None:
        record = self.claims.get(bank_id, claim_id)
        return None if record is None else claim_from_record(record)

    def get_claims(self, bank_id: str, claim_ids: Sequence[str]) -> Mapping[str, Claim]:
        claims = (self.get_claim(bank_id, claim_id) for claim_id in claim_ids)
        return {claim.id: claim for claim in claims if claim is not None}

    def list_claims_for_slot(
        self,
        bank_id: str,
        subject_entity_id: str,
        predicate: str,
    ) -> Sequence[Claim]:
        return tuple(
            claim_from_record(record)
            for record in self.claims.list_versions(bank_id, subject_entity_id, predicate)
        )

    def list_evidence_for_claim(
        self,
        bank_id: str,
        claim_id: str,
    ) -> Sequence[ClaimEvidence]:
        return tuple(
            evidence_from_record(record)
            for record in self.evidence.list_for_claim(bank_id, claim_id)
        )

    def list_relations_for_claim(
        self,
        bank_id: str,
        claim_id: str,
    ) -> Sequence[ClaimRelation]:
        rows = self.connection.execute(
            """
            SELECT id
            FROM claim_relations
            WHERE bank_id = ? AND (from_claim_id = ? OR to_claim_id = ?)
            ORDER BY created_at, id
            """,
            (bank_id, claim_id, claim_id),
        ).fetchall()
        return tuple(
            relation_from_record(record)
            for record in (
                self.relations.get(bank_id, row["id"])
                for row in rows
            )
            if record is not None
        )

    def event_watermark(self, bank_id: str) -> int:
        """Return the latest committed mutation sequence visible to a bank."""

        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS watermark FROM memory_events WHERE bank_id = ?",
            (bank_id,),
        ).fetchone()
        return int(row["watermark"])


def claim_from_record(record: ClaimRecord) -> Claim:
    return Claim(
        id=record.id,
        bank_id=record.bank_id,
        subject_entity_id=record.subject_entity_id,
        predicate=record.predicate,
        object_kind=ClaimObjectKind(record.object_kind),
        object_entity_id=record.object_entity_id,
        object_value_json=(
            None
            if record.object_kind == "entity"
            else json.dumps(record.object_value, sort_keys=True, separators=(",", ":"))
        ),
        polarity=ClaimPolarity(record.polarity),
        valid_from=_optional_timestamp(record.valid_from),
        valid_to=_optional_timestamp(record.valid_to),
        system_from=_required_timestamp(record.system_from),
        system_to=_optional_timestamp(record.system_to),
        lifecycle=ClaimLifecycle(record.lifecycle),
        origin=ClaimOrigin(record.origin),
        importance=record.importance,
        created_at=_required_timestamp(record.created_at),
        created_by_run_id=record.created_by_run_id,
    )


def evidence_from_record(record: ClaimEvidenceRecord) -> ClaimEvidence:
    return ClaimEvidence(
        id=record.id,
        bank_id=record.bank_id,
        claim_id=record.claim_id,
        observation_id=record.observation_id,
        chunk_id=record.chunk_id,
        start_offset=record.start_offset,
        end_offset=record.end_offset,
        excerpt=record.excerpt,
        stance=EvidenceStance(record.stance),
        explicitness=EvidenceExplicitness(record.explicitness),
        source_reliability=record.source_reliability,
        extraction_confidence=record.extraction_confidence,
        extractor_name=record.extractor_name,
        extractor_version=record.extractor_version,
        created_at=_required_timestamp(record.created_at),
    )


def relation_from_record(record: ClaimRelationRecord) -> ClaimRelation:
    return ClaimRelation(
        id=record.id,
        bank_id=record.bank_id,
        from_claim_id=record.from_claim_id,
        to_claim_id=record.to_claim_id,
        relation=ClaimRelationKind(record.relation),
        rationale=record.rationale,
        decision_method=DecisionMethod(record.decision_method),
        decision_confidence=record.decision_confidence,
        dream_run_id=record.dream_run_id,
        created_at=_required_timestamp(record.created_at),
    )


def predicate_from_record(record: PredicateDefinitionRecord) -> PredicateDefinition:
    return PredicateDefinition(
        name=record.name,
        cardinality=PredicateCardinality(record.cardinality),
        volatility=PredicateVolatility(record.volatility),
        conflict_policy=ConflictPolicy(record.conflict_policy),
        bank_id=record.bank_id,
        subject_type=record.subject_type,
        object_type=record.object_type,
        default_validity_seconds=record.default_validity_seconds,
        sensitivity=record.sensitivity,
        created_at=_required_timestamp(record.created_at),
    )


def _optional_timestamp(value: str | None) -> datetime | None:
    return None if value is None else _required_timestamp(value)


def _required_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Persisted timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)
