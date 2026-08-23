"""Append-only benchmark log with corpus and evaluator fingerprints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .runner import BenchmarkRunResult
from .scenario_loader import Scenario

LOG_SCHEMA = "memoryrotbench.experiment/v1"


class DuplicateRunError(ValueError):
    pass


class ExperimentLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        result: BenchmarkRunResult,
        *,
        scenarios: tuple[Scenario, ...] | list[Scenario],
        adapter_config: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result.run_id in self._run_ids():
            raise DuplicateRunError(f"run_id already exists: {result.run_id}")
        record = {
            "schema": LOG_SCHEMA,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": result.run_id,
            "adapter_name": result.adapter_name,
            "adapter_config": adapter_config or {},
            "corpus_sha256": _corpus_fingerprint(scenarios),
            "evaluator_sha256": _evaluator_fingerprint(),
            "provenance": provenance or {},
            "report": result.to_dict(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_separator = _needs_record_separator(self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            if needs_separator:
                handle.write("\n")
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        return record

    def _run_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        run_ids: set[str] = set()
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    run_ids.add(str(json.loads(line)["run_id"]))
                except (json.JSONDecodeError, KeyError) as error:
                    raise ValueError(
                        f"invalid experiment log record on line {line_number}"
                    ) from error
        return run_ids


def _corpus_fingerprint(scenarios: tuple[Scenario, ...] | list[Scenario]) -> str:
    payload = [scenario.raw for scenario in sorted(scenarios, key=lambda item: item.scenario_id)]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(canonical).hexdigest()


def _evaluator_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    paths = [root / "runner.py", root / "results.py", root / "graders" / "retrieval.py"]
    digest = sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _needs_record_separator(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("rb") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            return False
        handle.seek(-1, 2)
        return handle.read(1) not in {b"\n", b"\r"}
