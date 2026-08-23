from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database import transaction


@dataclass(frozen=True)
class SearchDocumentRecord:
    rowid: int
    bank_id: str
    resource_type: str
    resource_id: str
    title: str
    body: str
    metadata_text: str
    content_sha256: str
    created_at: str


@dataclass(frozen=True)
class SearchHit:
    document: SearchDocumentRecord
    score: float


class SearchDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert(
        self,
        *,
        bank_id: str,
        resource_type: str,
        resource_id: str,
        body: str,
        content_sha256: str,
        created_at: str,
        title: str = "",
        metadata_text: str = "",
    ) -> SearchDocumentRecord:
        with transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO search_documents(
                    bank_id,
                    resource_type,
                    resource_id,
                    title,
                    body,
                    metadata_text,
                    content_sha256,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bank_id, resource_type, resource_id) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    metadata_text = excluded.metadata_text,
                    content_sha256 = excluded.content_sha256,
                    created_at = excluded.created_at
                """,
                (
                    bank_id,
                    resource_type,
                    resource_id,
                    title,
                    body,
                    metadata_text,
                    content_sha256,
                    created_at,
                ),
            )
        record = self.get(bank_id, resource_type, resource_id)
        if record is None:
            raise RuntimeError("Search document upsert committed without a readable record.")
        return record

    def get(
        self, bank_id: str, resource_type: str, resource_id: str
    ) -> SearchDocumentRecord | None:
        row = self._connection.execute(
            """
            SELECT
                rowid,
                bank_id,
                resource_type,
                resource_id,
                title,
                body,
                metadata_text,
                content_sha256,
                created_at
            FROM search_documents
            WHERE bank_id = ? AND resource_type = ? AND resource_id = ?
            """,
            (bank_id, resource_type, resource_id),
        ).fetchone()
        return _hydrate_search_document(row)

    def delete(self, bank_id: str, resource_type: str, resource_id: str) -> None:
        with transaction(self._connection):
            self._connection.execute(
                """
                DELETE FROM search_documents
                WHERE bank_id = ? AND resource_type = ? AND resource_id = ?
                """,
                (bank_id, resource_type, resource_id),
            )

    def search(self, *, bank_id: str, query: str, limit: int = 10) -> tuple[SearchHit, ...]:
        rows = self._connection.execute(
            """
            SELECT
                search_documents.rowid,
                search_documents.bank_id,
                search_documents.resource_type,
                search_documents.resource_id,
                search_documents.title,
                search_documents.body,
                search_documents.metadata_text,
                search_documents.content_sha256,
                search_documents.created_at,
                bm25(search_fts) AS score
            FROM search_fts
            JOIN search_documents ON search_documents.rowid = search_fts.rowid
            WHERE search_fts MATCH ? AND search_documents.bank_id = ?
            ORDER BY score ASC, search_documents.rowid ASC
            LIMIT ?
            """,
            (query, bank_id, limit),
        ).fetchall()
        return tuple(
            SearchHit(document=_hydrate_search_document(row), score=row["score"])
            for row in rows
            if row is not None
        )


def _hydrate_search_document(row: sqlite3.Row | None) -> SearchDocumentRecord | None:
    if row is None:
        return None
    return SearchDocumentRecord(
        rowid=row["rowid"],
        bank_id=row["bank_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        title=row["title"],
        body=row["body"],
        metadata_text=row["metadata_text"],
        content_sha256=row["content_sha256"],
        created_at=row["created_at"],
    )
