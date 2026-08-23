from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.memoryrotbench.scenario_loader import (
    ScenarioValidationError,
    load_scenario,
    load_scenarios,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"


class ScenarioLoaderTest(unittest.TestCase):
    def test_loads_public_scenarios(self) -> None:
        scenarios = load_scenarios(PUBLIC_DIR)
        self.assertGreaterEqual(len(scenarios), 8)
        self.assertEqual(len({scenario.scenario_id for scenario in scenarios}), len(scenarios))

    def test_public_categories_cover_mvp_seed_set(self) -> None:
        scenarios = load_scenarios(PUBLIC_DIR)
        categories = {scenario.category for scenario in scenarios}
        self.assertTrue(
            {
                "duplicate_paraphrase",
                "historical_mention_trap",
                "multi_valued_truth",
                "negation_and_correction",
                "conflicting_sources",
                "expiring_state",
                "poisoning_and_injection",
                "isolation",
            }.issubset(categories)
        )

    def test_employment_fixture_encodes_current_and_historical_queries(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "employment-historical-mention-001.json")
        current_query = scenario.query_by_id("q_current")
        historical_query = scenario.query_by_id("q_february")
        self.assertEqual(current_query.answer, "Stripe")
        self.assertEqual(historical_query.answer, "Acme")
        self.assertIn("claim.alice_works_at_acme", current_query.forbidden_current_claim_ids)
        self.assertEqual(historical_query.required_evidence_event_ids, ("e1",))

    def test_duplicate_confirmation_fixture_aggregates_three_evidence_events(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "duplicate-confirmation-001.json")
        self.assertEqual(len(scenario.claims), 1)
        self.assertEqual(
            scenario.claims[0].evidence_event_ids,
            ("e1", "e2", "e3"),
        )

    def test_loader_rejects_unknown_claim_reference(self) -> None:
        scenario_path = PUBLIC_DIR / "explicit-correction-001.json"
        payload = json.loads(scenario_path.read_text(encoding="utf-8"))
        payload["queries"][0]["required_claim_ids"].append("claim.missing")
        with self.assertRaises(ScenarioValidationError):
            load_scenario(_write_temp_payload(payload))


def _write_temp_payload(payload: dict) -> Path:
    temp_path = ROOT / "tests" / "acceptance" / "_tmp_invalid_scenario.json"
    temp_path.write_text(json.dumps(payload), encoding="utf-8")
    return temp_path


def tearDownModule() -> None:
    temp_path = ROOT / "tests" / "acceptance" / "_tmp_invalid_scenario.json"
    if temp_path.exists():
        temp_path.unlink()
