from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.memoryrotbench import (
    ChaosRunner,
    FakeDreamRuntime,
    load_chaos_case,
    load_chaos_cases,
    load_scenario,
    load_scenarios,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "public"
CHAOS_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "development"


class DreamCycleAcceptanceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = load_scenarios(PUBLIC_DIR)
        cls.covered_cases = {
            case for scenario in cls.scenarios for case in scenario.acceptance_cases
        }
        cls.chaos_cases = load_chaos_cases(CHAOS_DIR)

    def test_public_seed_set_covers_acceptance_cases_1_through_8(self) -> None:
        self.assertEqual(set(range(1, 9)) - self.covered_cases, set())

    def test_chaos_seed_set_covers_acceptance_cases_9_through_15(self) -> None:
        covered = {case.acceptance_case for case in self.chaos_cases}
        self.assertEqual(covered, set(range(9, 16)))

    def test_case_5_uses_many_cardinality(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "multi-valued-hobbies-001.json")
        cardinals = {claim.cardinality for claim in scenario.claims}
        self.assertEqual(cardinals, {"many"})
        self.assertTrue(all(claim.lifecycle == "current" for claim in scenario.claims))

    def test_case_6_requires_contested_outcome(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "contested-authority-001.json")
        query = scenario.query_by_id("q1")
        self.assertEqual(query.outcome_type, "contested")
        self.assertEqual(len(query.required_claim_ids), 2)

    def test_case_7_and_8_keep_untrusted_text_out_of_claims(self) -> None:
        scenario = load_scenario(PUBLIC_DIR / "poisoning-directive-separation-001.json")
        self.assertEqual(len(scenario.claims), 1)
        self.assertEqual(
            scenario.events[1].expected_outcomes[0]["action"],
            "store_untrusted_content",
        )
        for query in scenario.queries:
            self.assertIn("ignore previous instructions", query.forbidden_answer_fragments)

    def test_chaos_cases_execute_against_reference_runtime(self) -> None:
        result = ChaosRunner(FakeDreamRuntime).run(
            self.chaos_cases,
            run_id="acceptance-chaos-reference",
        )
        self.assertTrue(all(case_result.passed for case_result in result.case_results))
        self.assertEqual(len(result.case_results), 7)

    def test_case_12_fixture_keeps_stripe_as_current_winner(self) -> None:
        case = load_chaos_case(CHAOS_DIR / "chaos-stale-proposal-race-001.json")
        result = ChaosRunner(FakeDreamRuntime).run([case], run_id="case-12")
        self.assertTrue(result.case_results[0].passed)
        self.assertEqual(
            result.case_results[0].final_snapshot.current_view,
            (("user:alice|works_at", "claim.alice_stripe"),),
        )

    def test_case_15_fixture_blocks_artifact_cycles(self) -> None:
        case = load_chaos_case(CHAOS_DIR / "chaos-artifact-citation-cycle-001.json")
        result = ChaosRunner(FakeDreamRuntime).run([case], run_id="case-15")
        self.assertTrue(result.case_results[0].passed)
        self.assertEqual(
            result.case_results[0].final_snapshot.artifact_ids,
            ("artifact.profile.v1",),
        )


if __name__ == "__main__":
    unittest.main()
