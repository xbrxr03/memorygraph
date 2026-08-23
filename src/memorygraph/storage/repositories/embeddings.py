from __future__ import annotations

import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from ..database import transaction


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    id: str
    bank_id: str
    resource_type: str
    resource_id: str
    model: str
    dimensions: int
    content_sha256: str
    vector: tuple[float, ...]
    created_at: str


class EmbeddingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def replace(
        self,
        *,
        bank_id: str,
        resource_type: str,
        resource_id: str,
        model: str,
        content_sha256: str,
        vector: Sequence[float],
        created_at: str,
    ) -> EmbeddingRecord:
        values = tuple(float(value) for value in vector)
        if not values:
            raise ValueError("embedding vector cannot be empty")
        vector_blob = struct.pack(f"!{len(values)}f", *values)
        embedding_id = str(uuid4())
        with transaction(self._connection):
            self._connection.execute(
                """
                DELETE FROM embeddings
                WHERE bank_id = ? AND resource_type = ? AND resource_id = ? AND model = ?
                """,
                (bank_id, resource_type, resource_id, model),
            )
            self._connection.execute(
                """
                INSERT INTO embeddings(
                    id, bank_id, resource_type, resource_id, model, dimensions,
                    content_sha256, vector_blob, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    embedding_id,
                    bank_id,
                    resource_type,
                    resource_id,
                    model,
                    len(values),
                    content_sha256,
                    vector_blob,
                    created_at,
                ),
            )
        record = self.get(bank_id=bank_id, embedding_id=embedding_id)
        if record is None:
            raise RuntimeError("Embedding replace committed without a readable record.")
        return record

    def get(self, *, bank_id: str, embedding_id: str) -> EmbeddingRecord | None:
        row = self._connection.execute(
            "SELECT * FROM embeddings WHERE bank_id = ? AND id = ?",
            (bank_id, embedding_id),
        ).fetchone()
        return _hydrate(row)

    def list_for_bank(
        self,
        *,
        bank_id: str,
        model: str,
        resource_type: str | None = None,
    ) -> tuple[EmbeddingRecord, ...]:
        if resource_type is None:
            rows = self._connection.execute(
                """
                SELECT * FROM embeddings
                WHERE bank_id = ? AND model = ?
                ORDER BY resource_type, resource_id, created_at DESC
                """,
                (bank_id, model),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT * FROM embeddings
                WHERE bank_id = ? AND model = ? AND resource_type = ?
                ORDER BY resource_id, created_at DESC
                """,
                (bank_id, model, resource_type),
            ).fetchall()
        return tuple(_hydrate(row) for row in rows if row is not None)

    def delete_resource(self, *, bank_id: str, resource_type: str, resource_id: str) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                DELETE FROM embeddings
                WHERE bank_id = ? AND resource_type = ? AND resource_id = ?
                """,
                (bank_id, resource_type, resource_id),
            )
        return cursor.rowcount


def _hydrate(row: sqlite3.Row | None) -> EmbeddingRecord | None:
    if row is None:
        return None
    dimensions = int(row["dimensions"])
    vector = struct.unpack(f"!{dimensions}f", row["vector_blob"])
    return EmbeddingRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        model=row["model"],
        dimensions=dimensions,
        content_sha256=row["content_sha256"],
        vector=tuple(vector),
        created_at=row["created_at"],
    )
