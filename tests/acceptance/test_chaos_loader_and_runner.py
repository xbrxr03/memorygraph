from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.memoryrotbench import (
    ChaosCaseValidationError,
    ChaosRunner,
    FakeDreamRuntime,
    discover_chaos_case_files,
    load_chaos_case,
    load_chaos_cases,
)

ROOT = Path(__file__).resolve().parents[2]
CHAOS_DIR = ROOT / "benchmarks" / "memoryrotbench" / "scenarios" / "development"


class ChaosLoaderAndRunnerTest(unittest.TestCase):
    def test_loads_all_chaos_cases(self) -> None:
        cases = load_chaos_cases(CHAOS_DIR)
        self.assertEqual(len(cases), 7)
        self.assertEqual(
            [case.acceptance_case for case in cases],
            [15, 11, 9, 10, 13, 14, 12],
        )

    def test_discovers_only_json_cases(self) -> None:
        paths = discover_chaos_case_files(CHAOS_DIR)
        self.assertTrue(all(path.suffix == ".json" for path in paths))
        self.assertTrue(all(not path.name.startswith(".") for path in paths))

    def test_loader_rejects_unknown_observation_reference(self) -> None:
        payload = json.loads(
            (CHAOS_DIR / "chaos-invalid-evidence-001.json").read_text(encoding="utf-8")
        )
        payload["steps"][1]["proposal"]["evidence_spans"][0]["observation_id"] = "obs-missing"
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir) / "invalid.json"
            temp_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ChaosCaseValidationError):
                load_chaos_case(temp_path)

    def test_runner_writes_serializable_report(self) -> None:
        case = load_chaos_case(CHAOS_DIR / "chaos-idempotent-replay-001.json")
        result = ChaosRunner(FakeDreamRuntime).run([case], run_id="chaos-report")
        self.assertEqual(result.to_dict()["summary"]["failed_cases"], 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "chaos-report.json"
            result.write_json_report(str(report_path))
            saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["run_id"], "chaos-report")
        self.assertEqual(saved["cases"][0]["case_id"], "chaos-idempotent-replay-001")
        self.assertEqual(saved["cases"][0]["step_results"][1]["passed"], True)


if __name__ == "__main__":
    unittest.main()
