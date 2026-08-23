from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.dogfood.manifest import ManifestValidationError, load_manifest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "benchmarks" / "dogfood" / "fixtures" / "offline-mvp.json"


def test_fixture_manifest_loads() -> None:
    manifest = load_manifest(FIXTURE)
    assert manifest.manifest_id == "offline-mvp"
    assert len(manifest.tasks) == 3
    assert manifest.tasks[0].steps[0].step_id == "release-note"


def test_manifest_rejects_duplicate_step_ids(tmp_path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"][1]["step_id"] = "release-note"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        load_manifest(path)


def test_manifest_rejects_non_utc_or_out_of_order_timestamps(tmp_path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"][0]["at"] = "2026-04-01T14:00:00-04:00"
    path = tmp_path / "non-utc.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="ISO-8601 UTC"):
        load_manifest(path)

    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"][1]["at"] = "2026-03-01T08:00:00Z"
    path = tmp_path / "out-of-order.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="non-decreasing"):
        load_manifest(path)


def test_manifest_rejects_invalid_budgets_and_expectation_bounds(tmp_path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"][2]["max_tokens"] = 0
    path = tmp_path / "bad-budget.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="max_tokens must be positive"):
        load_manifest(path)

    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["tasks"][0]["steps"][2]["expectations"]["min_hits"] = 2
    document["tasks"][0]["steps"][2]["expectations"]["max_hits"] = 1
    path = tmp_path / "bad-bounds.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="max_hits must be >="):
        load_manifest(path)
