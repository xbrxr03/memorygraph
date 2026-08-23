"""Deterministic benchmark runner over the scenario corpus."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from benchmarks.memoryrotbench.adapters.base import RetrievalAdapter
from benchmarks.memoryrotbench.graders import grade_query_result
from benchmarks.memoryrotbench.results import (
    QueryResultRecord,
    ReportSummary,
    make_run_id,
    write_json_report,
)
from benchmarks.memoryrotbench.scenario_loader import Scenario


@dataclass(frozen=True)
class BenchmarkRunResult:
    run_id: str
    adapter_name: str
    query_results: tuple[QueryResultRecord, ...]

    def summary(self) -> ReportSummary:
        total = len(self.query_results)
        passed = sum(1 for result in self.query_results if result.grade.passed)
        recall_total = sum(result.grade.required_evidence_recall for result in self.query_results)
        leaked = sum(1 for result in self.query_results if result.grade.leaked_banks)
        forbidden_evidence = sum(
            1 for result in self.query_results if result.grade.present_forbidden_evidence
        )
        forbidden_fragments = sum(
            1 for result in self.query_results if result.grade.present_forbidden_fragments
        )
        return ReportSummary(
            run_id=self.run_id,
            adapter_name=self.adapter_name,
            query_count=total,
            passed_queries=passed,
            failed_queries=total - passed,
            average_required_evidence_recall=(recall_total / total) if total else 0.0,
            leaked_query_count=leaked,
            forbidden_evidence_query_count=forbidden_evidence,
            forbidden_fragment_query_count=forbidden_fragments,
        )

    def to_dict(self) -> dict:
        summary = self.summary()
        return {
            "run_id": self.run_id,
            "adapter_name": self.adapter_name,
            "summary": {
                "query_count": summary.query_count,
                "passed_queries": summary.passed_queries,
                "failed_queries": summary.failed_queries,
                "average_required_evidence_recall": summary.average_required_evidence_recall,
                "leaked_query_count": summary.leaked_query_count,
                "forbidden_evidence_query_count": summary.forbidden_evidence_query_count,
                "forbidden_fragment_query_count": summary.forbidden_fragment_query_count,
            },
            "results": [record.to_dict() for record in self.query_results],
        }

    def write_json_report(self, path: str) -> None:
        write_json_report(path, self.to_dict())


class BenchmarkRunner:
    def __init__(self, scenarios: Sequence[Scenario]) -> None:
        self.scenarios = tuple(scenarios)

    def run(
        self,
        adapter: RetrievalAdapter,
        *,
        scenario_ids: Sequence[str] | None = None,
        run_id: str | None = None,
    ) -> BenchmarkRunResult:
        target_ids = set(scenario_ids or [])
        records: list[QueryResultRecord] = []
        active_run_id = run_id or make_run_id(adapter.adapter_name)
        for scenario in self.scenarios:
            if target_ids and scenario.scenario_id not in target_ids:
                continue
            for query in scenario.queries:
                adapter_result = adapter.run_query(self.scenarios, scenario, query)
                grade = grade_query_result(scenario, query, adapter_result)
                records.append(
                    QueryResultRecord(
                        run_id=active_run_id,
                        adapter_result=adapter_result,
                        grade=grade,
                    )
                )
        return BenchmarkRunResult(
            run_id=active_run_id,
            adapter_name=adapter.adapter_name,
            query_results=tuple(records),
        )
