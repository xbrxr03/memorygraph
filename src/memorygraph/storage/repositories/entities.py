from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database import transaction


@dataclass(frozen=True)
class EntityRecord:
    id: str
    bank_id: str
    canonical_name: str
    normalized_name: str
    entity_type: str
    description: str | None
    status: str
    merged_into_id: str | None
    created_at: str


@dataclass(frozen=True)
class EntityAliasRecord:
    id: str
    bank_id: str
    entity_id: str
    alias: str
    normalized_alias: str
    source_observation_id: str | None
    confidence: float
    created_at: str


class EntityRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_entity(
        self,
        *,
        id: str,
        bank_id: str,
        canonical_name: str,
        normalized_name: str,
        entity_type: str,
        created_at: str,
        description: str | None = None,
        status: str = "active",
        merged_into_id: str | None = None,
    ) -> EntityRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO entities(
                    id,
                    bank_id,
                    canonical_name,
                    normalized_name,
                    entity_type,
                    description,
                    status,
                    merged_into_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    canonical_name,
                    normalized_name,
                    entity_type,
                    description,
                    status,
                    merged_into_id,
                    created_at,
                ),
            )
        entity = self.get_entity(bank_id, id)
        if entity is None:
            raise RuntimeError("Entity insert committed without a readable record.")
        return entity

    def get_entity(self, bank_id: str, entity_id: str) -> EntityRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                canonical_name,
                normalized_name,
                entity_type,
                description,
                status,
                merged_into_id,
                created_at
            FROM entities
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, entity_id),
        ).fetchone()
        return _hydrate_entity(row)

    def list_by_name(
        self,
        bank_id: str,
        normalized_name: str,
        *,
        entity_type: str | None = None,
    ) -> tuple[EntityRecord, ...]:
        if entity_type is None:
            rows = self._connection.execute(
                """
                SELECT
                    id,
                    bank_id,
                    canonical_name,
                    normalized_name,
                    entity_type,
                    description,
                    status,
                    merged_into_id,
                    created_at
                FROM entities
                WHERE bank_id = ? AND normalized_name = ?
                ORDER BY canonical_name ASC, id ASC
                """,
                (bank_id, normalized_name),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT
                    id,
                    bank_id,
                    canonical_name,
                    normalized_name,
                    entity_type,
                    description,
                    status,
                    merged_into_id,
                    created_at
                FROM entities
                WHERE bank_id = ? AND normalized_name = ? AND entity_type = ?
                ORDER BY canonical_name ASC, id ASC
                """,
                (bank_id, normalized_name, entity_type),
            ).fetchall()
        return tuple(_hydrate_entity(row) for row in rows if row is not None)

    def create_alias(
        self,
        *,
        id: str,
        bank_id: str,
        entity_id: str,
        alias: str,
        normalized_alias: str,
        confidence: float,
        created_at: str,
        source_observation_id: str | None = None,
    ) -> EntityAliasRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO entity_aliases(
                    id,
                    bank_id,
                    entity_id,
                    alias,
                    normalized_alias,
                    source_observation_id,
                    confidence,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    entity_id,
                    alias,
                    normalized_alias,
                    source_observation_id,
                    confidence,
                    created_at,
                ),
            )
        alias_record = self.get_alias(bank_id, id)
        if alias_record is None:
            raise RuntimeError("Entity alias insert committed without a readable record.")
        return alias_record

    def get_alias(self, bank_id: str, alias_id: str) -> EntityAliasRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                entity_id,
                alias,
                normalized_alias,
                source_observation_id,
                confidence,
                created_at
            FROM entity_aliases
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, alias_id),
        ).fetchone()
        return _hydrate_alias(row)

    def list_aliases_for_entity(
        self, bank_id: str, entity_id: str
    ) -> tuple[EntityAliasRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                entity_id,
                alias,
                normalized_alias,
                source_observation_id,
                confidence,
                created_at
            FROM entity_aliases
            WHERE bank_id = ? AND entity_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (bank_id, entity_id),
        ).fetchall()
        return tuple(_hydrate_alias(row) for row in rows if row is not None)

    def find_by_alias(self, bank_id: str, normalized_alias: str) -> tuple[EntityAliasRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                entity_id,
                alias,
                normalized_alias,
                source_observation_id,
                confidence,
                created_at
            FROM entity_aliases
            WHERE bank_id = ? AND normalized_alias = ?
            ORDER BY confidence DESC, created_at ASC, id ASC
            """,
            (bank_id, normalized_alias),
        ).fetchall()
        return tuple(_hydrate_alias(row) for row in rows if row is not None)


def _hydrate_entity(row: sqlite3.Row | None) -> EntityRecord | None:
    if row is None:
        return None
    return EntityRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        canonical_name=row["canonical_name"],
        normalized_name=row["normalized_name"],
        entity_type=row["entity_type"],
        description=row["description"],
        status=row["status"],
        merged_into_id=row["merged_into_id"],
        created_at=row["created_at"],
    )


def _hydrate_alias(row: sqlite3.Row | None) -> EntityAliasRecord | None:
    if row is None:
        return None
    return EntityAliasRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        entity_id=row["entity_id"],
        alias=row["alias"],
        normalized_alias=row["normalized_alias"],
        source_observation_id=row["source_observation_id"],
        confidence=row["confidence"],
        created_at=row["created_at"],
    )
