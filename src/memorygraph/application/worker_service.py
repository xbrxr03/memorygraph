from __future__ import annotations

from dataclasses import dataclass

from memorygraph.worker import (
    DurableDreamWorker,
    WorkerConfig,
    WorkerPollResult,
    WorkerProcessResult,
)


@dataclass(slots=True)
class DurableWorkerService:
    memory: object
    worker: DurableDreamWorker

    def __init__(
        self, memory, *, worker_id: str | None = None, config: WorkerConfig | None = None
    ) -> None:
        self.memory = memory
        self.worker = DurableDreamWorker(memory, worker_id=worker_id, config=config)

    def process_next(self, *, bank: str, provider=None) -> WorkerProcessResult | None:
        return self.worker.process_next(bank=bank, provider=provider)

    def run_until_idle(
        self, *, bank: str, provider=None, max_iterations: int | None = None
    ) -> tuple[WorkerProcessResult, ...]:
        return self.worker.run_until_idle(
            bank=bank,
            provider=provider,
            max_iterations=max_iterations,
        )

    def poll(
        self,
        *,
        bank: str,
        provider=None,
        stop_when_idle: bool = False,
        max_idle_cycles: int | None = None,
        max_iterations: int | None = None,
    ) -> WorkerPollResult:
        return self.worker.poll(
            bank=bank,
            provider=provider,
            stop_when_idle=stop_when_idle,
            max_idle_cycles=max_idle_cycles,
            max_iterations=max_iterations,
        )
