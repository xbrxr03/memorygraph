"""The zero-context control: answer with no persisted memory."""

from __future__ import annotations

from collections.abc import Sequence

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter


class NoMemoryAdapter(RetrievalAdapter):
    adapter_name = "no_memory"

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        del corpus, target_scenario, query
        return ()
