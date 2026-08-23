"""Base classes for deterministic retrieval-only benchmark adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery


@dataclass(frozen=True)
class ContextEvent:
    scenario_id: str
    bank_id: str
    event_id: str
    at: str
    content: str
    score: float | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class AdapterQueryResult:
    adapter_name: str
    scenario_id: str
    query_id: str
    bank_id: str
    context_events: tuple[ContextEvent, ...]
    token_estimate: int


class RetrievalAdapter:
    """Corpus-aware retrieval baseline."""

    adapter_name = "base"

    def run_query(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> AdapterQueryResult:
        context_events = tuple(self.select_events(corpus, target_scenario, query))
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
        raise NotImplementedError

    def visible_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> list[ContextEvent]:
        checkpoint = target_scenario.checkpoint_time(query)
        events: list[ContextEvent] = []
        for scenario in corpus:
            if scenario.bank_id != target_scenario.bank_id:
                continue
            for event in scenario.events:
                if event.timestamp <= checkpoint:
                    events.append(
                        ContextEvent(
                            scenario_id=scenario.scenario_id,
                            bank_id=scenario.bank_id,
                            event_id=event.event_id,
                            at=event.at,
                            content=event.content,
                        )
                    )
        events.sort(key=lambda item: (_parse_time(item.at), item.scenario_id, item.event_id))
        return events


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
