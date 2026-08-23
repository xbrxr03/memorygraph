"""Protocol wrapper for wiring MemoryGraph recall into the benchmark runner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import AdapterQueryResult, ContextEvent, RetrievalAdapter


@dataclass(frozen=True)
class MemoryGraphRecallHit:
    event_id: str
    at: str
    content: str
    bank_id: str
    scenario_id: str = "memorygraph"
    score: float | None = None
    metadata: dict[str, str] | None = None


class MemoryGraphRecallClient(Protocol):
    """Minimal protocol expected from a future MemoryGraph recall surface."""

    def recall(
        self,
        *,
        bank_id: str,
        query_text: str,
        as_of: str,
        max_items: int,
        max_tokens: int,
    ) -> Sequence[MemoryGraphRecallHit]: ...


class MemoryGraphAdapter(RetrievalAdapter):
    adapter_name = "memorygraph"

    def __init__(
        self,
        client: MemoryGraphRecallClient,
        *,
        max_items: int = 10,
        max_tokens: int = 512,
    ) -> None:
        self.client = client
        self.max_items = max_items
        self.max_tokens = max_tokens

    def run_query(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> AdapterQueryResult:
        del corpus
        hits = self.client.recall(
            bank_id=target_scenario.bank_id,
            query_text=query.question,
            as_of=target_scenario.event_by_id(query.after_event).at,
            max_items=self.max_items,
            max_tokens=self.max_tokens,
        )
        context_events = tuple(
            ContextEvent(
                scenario_id=hit.scenario_id,
                bank_id=hit.bank_id,
                event_id=hit.event_id,
                at=hit.at,
                content=hit.content,
                score=hit.score,
                metadata=hit.metadata,
            )
            for hit in hits
        )
        token_estimate = sum(len(item.content.split()) for item in context_events)
        return AdapterQueryResult(
            adapter_name=self.adapter_name,
            scenario_id=target_scenario.scenario_id,
            query_id=query.query_id,
            bank_id=target_scenario.bank_id,
            context_events=context_events,
            token_estimate=token_estimate,
        )

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        del corpus, target_scenario, query
        raise NotImplementedError("MemoryGraphAdapter overrides run_query directly")
