from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.claims import ClaimRepository
from memorygraph.storage.repositories.entities import EntityRepository
from memorygraph.storage.repositories.evidence import ClaimEvidenceRepository
from memorygraph.storage.repositories.observations import (
    ObservationChunkInput,
    ObservationRepository,
)
from memorygraph.storage.repositories.relations import ClaimRelationRepository
from memorygraph.storage.repositories.search import SearchDocumentRepository


class ClaimEvidenceRepositoryTests(unittest.TestCase):
    def test_create_evidence_validates_chunk_excerpt(self) -> None:
        with temporary_connection() as connection:
            bank_id, claim_id, observation_id = seed_graph(connection)
            repository = ClaimEvidenceRepository(connection)

            record = repository.create(
                id="evidence-1",
                bank_id=bank_id,
                claim_id=claim_id,
                observation_id=observation_id,
                chunk_id="chunk-1",
                start_offset=0,
                end_offset=11,
                excerpt="Use uv sync",
                stance="supports",
                explicitness="explicit",
                source_reliability=1.0,
                extraction_confidence=1.0,
                extractor_name="manual",
                extractor_version="1",
                created_at="2026-08-21T00:00:01.000000Z",
            )

            self.assertEqual(repository.list_for_claim(bank_id, claim_id), (record,))

    def test_invalid_excerpt_is_rejected(self) -> None:
        with temporary_connection() as connection:
            bank_id, claim_id, observation_id = seed_graph(connection)
            repository = ClaimEvidenceRepository(connection)

            with self.assertRaises(ValueError):
                repository.create(
                    id="evidence-1",
                    bank_id=bank_id,
                    claim_id=claim_id,
                    observation_id=observation_id,
                    start_offset=0,
                    end_offset=4,
                    excerpt="Fail",
                    stance="supports",
                    explicitness="explicit",
                    source_reliability=1.0,
                    extraction_confidence=1.0,
                    extractor_name="manual",
                    extractor_version="1",
                    created_at="2026-08-21T00:00:01.000000Z",
                )

    def test_list_and_delete_for_observation(self) -> None:
        with temporary_connection() as connection:
            bank_id, claim_id, observation_id = seed_graph(connection)
            repository = ClaimEvidenceRepository(connection)

            first = repository.create(
                id="evidence-1",
                bank_id=bank_id,
                claim_id=claim_id,
                observation_id=observation_id,
                chunk_id="chunk-1",
                start_offset=0,
                end_offset=11,
                excerpt="Use uv sync",
                stance="supports",
                explicitness="explicit",
                source_reliability=1.0,
                extraction_confidence=1.0,
                extractor_name="manual",
                extractor_version="1",
                created_at="2026-08-21T00:00:01.000000Z",
            )
            second = repository.create(
                id="evidence-2",
                bank_id=bank_id,
                claim_id=claim_id,
                observation_id=observation_id,
                start_offset=12,
                end_offset=24,
                excerpt="before tests",
                stance="mentions",
                explicitness="strongly_implied",
                source_reliability=0.9,
                extraction_confidence=0.8,
                extractor_name="manual",
                extractor_version="1",
                created_at="2026-08-21T00:00:02.000000Z",
            )

            listed = repository.list_for_observation(bank_id, observation_id)
            deleted = repository.delete_matching(
                bank_id=bank_id,
                claim_id=claim_id,
                observation_id=observation_id,
                start_offset=0,
                end_offset=11,
            )
            remaining = repository.list_for_observation(bank_id, observation_id)
            deleted_rest = repository.delete_for_observation(bank_id, observation_id)

            self.assertEqual(listed, (first, second))
            self.assertEqual(deleted, 1)
            self.assertEqual(remaining, (second,))
            self.assertEqual(deleted_rest, 1)
            self.assertEqual(repository.list_for_observation(bank_id, observation_id), ())


class ClaimRelationRepositoryTests(unittest.TestCase):
    def test_create_relation(self) -> None:
        with temporary_connection() as connection:
            bank_id, claim_id, _ = seed_graph(connection)
            successor_id = seed_second_claim(connection, bank_id=bank_id)
            repository = ClaimRelationRepository(connection)

            record = repository.create(
                id="relation-1",
                bank_id=bank_id,
                from_claim_id=claim_id,
                to_claim_id=successor_id,
                relation="supersedes",
                rationale="Later user correction.",
                decision_method="explicit",
                decision_confidence=1.0,
                created_at="2026-08-21T00:00:02.000000Z",
            )

            self.assertEqual(repository.get(bank_id, record.id), record)
            self.assertEqual(repository.list_outgoing(bank_id, claim_id), (record,))


