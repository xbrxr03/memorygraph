from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction

_UNCHANGED = object()


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    bank_id: str
    subject_entity_id: str
    predicate: str
    object_kind: str
    object_entity_id: str | None
    object_value: Any
    polarity: str
    valid_from: str | None
    valid_to: str | None
    system_from: str
    system_to: str | None
    lifecycle: str
    origin: str
    importance: float
    created_by_run_id: str | None
    created_at: str


@dataclass(frozen=True)
class ClaimSuccessorResult:
    prior: ClaimRecord
    successor: ClaimRecord


class ClaimRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        subject_entity_id: str,
        predicate: str,
        object_kind: str,
        polarity: str,
        system_from: str,
        lifecycle: str,
        origin: str,
        importance: float,
        created_at: str,
        object_entity_id: str | None = None,
        object_value: Any = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        created_by_run_id: str | None = None,
        system_to: str | None = None,
    ) -> ClaimRecord:
        payload = _serialize_object_value(object_kind, object_value)
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO claims(
                    id,
                    bank_id,
                    subject_entity_id,
                    predicate,
                    object_kind,
                    object_entity_id,
                    object_value_json,
                    polarity,
                    valid_from,
                    valid_to,
                    system_from,
                    system_to,
                    lifecycle,
                    origin,
                    importance,
                    created_by_run_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    subject_entity_id,
                    predicate,
                    object_kind,
                    object_entity_id,
                    payload,
                    polarity,
                    valid_from,
                    valid_to,
                    system_from,
                    system_to,
                    lifecycle,
                    origin,
                    importance,
                    created_by_run_id,
                    created_at,
                ),
            )
        claim = self.get(bank_id, id)
        if claim is None:
            raise RuntimeError("Claim insert committed without a readable record.")
        return claim

    def get(self, bank_id: str, claim_id: str) -> ClaimRecord | None:
        row = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                subject_entity_id,
                predicate,
                object_kind,
                object_entity_id,
                object_value_json,
                polarity,
                valid_from,
                valid_to,
                system_from,
                system_to,
                lifecycle,
                origin,
                importance,
                created_by_run_id,
                created_at
            FROM claims
            WHERE bank_id = ? AND id = ?
            """,
            (bank_id, claim_id),
        ).fetchone()
        return _hydrate_claim(row)

    def list_versions(
        self, bank_id: str, subject_entity_id: str, predicate: str
    ) -> tuple[ClaimRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT
                id,
                bank_id,
                subject_entity_id,
                predicate,
                object_kind,
                object_entity_id,
                object_value_json,
                polarity,
                valid_from,
                valid_to,
                system_from,
                system_to,
                lifecycle,
                origin,
                importance,
                created_by_run_id,
                created_at
            FROM claims
            WHERE bank_id = ? AND subject_entity_id = ? AND predicate = ?
            ORDER BY system_from ASC, created_at ASC, id ASC
            """,
            (bank_id, subject_entity_id, predicate),
        ).fetchall()
        return tuple(_hydrate_claim(row) for row in rows if row is not None)

    def create_successor(
        self,
        *,
        bank_id: str,
        prior_claim_id: str,
        successor_id: str,
        successor_system_from: str,
        successor_created_at: str,
        successor_lifecycle: str,
        subject_entity_id: str | object = _UNCHANGED,
        predicate: str | object = _UNCHANGED,
        object_kind: str | object = _UNCHANGED,
        object_entity_id: str | None | object = _UNCHANGED,
        object_value: Any = _UNCHANGED,
        polarity: str | object = _UNCHANGED,
        valid_from: str | None | object = _UNCHANGED,
        valid_to: str | None | object = _UNCHANGED,
        origin: str | object = _UNCHANGED,
        importance: float | object = _UNCHANGED,
        created_by_run_id: str | None | object = _UNCHANGED,
    ) -> ClaimSuccessorResult:
        with transaction(self._connection):
            prior = self.get(bank_id, prior_claim_id)
            if prior is None:
                raise ValueError(f"Unknown claim {prior_claim_id!r} for bank {bank_id!r}.")
            if prior.system_to is not None:
                raise ValueError(
                    f"Claim {prior_claim_id!r} is already closed at {prior.system_to!r}."
                )
            if successor_system_from <= prior.system_from:
                raise ValueError(
                    "Successor system_from must be strictly after the prior system_from."
                )

            self._connection.execute(
                """
                UPDATE claims
                SET system_to = ?
                WHERE bank_id = ? AND id = ? AND system_to IS NULL
                """,
                (successor_system_from, bank_id, prior_claim_id),
            )

            successor = self.create(
                id=successor_id,
                bank_id=bank_id,
                subject_entity_id=prior.subject_entity_id
                if subject_entity_id is _UNCHANGED
                else subject_entity_id,
                predicate=prior.predicate if predicate is _UNCHANGED else predicate,
                object_kind=prior.object_kind if object_kind is _UNCHANGED else object_kind,
                object_entity_id=prior.object_entity_id
                if object_entity_id is _UNCHANGED
                else object_entity_id,
                object_value=prior.object_value if object_value is _UNCHANGED else object_value,
                polarity=prior.polarity if polarity is _UNCHANGED else polarity,
                valid_from=prior.valid_from if valid_from is _UNCHANGED else valid_from,
                valid_to=prior.valid_to if valid_to is _UNCHANGED else valid_to,
                system_from=successor_system_from,
                system_to=None,
                lifecycle=successor_lifecycle,
                origin=prior.origin if origin is _UNCHANGED else origin,
                importance=prior.importance if importance is _UNCHANGED else importance,
                created_by_run_id=(
                    prior.created_by_run_id
                    if created_by_run_id is _UNCHANGED
                    else created_by_run_id
                ),
                created_at=successor_created_at,
            )
            closed_prior = self.get(bank_id, prior_claim_id)
            if closed_prior is None:
                raise RuntimeError("Prior claim disappeared during successor creation.")
            return ClaimSuccessorResult(prior=closed_prior, successor=successor)


def _serialize_object_value(object_kind: str, object_value: Any) -> str | None:
    if object_kind == "entity":
        if object_value is not None:
            raise ValueError("Entity claims may not carry object_value.")
        return None
    return json.dumps(object_value)


def _hydrate_claim(row: sqlite3.Row | None) -> ClaimRecord | None:
    if row is None:
        return None
    object_value = None
    if row["object_value_json"] is not None:
        object_value = json.loads(row["object_value_json"])
    return ClaimRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        subject_entity_id=row["subject_entity_id"],
        predicate=row["predicate"],
        object_kind=row["object_kind"],
        object_entity_id=row["object_entity_id"],
        object_value=object_value,
        polarity=row["polarity"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        system_from=row["system_from"],
        system_to=row["system_to"],
        lifecycle=row["lifecycle"],
        origin=row["origin"],
        importance=row["importance"],
        created_by_run_id=row["created_by_run_id"],
        created_at=row["created_at"],
    )
