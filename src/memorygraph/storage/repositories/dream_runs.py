from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class DreamRunRecord:
    id: str
    bank_id: str
    trigger: str
    mode: str
    state: str
    input_watermark: int
    policy_version: str
    provider_config_hash: str
    lease_owner: str | None
    lease_expires_at: str | None
    attempt_count: int
    budget: Any
    usage: Any
    error: Any
    started_at: str | None
    completed_at: str | None
    created_at: str


class DreamRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        trigger: str,
        mode: str,
        state: str,
        policy_version: str,
        provider_config_hash: str,
        created_at: str,
        input_watermark: int = 0,
        lease_owner: str | None = None,
        lease_expires_at: str | None = None,
        attempt_count: int = 0,
        budget: Any = None,
        usage: Any = None,
        error: Any = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> DreamRunRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO dream_runs(
                    id,
                    bank_id,
                    trigger,
                    mode,
                    state,
                    input_watermark,
                    policy_version,
                    provider_config_hash,
                    lease_owner,
                    lease_expires_at,
                    attempt_count,
                    budget_json,
                    usage_json,
                    error_json,
                    started_at,
                    completed_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    trigger,
                    mode,
                    state,
                    input_watermark,
                    policy_version,
                    provider_config_hash,
                    lease_owner,
                    lease_expires_at,
                    attempt_count,
                    _dump_json(budget, default={}),
                    _dump_json(usage, default={}),
                    _dump_json(error),
                    started_at,
                    completed_at,
                    created_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Dream run insert committed without a readable record.")
        return record

    def get(self, bank_id: str, run_id: str) -> DreamRunRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id, bank_id, trigger, mode, state, input_watermark, policy_version,
                provider_config_hash, lease_owner, lease_expires_at, attempt_count,
                budget_json, usage_json, error_json, started_at, completed_at, created_at
            FROM dream_runs
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, run_id),
        ).fetchone()
        return _hydrate_dream_run(row)

    def lease_next(
        self,
        *,
        bank_id: str,
        lease_owner: str,
        lease_expires_at: str,
        now: str | None = None,
    ) -> DreamRunRecord | None:
        with transaction(self._connection):
            conditions = ["bank_id = ?", "state = 'queued'"]
            params: list[Any] = [bank_id]
            if now is not None:
                conditions.append("(lease_expires_at IS NULL OR lease_expires_at <= ?)")
                params.append(now)
            row = self._connection.execute(
                f"""
                SELECT id
                FROM dream_runs
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            cursor = self._connection.execute(
                """
                UPDATE dream_runs
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
        run_id: str,
        lease_owner: str,
        lease_expires_at: str,
        from_states: tuple[str, ...] = ("leased", "running"),
    ) -> DreamRunRecord | None:
        if not from_states:
            raise ValueError("from_states must not be empty.")
        placeholders = ", ".join("?" for _ in from_states)
        params: list[Any] = [
            lease_expires_at,
            bank_id,
            run_id,
            lease_owner,
            *from_states,
        ]
        with transaction(self._connection):
            cursor = self._connection.execute(
                f"""
                UPDATE dream_runs
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
        return self.get(bank_id, run_id)

    def requeue_expired_leases(self, *, bank_id: str, now: str) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                UPDATE dream_runs
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
        run_id: str,
        from_states: tuple[str, ...],
        to_state: str,
        started_at: str | None | object = ...,
        completed_at: str | None | object = ...,
        lease_owner: str | None | object = ...,
        lease_expires_at: str | None | object = ...,
        usage: Any = ...,
        error: Any = ...,
    ) -> DreamRunRecord:
        if not from_states:
            raise ValueError("from_states must not be empty.")
        with transaction(self._connection):
            record = self.get(bank_id, run_id)
            if record is None:
                raise ValueError(f"Unknown dream run {run_id!r} for bank {bank_id!r}.")
            if record.state not in from_states:
                raise ValueError(
                    f"Dream run {run_id!r} is in state {record.state!r}, "
                    f"expected one of {from_states!r}."
                )

            updates = ["state = ?"]
            params: list[Any] = [to_state]
            if started_at is not ...:
                updates.append("started_at = ?")
                params.append(started_at)
            if completed_at is not ...:
                updates.append("completed_at = ?")
                params.append(completed_at)
            if lease_owner is not ...:
                updates.append("lease_owner = ?")
                params.append(lease_owner)
            if lease_expires_at is not ...:
                updates.append("lease_expires_at = ?")
                params.append(lease_expires_at)
            if usage is not ...:
                updates.append("usage_json = ?")
                params.append(_dump_json(usage, default={}))
            if error is not ...:
                updates.append("error_json = ?")
                params.append(_dump_json(error))

            params.extend([bank_id, run_id])
            self._connection.execute(
                f"""
                UPDATE dream_runs
                SET {", ".join(updates)}
                WHERE bank_id = ? AND id = ?
                """,
                params,
            )
        updated = self.get(bank_id, run_id)
        if updated is None:
            raise RuntimeError("Dream run state update committed without a readable record.")
        return updated


def _dump_json(value: Any, *, default: Any | None = None) -> str | None:
    if value is ...:
        raise ValueError("Sentinel value cannot be serialized.")
    if value is None:
        if default is None:
            return None
        return json.dumps(default, sort_keys=True)
    return json.dumps(value, sort_keys=True)


def _hydrate_dream_run(row: sqlite3.Row | None) -> DreamRunRecord | None:
    if row is None:
        return None
    return DreamRunRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        trigger=row["trigger"],
        mode=row["mode"],
        state=row["state"],
        input_watermark=row["input_watermark"],
        policy_version=row["policy_version"],
        provider_config_hash=row["provider_config_hash"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        attempt_count=row["attempt_count"],
        budget=json.loads(row["budget_json"]),
        usage=json.loads(row["usage_json"]),
        error=None if row["error_json"] is None else json.loads(row["error_json"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )
