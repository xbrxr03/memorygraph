from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import (
    DatabaseConfig,
    Migration,
    MigrationRunner,
    connect,
    transaction,
)


class DatabaseTests(unittest.TestCase):
    def test_migrations_create_schema_and_search_triggers(self) -> None:
        with temporary_connection() as connection:
            version = MigrationRunner(connection).migrate()

            self.assertEqual(version, 2)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM schema_metadata WHERE key = 'minimum_reader_version'"
                ).fetchone()[0],
                "1",
            )

            connection.execute(
                """
                INSERT INTO banks(id, slug, name, created_at)
                VALUES ('bank-1', 'project:demo', 'Demo', '2026-08-21T00:00:00.000000Z')
                """
            )
            connection.execute(
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
                """,
                (
                    "bank-1",
                    "artifact",
                    "artifact-1",
                    "Build Notes",
                    "Use uv sync before tests.",
                    "uv sync",
                    "sha",
                    "2026-08-21T00:00:00.000000Z",
                ),
            )

            row = connection.execute(
                """
                SELECT search_documents.resource_id
                FROM search_fts
                JOIN search_documents ON search_documents.rowid = search_fts.rowid
                WHERE search_fts MATCH 'uv'
                """
            ).fetchone()
            self.assertEqual(row["resource_id"], "artifact-1")

            connection.execute("INSERT INTO procedural_fts(procedural_fts) VALUES('rebuild')")

    def test_migrate_is_idempotent(self) -> None:
        with temporary_connection() as connection:
            runner = MigrationRunner(connection)
            self.assertEqual(runner.migrate(), 2)
            self.assertEqual(runner.migrate(), 2)

    def test_transaction_rolls_back_on_error(self) -> None:
        with temporary_connection() as connection:
            MigrationRunner(connection).migrate()

            with self.assertRaises(sqlite3.IntegrityError), transaction(connection):
                connection.execute(
                    """
                        INSERT INTO banks(id, slug, name, created_at)
                        VALUES ('bank-1', 'project:demo', 'Demo', '2026-08-21T00:00:00.000000Z')
                        """
                )
                connection.execute(
                    """
                        INSERT INTO banks(id, slug, name, created_at)
                        VALUES ('bank-2', 'project:demo', 'Demo 2', '2026-08-21T00:00:00.000000Z')
                        """
                )

            count = connection.execute("SELECT COUNT(*) FROM banks").fetchone()[0]
            self.assertEqual(count, 0)

    def test_nested_transactions_use_savepoints(self) -> None:
        with temporary_connection() as connection:
            MigrationRunner(connection).migrate()

            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO banks(id, slug, name, created_at)
                    VALUES ('bank-1', 'project:first', 'First', '2026-08-21T00:00:00.000000Z')
                    """
                )
                with self.assertRaises(sqlite3.IntegrityError), transaction(connection):
                    connection.execute(
                        """
                        INSERT INTO banks(id, slug, name, created_at)
                        VALUES (
                            'bank-2', 'project:first', 'Duplicate Slug',
                            '2026-08-21T00:00:00.000000Z'
                        )
                        """
                    )
                connection.execute(
                    """
                    INSERT INTO banks(id, slug, name, created_at)
                    VALUES ('bank-3', 'project:second', 'Second', '2026-08-21T00:00:00.000000Z')
                    """
                )

            slugs = {
                row["slug"]
                for row in connection.execute("SELECT slug FROM banks ORDER BY slug").fetchall()
            }
            self.assertEqual(slugs, {"project:first", "project:second"})

    def test_transaction_rejects_unknown_mode(self) -> None:
        with (
            temporary_connection() as connection,
            self.assertRaisesRegex(ValueError, "Unsupported SQLite transaction mode"),
            transaction(connection, mode="IMMEDIATE; DROP TABLE banks"),
        ):
            pass

    def test_failed_migration_rolls_back_the_whole_script(self) -> None:
        with temporary_connection() as connection:
            runner = MigrationRunner(connection)
            runner.migrations = lambda: [
                Migration(
                    version=1,
                    name="0001_broken.sql",
                    sql="""
                    CREATE TABLE should_not_survive(id INTEGER PRIMARY KEY);
                    THIS IS NOT VALID SQL;
                    """,
                )
            ]

            with self.assertRaises(sqlite3.OperationalError):
                runner.migrate()

            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'should_not_survive'"
            ).fetchone()
            self.assertIsNone(row)


class temporary_connection:
    def __enter__(self) -> sqlite3.Connection:
        self._tempdir = tempfile.TemporaryDirectory()
        database_path = Path(self._tempdir.name) / "memorygraph.sqlite3"
        self._connection = connect(DatabaseConfig(path=database_path))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
