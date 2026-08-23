from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class ObservationChunkInput:
    id: str
    ordinal: int
    start_offset: int
    end_offset: int
    content: str
    content_sha256: str
    created_at: str


@dataclass(frozen=True)
class ObservationChunkRecord:
    id: str
    bank_id: str
    observation_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    content: str
    content_sha256: str
    created_at: str


@dataclass(frozen=True)
class ObservationRecord:
    id: str
    bank_id: str
    kind: str
    source_key: str
    content_sha256: str
    content: str
    actor_type: str
    actor_id: str | None
    observed_at: str
    effective_at: str | None
    trust_class: str
    sensitivity: str
    metadata_json: dict[str, Any]
    ingestion_state: str
    created_at: str
    chunks: tuple[ObservationChunkRecord, ...]


class ObservationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        kind: str,
        source_key: str,
        content_sha256: str,
        content: str,
        actor_type: str,
        observed_at: str,
        trust_class: str,
        created_at: str,
        actor_id: str | None = None,
        effective_at: str | None = None,
        sensitivity: str = "normal",
        metadata_json: dict[str, Any] | None = None,
        ingestion_state: str = "pending",
        chunks: Iterable[ObservationChunkInput] = (),
    ) -> ObservationRecord:
        metadata_payload = json.dumps(metadata_json or {}, sort_keys=True)
        chunk_rows = tuple(chunks)
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO observations(
                    id,
                    bank_id,
                    kind,
                    source_key,
                    content_sha256,
                    content,
                    actor_type,
                    actor_id,
                    observed_at,
                    effective_at,
                    trust_class,
                    sensitivity,
                    metadata_json,
                    ingestion_state,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    kind,
                    source_key,
                    content_sha256,
                    content,
                    actor_type,
                    actor_id,
                    observed_at,
                    effective_at,
                    trust_class,
                    sensitivity,
                    metadata_payload,
                    ingestion_state,
                    created_at,
                ),
            )
            for chunk in chunk_rows:
                self._connection.execute(
                    """
                    INSERT INTO observation_chunks(
                        id,
                        bank_id,
                        observation_id,
                        ordinal,
                        start_offset,
                        end_offset,
                        content,
                        content_sha256,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        bank_id,
                        id,
                        chunk.ordinal,
                        chunk.start_offset,
                        chunk.end_offset,
                        chunk.content,
                        chunk.content_sha256,
                        chunk.created_at,
                    ),
                )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Observation insert committed without a readable record.")
        return record

    def get(self, bank_id: str, observation_id: str) -> ObservationRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                kind,
                source_key,
                content_sha256,
                content,
                actor_type,
                actor_id,
                observed_at,
                effective_at,
                trust_class,
                sensitivity,
                metadata_json,
                ingestion_state,
                created_at
            FROM observations
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, observation_id),
        ).fetchone()
        if row is None:
            return None
        chunks = self.get_chunks(bank_id, observation_id)
        return ObservationRecord(
            id=row["id"],
            bank_id=row["bank_id"],
            kind=row["kind"],
            source_key=row["source_key"],
            content_sha256=row["content_sha256"],
            content=row["content"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            observed_at=row["observed_at"],
            effective_at=row["effective_at"],
            trust_class=row["trust_class"],
            sensitivity=row["sensitivity"],
            metadata_json=json.loads(row["metadata_json"]),
            ingestion_state=row["ingestion_state"],
            created_at=row["created_at"],
            chunks=chunks,
        )

    def list_by_source_key(self, bank_id: str, source_key: str) -> tuple[ObservationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT id
            FROM observations
            WHERE bank_id = ? AND source_key = ?
            ORDER BY observed_at ASC, created_at ASC, id ASC
            """,
            (bank_id, source_key),
        ).fetchall()
        return tuple(
            record
            for record in (self.get(bank_id, row["id"]) for row in rows)
            if record is not None
        )

    def list_for_ingestion(
        self,
        bank_id: str,
        *,
        states: tuple[str, ...] = ("pending",),
        after_event_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[ObservationRecord, ...]:
        if not states:
            raise ValueError("states must not be empty.")

        placeholders = ", ".join("?" for _ in states)
        params: list[object] = [bank_id, *states]
        watermark_filter = ""
        if after_event_sequence is not None:
            watermark_filter = """
                AND EXISTS (
                    SELECT 1
                    FROM memory_events AS me
                    WHERE me.bank_id = observations.bank_id
                      AND me.aggregate_type = 'observation'
                      AND me.aggregate_id = observations.id
                      AND me.sequence > ?
                )
            """
            params.append(after_event_sequence)
        params.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT observations.id
            FROM observations
            WHERE observations.bank_id = ?
              AND observations.ingestion_state IN ({placeholders})
              {watermark_filter}
            ORDER BY observations.observed_at ASC, observations.created_at ASC, observations.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return tuple(
            record
            for record in (self.get(bank_id, row["id"]) for row in rows)
            if record is not None
        )

    def transition_ingestion_state(
        self,
        *,
        bank_id: str,
        observation_id: str,
        from_states: tuple[str, ...],
        to_state: str,
    ) -> ObservationRecord:
        if not from_states:
            raise ValueError("from_states must not be empty.")
        placeholders = ", ".join("?" for _ in from_states)
        with transaction(self._connection):
            current = self.get(bank_id, observation_id)
            if current is None:
                raise ValueError(f"Unknown observation {observation_id!r} for bank {bank_id!r}.")
            if current.ingestion_state not in from_states:
                raise ValueError(
                    f"Observation {observation_id!r} is in state {current.ingestion_state!r}, "
                    f"expected one of {from_states!r}."
                )
            cursor = self._connection.execute(
                f"""
                UPDATE observations
                SET ingestion_state = ?
                WHERE bank_id = ? AND id = ? AND ingestion_state IN ({placeholders})
                """,
                (to_state, bank_id, observation_id, *from_states),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Observation ingestion state update did not affect exactly one row."
                )
        updated = self.get(bank_id, observation_id)
        if updated is None:
            raise RuntimeError("Observation state update committed without a readable record.")
        return updated

    def tombstone(
        self,
        bank_id: str,
        observation_id: str,
        deleted_at: str,
    ) -> ObservationRecord:
        with transaction(self._connection):
            current = self.get(bank_id, observation_id)
            if current is None:
                raise ValueError(f"Unknown observation {observation_id!r} for bank {bank_id!r}.")
            if current.ingestion_state == "deleted":
                return current

            observation_hash = _deletion_hash(
                bank_id=bank_id,
                record_id=observation_id,
                deleted_at=deleted_at,
                label="observation",
            )
            observation_content = _deleted_content(observation_hash)
            self._connection.execute(
                """
                UPDATE observations
                SET content = ?,
                    content_sha256 = ?,
                    metadata_json = ?,
                    ingestion_state = 'deleted'
                WHERE bank_id = ? AND id = ?
                """,
                (
                    observation_content,
                    hashlib.sha256(observation_content.encode("utf-8")).hexdigest(),
                    json.dumps(
                        {
                            "deleted": True,
                            "deleted_at": deleted_at,
                            "deletion_hash": observation_hash,
                        },
                        sort_keys=True,
                    ),
                    bank_id,
                    observation_id,
                ),
            )

            chunk_rows = self._connection.execute(
                """
                SELECT id
                FROM observation_chunks
                WHERE bank_id = ? AND observation_id = ?
                ORDER BY ordinal ASC
                """,
                (bank_id, observation_id),
            ).fetchall()
            for row in chunk_rows:
                chunk_hash = _deletion_hash(
                    bank_id=bank_id,
                    record_id=row["id"],
                    deleted_at=deleted_at,
                    label="observation_chunk",
                )
                chunk_content = _deleted_content(chunk_hash)
                self._connection.execute(
                    """
                    UPDATE observation_chunks
                    SET content = ?,
                        content_sha256 = ?
                    WHERE bank_id = ? AND id = ?
                    """,
                    (
                        chunk_content,
                        hashlib.sha256(chunk_content.encode("utf-8")).hexdigest(),
                        bank_id,
                        row["id"],
                    ),
                )

        updated = self.get(bank_id, observation_id)
        if updated is None:
            raise RuntimeError("Observation tombstone committed without a readable record.")
        return updated

    def get_chunks(self, bank_id: str, observation_id: str) -> tuple[ObservationChunkRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                observation_id,
                ordinal,
                start_offset,
                end_offset,
                content,
                content_sha256,
                created_at
            FROM observation_chunks
            WHERE bank_id = ? AND observation_id = ?
            ORDER BY ordinal ASC
            """,
            (bank_id, observation_id),
        ).fetchall()
        return tuple(
            ObservationChunkRecord(
                id=row["id"],
                bank_id=row["bank_id"],
                observation_id=row["observation_id"],
                ordinal=row["ordinal"],
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
                content=row["content"],
                content_sha256=row["content_sha256"],
                created_at=row["created_at"],
            )
            for row in rows
        )


def _deletion_hash(*, bank_id: str, record_id: str, deleted_at: str, label: str) -> str:
    digest = hashlib.sha256(f"{label}:{bank_id}:{record_id}:{deleted_at}".encode()).hexdigest()
    return digest


def _deleted_content(deletion_hash: str) -> str:
    return f"[deleted:{deletion_hash}]"
