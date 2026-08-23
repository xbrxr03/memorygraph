from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database import transaction


@dataclass(frozen=True)
class PredicateDefinitionRecord:
    id: str
    bank_id: str | None
    name: str
    subject_type: str | None
    object_type: str | None
    cardinality: str
    volatility: str
    conflict_policy: str
    default_validity_seconds: int | None
    sensitivity: str
    created_at: str


class PredicateDefinitionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        name: str,
        cardinality: str,
        volatility: str,
        created_at: str,
        bank_id: str | None = None,
        subject_type: str | None = None,
        object_type: str | None = None,
        conflict_policy: str = "conservative",
        default_validity_seconds: int | None = None,
        sensitivity: str = "normal",
    ) -> PredicateDefinitionRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO predicate_definitions(
                    id,
                    bank_id,
                    name,
                    subject_type,
                    object_type,
                    cardinality,
                    volatility,
                    conflict_policy,
                    default_validity_seconds,
                    sensitivity,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    name,
                    subject_type,
                    object_type,
                    cardinality,
                    volatility,
                    conflict_policy,
                    default_validity_seconds,
                    sensitivity,
                    created_at,
                ),
            )
        record = self.get_exact(bank_id, name)
        if record is None:
            raise RuntimeError("Predicate insert committed without a readable record.")
        return record

    def get_exact(self, bank_id: str | None, name: str) -> PredicateDefinitionRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                name,
                subject_type,
                object_type,
                cardinality,
                volatility,
                conflict_policy,
                default_validity_seconds,
                sensitivity,
                created_at
            FROM predicate_definitions
            WHERE bank_id IS ? AND name = ?
            """,
            (bank_id, name),
        ).fetchone()
        return _hydrate_predicate(row)

    def resolve(self, bank_id: str, name: str) -> PredicateDefinitionRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                name,
                subject_type,
                object_type,
                cardinality,
                volatility,
                conflict_policy,
                default_validity_seconds,
                sensitivity,
                created_at
            FROM predicate_definitions
            WHERE name = ? AND (bank_id = ? OR bank_id IS NULL)
            ORDER BY CASE WHEN bank_id = ? THEN 0 ELSE 1 END, created_at DESC, id DESC
            LIMIT 1
            """,
            (name, bank_id, bank_id),
        ).fetchone()
        return _hydrate_predicate(row)


def _hydrate_predicate(row: sqlite3.Row | None) -> PredicateDefinitionRecord | None:
    if row is None:
        return None
    return PredicateDefinitionRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        name=row["name"],
        subject_type=row["subject_type"],
        object_type=row["object_type"],
        cardinality=row["cardinality"],
        volatility=row["volatility"],
        conflict_policy=row["conflict_policy"],
        default_validity_seconds=row["default_validity_seconds"],
        sensitivity=row["sensitivity"],
        created_at=row["created_at"],
    )
