from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class MemoryEventRecord:
    sequence: int
    event_id: str
    bank_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    actor_id: str | None
    payload: Any
    idempotency_key: str | None
    created_at: str


class MemoryEventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(
        self,
        *,
        event_id: str,
        bank_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        actor_type: str,
        payload: Any,
        created_at: str,
        actor_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> MemoryEventRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO memory_events(
                    event_id,
                    bank_id,
                    event_type,
                    aggregate_type,
                    aggregate_id,
                    actor_type,
                    actor_id,
                    payload_json,
                    idempotency_key,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    bank_id,
                    event_type,
                    aggregate_type,
                    aggregate_id,
                    actor_type,
                    actor_id,
                    json.dumps(payload, sort_keys=True),
                    idempotency_key,
                    created_at,
                ),
            )
            row = self._connection.execute(
                """
                SELECT
                    sequence, event_id, bank_id, event_type, aggregate_type,
                    aggregate_id, actor_type, actor_id, payload_json,
                    idempotency_key, created_at
                FROM memory_events
                WHERE bank_id = ? AND event_id = ?
                """,
                (bank_id, event_id),
            ).fetchone()
        record = _hydrate_event(row)
        if record is None:
            raise RuntimeError("Memory event insert committed without a readable record.")
        return record

    def get(self, bank_id: str, event_id: str) -> MemoryEventRecord | None:
        row = self._connection.execute(
            """
            SELECT
                sequence, event_id, bank_id, event_type, aggregate_type,
                aggregate_id, actor_type, actor_id, payload_json,
                idempotency_key, created_at
            FROM memory_events
            WHERE bank_id = ? AND event_id = ?
            """,
            (bank_id, event_id),
        ).fetchone()
        return _hydrate_event(row)

    def get_by_idempotency_key(
        self, bank_id: str, idempotency_key: str
    ) -> MemoryEventRecord | None:
        row = self._connection.execute(
            """
            SELECT
                sequence, event_id, bank_id, event_type, aggregate_type,
                aggregate_id, actor_type, actor_id, payload_json,
                idempotency_key, created_at
            FROM memory_events
            WHERE bank_id = ? AND idempotency_key = ?
            """,
            (bank_id, idempotency_key),
        ).fetchone()
        return _hydrate_event(row)

    def current_watermark(self, bank_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS watermark FROM memory_events WHERE bank_id = ?",
            (bank_id,),
        ).fetchone()
        return int(row["watermark"])

    def list_after(
        self, bank_id: str, *, sequence_exclusive: int, limit: int = 100
    ) -> tuple[MemoryEventRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                sequence, event_id, bank_id, event_type, aggregate_type,
                aggregate_id, actor_type, actor_id, payload_json,
                idempotency_key, created_at
            FROM memory_events
            WHERE bank_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (bank_id, sequence_exclusive, limit),
        ).fetchall()
        return tuple(_hydrate_event(row) for row in rows if row is not None)


def _hydrate_event(row: sqlite3.Row | None) -> MemoryEventRecord | None:
    if row is None:
        return None
    return MemoryEventRecord(
        sequence=row["sequence"],
        event_id=row["event_id"],
        bank_id=row["bank_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        actor_type=row["actor_type"],
        actor_id=row["actor_id"],
        payload=json.loads(row["payload_json"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
    )
