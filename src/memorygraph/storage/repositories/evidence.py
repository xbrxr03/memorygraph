from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database import transaction


@dataclass(frozen=True)
class ClaimEvidenceRecord:
    id: str
    bank_id: str
    claim_id: str
    observation_id: str
    chunk_id: str | None
    start_offset: int
    end_offset: int
    excerpt: str
    stance: str
    explicitness: str
    source_reliability: float
    extraction_confidence: float
    extractor_name: str
    extractor_version: str
    created_at: str


class ClaimEvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        claim_id: str,
        observation_id: str,
        start_offset: int,
        end_offset: int,
        excerpt: str,
        stance: str,
        explicitness: str,
        source_reliability: float,
        extraction_confidence: float,
        extractor_name: str,
        extractor_version: str,
        created_at: str,
        chunk_id: str | None = None,
    ) -> ClaimEvidenceRecord:
        self._validate_excerpt(
            bank_id=bank_id,
            observation_id=observation_id,
            chunk_id=chunk_id,
            start_offset=start_offset,
            end_offset=end_offset,
            excerpt=excerpt,
        )
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO claim_evidence(
                    id,
                    bank_id,
                    claim_id,
                    observation_id,
                    chunk_id,
                    start_offset,
                    end_offset,
                    excerpt,
                    stance,
                    explicitness,
                    source_reliability,
                    extraction_confidence,
                    extractor_name,
                    extractor_version,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    claim_id,
                    observation_id,
                    chunk_id,
                    start_offset,
                    end_offset,
                    excerpt,
                    stance,
                    explicitness,
                    source_reliability,
                    extraction_confidence,
                    extractor_name,
                    extractor_version,
                    created_at,
                ),
            )
        evidence = self.get(bank_id, id)
        if evidence is None:
            raise RuntimeError("Claim evidence insert committed without a readable record.")
        return evidence

    def get(self, bank_id: str, evidence_id: str) -> ClaimEvidenceRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                claim_id,
                observation_id,
                chunk_id,
                start_offset,
                end_offset,
                excerpt,
                stance,
                explicitness,
                source_reliability,
                extraction_confidence,
                extractor_name,
                extractor_version,
                created_at
            FROM claim_evidence
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, evidence_id),
        ).fetchone()
        return _hydrate_evidence(row)

    def list_for_claim(self, bank_id: str, claim_id: str) -> tuple[ClaimEvidenceRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                claim_id,
                observation_id,
                chunk_id,
                start_offset,
                end_offset,
                excerpt,
                stance,
                explicitness,
                source_reliability,
                extraction_confidence,
                extractor_name,
                extractor_version,
                created_at
            FROM claim_evidence
            WHERE bank_id = ? AND claim_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (bank_id, claim_id),
        ).fetchall()
        return tuple(_hydrate_evidence(row) for row in rows if row is not None)

    def list_for_observation(
        self, bank_id: str, observation_id: str
    ) -> tuple[ClaimEvidenceRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                claim_id,
                observation_id,
                chunk_id,
                start_offset,
                end_offset,
                excerpt,
                stance,
                explicitness,
                source_reliability,
                extraction_confidence,
                extractor_name,
                extractor_version,
                created_at
            FROM claim_evidence
            WHERE bank_id = ? AND observation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (bank_id, observation_id),
        ).fetchall()
        return tuple(_hydrate_evidence(row) for row in rows if row is not None)

    def delete_for_observation(self, bank_id: str, observation_id: str) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                DELETE FROM claim_evidence
                WHERE bank_id = ? AND observation_id = ?
                """,
                (bank_id, observation_id),
            )
            return cursor.rowcount

    def delete_matching(
        self,
        *,
        bank_id: str,
        claim_id: str,
        observation_id: str,
        start_offset: int,
        end_offset: int,
    ) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                DELETE FROM claim_evidence
                WHERE bank_id = ?
                  AND claim_id = ?
                  AND observation_id = ?
                  AND start_offset = ?
                  AND end_offset = ?
                """,
                (bank_id, claim_id, observation_id, start_offset, end_offset),
            )
            return cursor.rowcount

    def _validate_excerpt(
        self,
        *,
        bank_id: str,
        observation_id: str,
        chunk_id: str | None,
        start_offset: int,
        end_offset: int,
        excerpt: str,
    ) -> None:
        if start_offset < 0 or end_offset < start_offset:
            raise ValueError("Evidence offsets must form a valid half-open interval.")

        if chunk_id is None:
            row = self._connection.execute(
                """
                SELECT content
                FROM observations
                WHERE bank_id = ? AND id = ?
                """,
                (bank_id, observation_id),
            ).fetchone()
        else:
            row = self._connection.execute(
                """
                SELECT content, observation_id
                FROM observation_chunks
                WHERE bank_id = ? AND id = ?
                """,
                (bank_id, chunk_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown chunk {chunk_id!r} for bank {bank_id!r}.")
            if row["observation_id"] != observation_id:
                raise ValueError("Chunk does not belong to the supplied observation.")
        if row is None:
            raise ValueError(f"Unknown observation {observation_id!r} for bank {bank_id!r}.")

        content = row["content"]
        if end_offset > len(content):
            raise ValueError("Evidence offsets exceed the referenced source content length.")
        if content[start_offset:end_offset] != excerpt:
            raise ValueError("Evidence excerpt does not match the referenced source span.")


def _hydrate_evidence(row: sqlite3.Row | None) -> ClaimEvidenceRecord | None:
    if row is None:
        return None
    return ClaimEvidenceRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        claim_id=row["claim_id"],
        observation_id=row["observation_id"],
        chunk_id=row["chunk_id"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        excerpt=row["excerpt"],
        stance=row["stance"],
        explicitness=row["explicitness"],
        source_reliability=row["source_reliability"],
        extraction_confidence=row["extraction_confidence"],
        extractor_name=row["extractor_name"],
        extractor_version=row["extractor_version"],
        created_at=row["created_at"],
    )
