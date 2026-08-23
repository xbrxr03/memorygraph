from __future__ import annotations

import tempfile
import time
from pathlib import Path

from memorygraph import MemoryGraph
from memorygraph.application.dream_service import EmbeddedDreamComponents
from memorygraph.dream import DreamProposalValidator, DreamRuntime
from memorygraph.worker import DurableDreamWorker, WorkerConfig


class FlakyMetadataProvider:
    def __init__(self) -> None:
        from memorygraph.application.dream_service import MetadataDreamProvider

        self._delegate = MetadataDreamProvider()
        self._failed = False

    def extract(self, source_bundle):
        if not self._failed:
            self._failed = True
            raise RuntimeError("transient extract failure")
        return self._delegate.extract(source_bundle)

    def challenge(self, request):
        return self._delegate.challenge(request)


class SlowMetadataProvider:
    def __init__(self, *, sleep_seconds: float) -> None:
        from memorygraph.application.dream_service import MetadataDreamProvider

        self._delegate = MetadataDreamProvider()
        self._sleep_seconds = sleep_seconds

    def extract(self, source_bundle):
        time.sleep(self._sleep_seconds)
        return self._delegate.extract(source_bundle)

    def challenge(self, request):
        return self._delegate.challenge(request)


def test_process_next_completes_queued_run_and_task() -> None:
    with dream_fixture() as fixture:
        worker = DurableDreamWorker(
            fixture.memory,
            worker_id="worker:test",
            config=WorkerConfig(
                lease_seconds=0.2,
                heartbeat_seconds=0.05,
                retry_backoff_seconds=(0.0,),
            ),
        )

        result = worker.process_next(bank=fixture.bank.id)

        assert result is not None
        assert result.state == "completed"
        assert result.retried is False
        assert result.report.status.value == "completed"
        assert fixture.memory.dream_runs.get(fixture.bank.id, fixture.run_id).state == "completed"
        assert fixture.memory.dream_tasks.get(fixture.bank.id, fixture.task_id).state == "completed"
        assert (
            fixture.memory.observations.get(fixture.bank.id, fixture.observation.id).ingestion_state
            == "processed"
        )
        current = fixture.memory.history(
            bank=fixture.bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        assert len(current) == 1
        assert current[0].object == "Stripe"


def test_failed_attempt_is_requeued_with_backoff_then_succeeds() -> None:
    with dream_fixture() as fixture:
        provider = FlakyMetadataProvider()
        worker = DurableDreamWorker(
            fixture.memory,
            worker_id="worker:test",
            config=WorkerConfig(
                lease_seconds=0.2,
                heartbeat_seconds=0.05,
                retry_backoff_seconds=(0.0, 0.0),
            ),
        )

        first = worker.process_next(bank=fixture.bank.id, provider=provider)
        requeued_run = fixture.memory.dream_runs.get(fixture.bank.id, fixture.run_id)
        requeued_task = fixture.memory.dream_tasks.get(fixture.bank.id, fixture.task_id)

        assert first is not None
        assert first.report.status.value == "failed"
        assert first.retried is True
        assert first.state == "queued"
        assert requeued_run.state == "queued"
        assert requeued_run.error["retry"]["will_retry"] is True
        assert requeued_task.state == "queued"
        assert (
            fixture.memory.observations.get(fixture.bank.id, fixture.observation.id).ingestion_state
            == "pending"
        )

        second = worker.process_next(bank=fixture.bank.id, provider=provider)
        current = fixture.memory.history(
            bank=fixture.bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )

        assert second is not None
        assert second.report.status.value == "completed"
        assert second.state == "completed"
        assert second.attempt_count == 2
        assert len(current) == 1
        assert current[0].object == "Stripe"


def test_recovered_run_replays_without_duplicate_claims_or_proposals() -> None:
    with dream_fixture() as fixture:
        worker = DurableDreamWorker(
            fixture.memory,
            worker_id="worker:test",
            config=WorkerConfig(
                lease_seconds=0.2,
                heartbeat_seconds=0.05,
                retry_backoff_seconds=(0.0,),
            ),
        )
        run = fixture.memory.dream_runs.lease_next(
            bank_id=fixture.bank.id,
            lease_owner="worker:crashed",
            lease_expires_at="2026-08-21T12:00:01.000000Z",
            now="2026-08-21T12:00:00.000000Z",
        )
        task_record = fixture.memory.dream_tasks.lease_next(
            bank_id=fixture.bank.id,
            dream_run_id=fixture.run_id,
            lease_owner="worker:crashed",
            lease_expires_at="2026-08-21T12:00:01.000000Z",
            now="2026-08-21T12:00:00.000000Z",
        )
        assert run is not None and task_record is not None
        run, task_record = worker._mark_running(
            run,
            task_record,
            now="2026-08-21T12:00:00.100000Z",
        )

        task = worker._build_task(run, task_record)
        components = EmbeddedDreamComponents(fixture.memory)
        runtime = DreamRuntime(
            context_builder=components,
            pipeline=components,
            provider=fixture.provider,
            validator=DreamProposalValidator(),
            committer=components,
        )
        first_report = runtime.run(task)

        proposals = fixture.memory.dream_proposals.list_for_run(fixture.bank.id, fixture.run_id)
        current = fixture.memory.history(
            bank=fixture.bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        assert first_report.status.value == "completed"
        assert len(proposals) == 1
        assert proposals[0].disposition == "pending"
        assert len(current) == 1

        fixture.memory.dream_tasks.requeue_expired_leases(
            bank_id=fixture.bank.id,
            now="2026-08-21T12:01:00.000000Z",
        )
        fixture.memory.dream_runs.requeue_expired_leases(
            bank_id=fixture.bank.id,
            now="2026-08-21T12:01:00.000000Z",
        )

        recovered = worker.process_next(bank=fixture.bank.id, provider=fixture.provider)
        final_proposals = fixture.memory.dream_proposals.list_for_run(
            fixture.bank.id, fixture.run_id
        )
        events = fixture.memory.events.list_after(fixture.bank.id, sequence_exclusive=0, limit=100)
        claim_events = [event for event in events if event.event_type.startswith("claim.")]

        assert recovered is not None
        assert recovered.report.status.value == "completed"
        assert recovered.report.metrics.replayed == 1
        assert recovered.state == "completed"
        assert recovered.attempt_count == 2
        assert len(final_proposals) == 1
        assert final_proposals[0].disposition == "committed"
        assert [event.event_type for event in claim_events] == ["claim.asserted"]


def test_heartbeat_renews_lease_for_slow_provider() -> None:
    with dream_fixture() as fixture:
        worker = DurableDreamWorker(
            fixture.memory,
            worker_id="worker:test",
            config=WorkerConfig(
                lease_seconds=0.15,
                heartbeat_seconds=0.03,
                retry_backoff_seconds=(0.0,),
            ),
        )
        provider = SlowMetadataProvider(sleep_seconds=0.25)

        result = worker.process_next(bank=fixture.bank.id, provider=provider)
        run = fixture.memory.dream_runs.get(fixture.bank.id, fixture.run_id)
        task = fixture.memory.dream_tasks.get(fixture.bank.id, fixture.task_id)

        assert result is not None
        assert result.report.status.value == "completed"
        assert run.state == "completed"
        assert task.state == "completed"
        assert run.attempt_count == 1


class dream_fixture:
    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.memory = MemoryGraph.open(Path(self._tempdir.name) / "memory.db")
        self.bank = self.memory.create_bank("project:worker")
        self.memory.define_predicate(
            "works_at",
            bank=self.bank.id,
            cardinality="one",
            volatility="volatile",
            subject_type="person",
            object_type="organization",
        )
        self.observation = self.memory.observe(
            "Abrar now works at Stripe.",
            bank=self.bank.id,
            source_key="worker:employment:stripe",
            observed_at="2026-08-21T12:00:00Z",
            metadata={
                "memorygraph": {
                    "entities": [
                        {"local_id": "subject", "name": "Abrar", "type": "person"},
                        {"local_id": "employer", "name": "Stripe", "type": "organization"},
                    ],
                    "claims": [
                        {
                            "local_id": "claim-1",
                            "subject": "subject",
                            "predicate": "works_at",
                            "object": {"kind": "entity", "value": "employer"},
                            "confidence": 1.0,
                        }
                    ],
                }
            },
        )
        self.run_id = "run-1"
        self.task_id = "task-1"
        self.provider = __import__(
            "memorygraph.application.dream_service",
            fromlist=["MetadataDreamProvider"],
        ).MetadataDreamProvider()
        self._queue_run_and_task()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.memory.close()
        self._tempdir.cleanup()

    def _queue_run_and_task(self) -> None:
        watermark = self.memory.events.current_watermark(self.bank.id)
        self.memory.dream_runs.create(
            id=self.run_id,
            bank_id=self.bank.id,
            trigger="manual",
            mode="apply",
            state="queued",
            input_watermark=watermark,
            policy_version="dream-policy-v1",
            provider_config_hash="cfg-1",
            created_at="2026-08-21T12:00:00.000000Z",
        )
        self.memory.dream_tasks.create(
            id=self.task_id,
            bank_id=self.bank.id,
            dream_run_id=self.run_id,
            task_type="maintain_observations",
            resource_type="bank",
            resource_id=self.bank.id,
            idempotency_key=f"dream-task:{self.run_id}:{watermark}",
            state="queued",
            input={
                "observation_ids": [self.observation.id],
                "input_watermark": watermark,
                "trigger": "manual",
                "reason": "manual",
            },
            created_at="2026-08-21T12:00:00.000000Z",
        )