class SearchDocumentRepositoryTests(unittest.TestCase):
    def test_search_is_bank_scoped_and_upsert_updates_fts(self) -> None:
        with temporary_connection() as connection:
            bank_one = seed_bank(connection, bank_id="bank-1", slug="project:one")
            bank_two = seed_bank(connection, bank_id="bank-2", slug="project:two")
            repository = SearchDocumentRepository(connection)

            repository.upsert(
                bank_id=bank_one,
                resource_type="claim",
                resource_id="claim-1",
                title="Build",
                body="Use uv sync before tests",
                metadata_text="pytest",
                content_sha256="sha-1",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            repository.upsert(
                bank_id=bank_two,
                resource_type="claim",
                resource_id="claim-1",
                title="Build",
                body="Use poetry install",
                metadata_text="pytest",
                content_sha256="sha-2",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            repository.upsert(
                bank_id=bank_one,
                resource_type="claim",
                resource_id="claim-1",
                title="Build",
                body="Use uv sync and uv run pytest",
                metadata_text="pytest",
                content_sha256="sha-3",
                created_at="2026-08-21T00:00:01.000000Z",
            )

            first_hits = repository.search(bank_id=bank_one, query="uv", limit=5)
            second_hits = repository.search(bank_id=bank_two, query="uv", limit=5)

            self.assertEqual(len(first_hits), 1)
            self.assertEqual(first_hits[0].document.content_sha256, "sha-3")
            self.assertEqual(second_hits, ())


def seed_bank(connection, bank_id: str = "bank-1", slug: str = "project:memorygraph") -> str:
    MigrationRunner(connection).migrate()
    BankRepository(connection).create(
        id=bank_id,
        slug=slug,
        name=slug,
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return bank_id


def seed_graph(connection):
    bank_id = seed_bank(connection)
    entities = EntityRepository(connection)
    subject = entities.create_entity(
        id="entity-subject",
        bank_id=bank_id,
        canonical_name="MemoryGraph",
        normalized_name="memorygraph",
        entity_type="project",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    observation_id = "obs-1"
    ObservationRepository(connection).create(
        id=observation_id,
        bank_id=bank_id,
        kind="message",
        source_key="session:1",
        content_sha256="sha-obs-1",
        content="Use uv sync before tests.",
        actor_type="user",
        observed_at="2026-08-21T00:00:00.000000Z",
        trust_class="owner_explicit",
        created_at="2026-08-21T00:00:00.000000Z",
        chunks=(
            ObservationChunkInput(
                id="chunk-1",
                ordinal=0,
                start_offset=0,
                end_offset=11,
                content="Use uv sync",
                content_sha256="sha-chunk-1",
                created_at="2026-08-21T00:00:00.000000Z",
            ),
        ),
    )
    claim_id = (
        ClaimRepository(connection)
        .create(
            id="claim-1",
            bank_id=bank_id,
            subject_entity_id=subject.id,
            predicate="build_hint",
            object_kind="string",
            object_value="Use uv sync before tests.",
            polarity="positive",
            system_from="2026-08-21T00:00:00.000000Z",
            lifecycle="active",
            origin="explicit",
            importance=0.8,
            created_at="2026-08-21T00:00:00.000000Z",
        )
        .id
    )
    return bank_id, claim_id, observation_id


def seed_second_claim(connection, bank_id: str) -> str:
    subject = EntityRepository(connection).create_entity(
        id="entity-subject-2",
        bank_id=bank_id,
        canonical_name="MemoryGraph 2",
        normalized_name="memorygraph 2",
        entity_type="project",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return (
        ClaimRepository(connection)
        .create(
            id="claim-2",
            bank_id=bank_id,
            subject_entity_id=subject.id,
            predicate="build_hint",
            object_kind="string",
            object_value="Use uv run pytest",
            polarity="positive",
            system_from="2026-08-21T00:00:01.000000Z",
            lifecycle="active",
            origin="explicit",
            importance=0.7,
            created_at="2026-08-21T00:00:01.000000Z",
        )
        .id
    )


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
