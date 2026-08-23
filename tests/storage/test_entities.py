from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.entities import EntityRepository
from memorygraph.storage.repositories.observations import ObservationRepository


class EntityRepositoryTests(unittest.TestCase):
    def test_create_entity_and_aliases(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            observation_id = seed_observation(connection, bank_id=bank_id)
            repository = EntityRepository(connection)

            entity = repository.create_entity(
                id="entity-1",
                bank_id=bank_id,
                canonical_name="MemoryGraph",
                normalized_name="memorygraph",
                entity_type="project",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            alias = repository.create_alias(
                id="alias-1",
                bank_id=bank_id,
                entity_id=entity.id,
                alias="memory graph",
                normalized_alias="memory graph",
                source_observation_id=observation_id,
                confidence=0.9,
                created_at="2026-08-21T00:00:01.000000Z",
            )

            self.assertEqual(repository.get_entity(bank_id, entity.id), entity)
            self.assertEqual(repository.get_alias(bank_id, alias.id), alias)
            self.assertEqual(repository.list_by_name(bank_id, "memorygraph"), (entity,))
            self.assertEqual(repository.find_by_alias(bank_id, "memory graph"), (alias,))

    def test_alias_lookup_is_bank_scoped(self) -> None:
        with temporary_connection() as connection:
            bank_one = seed_bank(connection, bank_id="bank-1", slug="project:one")
            bank_two = seed_bank(connection, bank_id="bank-2", slug="project:two")
            repository = EntityRepository(connection)

            first = repository.create_entity(
                id="entity-1",
                bank_id=bank_one,
                canonical_name="MemoryGraph",
                normalized_name="memorygraph",
                entity_type="project",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            second = repository.create_entity(
                id="entity-2",
                bank_id=bank_two,
                canonical_name="MemoryGraph",
                normalized_name="memorygraph",
                entity_type="project",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            repository.create_alias(
                id="alias-1",
                bank_id=bank_one,
                entity_id=first.id,
                alias="mg",
                normalized_alias="mg",
                confidence=0.9,
                created_at="2026-08-21T00:00:01.000000Z",
            )
            repository.create_alias(
                id="alias-2",
                bank_id=bank_two,
                entity_id=second.id,
                alias="mg",
                normalized_alias="mg",
                confidence=0.8,
                created_at="2026-08-21T00:00:01.000000Z",
            )

            self.assertEqual(len(repository.find_by_alias(bank_one, "mg")), 1)
            self.assertEqual(len(repository.find_by_alias(bank_two, "mg")), 1)


def seed_bank(connection, bank_id: str = "bank-1", slug: str = "project:memorygraph") -> str:
    MigrationRunner(connection).migrate()
    BankRepository(connection).create(
        id=bank_id,
        slug=slug,
        name=slug,
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return bank_id


def seed_observation(connection, bank_id: str) -> str:
    ObservationRepository(connection).create(
        id="obs-1",
        bank_id=bank_id,
        kind="message",
        source_key="session:1",
        content_sha256="sha-obs-1",
        content="MemoryGraph is the package name.",
        actor_type="user",
        observed_at="2026-08-21T00:00:00.000000Z",
        trust_class="owner_explicit",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return "obs-1"


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
