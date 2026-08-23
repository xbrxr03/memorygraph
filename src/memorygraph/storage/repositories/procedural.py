from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database import transaction


@dataclass(frozen=True, slots=True)
class ProceduralEpisodeRecord:
    id: str
    bank_id: str
    source_observation_id: str
    task_key: str
    strategy: str
    outcome: str
    failure: str | None
    applicability: Any
    environment: Any
    started_at: str | None
    completed_at: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ProceduralSearchHit:
    episode: ProceduralEpisodeRecord
    score: float


class ProceduralEpisodeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        id: str,
        bank_id: str,
        source_observation_id: str,
        task_key: str,
        strategy: str,
        outcome: str,
        applicability: Any,
        environment: Any,
        created_at: str,
        failure: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> ProceduralEpisodeRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO procedural_episodes(
                    id, bank_id, source_observation_id, task_key, strategy, outcome,
                    failure, applicability_json, environment_json,
                    started_at, completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id,
                    bank_id,
                    source_observation_id,
                    task_key,
                    strategy,
                    outcome,
                    failure,
                    json.dumps(applicability, sort_keys=True),
                    json.dumps(environment, sort_keys=True),
                    started_at,
                    completed_at,
                    created_at,
                ),
            )
        record = self.get(bank_id, id)
        if record is None:
            raise RuntimeError("Procedural episode insert committed without a readable record.")
        return record

    def get(self, bank_id: str, episode_id: str) -> ProceduralEpisodeRecord | None:
        row = self._connection.execute(
            "SELECT * FROM procedural_episodes WHERE bank_id = ? AND id = ?",
            (bank_id, episode_id),
        ).fetchone()
        return _hydrate(row)

    def get_by_source(
        self,
        bank_id: str,
        source_observation_id: str,
        task_key: str,
        strategy: str,
    ) -> ProceduralEpisodeRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM procedural_episodes
            WHERE bank_id = ? AND source_observation_id = ? AND task_key = ? AND strategy = ?
            """,
            (bank_id, source_observation_id, task_key, strategy),
        ).fetchone()
        return _hydrate(row)

    def search(
        self,
        *,
        bank_id: str,
        query: str,
        limit: int = 10,
    ) -> tuple[ProceduralSearchHit, ...]:
        rows = self._connection.execute(
            """
            SELECT procedural_episodes.*, bm25(procedural_fts) AS score
            FROM procedural_fts
            JOIN procedural_episodes ON procedural_episodes.rowid = procedural_fts.rowid
            WHERE procedural_fts MATCH ? AND procedural_episodes.bank_id = ?
            ORDER BY score ASC, procedural_episodes.created_at DESC, procedural_episodes.id
            LIMIT ?
            """,
            (query, bank_id, limit),
        ).fetchall()
        return tuple(
            ProceduralSearchHit(episode=_hydrate(row), score=float(row["score"]))
            for row in rows
            if row is not None
        )

    def redact_for_source(
        self,
        *,
        bank_id: str,
        source_observation_id: str,
        replacement: str,
    ) -> int:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """
                UPDATE procedural_episodes
                SET strategy = ?, failure = ?, applicability_json = '{}', environment_json = '{}'
                WHERE bank_id = ? AND source_observation_id = ?
                """,
                (replacement, replacement, bank_id, source_observation_id),
            )
        return cursor.rowcount


def _hydrate(row: sqlite3.Row | None) -> ProceduralEpisodeRecord | None:
    if row is None:
        return None
    return ProceduralEpisodeRecord(
        id=row["id"],
        bank_id=row["bank_id"],
        source_observation_id=row["source_observation_id"],
        task_key=row["task_key"],
        strategy=row["strategy"],
        outcome=row["outcome"],
        failure=row["failure"],
        applicability=json.loads(row["applicability_json"]),
        environment=json.loads(row["environment_json"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )
