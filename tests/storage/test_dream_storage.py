from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph.storage.database import DatabaseConfig, MigrationRunner, connect
from memorygraph.storage.repositories.banks import BankRepository
from memorygraph.storage.repositories.dream_proposals import DreamProposalRepository
from memorygraph.storage.repositories.dream_runs import DreamRunRepository
from memorygraph.storage.repositories.dream_tasks import DreamTaskRepository
from memorygraph.storage.repositories.reviews import ReviewItemRepository


class DreamRunRepositoryTests(unittest.TestCase):
    def test_lease_next_respects_retry_backoff_until_due(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = DreamRunRepository(connection)
            repository.create(
                id="run-1",
                bank_id=bank_id,
                trigger="manual",
                mode="dry_run",
                state="queued",
                policy_version="p1",
                provider_config_hash="cfg-1",
                created_at="2026-08-21T00:00:00.000000Z",
                lease_expires_at="2026-08-21T00:10:00.000000Z",
            )

            too_early = repository.lease_next(
                bank_id=bank_id,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:20:00.000000Z",
                now="2026-08-21T00:05:00.000000Z",
            )
            due = repository.lease_next(
                bank_id=bank_id,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:20:00.000000Z",
                now="2026-08-21T00:10:00.000000Z",
            )

            self.assertIsNone(too_early)
            self.assertIsNotNone(due)
            self.assertEqual(due.state, "leased")

    def test_renew_lease_only_updates_matching_owner(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = DreamRunRepository(connection)
            repository.create(
                id="run-1",
                bank_id=bank_id,
                trigger="manual",
                mode="dry_run",
                state="running",
                policy_version="p1",
                provider_config_hash="cfg-1",
                created_at="2026-08-21T00:00:00.000000Z",
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:01:00.000000Z",
            )

            renewed = repository.renew_lease(
                bank_id=bank_id,
                run_id="run-1",
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:02:00.000000Z",
            )
            denied = repository.renew_lease(
                bank_id=bank_id,
                run_id="run-1",
                lease_owner="worker-2",
                lease_expires_at="2026-08-21T00:03:00.000000Z",
            )

            self.assertIsNotNone(renewed)
            self.assertEqual(renewed.lease_expires_at, "2026-08-21T00:02:00.000000Z")
            self.assertIsNone(denied)

    def test_lease_and_requeue_expired_runs_are_bank_scoped(self) -> None:
        with temporary_connection() as connection:
            bank_one = seed_bank(connection, bank_id="bank-1", slug="project:one")
            bank_two = seed_bank(connection, bank_id="bank-2", slug="project:two")
            repository = DreamRunRepository(connection)

            repository.create(
                id="run-1",
                bank_id=bank_one,
                trigger="manual",
                mode="dry_run",
                state="queued",
                policy_version="p1",
                provider_config_hash="cfg-1",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            repository.create(
                id="run-2",
                bank_id=bank_two,
                trigger="manual",
                mode="dry_run",
                state="leased",
                policy_version="p1",
                provider_config_hash="cfg-1",
                created_at="2026-08-21T00:00:00.000000Z",
                lease_owner="worker-old",
                lease_expires_at="2026-08-21T00:00:01.000000Z",
                attempt_count=1,
            )

            leased = repository.lease_next(
                bank_id=bank_one,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:10:00.000000Z",
            )
            requeued = repository.requeue_expired_leases(
                bank_id=bank_two,
                now="2026-08-21T00:05:00.000000Z",
            )

            self.assertIsNotNone(leased)
            self.assertEqual(leased.state, "leased")
            self.assertEqual(leased.attempt_count, 1)
            self.assertEqual(requeued, 1)
            self.assertEqual(repository.get(bank_two, "run-2").state, "queued")

    def test_transition_state_updates_usage_and_error_payloads(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            repository = DreamRunRepository(connection)
            repository.create(
                id="run-1",
                bank_id=bank_id,
                trigger="manual",
                mode="apply",
                state="leased",
                policy_version="p1",
                provider_config_hash="cfg-1",
                created_at="2026-08-21T00:00:00.000000Z",
            )

            running = repository.transition_state(
                bank_id=bank_id,
                run_id="run-1",
                from_states=("leased",),
                to_state="running",
                started_at="2026-08-21T00:00:01.000000Z",
            )
            failed = repository.transition_state(
                bank_id=bank_id,
                run_id="run-1",
                from_states=("running",),
                to_state="failed",
                completed_at="2026-08-21T00:00:02.000000Z",
                usage={"tokens": 42},
                error={"message": "boom"},
            )

            self.assertEqual(running.state, "running")
            self.assertEqual(failed.usage, {"tokens": 42})
            self.assertEqual(failed.error, {"message": "boom"})

    def test_only_one_connection_can_claim_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.sqlite3"
            connection_one = connect(DatabaseConfig(path=database_path))
            connection_two = connect(DatabaseConfig(path=database_path))
            try:
                bank_id = seed_bank(connection_one)
                repository_one = DreamRunRepository(connection_one)
                repository_two = DreamRunRepository(connection_two)
                repository_one.create(
                    id="run-1",
                    bank_id=bank_id,
                    trigger="manual",
                    mode="dry_run",
                    state="queued",
                    policy_version="p1",
                    provider_config_hash="cfg-1",
                    created_at="2026-08-21T00:00:00.000000Z",
                )

                first = repository_one.lease_next(
                    bank_id=bank_id,
                    lease_owner="worker-1",
                    lease_expires_at="2026-08-21T00:10:00.000000Z",
                    now="2026-08-21T00:00:00.000000Z",
                )
                second = repository_two.lease_next(
                    bank_id=bank_id,
                    lease_owner="worker-2",
                    lease_expires_at="2026-08-21T00:10:00.000000Z",
                    now="2026-08-21T00:00:00.000000Z",
                )

                self.assertIsNotNone(first)
                self.assertIsNone(second)
            finally:
                connection_one.close()
                connection_two.close()


class DreamTaskRepositoryTests(unittest.TestCase):
    def test_task_lease_respects_backoff_and_renewal(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            seed_run(connection, bank_id=bank_id)
            repository = DreamTaskRepository(connection)
            repository.create(
                id="task-1",
                bank_id=bank_id,
                dream_run_id="run-1",
                task_type="extract",
                resource_type="observation",
                resource_id="obs-1",
                idempotency_key="task-key-1",
                state="queued",
                input={"observation_id": "obs-1"},
                created_at="2026-08-21T00:00:00.000000Z",
                lease_expires_at="2026-08-21T00:10:00.000000Z",
            )

            too_early = repository.lease_next(
                bank_id=bank_id,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:15:00.000000Z",
                now="2026-08-21T00:05:00.000000Z",
            )
            leased = repository.lease_next(
                bank_id=bank_id,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:15:00.000000Z",
                now="2026-08-21T00:10:00.000000Z",
            )
            renewed = repository.renew_lease(
                bank_id=bank_id,
                task_id="task-1",
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:20:00.000000Z",
            )

            self.assertIsNone(too_early)
            self.assertIsNotNone(leased)
            self.assertEqual(leased.state, "leased")
            self.assertIsNotNone(renewed)
            self.assertEqual(renewed.lease_expires_at, "2026-08-21T00:20:00.000000Z")

    def test_task_idempotency_lookup_and_lease_cycle(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            seed_run(connection, bank_id=bank_id)
            repository = DreamTaskRepository(connection)

            created = repository.create(
                id="task-1",
                bank_id=bank_id,
                dream_run_id="run-1",
                task_type="extract",
                resource_type="observation",
                resource_id="obs-1",
                idempotency_key="task-key-1",
                state="queued",
                input={"observation_id": "obs-1"},
                created_at="2026-08-21T00:00:00.000000Z",
            )

            leased = repository.lease_next(
                bank_id=bank_id,
                lease_owner="worker-1",
                lease_expires_at="2026-08-21T00:05:00.000000Z",
            )
            completed = repository.transition_state(
                bank_id=bank_id,
                task_id="task-1",
                from_states=("leased",),
                to_state="completed",
                output={"claims": 2},
                completed_at="2026-08-21T00:01:00.000000Z",
            )

            lookup = repository.get_by_idempotency_key(bank_id, "task-key-1")
            self.assertIsNotNone(lookup)
            self.assertEqual(lookup.id, created.id)
            self.assertIsNotNone(leased)
            self.assertEqual(leased.state, "leased")
            self.assertEqual(completed.output, {"claims": 2})

    def test_requeue_expired_running_task(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            seed_run(connection, bank_id=bank_id)
            repository = DreamTaskRepository(connection)
            repository.create(
                id="task-1",
                bank_id=bank_id,
                dream_run_id="run-1",
                task_type="extract",
                resource_type="observation",
                resource_id="obs-1",
                idempotency_key="task-key-1",
                state="running",
                input={"observation_id": "obs-1"},
                created_at="2026-08-21T00:00:00.000000Z",
                lease_owner="worker-old",
                lease_expires_at="2026-08-21T00:00:10.000000Z",
                attempt_count=1,
            )

            requeued = repository.requeue_expired_leases(
                bank_id=bank_id,
                now="2026-08-21T00:10:00.000000Z",
            )

            self.assertEqual(requeued, 1)
            self.assertEqual(repository.get(bank_id, "task-1").state, "queued")


class ProposalAndReviewRepositoryTests(unittest.TestCase):
    def test_proposal_update_and_review_queue(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            seed_run(connection, bank_id=bank_id)
            proposals = DreamProposalRepository(connection)
            reviews = ReviewItemRepository(connection)

            proposal = proposals.create(
                id="proposal-1",
                bank_id=bank_id,
                dream_run_id="run-1",
                proposal_type="supersede_claim",
                preconditions={"claim_id": "claim-1"},
                action={"new_claim_id": "claim-2"},
                evidence_ids=["evidence-1"],
                disposition="pending",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            updated = proposals.update(
                bank_id=bank_id,
                proposal_id=proposal.id,
                disposition="review_required",
                validation={"score": 0.62},
            )
            review = reviews.create(
                id="review-1",
                bank_id=bank_id,
                proposal_id=proposal.id,
                reason="Low confidence",
                state="pending",
                created_at="2026-08-21T00:00:01.000000Z",
            )
            self.assertEqual(reviews.list_pending(bank_id), (review,))
            decided = reviews.decide(
                bank_id=bank_id,
                review_id=review.id,
                state="approved",
                reviewer_type="user",
                reviewer_id="abrar",
                decision={"note": "looks good"},
                decided_at="2026-08-21T00:00:02.000000Z",
            )

            self.assertEqual(updated.disposition, "review_required")
            self.assertEqual(proposals.list_by_disposition(bank_id, "review_required"), (updated,))
            self.assertEqual(decided.state, "approved")
            self.assertEqual(decided.decision, {"note": "looks good"})
            self.assertEqual(reviews.get_by_proposal(bank_id, proposal.id).id, review.id)

    def test_review_cannot_be_decided_twice(self) -> None:
        with temporary_connection() as connection:
            bank_id = seed_bank(connection)
            seed_run(connection, bank_id=bank_id)
            proposals = DreamProposalRepository(connection)
            reviews = ReviewItemRepository(connection)
            proposals.create(
                id="proposal-1",
                bank_id=bank_id,
                dream_run_id="run-1",
                proposal_type="supersede_claim",
                preconditions={},
                action={},
                evidence_ids=[],
                disposition="pending",
                created_at="2026-08-21T00:00:00.000000Z",
            )
            reviews.create(
                id="review-1",
                bank_id=bank_id,
                proposal_id="proposal-1",
                reason="Needs approval",
                state="pending",
                created_at="2026-08-21T00:00:01.000000Z",
            )
            reviews.decide(
                bank_id=bank_id,
                review_id="review-1",
                state="rejected",
                reviewer_type="user",
                reviewer_id="abrar",
                decided_at="2026-08-21T00:00:02.000000Z",
            )

            with self.assertRaises(ValueError):
                reviews.decide(
                    bank_id=bank_id,
                    review_id="review-1",
                    state="approved",
                    reviewer_type="user",
                    reviewer_id="abrar",
                    decided_at="2026-08-21T00:00:03.000000Z",
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


def seed_run(connection, *, bank_id: str) -> None:
    DreamRunRepository(connection).create(
        id="run-1",
        bank_id=bank_id,
        trigger="manual",
        mode="dry_run",
        state="queued",
        policy_version="p1",
        provider_config_hash="cfg-1",
        created_at="2026-08-21T00:00:00.000000Z",
    )


class temporary_connection:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._connection = connect(DatabaseConfig(path=Path(self._tempdir.name) / "test.sqlite3"))
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        self._connection.close()
        self._tempdir.cleanup()
