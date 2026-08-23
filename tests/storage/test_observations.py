from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.claims import ClaimRepository
from memorygraph.storage.repositories.entities import EntityRepository
from memorygraph.storage.repositories.events import MemoryEventRepository
from memorygraph.storage.repositories.evidence import ClaimEvidenceRepository
from memorygraph.storage.repositories.observations import (
    ObservationChunkInput,
    ObservationRepository,
)


class ObservationRepositoryTests(unittest.TestCase):
    def test_create_observation_with_chunks(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ObservationRepository(connection)

            record = repository.create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="Use uv sync before tests.",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
                metadata_json={"channel": "chat"},
                chunks=(
                    ObservationChunkInput(
                        id="chunk-1",
                        ordinal=0,
                        start_offset=0,
                        end_offset=10,
                        content="Use uv sync",
                        content_sha256="sha-c1",
                        created_at="2026-08-21T00:00:00.000000Z",
                    ),
                ),
            )

            self.assertEqual(record.metadata_json, {"channel": "chat"})
            self.assertEqual(len(record.chunks), 1)
            self.assertEqual(record.chunks[0].content, "Use uv sync")

    def test_source_key_idempotency_is_scoped_per_bank_and_sha(self) -> None:
        with temporary_connection() as connection:
            first_bank = seed_bank(connection, bank_id="bank-1", slug="project:first")
            second_bank = seed_bank(connection, bank_id="bank-2", slug="project:second")
            repository = ObservationRepository(connection)

            repository.create(
                id="obs-1",
                bank_id=first_bank,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="alpha",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            with self.assertRaises(sqlite3.IntegrityError):
                repository.create(
                    id="obs-duplicate",
                    bank_id=first_bank,
                    kind="message",
                    source_key="session:1",
                    content_sha256="sha-1",
                    content="alpha",
                    actor_type="user",
                    observed_at="2026-08-21T00:00:00.000000Z",
                    trust_class="owner_explicit",
                    created_at="2026-08-21T00:00:00.000000Z",
                )

            repository.create(
                id="obs-2",
                bank_id=first_bank,
                kind="message",
                source_key="session:1",
                content_sha256="sha-2",
                content="beta",
                actor_type="user",
                observed_at="2026-08-21T00:00:01.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:01.000000Z",
            )
            repository.create(
                id="obs-3",
                bank_id=second_bank,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="alpha",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            first_versions = repository.list_by_source_key(first_bank, "session:1")
            second_versions = repository.list_by_source_key(second_bank, "session:1")

            self.assertEqual([record.id for record in first_versions], ["obs-1", "obs-2"])
            self.assertEqual([record.id for record in second_versions], ["obs-3"])

    def test_cross_bank_chunk_reference_is_rejected(self) -> None:
        with temporary_connection() as connection:
            first_bank = seed_bank(connection, bank_id="bank-1", slug="project:first")
            second_bank = seed_bank(connection, bank_id="bank-2", slug="project:second")
            repository = ObservationRepository(connection)

            repository.create(
                id="obs-1",
                bank_id=first_bank,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="alpha",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO observation_chunks(
                        id,
                        bank_id,
                        observation_id,
                        ordinal,
                        start_offset,
                        end_offset,
                        content,
                        content_sha256,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "chunk-1",
                        second_bank,
                        "obs-1",
                        0,
                        0,
                        5,
                        "alpha",
                        "sha-c1",
                        "2026-08-21T00:00:00.000000Z",
                    ),
                )

    def test_list_for_ingestion_uses_observation_events_as_watermark(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ObservationRepository(connection)
            events = MemoryEventRepository(connection)

            repository.create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="alpha",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            first_event = events.append(
                event_id="event-1",
                bank_id=bank_id,
                event_type="observation.created",
                aggregate_type="observation",
                aggregate_id="obs-1",
                actor_type="user",
                payload={"observation_id": "obs-1"},
                created_at="2026-08-21T00:00:00.000000Z",
            )

            repository.create(
                id="obs-2",
                bank_id=bank_id,
                kind="message",
                source_key="session:2",
                content_sha256="sha-2",
                content="beta",
                actor_type="user",
                observed_at="2026-08-21T00:00:01.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:01.000000Z",
            )
            events.append(
                event_id="event-2",
                bank_id=bank_id,
                event_type="observation.created",
                aggregate_type="observation",
                aggregate_id="obs-2",
                actor_type="user",
                payload={"observation_id": "obs-2"},
                created_at="2026-08-21T00:00:01.000000Z",
            )

            pending = repository.list_for_ingestion(
                bank_id,
                states=("pending",),
                after_event_sequence=first_event.sequence,
            )

            self.assertEqual([record.id for record in pending], ["obs-2"])

    def test_transition_ingestion_state_keeps_content_immutable(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ObservationRepository(connection)
            created = repository.create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="alpha",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            updated = repository.transition_ingestion_state(
                bank_id=bank_id,
                observation_id="obs-1",
                from_states=("pending",),
                to_state="processing",
            )

            self.assertEqual(updated.ingestion_state, "processing")
            self.assertEqual(updated.content, created.content)
            self.assertEqual(updated.content_sha256, created.content_sha256)

    def test_tombstone_erases_observation_and_chunk_content_but_preserves_rows(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ObservationRepository(connection)
            record = repository.create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="secret token 12345",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
                metadata_json={"secret": "token 12345"},
                chunks=(
                    ObservationChunkInput(
                        id="chunk-1",
                        ordinal=0,
                        start_offset=0,
                        end_offset=18,
                        content="secret token 12345",
                        content_sha256="sha-c1",
                        created_at="2026-08-21T00:00:00.000000Z",
                    ),
                ),
            )

            tombstoned = repository.tombstone(
                bank_id,
                record.id,
                "2026-08-21T00:10:00.000000Z",
            )

            self.assertEqual(tombstoned.ingestion_state, "deleted")
            self.assertNotIn("secret token 12345", tombstoned.content)
            self.assertEqual(tombstoned.metadata_json["deleted"], True)
            self.assertEqual(
                tombstoned.metadata_json["deleted_at"],
                "2026-08-21T00:10:00.000000Z",
            )
            self.assertTrue(tombstoned.metadata_json["deletion_hash"])
            self.assertEqual(len(tombstoned.chunks), 1)
            self.assertNotIn("secret token 12345", tombstoned.chunks[0].content)
            self.assertTrue(tombstoned.chunks[0].content.startswith("[deleted:"))

    def test_repeat_tombstone_is_safe_and_keeps_existing_deletion_markers(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = ObservationRepository(connection)
            repository.create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="secret token 12345",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            first = repository.tombstone(
                bank_id,
                "obs-1",
                "2026-08-21T00:10:00.000000Z",
            )
            second = repository.tombstone(
                bank_id,
                "obs-1",
                "2026-08-21T00:20:00.000000Z",
            )

            self.assertEqual(second.ingestion_state, "deleted")
            self.assertEqual(second.content, first.content)
            self.assertEqual(second.content_sha256, first.content_sha256)
            self.assertEqual(second.metadata_json, first.metadata_json)

    def test_tombstone_plus_evidence_deletion_removes_sensitive_excerpts(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            observation = ObservationRepository(connection).create(
                id="obs-1",
                bank_id=bank_id,
                kind="message",
                source_key="session:1",
                content_sha256="sha-1",
                content="secret token 12345",
                actor_type="user",
                observed_at="2026-08-21T00:00:00.000000Z",
                trust_class="owner_explicit",
                created_at="2026-08-21T00:00:00.000000Z",
                chunks=(
                    ObservationChunkInput(
                        id="chunk-1",
                        ordinal=0,
                        start_offset=0,
                        end_offset=18,
                        content="secret token 12345",
                        content_sha256="sha-c1",
                        created_at="2026-08-21T00:00:00.000000Z",
                    ),
                ),
            )
            subject = EntityRepository(connection).create_entity(
                id="entity-1",
                bank_id=bank_id,
                canonical_name="Worker",
                normalized_name="worker",
                entity_type="person",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            claim_id = (
                ClaimRepository(connection)
                .create(
                    id="claim-1",
                    bank_id=bank_id,
                    subject_entity_id=subject.id,
                    predicate="secret_note",
                    object_kind="string",
                    object_value="secret token 12345",
                    polarity="positive",
                    system_from="2026-08-21T00:00:00.000000Z",
                    lifecycle="active",
                    origin="explicit",
                    importance=0.5,
                    created_at="2026-08-21T00:00:00.000000Z",
                )
                .id
            )
            evidence = ClaimEvidenceRepository(connection).create(
                id="evidence-1",
                bank_id=bank_id,
                claim_id=claim_id,
                observation_id=observation.id,
                chunk_id="chunk-1",
                start_offset=0,
                end_offset=18,
                excerpt="secret token 12345",
                stance="supports",
                explicitness="explicit",
                source_reliability=1.0,
                extraction_confidence=1.0,
                extractor_name="manual",
                extractor_version="1",
                created_at="2026-08-21T00:00:01.000000Z",
            )

            ObservationRepository(connection).tombstone(
                bank_id,
                observation.id,
                "2026-08-21T00:10:00.000000Z",
            )
            deleted = ClaimEvidenceRepository(connection).delete_for_observation(
                bank_id,
                observation.id,
            )
            stored = ObservationRepository(connection).get(bank_id, observation.id)

            self.assertEqual(deleted, 1)
            self.assertEqual(evidence.observation_id, observation.id)
            self.assertEqual(
                ClaimEvidenceRepository(connection).list_for_observation(
                    bank_id,
                    observation.id,
                ),
                (),
            )
            self.assertNotIn("secret token 12345", stored.content)
            self.assertNotIn("secret token 12345", stored.chunks[0].content)


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
