"""A bounded chronological Markdown/runbook baseline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter


class MarkdownRunbookAdapter(RetrievalAdapter):
    """Model a plain MEMORY.md file whose newest sections survive a token cap."""

    adapter_name = "markdown_runbook"

    def __init__(self, *, max_tokens: int = 512) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        selected: list[ContextEvent] = []
        used_tokens = 0
        for event in reversed(self.visible_events(corpus, target_scenario, query)):
            rendered = f"## {event.at} · {event.event_id}\n\n{event.content}"
            token_count = max(1, len(rendered.split()))
            if selected and used_tokens + token_count > self.max_tokens:
                continue
            selected.append(
                replace(
                    event,
                    content=rendered,
                    metadata={"retrieval": "markdown_runbook", "order": "newest_first"},
                )
            )
            used_tokens += token_count
        return selected
