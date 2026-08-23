from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    bank_id: str
    kind: str
    artifact_key: str
    content: str
    source_claim_ids: Any
    source_watermark: int
    generator_name: str
    generator_version: str
    stale_at: str | None
    created_at: str


class ArtifactRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        kind: str,
        artifact_key: str,
        content: str,
        source_claim_ids: Any,
        source_watermark: int,
        generator_name: str,
        generator_version: str,
        created_at: str,
        stale_at: str | None = None,
    ) -> ArtifactRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO artifacts(
                    id,
                    bank_id,
                    kind,
                    artifact_key,
                    content,
                    source_claim_ids_json,
                    source_watermark,
                    generator_name,
                    generator_version,
                    stale_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    kind,
                    artifact_key,
                    content,
                    json.dumps(source_claim_ids, sort_keys=True),
                    source_watermark,
                    generator_name,
                    generator_version,
                    stale_at,
                    created_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Artifact insert committed without a readable record.")
        return record

    def get(self, bank_id: str, artifact_id: str) -> ArtifactRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, kind, artifact_key, content, source_claim_ids_json,
                source_watermark, generator_name, generator_version, stale_at, created_at
            FROM artifacts
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, artifact_id),
        ).fetchone()
        return _hydrate_artifact(row)

    def list_versions(
        self, bank_id: str, kind: str, artifact_key: str
    ) -> tuple[ArtifactRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id, bank_id, kind, artifact_key, content, source_claim_ids_json,
                source_watermark, generator_name, generator_version, stale_at, created_at
            FROM artifacts
            WHERE bank_id = ? AND kind = ? AND artifact_key = ?
            ORDER BY source_watermark DESC, created_at DESC, id DESC
            """,
            (bank_id, kind, artifact_key),
        ).fetchall()
        return tuple(_hydrate_artifact(row) for row in rows if row is not None)

    def get_latest(self, bank_id: str, kind: str, artifact_key: str) -> ArtifactRecord | None:
        rows = self.list_versions(bank_id, kind, artifact_key)
        return rows[0] if rows else None

    def list_current(
        self,
        bank_id: str,
        *,
        as_of: str,
        kind: str | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        query = """
            SELECT
                id, bank_id, kind, artifact_key, content, source_claim_ids_json,
                source_watermark, generator_name, generator_version, stale_at, created_at
            FROM artifacts
            WHERE bank_id = ?
              AND (stale_at IS NULL OR stale_at > ?)
        """
        params: list[Any] = [bank_id, as_of]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += (
            " ORDER BY kind ASC, artifact_key ASC, source_watermark DESC, "
            "created_at DESC, id DESC"
        )
        rows = self._connection.execute(query, params).fetchall()
        return tuple(_hydrate_artifact(row) for row in rows if row is not None)

    def mark_stale(self, *, bank_id: str, artifact_id: str, stale_at: str) -> ArtifactRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                UPDATE artifacts
                SET stale_at = ?
                WHERE bank_id = ? AND id = ?
                """,
                (stale_at, bank_id, artifact_id),
            )
        updated = self.get(bank_id, artifact_id)
        if updated is None:
            raise RuntimeError("Artifact stale marker committed without a readable record.")
        return updated

    def redact(
        self,
        *,
        bank_id: str,
        artifact_id: str,
        replacement_content: str,
        stale_at: str,
    ) -> ArtifactRecord:
        """Privacy-redact derived prose while retaining its audit identity."""

        with transaction(self._connection):
            self._connection.execute(
                """
                UPDATE artifacts
                SET content = ?, stale_at = ?
                WHERE bank_id = ? AND id = ?
                """,
                (replacement_content, stale_at, bank_id, artifact_id),
            )
        updated = self.get(bank_id, artifact_id)
        if updated is None:
            raise RuntimeError("Artifact redaction committed without a readable record.")
        return updated


def _hydrate_artifact(row: sqlite3.Row | None) -> ArtifactRecord | None:
    if row is None:
        return None
    return ArtifactRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        kind=row["kind"],
        artifact_key=row["artifact_key"],
        content=row["content"],
        source_claim_ids=json.loads(row["source_claim_ids_json"]),
        source_watermark=row["source_watermark"],
        generator_name=row["generator_name"],
        generator_version=row["generator_version"],
        stale_at=row["stale_at"],
        created_at=row["created_at"],
    )
