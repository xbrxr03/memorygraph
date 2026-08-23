from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class DreamProposalRecord:
    id: str
    bank_id: str
    dream_run_id: str
    proposal_type: str
    preconditions: Any
    action: Any
    evidence_ids: Any
    model_trace: Any
    validation: Any
    disposition: str
    created_at: str


class DreamProposalRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        dream_run_id: str,
        proposal_type: str,
        preconditions: Any,
        action: Any,
        evidence_ids: Any,
        disposition: str,
        created_at: str,
        model_trace: Any = None,
        validation: Any = None,
    ) -> DreamProposalRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO dream_proposals(
                    id,
                    bank_id,
                    dream_run_id,
                    proposal_type,
                    preconditions_json,
                    action_json,
                    evidence_ids_json,
                    model_trace_json,
                    validation_json,
                    disposition,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    dream_run_id,
                    proposal_type,
                    json.dumps(preconditions, sort_keys=True),
                    json.dumps(action, sort_keys=True),
                    json.dumps(evidence_ids, sort_keys=True),
                    _optional_json(model_trace),
                    json.dumps(validation if validation is not None else {}, sort_keys=True),
                    disposition,
                    created_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Dream proposal insert committed without a readable record.")
        return record

    def get(self, bank_id: str, proposal_id: str) -> DreamProposalRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, dream_run_id, proposal_type, preconditions_json,
                action_json, evidence_ids_json, model_trace_json, validation_json,
                disposition, created_at
            FROM dream_proposals
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, proposal_id),
        ).fetchone()
        return _hydrate_proposal(row)

    def list_for_run(self, bank_id: str, dream_run_id: str) -> tuple[DreamProposalRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id, bank_id, dream_run_id, proposal_type, preconditions_json,
                action_json, evidence_ids_json, model_trace_json, validation_json,
                disposition, created_at
            FROM dream_proposals
            WHERE bank_id = ? AND dream_run_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (bank_id, dream_run_id),
        ).fetchall()
        return tuple(_hydrate_proposal(row) for row in rows if row is not None)

    def list_by_disposition(
        self, bank_id: str, disposition: str, *, limit: int = 100
    ) -> tuple[DreamProposalRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id, bank_id, dream_run_id, proposal_type, preconditions_json,
                action_json, evidence_ids_json, model_trace_json, validation_json,
                disposition, created_at
            FROM dream_proposals
            WHERE bank_id = ? AND disposition = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (bank_id, disposition, limit),
        ).fetchall()
        return tuple(_hydrate_proposal(row) for row in rows if row is not None)

    def update(
        self,
        *,
        bank_id: str,
        proposal_id: str,
        disposition: str | object = ...,
        validation: Any = ...,
        model_trace: Any = ...,
    ) -> DreamProposalRecord:
        updates: list[str] = []
        params: list[Any] = []
        if disposition is not ...:
            updates.append("disposition = ?")
            params.append(disposition)
        if validation is not ...:
            updates.append("validation_json = ?")
            params.append(json.dumps(validation if validation is not None else {}, sort_keys=True))
        if model_trace is not ...:
            updates.append("model_trace_json = ?")
            params.append(_optional_json(model_trace))
        if not updates:
            raise ValueError("At least one field must be updated.")

        with transaction(self._connection):
            params.extend([bank_id, proposal_id])
            self._connection.execute(
                f"""
                UPDATE dream_proposals
                SET {", ".join(updates)}
                WHERE bank_id = ? AND id = ?
                """,
                params,
            )
        record = self.get(bank_id, proposal_id)
        if record is None:
            raise RuntimeError("Dream proposal update committed without a readable record.")
        return record


def _optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _hydrate_proposal(row: sqlite3.Row | None) -> DreamProposalRecord | None:
    if row is None:
        return None
    return DreamProposalRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        dream_run_id=row["dream_run_id"],
        proposal_type=row["proposal_type"],
        preconditions=json.loads(row["preconditions_json"]),
        action=json.loads(row["action_json"]),
        evidence_ids=json.loads(row["evidence_ids_json"]),
        model_trace=(
            None
            if row["model_trace_json"] is None
            else json.loads(row["model_trace_json"])
        ),
        validation=json.loads(row["validation_json"]),
        disposition=row["disposition"],
        created_at=row["created_at"],
    )
