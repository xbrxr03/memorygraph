from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.artifacts import ArtifactRepository
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.events import MemoryEventRepository


class ArtifactRepositoryTests(unittest.TestCase):
    def test_version_order_and_stale_filtering(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ArtifactRepository(connection)

            first = repository.create(
                id="artifact-1",
                bank_id=bank_id,
                kind="profile",
                artifact_key="project",
                content="v1",
                source_claim_ids=["claim-1"],
                source_watermark=10,
                generator_name="dream",
                generator_version="1",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            second = repository.create(
                id="artifact-2",
                bank_id=bank_id,
                kind="profile",
                artifact_key="project",
                content="v2",
                source_claim_ids=["claim-1", "claim-2"],
                source_watermark=11,
                generator_name="dream",
                generator_version="1",
                created_at="2026-08-21T00:01:00.000000Z",
            )

            stale = repository.mark_stale(
                bank_id=bank_id,
                artifact_id=first.id,
                stale_at="2026-08-21T00:00:30.000000Z",
            )
            current = repository.list_current(
                bank_id,
                as_of="2026-08-21T00:00:45.000000Z",
            )

            self.assertEqual(repository.get_latest(bank_id, "profile", "project"), second)
            self.assertEqual(
                [
                    artifact.id
                    for artifact in repository.list_versions(bank_id, "profile", "project")
                ],
                ["artifact-2", "artifact-1"],
            )
            self.assertEqual(stale.stale_at, "2026-08-21T00:00:30.000000Z")
            self.assertEqual([artifact.id for artifact in current], ["artifact-2"])


class MemoryEventRepositoryTests(unittest.TestCase):
    def test_watermark_and_idempotency_lookup(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = MemoryEventRepository(connection)

            first = repository.append(
                event_id="event-1",
                bank_id=bank_id,
                event_type="claim.created",
                aggregate_type="claim",
                aggregate_id="claim-1",
                actor_type="user",
                payload={"claim_id": "claim-1"},
                idempotency_key="event-key-1",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            second = repository.append(
                event_id="event-2",
                bank_id=bank_id,
                event_type="claim.superseded",
                aggregate_type="claim",
                aggregate_id="claim-1",
                actor_type="worker",
                payload={"claim_id": "claim-1", "successor_id": "claim-2"},
                created_at="2026-08-21T00:01:00.000000Z",
            )

            self.assertEqual(repository.get_by_idempotency_key(bank_id, "event-key-1"), first)
            self.assertEqual(repository.current_watermark(bank_id), second.sequence)
            self.assertEqual(
                repository.list_after(bank_id, sequence_exclusive=first.sequence),
                (second,),
            )

    def test_idempotency_key_is_bank_scoped(self) -> None:
        with temporary_connection() as connection:
            first_bank = seed_bank(connection, bank_id="bank-1", slug="project:one")
            second_bank = seed_bank(connection, bank_id="bank-2", slug="project:two")
            repository = MemoryEventRepository(connection)

            repository.append(
                event_id="event-1",
                bank_id=first_bank,
                event_type="claim.created",
                aggregate_type="claim",
                aggregate_id="claim-1",
                actor_type="user",
                payload={},
                idempotency_key="shared-key",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            repository.append(
                event_id="event-2",
                bank_id=second_bank,
                event_type="claim.created",
                aggregate_type="claim",
                aggregate_id="claim-1",
                actor_type="user",
                payload={},
                idempotency_key="shared-key",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            with self.assertRaises(sqlite3.IntegrityError):
                repository.append(
                    event_id="event-3",
                    bank_id=first_bank,
                    event_type="claim.created",
                    aggregate_type="claim",
                    aggregate_id="claim-2",
                    actor_type="user",
                    payload={},
                    idempotency_key="shared-key",
                    created_at="2026-08-21T00:00:01.000000Z",
                )


def seed_bank(connection, bank_id: str = "bank-1", slug: str = "project:memorygraph") -> str:
    MigrationRunner(connection).migrate()
    BankRepository(connection).create(
        id=bank_id,
        slug=slug,
        name=slug,
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return bank_id


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
