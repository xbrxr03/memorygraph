from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True)
class BankRecord:
    id: str
    slug: str
    name: str
    mission: str | None
    policy_json: dict[str, Any]
    created_at: str
    archived_at: str | None


class BankRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        slug: str,
        name: str,
        created_at: str,
        mission: str | None = None,
        policy_json: dict[str, Any] | None = None,
        archived_at: str | None = None,
    ) -> BankRecord:
        payload = json.dumps(policy_json or {}, sort_keys=True)
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO banks(
                    id,
                    slug,
                    name,
                    mission,
                    policy_json,
                    created_at,
                    archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (id, slug, name, mission, payload, created_at, archived_at),
            )
        record = self.get(id)
        if record is None:
            raise RuntimeError("Bank insert committed without a readable record.")
        return record

    def get(self, bank_id: str) -> BankRecord | None:
        row = self._connection.execute(
            """
            SELECT id, slug, name, mission, policy_json, created_at, archived_at
            FROM banks
            WHERE id = ?
            """,
            (bank_id,),
        ).fetchone()
        return self._hydrate(row)

    def get_by_slug(self, slug: str) -> BankRecord | None:
        row = self._connection.execute(
            """
            SELECT id, slug, name, mission, policy_json, created_at, archived_at
            FROM banks
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()
        return self._hydrate(row)

    def _hydrate(self, row: sqlite3.Row | None) -> BankRecord | None:
        if row is None:
            return None
        return BankRecord(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            mission=row["mission"],
            policy_json=json.loads(row["policy_json"]),
            created_at=row["created_at"],
            archived_at=row["archived_at"],
        )
