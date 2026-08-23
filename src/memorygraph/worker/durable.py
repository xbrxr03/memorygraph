from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from memorygraph.application.dream_service import (
    EmbeddedDreamComponents,
    MetadataDreamProvider,
    _parse_datetime,
)
from memorygraph.dream import (
    DreamAction,
    DreamActionKind,
    DreamProposal,
    DreamProposalValidator,
    DreamRunMetrics,
    DreamRunMode,
    DreamRunReport,
    DreamRunStatus,
    DreamRuntime,
    DreamTask,
    ProposalCommitOutcome,
    ProposalCommitStatus,
    ProposalDisposition,
    ProposalPreconditions,
    ProposalRunResult,
    ProposalValidation,
)
from memorygraph.dream.models import build_commit_recheck_contract
from memorygraph.models import PredicateDefinition
from memorygraph.storage import (
    DatabaseConfig,
    DreamRunRecord,
    DreamTaskRecord,
    connect,
    transaction,
)

if TYPE_CHECKING:
    from memorygraph.api import MemoryGraph
    from memorygraph.dream import DreamProvider


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _datetime_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _jsonable_retry_error(
    *,
    attempt_count: int,
    max_attempts: int,
    next_retry_at: str | None,
    report: DreamRunReport,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "stage": report.failure_stage,
        "message": report.error_message,
        "retry": {
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "will_retry": attempt_count < max_attempts,
        },
    }
    if next_retry_at is not None:
        payload["retry"]["next_retry_at"] = next_retry_at
    return payload


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    lease_seconds: float = 30.0
    heartbeat_seconds: float = 10.0
    poll_interval_seconds: float = 1.0
    max_attempts: int = 3
    retry_backoff_seconds: tuple[float, ...] = (1.0, 5.0, 30.0)

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        if self.heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive.")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be less than lease_seconds.")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative.")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive.")
        if not self.retry_backoff_seconds:
            raise ValueError("retry_backoff_seconds must not be empty.")
        if any(value < 0 for value in self.retry_backoff_seconds):
            raise ValueError("retry_backoff_seconds values must be non-negative.")

    def retry_delay_seconds(self, attempt_count: int) -> float:
        index = max(0, min(attempt_count - 1, len(self.retry_backoff_seconds) - 1))
        return self.retry_backoff_seconds[index]


@dataclass(frozen=True, slots=True)
class WorkerProcessResult:
    worker_id: str
    bank_id: str
    run_id: str
    task_id: str
    state: str
    attempt_count: int
    retried: bool
    report: DreamRunReport


@dataclass(frozen=True, slots=True)
class WorkerPollResult:
    worker_id: str
    processed: tuple[WorkerProcessResult, ...]
    idle_cycles: int


