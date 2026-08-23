from __future__ import annotations

from pathlib import Path

from benchmarks.dogfood.accelerated_beta import run_accelerated_beta, write_accelerated_beta

ROOT = Path(__file__).resolve().parents[2]


def test_accelerated_beta_passes_all_composite_gates() -> None:
    report = run_accelerated_beta(ROOT, run_id="accelerated-beta-test")

    assert report["passed"] is True
    assert all(report["gates"].values())
    summaries = {
        item["arm_name"]: item for item in report["dogfood"]["summary"]["arm_summaries"]
    }
    assert summaries["memorygraph_always_dream"]["passed_tasks"] == 5
    assert summaries["no_memory"]["passed_tasks"] == 0
    assert "not five sustained real users" in report["claim_scope"]
    assert len(report["fingerprints"]["evaluator_sha256"]) == 64


def test_accelerated_beta_writes_report_and_fingerprinted_ledger(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    ledger_path = tmp_path / "ledger.jsonl"

    report = write_accelerated_beta(
        ROOT,
        report_path=report_path,
        ledger_path=ledger_path,
        run_id="accelerated-beta-files",
    )

    assert report["passed"] is True
    assert report_path.exists()
    assert "composite_report_sha256" in ledger_path.read_text(encoding="utf-8")
