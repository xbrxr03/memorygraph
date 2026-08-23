from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database import transaction


@dataclass(frozen=True)
class ClaimRelationRecord:
    id: str
    bank_id: str
    from_claim_id: str
    to_claim_id: str
    relation: str
    rationale: str
    decision_method: str
    decision_confidence: float
    dream_run_id: str | None
    created_at: str


class ClaimRelationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        from_claim_id: str,
        to_claim_id: str,
        relation: str,
        rationale: str,
        decision_method: str,
        decision_confidence: float,
        created_at: str,
        dream_run_id: str | None = None,
    ) -> ClaimRelationRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO claim_relations(
                    id,
                    bank_id,
                    from_claim_id,
                    to_claim_id,
                    relation,
                    rationale,
                    decision_method,
                    decision_confidence,
                    dream_run_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    from_claim_id,
                    to_claim_id,
                    relation,
                    rationale,
                    decision_method,
                    decision_confidence,
                    dream_run_id,
                    created_at,
                ),
            )
        relation_record = self.get(bank_id, id)
        if relation_record is None:
            raise RuntimeError("Claim relation insert committed without a readable record.")
        return relation_record

    def get(self, bank_id: str, relation_id: str) -> ClaimRelationRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                from_claim_id,
                to_claim_id,
                relation,
                rationale,
                decision_method,
                decision_confidence,
                dream_run_id,
                created_at
            FROM claim_relations
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, relation_id),
        ).fetchone()
        return _hydrate_relation(row)

    def list_outgoing(self, bank_id: str, claim_id: str) -> tuple[ClaimRelationRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                from_claim_id,
                to_claim_id,
                relation,
                rationale,
                decision_method,
                decision_confidence,
                dream_run_id,
                created_at
            FROM claim_relations
            WHERE bank_id = ? AND from_claim_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (bank_id, claim_id),
        ).fetchall()
        return tuple(_hydrate_relation(row) for row in rows if row is not None)


def _hydrate_relation(row: sqlite3.Row | None) -> ClaimRelationRecord | None:
    if row is None:
        return None
    return ClaimRelationRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        from_claim_id=row["from_claim_id"],
        to_claim_id=row["to_claim_id"],
        relation=row["relation"],
        rationale=row["rationale"],
        decision_method=row["decision_method"],
        decision_confidence=row["decision_confidence"],
        dream_run_id=row["dream_run_id"],
        created_at=row["created_at"],
    )
