from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class ReviewItemRecord:
    id: str
    bank_id: str
    proposal_id: str
    reason: str
    state: str
    reviewer_type: str | None
    reviewer_id: str | None
    decision: Any
    created_at: str
    decided_at: str | None


class ReviewItemRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        proposal_id: str,
        reason: str,
        state: str,
        created_at: str,
        reviewer_type: str | None = None,
        reviewer_id: str | None = None,
        decision: Any = None,
        decided_at: str | None = None,
    ) -> ReviewItemRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO review_items(
                    id,
                    bank_id,
                    proposal_id,
                    reason,
                    state,
                    reviewer_type,
                    reviewer_id,
                    decision_json,
                    created_at,
                    decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    proposal_id,
                    reason,
                    state,
                    reviewer_type,
                    reviewer_id,
                    _optional_json(decision),
                    created_at,
                    decided_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Review item insert committed without a readable record.")
        return record

    def get(self, bank_id: str, review_id: str) -> ReviewItemRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, proposal_id, reason, state, reviewer_type,
                reviewer_id, decision_json, created_at, decided_at
            FROM review_items
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, review_id),
        ).fetchone()
        return _hydrate_review(row)

    def get_by_proposal(self, bank_id: str, proposal_id: str) -> ReviewItemRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, proposal_id, reason, state, reviewer_type,
                reviewer_id, decision_json, created_at, decided_at
            FROM review_items
            WHERE bank_id = ? AND proposal_id = ?
            """,
            (bank_id, proposal_id),
        ).fetchone()
        return _hydrate_review(row)

    def list_pending(self, bank_id: str, *, limit: int = 100) -> tuple[ReviewItemRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id, bank_id, proposal_id, reason, state, reviewer_type,
                reviewer_id, decision_json, created_at, decided_at
            FROM review_items
            WHERE bank_id = ? AND state = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (bank_id, limit),
        ).fetchall()
        return tuple(_hydrate_review(row) for row in rows if row is not None)

    def decide(
        self,
        *,
        bank_id: str,
        review_id: str,
        state: str,
        reviewer_type: str,
        reviewer_id: str,
        decided_at: str,
        decision: Any = None,
    ) -> ReviewItemRecord:
        with transaction(self._connection):
            current = self.get(bank_id, review_id)
            if current is None:
                raise ValueError(f"Unknown review item {review_id!r} for bank {bank_id!r}.")
            if current.state != "pending":
                raise ValueError(
                    f"Review item {review_id!r} is already decided with state "
                    f"{current.state!r}."
                )
            self._connection.execute(
                """
                UPDATE review_items
                SET state = ?,
                    reviewer_type = ?,
                    reviewer_id = ?,
                    decision_json = ?,
                    decided_at = ?
                WHERE bank_id = ? AND id = ? AND state = 'pending'
                """,
                (
                    state,
                    reviewer_type,
                    reviewer_id,
                    _optional_json(decision),
                    decided_at,
                    bank_id,
                    review_id,
                ),
            )
        updated = self.get(bank_id, review_id)
        if updated is None:
            raise RuntimeError("Review item update committed without a readable record.")
        return updated


def _optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _hydrate_review(row: sqlite3.Row | None) -> ReviewItemRecord | None:
    if row is None:
        return None
    return ReviewItemRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        proposal_id=row["proposal_id"],
        reason=row["reason"],
        state=row["state"],
        reviewer_type=row["reviewer_type"],
        reviewer_id=row["reviewer_id"],
        decision=None if row["decision_json"] is None else json.loads(row["decision_json"]),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )
