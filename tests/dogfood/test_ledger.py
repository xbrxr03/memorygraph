from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.dogfood import (
    DogfoodExperimentLog,
    DogfoodRunner,
    DuplicateRunError,
    RunConfig,
    load_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "benchmarks" / "dogfood" / "fixtures" / "offline-mvp.json"
BRIDGE = ROOT / "tests" / "dogfood" / "fixtures" / "graphify_bridge.py"


def test_dogfood_ledger_is_append_only_and_fingerprinted(tmp_path) -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(
            arms=("no_memory", "graphify_compatible"),
            external_command=("python3", str(BRIDGE)),
        ),
        run_id="dogfood-ledger",
    )
    path = tmp_path / "dogfood.jsonl"
    record = DogfoodExperimentLog(path).append(
        result,
        manifest=manifest,
        config=RunConfig(
            arms=("no_memory", "graphify_compatible"),
            external_command=("python3", str(BRIDGE)),
        ),
        provenance={"purpose": "unit-test"},
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == record
    assert len(stored["manifest_sha256"]) == 64
    assert len(stored["config_sha256"]) == 64
    assert len(stored["evaluator_sha256"]) == 64
    with pytest.raises(DuplicateRunError):
        DogfoodExperimentLog(path).append(
            result,
            manifest=manifest,
            config=RunConfig(
                arms=("no_memory", "graphify_compatible"),
                external_command=("python3", str(BRIDGE)),
            ),
        )


def test_dogfood_ledger_preserves_valid_jsonl_without_trailing_newline(tmp_path) -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(
            arms=("no_memory",),
            external_command=None,
        ),
        run_id="dogfood-ledger-newline",
    )
    path = tmp_path / "dogfood.jsonl"
    path.write_text(
        '{"schema":"memorygraph.dogfood.experiment/v1","run_id":"prior"}',
        encoding="utf-8",
    )

    DogfoodExperimentLog(path).append(
        result,
        manifest=manifest,
        config=RunConfig(arms=("no_memory",), external_command=None),
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "prior"
    assert json.loads(lines[1])["run_id"] == "dogfood-ledger-newline"
