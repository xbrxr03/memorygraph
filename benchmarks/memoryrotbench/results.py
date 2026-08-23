"""Dataclasses and serializers for benchmark query results and reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.memoryrotbench.adapters.base import AdapterQueryResult


@dataclass(frozen=True)
class QueryGrade:
    adapter_name: str
    scenario_id: str
    query_id: str
    bank_id: str
    passed: bool
    required_evidence_found: tuple[str, ...]
    missing_required_evidence: tuple[str, ...]
    present_forbidden_evidence: tuple[str, ...]
    leaked_banks: tuple[str, ...]
    present_forbidden_fragments: tuple[str, ...]
    required_evidence_recall: float


@dataclass(frozen=True)
class QueryResultRecord:
    run_id: str
    adapter_result: AdapterQueryResult
    grade: QueryGrade

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "adapter_name": self.adapter_result.adapter_name,
            "scenario_id": self.adapter_result.scenario_id,
            "query_id": self.adapter_result.query_id,
            "bank_id": self.adapter_result.bank_id,
            "context_event_ids": [event.event_id for event in self.adapter_result.context_events],
            "token_estimate": self.adapter_result.token_estimate,
            "context_events": [
                {
                    "scenario_id": event.scenario_id,
                    "bank_id": event.bank_id,
                    "event_id": event.event_id,
                    "at": event.at,
                    "content": event.content,
                    "score": event.score,
                    "metadata": event.metadata or {},
                }
                for event in self.adapter_result.context_events
            ],
            "grade": {
                **asdict(self.grade),
                "required_evidence_found": list(self.grade.required_evidence_found),
                "missing_required_evidence": list(self.grade.missing_required_evidence),
                "present_forbidden_evidence": list(self.grade.present_forbidden_evidence),
                "leaked_banks": list(self.grade.leaked_banks),
                "present_forbidden_fragments": list(self.grade.present_forbidden_fragments),
            },
        }


@dataclass(frozen=True)
class ReportSummary:
    run_id: str
    adapter_name: str
    query_count: int
    passed_queries: int
    failed_queries: int
    average_required_evidence_recall: float
    leaked_query_count: int
    forbidden_evidence_query_count: int
    forbidden_fragment_query_count: int

    def to_text(self) -> str:
        return "\n".join(
            [
                f"Run ID: {self.run_id}",
                f"Adapter: {self.adapter_name}",
                f"Queries: {self.query_count}",
                f"Passed: {self.passed_queries}",
                f"Failed: {self.failed_queries}",
                f"Avg required evidence recall: {self.average_required_evidence_recall:.3f}",
                f"Bank leakage failures: {self.leaked_query_count}",
                f"Forbidden evidence failures: {self.forbidden_evidence_query_count}",
                f"Forbidden fragment failures: {self.forbidden_fragment_query_count}",
            ]
        )


def make_run_id(prefix: str = "memoryrotbench") -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{now}"


def write_json_report(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
