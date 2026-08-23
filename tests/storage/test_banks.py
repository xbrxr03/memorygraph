from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository


class BankRepositoryTests(unittest.TestCase):
    def test_create_and_lookup_bank(self) -> None:
        with temporary_connection() as connection:
            MigrationRunner(connection).migrate()
            repository = BankRepository(connection)

            created = repository.create(
                id="bank-1",
                slug="project:memorygraph",
                name="MemoryGraph",
                mission="Preserve evidence.",
                policy_json={"rank": "strict"},
                created_at="2026-08-21T00:00:00.000000Z",
            )

            fetched = repository.get("bank-1")
            fetched_by_slug = repository.get_by_slug("project:memorygraph")

            self.assertEqual(created, fetched)
            self.assertEqual(fetched_by_slug, fetched)
            self.assertEqual(fetched.policy_json, {"rank": "strict"})


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
