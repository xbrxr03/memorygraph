from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
for candidate in (str(ROOT), str(SRC_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from benchmarks.memoryrotbench import ChaosRunner, load_chaos_cases  # noqa: E402
from benchmarks.memoryrotbench.dream_contracts import (  # noqa: E402
    DreamProposal,
    EvidenceSpan,
)
from benchmarks.memoryrotbench.production_runtime import (  # noqa: E402
    SUPPORTED_ACCEPTANCE_CASES,
    ProductionDreamRuntime,
    UnsupportedProductionHook,
    support_matrix,
)

CHAOS_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "development"


class ProductionRuntimeAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_chaos_cases(CHAOS_DIR)

    def test_support_matrix_reports_current_engine_boundary(self) -> None:
        matrix = support_matrix(self.cases)
        by_case = {row.acceptance_case: row for row in matrix}
        self.assertEqual(
            {row.acceptance_case for row in matrix if row.supported},
            SUPPORTED_ACCEPTANCE_CASES,
        )
        if 13 in SUPPORTED_ACCEPTANCE_CASES:
            self.assertTrue(by_case[13].supported)
        else:
            self.assertIn("rollback", by_case[13].reason)
            self.assertFalse(by_case[13].supported)
        if 14 in SUPPORTED_ACCEPTANCE_CASES:
            self.assertTrue(by_case[14].supported)
        else:
            self.assertIn("deletion", by_case[14].reason)
            self.assertFalse(by_case[14].supported)

    def test_all_production_chaos_cases_report_expected_matrix(self) -> None:
        result = ChaosRunner(ProductionDreamRuntime).run(
            self.cases,
            run_id="production-runtime-all",
        )
        self.assertEqual(len(result.case_results), 7)
        passed_cases = {
            case_result.acceptance_case for case_result in result.case_results if case_result.passed
        }
        failed_cases = {
            case_result.acceptance_case
            for case_result in result.case_results
            if not case_result.passed
        }
        self.assertEqual(passed_cases, SUPPORTED_ACCEPTANCE_CASES)
        self.assertEqual(failed_cases, set(range(9, 16)) - SUPPORTED_ACCEPTANCE_CASES)

    def test_committed_proposal_persists_synthetic_dream_records(self) -> None:
        runtime = ProductionDreamRuntime({"obs-1": "I started at Stripe today."})
        outcome = runtime.process_proposal(
            DreamProposal(
                proposal_id="proposal-1",
                idempotency_key="key-1",
                claim_id="claim.alice_stripe",
                subject="user:alice",
                predicate="works_at",
                object_value="Stripe",
                evidence_spans=(EvidenceSpan("obs-1", 0, 25),),
            )
        )
        self.assertEqual(outcome.status, "committed")
        self.assertIsNotNone(runtime.memory.dream_runs.get(runtime.bank.id, outcome.run_id))
        self.assertIsNotNone(
            runtime.memory.dream_tasks.get(runtime.bank.id, f"{outcome.run_id}:task")
        )
        self.assertIsNotNone(
            runtime.memory.dream_proposals.get(
                runtime.bank.id,
                f"{outcome.run_id}:proposal",
            )
        )
        committed_event = runtime.memory.events.get_by_idempotency_key(
            runtime.bank.id,
            f"dream-commit:{outcome.run_id}:proposal",
        )
        self.assertIsNotNone(committed_event)
        self.assertEqual(committed_event.event_type, "dream.proposal.committed")
        runtime.close()

    def test_missing_public_hooks_raise_explicit_errors(self) -> None:
        runtime = ProductionDreamRuntime({"obs-1": "I work at Acme."})
        if 13 not in SUPPORTED_ACCEPTANCE_CASES:
            with self.assertRaises(UnsupportedProductionHook):
                runtime.rollback("run-1")
        if 14 not in SUPPORTED_ACCEPTANCE_CASES:
            with self.assertRaises(UnsupportedProductionHook):
                runtime.delete_evidence("obs-1")
        runtime.close()


if __name__ == "__main__":
    unittest.main()
