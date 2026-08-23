from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.memoryrotbench import BenchmarkRunner, load_scenario, load_scenarios
from benchmarks.memoryrotbench.adapters import (
    FlatContextAdapter,
    LatestNAdapter,
    MemoryGraphAdapter,
    MemoryGraphRecallHit,
)
from benchmarks.memoryrotbench.graders import grade_query_result

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"


class _StubMemoryGraphClient:
    def __init__(self, hits: list[MemoryGraphRecallHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    def recall(
        self,
        *,
        bank_id: str,
        query_text: str,
        as_of: str,
        max_items: int,
        max_tokens: int,
    ) -> list[MemoryGraphRecallHit]:
        self.calls.append(
            {
                "bank_id": bank_id,
                "query_text": query_text,
                "as_of": as_of,
                "max_items": max_items,
                "max_tokens": max_tokens,
            }
        )
        return list(self.hits)


class RunnerAndGradingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = load_scenarios(PUBLIC_DIR)
        cls.runner = BenchmarkRunner(cls.scenarios)

    def test_grader_flags_missing_required_evidence_for_latest_n(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = LatestNAdapter(limit=1).run_query(self.scenarios, scenario, query)
        grade = grade_query_result(scenario, query, result)
        self.assertFalse(grade.passed)
        self.assertEqual(grade.missing_required_evidence, ("e2",))
        self.assertEqual(grade.required_evidence_recall, 0.0)

    def test_grader_flags_forbidden_evidence_on_historical_trap(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = FlatContextAdapter().run_query(self.scenarios, scenario, query)
        grade = grade_query_result(scenario, query, result)
        self.assertFalse(grade.passed)
        self.assertEqual(grade.present_forbidden_evidence, ("e1",))

    def test_runner_produces_summary_and_serializable_report(self) -> None:
        result = self.runner.run(
            FlatContextAdapter(),
            scenario_ids=["cross-bank-isolation-alpha-001"],
            run_id="flat-cross-bank",
        )
        self.assertEqual(result.summary().query_count, 1)
        self.assertEqual(result.summary().failed_queries, 0)
        payload = result.to_dict()
        self.assertEqual(payload["run_id"], "flat-cross-bank")
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.json"
            result.write_json_report(str(report_path))
            saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"]["failed_queries"], 0)
        self.assertEqual(saved["results"][0]["grade"]["leaked_banks"], [])

    def test_memorygraph_adapter_protocol_wraps_recall_client(self) -> None:
        client = _StubMemoryGraphClient(
            [
                MemoryGraphRecallHit(
                    event_id="evt-1",
                    at="2026-03-04T12:00:00Z",
                    content="I started at Stripe today.",
                    bank_id="user:alice",
                    scenario_id="employment-historical-mention-001",
                    score=0.9,
                )
            ]
        )
        adapter = MemoryGraphAdapter(client, max_items=5, max_tokens=100)
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = adapter.run_query(self.scenarios, scenario, query)
        self.assertEqual(result.adapter_name, "memorygraph")
        self.assertEqual([event.event_id for event in result.context_events], ["evt-1"])
        self.assertEqual(client.calls[0]["bank_id"], "user:alice")
        self.assertEqual(client.calls[0]["query_text"], "Where does Alice work now?")


if __name__ == "__main__":
    unittest.main()
