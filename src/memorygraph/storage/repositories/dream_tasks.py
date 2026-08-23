from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class DreamTaskRecord:
    id: str
    bank_id: str
    dream_run_id: str
    task_type: str
    resource_type: str
    resource_id: str
    idempotency_key: str
    state: str
    input: Any
    output: Any
    error: Any
    lease_owner: str | None
    lease_expires_at: str | None
    attempt_count: int
    created_at: str
    completed_at: str | None


class DreamTaskRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        dream_run_id: str,
        task_type: str,
        resource_type: str,
        resource_id: str,
        idempotency_key: str,
        state: str,
        input: Any,
        created_at: str,
        output: Any = None,
        error: Any = None,
        lease_owner: str | None = None,
        lease_expires_at: str | None = None,
        attempt_count: int = 0,
        completed_at: str | None = None,
    ) -> DreamTaskRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO dream_tasks(
                    id,
                    bank_id,
                    dream_run_id,
                    task_type,
                    resource_type,
                    resource_id,
                    idempotency_key,
                    state,
                    input_json,
                    output_json,
                    error_json,
                    lease_owner,
                    lease_expires_at,
                    attempt_count,
                    created_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    dream_run_id,
                    task_type,
                    resource_type,
                    resource_id,
                    idempotency_key,
                    state,
                    json.dumps(input, sort_keys=True),
                    _optional_json(output),
                    _optional_json(error),
                    lease_owner,
                    lease_expires_at,
                    attempt_count,
                    created_at,
                    completed_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Dream task insert committed without a readable record.")
        return record

    def get(self, bank_id: str, task_id: str) -> DreamTaskRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, dream_run_id, task_type, resource_type, resource_id,
                idempotency_key, state, input_json, output_json, error_json,
                lease_owner, lease_expires_at, attempt_count, created_at, completed_at
            FROM dream_tasks
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, task_id),
        ).fetchone()
        return _hydrate_dream_task(row)

    def get_by_idempotency_key(self, bank_id: str, idempotency_key: str) -> DreamTaskRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, dream_run_id, task_type, resource_type, resource_id,
                idempotency_key, state, input_json, output_json, error_json,
                lease_owner, lease_expires_at, attempt_count, created_at, completed_at
            FROM dream_tasks
            WHERE bank_id = ? AND idempotency_key = ?
            """,
            (bank_id, idempotency_key),
        ).fetchone()
        return _hydrate_dream_task(row)

    def lease_next(
        self,
        *,
        bank_id: str,
        lease_owner: str,
        lease_expires_at: str,
        task_type: str | None = None,
        dream_run_id: str | None = None,
        now: str | None = None,
    ) -> DreamTaskRecord | None:
        query = """
            SELECT id
            FROM dream_tasks
            WHERE bank_id = ? AND state = 'queued'
        """
        params: list[Any] = [bank_id]
        if task_type is not None:
            query += " AND task_type = ?"
            params.append(task_type)
        if dream_run_id is not None:
            query += " AND dream_run_id = ?"
            params.append(dream_run_id)
        if now is not None:
            query += " AND (lease_expires_at IS NULL OR lease_expires_at <= ?)"
            params.append(now)
        query += " ORDER BY created_at ASC, id ASC LIMIT 1"

        with transaction(self._connection):
            row = self._connection.execute(query, params).fetchone()
            if row is None:
                return None
            cursor = self._connection.execute(
                """
                UPDATE dream_tasks
                SET state = 'leased',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1
                WHERE bank_id = ? AND id = ? AND state = 'queued'
                """,
                (lease_owner, lease_expires_at, bank_id, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(bank_id, row["id"])

    def renew_lease(
        self,
        *,
        bank_id: str,
        task_id: str,
        lease_owner: str,
        lease_expires_at: str,
        from_states: tuple[str, ...] = ("leased", "running"),
    ) -> DreamTaskRecord | None:
        if not from_states:
            raise ValueError("from_states must not be empty.")
        placeholders = ", ".join("?" for _ in from_states)
        params: list[Any] = [
            lease_expires_at,
            bank_id,
            task_id,
            lease_owner,
            *from_states,
        ]
        with transaction(self._connection):
            cursor = self._connection.execute(
                f"""
                UPDATE dream_tasks
                SET lease_expires_at = ?
                WHERE bank_id = ?
                  AND id = ?
                  AND lease_owner = ?
                  AND state IN ({placeholders})
                """,
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get(bank_id, task_id)

    def requeue_expired_leases(self, *, bank_id: str, now: str) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                UPDATE dream_tasks
                SET state = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL
                WHERE bank_id = ?
                  AND state IN ('leased', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (bank_id, now),
            )
            return cursor.rowcount

    def transition_state(
        self,
        *,
        bank_id: str,
        task_id: str,
        from_states: tuple[str, ...],
        to_state: str,
        output: Any = ...,
        error: Any = ...,
        lease_owner: str | None | object = ...,
        lease_expires_at: str | None | object = ...,
        completed_at: str | None | object = ...,
    ) -> DreamTaskRecord:
        if not from_states:
            raise ValueError("from_states must not be empty.")
        with transaction(self._connection):
            record = self.get(bank_id, task_id)
            if record is None:
                raise ValueError(f"Unknown dream task {task_id!r} for bank {bank_id!r}.")
            if record.state not in from_states:
                raise ValueError(
                    f"Dream task {task_id!r} is in state {record.state!r}, "
                    f"expected one of {from_states!r}."
                )
            updates = ["state = ?"]
            params: list[Any] = [to_state]
            if output is not ...:
                updates.append("output_json = ?")
                params.append(_optional_json(output))
            if error is not ...:
                updates.append("error_json = ?")
                params.append(_optional_json(error))
            if lease_owner is not ...:
                updates.append("lease_owner = ?")
                params.append(lease_owner)
            if lease_expires_at is not ...:
                updates.append("lease_expires_at = ?")
                params.append(lease_expires_at)
            if completed_at is not ...:
                updates.append("completed_at = ?")
                params.append(completed_at)
            params.extend([bank_id, task_id])
            self._connection.execute(
                f"""
                UPDATE dream_tasks
                SET {", ".join(updates)}
                WHERE bank_id = ? AND id = ?
                """,
                params,
            )
        updated = self.get(bank_id, task_id)
        if updated is None:
            raise RuntimeError("Dream task state update committed without a readable record.")
        return updated


def _optional_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _hydrate_dream_task(row: sqlite3.Row | None) -> DreamTaskRecord | None:
    if row is None:
        return None
    return DreamTaskRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        dream_run_id=row["dream_run_id"],
        task_type=row["task_type"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        idempotency_key=row["idempotency_key"],
        state=row["state"],
        input=json.loads(row["input_json"]),
        output=None if row["output_json"] is None else json.loads(row["output_json"]),
        error=None if row["error_json"] is None else json.loads(row["error_json"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        attempt_count=row["attempt_count"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
