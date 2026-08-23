"""Append-only dogfood run ledger with immutable manifest and evaluator fingerprints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .manifest import DogfoodManifest
from .results import DogfoodRunResult
from .runner import RunConfig

LOG_SCHEMA = "memorygraph.dogfood.experiment/v1"


class DuplicateRunError(ValueError):
    pass


class DogfoodExperimentLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        result: DogfoodRunResult,
        *,
        manifest: DogfoodManifest,
        config: RunConfig,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result.run_id in self._run_ids():
            raise DuplicateRunError(f"run_id already exists: {result.run_id}")
        record = {
            "schema": LOG_SCHEMA,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": result.run_id,
            "manifest_id": manifest.manifest_id,
            "manifest_sha256": _fingerprint_value(manifest.raw),
            "config_sha256": _fingerprint_value(config.to_dict()),
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
                    raise ValueError(f"invalid log record on line {line_number}") from error
        return run_ids


def _fingerprint_value(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def _evaluator_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    digest = sha256()
    for name in ("manifest.py", "results.py", "ledger.py", "runner.py", "arms.py"):
        path = root / name
        digest.update(name.encode("utf-8"))
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
