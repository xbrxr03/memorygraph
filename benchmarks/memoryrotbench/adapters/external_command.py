"""Strict JSON bridge for Graphify and other external retrieval systems."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter


class ExternalAdapterError(RuntimeError):
    pass


class ExternalCommandAdapter(RetrievalAdapter):
    """Ask a subprocess to select IDs from the same bank- and time-bounded corpus."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        adapter_name: str = "external",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = tuple(command)
        self.adapter_name = adapter_name
        self.timeout_seconds = timeout_seconds

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        visible = self.visible_events(corpus, target_scenario, query)
        by_id = {event.event_id: event for event in visible}
        request = {
            "protocol": "memoryrotbench.external/v1",
            "adapter": self.adapter_name,
            "scenario_id": target_scenario.scenario_id,
            "bank_id": target_scenario.bank_id,
            "as_of": target_scenario.event_by_id(query.after_event).at,
            "query": query.question,
            "events": [
                {
                    "event_id": event.event_id,
                    "at": event.at,
                    "content": event.content,
                }
                for event in visible
            ],
        }
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(request),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExternalAdapterError(f"{self.adapter_name} bridge failed: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise ExternalAdapterError(f"{self.adapter_name} bridge failed: {detail}")
        try:
            response = json.loads(completed.stdout)
            event_ids = response["event_ids"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ExternalAdapterError(
                f"{self.adapter_name} bridge returned an invalid response"
            ) from error
        if not isinstance(event_ids, list) or not all(isinstance(item, str) for item in event_ids):
            raise ExternalAdapterError("external event_ids must be an array of strings")
        unknown = [event_id for event_id in event_ids if event_id not in by_id]
        if unknown:
            raise ExternalAdapterError(
                f"{self.adapter_name} selected non-visible event IDs: {', '.join(unknown)}"
            )
        return [by_id[event_id] for event_id in dict.fromkeys(event_ids)]
