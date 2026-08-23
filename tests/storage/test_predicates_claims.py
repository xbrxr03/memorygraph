from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.claims import ClaimRepository
from memorygraph.storage.repositories.entities import EntityRepository
from memorygraph.storage.repositories.predicates import PredicateDefinitionRepository


class PredicateDefinitionRepositoryTests(unittest.TestCase):
    def test_bank_override_resolves_before_global(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = PredicateDefinitionRepository(connection)

            repository.create(
                id="pred-global",
                name="works_at",
                cardinality="many",
                volatility="durable",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            repository.create(
                id="pred-bank",
                bank_id=bank_id,
                name="works_at",
                cardinality="one",
                volatility="volatile",
                created_at="2026-08-21T00:00:01.000000Z",
            )

            resolved = repository.resolve(bank_id, "works_at")

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.id, "pred-bank")
            self.assertEqual(resolved.cardinality, "one")


class ClaimRepositoryTests(unittest.TestCase):
    def test_create_successor_closes_prior_version_and_preserves_history(self) -> None:
        with temporary_connection() as connection:
            bank_id, subject_entity_id, company_one_id, company_two_id = seed_claim_dependencies(
                connection
            )
            repository = ClaimRepository(connection)

            first = repository.create(
                id="claim-1",
                bank_id=bank_id,
                subject_entity_id=subject_entity_id,
                predicate="works_at",
                object_kind="entity",
                object_entity_id=company_one_id,
                polarity="positive",
                valid_from="2026-01-01T00:00:00.000000Z",
                system_from="2026-08-21T00:00:00.000000Z",
                lifecycle="active",
                origin="explicit",
                importance=0.9,
                created_at="2026-08-21T00:00:00.000000Z",
            )

            transition = repository.create_successor(
                bank_id=bank_id,
                prior_claim_id=first.id,
                successor_id="claim-2",
                successor_system_from="2026-08-22T00:00:00.000000Z",
                successor_created_at="2026-08-22T00:00:00.000000Z",
                successor_lifecycle="superseded",
                object_entity_id=company_two_id,
                valid_from="2026-08-15T00:00:00.000000Z",
            )

            versions = repository.list_versions(bank_id, subject_entity_id, "works_at")

            self.assertEqual(transition.prior.system_to, "2026-08-22T00:00:00.000000Z")
            self.assertEqual(transition.successor.object_entity_id, company_two_id)
            self.assertEqual(transition.successor.lifecycle, "superseded")
            self.assertEqual([claim.id for claim in versions], ["claim-1", "claim-2"])

    def test_scalar_claim_values_round_trip(self) -> None:
        with temporary_connection() as connection:
            bank_id, subject_entity_id, _, _ = seed_claim_dependencies(connection)
            repository = ClaimRepository(connection)

            claim = repository.create(
                id="claim-json",
                bank_id=bank_id,
                subject_entity_id=subject_entity_id,
                predicate="tooling",
                object_kind="json",
                object_value={"runner": "pytest", "parallel": True},
                polarity="positive",
                system_from="2026-08-21T00:00:00.000000Z",
                lifecycle="active",
                origin="explicit",
                importance=0.7,
                created_at="2026-08-21T00:00:00.000000Z",
            )

            self.assertEqual(claim.object_value, {"runner": "pytest", "parallel": True})


def seed_bank(connection, bank_id: str = "bank-1", slug: str = "project:memorygraph") -> str:
    MigrationRunner(connection).migrate()
    BankRepository(connection).create(
        id=bank_id,
        slug=slug,
        name=slug,
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return bank_id


def seed_claim_dependencies(connection):
    bank_id = seed_bank(connection)
    entities = EntityRepository(connection)
    subject = entities.create_entity(
        id="entity-subject",
        bank_id=bank_id,
        canonical_name="Abrar",
        normalized_name="abrar",
        entity_type="person",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    company_one = entities.create_entity(
        id="entity-company-1",
        bank_id=bank_id,
        canonical_name="Acme",
        normalized_name="acme",
        entity_type="company",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    company_two = entities.create_entity(
        id="entity-company-2",
        bank_id=bank_id,
        canonical_name="Stripe",
        normalized_name="stripe",
        entity_type="company",
        created_at="2026-08-21T00:00:00.000000Z",
    )
    return bank_id, subject.id, company_one.id, company_two.id


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