class DurableDreamWorker:
    def __init__(
        self,
        memory: MemoryGraph,
        *,
        worker_id: str | None = None,
        config: WorkerConfig | None = None,
    ) -> None:
        self.memory = memory
        self.worker_id = worker_id or f"worker:{uuid4()}"
        self.config = config or WorkerConfig()

    def process_next(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
    ) -> WorkerProcessResult | None:
        bank_record = self.memory.get_bank(bank)
        now = _datetime_string(_utc_now())
        self.memory.dream_tasks.requeue_expired_leases(bank_id=bank_record.id, now=now)
        self.memory.dream_runs.requeue_expired_leases(bank_id=bank_record.id, now=now)

        run = self.memory.dream_runs.lease_next(
            bank_id=bank_record.id,
            lease_owner=self.worker_id,
            lease_expires_at=self._lease_deadline_string(),
            now=now,
        )
        if run is None:
            return None

        task = self.memory.dream_tasks.lease_next(
            bank_id=bank_record.id,
            dream_run_id=run.id,
            lease_owner=self.worker_id,
            lease_expires_at=self._lease_deadline_string(),
            now=now,
        )
        if task is None:
            self.memory.dream_runs.transition_state(
                bank_id=bank_record.id,
                run_id=run.id,
                from_states=("leased",),
                to_state="failed",
                error={"message": f"No queued task found for dream run {run.id}."},
                completed_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            return None

        run, task = self._mark_running(run, task, now=now)
        dream_task = self._build_task(run, task)
        report = self._resume_report_from_persisted_proposals(run, dream_task)
        if report is None:
            provider_instance = provider or MetadataDreamProvider()
            report = self._run_with_heartbeat(dream_task, provider_instance)
        else:
            service = _EmbeddedFinalizeAdapter(self.memory)
            service.finalize(report, EmbeddedDreamComponents(self.memory))
        retried = self._finalize_attempt(run=run, task=task, report=report)
        final_run = self.memory.dream_runs.get(bank_record.id, run.id)
        attempt_count = 0 if final_run is None else final_run.attempt_count
        state = "unknown" if final_run is None else final_run.state
        return WorkerProcessResult(
            worker_id=self.worker_id,
            bank_id=bank_record.id,
            run_id=run.id,
            task_id=task.id,
            state=state,
            attempt_count=attempt_count,
            retried=retried,
            report=report,
        )

    def run_until_idle(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        max_iterations: int | None = None,
    ) -> tuple[WorkerProcessResult, ...]:
        processed: list[WorkerProcessResult] = []
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            result = self.process_next(bank=bank, provider=provider)
            if result is None:
                break
            processed.append(result)
            iterations += 1
        return tuple(processed)

    def poll(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        stop_when_idle: bool = False,
        max_idle_cycles: int | None = None,
        max_iterations: int | None = None,
    ) -> WorkerPollResult:
        processed: list[WorkerProcessResult] = []
        idle_cycles = 0
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            result = self.process_next(bank=bank, provider=provider)
            if result is None:
                idle_cycles += 1
                if stop_when_idle:
                    break
                if max_idle_cycles is not None and idle_cycles >= max_idle_cycles:
                    break
                time.sleep(self.config.poll_interval_seconds)
                continue
            processed.append(result)
            idle_cycles = 0
            iterations += 1
        return WorkerPollResult(
            worker_id=self.worker_id,
            processed=tuple(processed),
            idle_cycles=idle_cycles,
        )

    def _mark_running(
        self,
        run: DreamRunRecord,
        task: DreamTaskRecord,
        *,
        now: str,
    ) -> tuple[DreamRunRecord, DreamTaskRecord]:
        input_observation_ids = tuple(str(item) for item in task.input.get("observation_ids", ()))
        with transaction(self.memory.connection):
            for observation_id in input_observation_ids:
                observation = self.memory.observations.get(task.bank_id, observation_id)
                if observation is None or observation.ingestion_state != "pending":
                    continue
                self.memory.observations.transition_ingestion_state(
                    bank_id=task.bank_id,
                    observation_id=observation_id,
                    from_states=("pending",),
                    to_state="processing",
                )
            run = self.memory.dream_runs.transition_state(
                bank_id=run.bank_id,
                run_id=run.id,
                from_states=("leased", "running"),
                to_state="running",
                started_at=run.started_at or now,
            )
            task = self.memory.dream_tasks.transition_state(
                bank_id=task.bank_id,
                task_id=task.id,
                from_states=("leased", "running"),
                to_state="running",
            )
        return run, task

    def _build_task(self, run: DreamRunRecord, task: DreamTaskRecord) -> DreamTask:
        observation_ids = tuple(str(item) for item in task.input.get("observation_ids", ()))
        trigger = str(task.input.get("trigger", run.trigger))
        reason = str(task.input.get("reason", run.trigger))
        return DreamTask(
            run_id=run.id,
            task_id=task.id,
            bank_id=run.bank_id,
            trigger=trigger,
            mode=DreamRunMode(run.mode),
            input_watermark=int(task.input.get("input_watermark", run.input_watermark)),
            reason=reason,
            observation_ids=observation_ids,
            created_at=_parse_datetime(task.created_at),
        )

    def _build_runtime(
        self, provider: DreamProvider
    ) -> tuple[EmbeddedDreamComponents, DreamRuntime]:
        components = EmbeddedDreamComponents(self.memory)
        runtime = DreamRuntime(
            context_builder=components,
            pipeline=components,
            provider=provider,
            validator=DreamProposalValidator(),
            committer=components,
        )
        return components, runtime

    def _run_with_heartbeat(
        self,
        task: DreamTask,
        provider: DreamProvider,
    ) -> DreamRunReport:
        components, runtime = self._build_runtime(provider)
        heartbeat = _LeaseHeartbeat(
            memory=self.memory,
            worker_id=self.worker_id,
            run_id=task.run_id,
            task_id=task.task_id,
            bank_id=task.bank_id,
            config=self.config,
        )
        heartbeat.start()
        try:
            report = runtime.run(task)
        finally:
            heartbeat.stop()
        service = _EmbeddedFinalizeAdapter(self.memory)
        service.finalize(report, components)
        return report

    def _resume_report_from_persisted_proposals(
        self,
        run: DreamRunRecord,
        task: DreamTask,
    ) -> DreamRunReport | None:
        persisted = self.memory.dream_proposals.list_for_run(run.bank_id, run.id)
        if not persisted:
            return None
        proposal_results: list[ProposalRunResult] = []
        for record in persisted:
            idempotency = record.preconditions.get("idempotency")
            if not isinstance(idempotency, dict):
                return None
            key = idempotency.get("key")
            fingerprint = idempotency.get("fingerprint")
            if not isinstance(key, str) or not isinstance(fingerprint, str):
                return None
            event = self.memory.events.get_by_idempotency_key(run.bank_id, key)
            if event is None or event.payload.get("fingerprint") != fingerprint:
                return None
            proposal = _placeholder_proposal(bank_id=run.bank_id, proposal_id=record.id)
            validation = ProposalValidation(
                proposal_id=record.id,
                disposition=ProposalDisposition.AUTO_ELIGIBLE,
                issues=(),
                commit_recheck=build_commit_recheck_contract(proposal),
            )
            proposal_results.append(
                ProposalRunResult(
                    proposal=proposal,
                    validation=validation,
                    commit_outcome=ProposalCommitOutcome(
                        proposal_id=record.id,
                        status=ProposalCommitStatus.REPLAYED,
                    ),
                )
            )
        metrics = DreamRunMetrics.from_results(
            selected_observations=len(task.observation_ids),
            extracted_entities=0,
            extracted_claims=0,
            proposal_results=tuple(proposal_results),
            provider_calls=(),
        )
        return DreamRunReport(
            task=task,
            status=DreamRunStatus.COMPLETED,
            source_bundle_id=None,
            provider_calls=(),
            proposal_results=tuple(proposal_results),
            metrics=metrics,
            commit_result=None,
        )

    def _finalize_attempt(
        self,
        *,
        run: DreamRunRecord,
        task: DreamTaskRecord,
        report: DreamRunReport,
    ) -> bool:
        if report.status.value != "failed":
            return False
        current_run = self.memory.dream_runs.get(run.bank_id, run.id)
        current_task = self.memory.dream_tasks.get(task.bank_id, task.id)
        if current_run is None or current_task is None:
            return False
        if current_run.attempt_count >= self.config.max_attempts:
            return False

        next_retry_at = _datetime_string(
            _utc_now()
            + timedelta(seconds=self.config.retry_delay_seconds(current_run.attempt_count))
        )
        error_payload = _jsonable_retry_error(
            attempt_count=current_run.attempt_count,
            max_attempts=self.config.max_attempts,
            next_retry_at=next_retry_at,
            report=report,
        )
        with transaction(self.memory.connection):
            self.memory.dream_tasks.transition_state(
                bank_id=task.bank_id,
                task_id=task.id,
                from_states=("failed",),
                to_state="queued",
                error=error_payload,
                lease_owner=None,
                lease_expires_at=next_retry_at,
                completed_at=None,
            )
            self.memory.dream_runs.transition_state(
                bank_id=run.bank_id,
                run_id=run.id,
                from_states=("failed",),
                to_state="queued",
                error=error_payload,
                lease_owner=None,
                lease_expires_at=next_retry_at,
                completed_at=None,
            )
        return True

    def _lease_deadline_string(self) -> str:
        return _datetime_string(_utc_now() + timedelta(seconds=self.config.lease_seconds))


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        memory: MemoryGraph,
        worker_id: str,
        bank_id: str,
        run_id: str,
        task_id: str,
        config: WorkerConfig,
    ) -> None:
        self._memory = memory
        self._worker_id = worker_id
        self._bank_id = bank_id
        self._run_id = run_id
        self._task_id = task_id
        self._config = config
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"{worker_id}-heartbeat", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self._config.heartbeat_seconds * 2, 0.1))

    def _run(self) -> None:
        connection = connect(
            DatabaseConfig(
                path=self._memory.config.database_path,
                busy_timeout_ms=self._memory.config.busy_timeout_ms,
            )
        )
        try:
            from memorygraph.storage.repositories.dream_runs import DreamRunRepository
            from memorygraph.storage.repositories.dream_tasks import DreamTaskRepository

            runs = DreamRunRepository(connection)
            tasks = DreamTaskRepository(connection)
            while not self._stop.wait(self._config.heartbeat_seconds):
                deadline = _datetime_string(
                    _utc_now() + timedelta(seconds=self._config.lease_seconds)
                )
                task = tasks.renew_lease(
                    bank_id=self._bank_id,
                    task_id=self._task_id,
                    lease_owner=self._worker_id,
                    lease_expires_at=deadline,
                )
                run = runs.renew_lease(
                    bank_id=self._bank_id,
                    run_id=self._run_id,
                    lease_owner=self._worker_id,
                    lease_expires_at=deadline,
                )
                if task is None or run is None:
                    return
        finally:
            connection.close()


class _EmbeddedFinalizeAdapter:
    def __init__(self, memory: MemoryGraph) -> None:
        from memorygraph.application.dream_service import EmbeddedDreamService

        self._service = EmbeddedDreamService(memory)

    def finalize(self, report: DreamRunReport, components: EmbeddedDreamComponents) -> None:
        self._service._finalize(report, components)


def _placeholder_proposal(*, bank_id: str, proposal_id: str) -> DreamProposal:
    action = DreamAction(
        action_type=DreamActionKind.ASSERT,
        bank_id=bank_id,
        predicate_definition=PredicateDefinition.unknown("resume", bank_id=bank_id),
        decision_confidence=1.0,
    )
    return DreamProposal(
        id=proposal_id,
        bank_id=bank_id,
        action=action,
        preconditions=ProposalPreconditions(
            bank_id=bank_id,
            observed_event_watermark=0,
        ),
    )
