"""Return the latest N visible events inside the bank at the checkpoint."""

from __future__ import annotations

from collections.abc import Sequence

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter


class LatestNAdapter(RetrievalAdapter):
    adapter_name = "latest_n"

    def __init__(self, limit: int = 3) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.limit = limit

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        return self.visible_events(corpus, target_scenario, query)[-self.limit :]
