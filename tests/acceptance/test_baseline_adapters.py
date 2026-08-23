from __future__ import annotations

import sys
import unittest
from pathlib import Path

from benchmarks.memoryrotbench import load_scenario, load_scenarios
from benchmarks.memoryrotbench.adapters import (
    BM25Adapter,
    ExternalAdapterError,
    ExternalCommandAdapter,
    FlatContextAdapter,
    LatestNAdapter,
    MarkdownRunbookAdapter,
    NoMemoryAdapter,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"


class BaselineAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_scenarios(PUBLIC_DIR)

    def test_flat_context_returns_all_visible_bank_events(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = FlatContextAdapter().run_query(self.corpus, scenario, query)
        self.assertEqual([event.event_id for event in result.context_events], ["e1", "e2", "e3"])
        self.assertGreater(result.token_estimate, 0)

    def test_latest_n_can_drop_the_true_current_update(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = LatestNAdapter(limit=1).run_query(self.corpus, scenario, query)
        self.assertEqual([event.event_id for event in result.context_events], ["e3"])

    def test_cross_bank_isolation_keeps_foreign_events_out_of_context(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "cross-bank-isolation-alpha-001.json")
        query = scenario.query_by_id("q1")
        result = FlatContextAdapter().run_query(self.corpus, scenario, query)
        self.assertEqual(len(result.context_events), 1)
        self.assertEqual(result.context_events[0].bank_id, "project:apollo-alpha")
        self.assertNotIn("pnpm shipit", result.context_events[0].content)

    def test_no_memory_is_an_explicit_zero_context_control(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "explicit-correction-001.json")
        query = scenario.queries[0]
        result = NoMemoryAdapter().run_query(self.corpus, scenario, query)
        self.assertEqual(result.context_events, ())

    def test_bm25_selects_lexically_relevant_visible_evidence(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "cross-bank-isolation-alpha-001.json")
        query = scenario.query_by_id("q1")
        result = BM25Adapter(max_items=3).run_query(self.corpus, scenario, query)
        self.assertEqual([event.event_id for event in result.context_events], ["e1"])
        self.assertGreater(result.context_events[0].score or 0, 0)

    def test_markdown_runbook_preserves_event_identity_and_renders_sections(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        query = scenario.query_by_id("q_current")
        result = MarkdownRunbookAdapter(max_tokens=512).run_query(self.corpus, scenario, query)
        self.assertEqual(
            [event.event_id for event in result.context_events],
            ["e3", "e2", "e1"],
        )
        self.assertTrue(result.context_events[0].content.startswith("## "))

    def test_external_bridge_can_only_select_visible_event_ids(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "cross-bank-isolation-alpha-001.json")
        query = scenario.query_by_id("q1")
        script = (
            "import json,sys; request=json.load(sys.stdin); "
            "print(json.dumps({'event_ids':[request['events'][0]['event_id']]}))"
        )
        result = ExternalCommandAdapter(
            [sys.executable, "-c", script], adapter_name="graphify"
        ).run_query(self.corpus, scenario, query)
        self.assertEqual([event.event_id for event in result.context_events], ["e1"])

    def test_external_bridge_rejects_non_visible_event_ids(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "cross-bank-isolation-alpha-001.json")
        query = scenario.query_by_id("q1")
        script = 'print("{\\"event_ids\\":[\\"foreign\\"]}")'
        with self.assertRaises(ExternalAdapterError):
            ExternalCommandAdapter([sys.executable, "-c", script]).run_query(
                self.corpus, scenario, query
            )


if __name__ == "__main__":
    unittest.main()
