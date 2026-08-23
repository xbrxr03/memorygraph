"""Return every visible event inside the bank at the checkpoint."""

from __future__ import annotations

from collections.abc import Sequence

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter


class FlatContextAdapter(RetrievalAdapter):
    adapter_name = "flat_context"

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        return self.visible_events(corpus, target_scenario, query)
