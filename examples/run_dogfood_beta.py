from __future__ import annotations

from pathlib import Path

from benchmarks.dogfood.accelerated_beta import write_accelerated_beta

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "benchmarks/reports/dogfood-beta.json"
LEDGER = ROOT / "benchmarks/reports/dogfood-beta.jsonl"


def main() -> None:
    report = write_accelerated_beta(ROOT, report_path=REPORT, ledger_path=LEDGER)
    print(f"Dogfood Beta: {'PASS' if report['passed'] else 'FAIL'}")
    for gate, passed in report["gates"].items():
        print(f"- {'PASS' if passed else 'FAIL'}: {gate}")
    print(REPORT)
    print(LEDGER)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
