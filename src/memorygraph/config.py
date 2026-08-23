"""Configuration for the embedded MemoryGraph runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MemoryGraphConfig:
    """Runtime configuration with safe local-first defaults."""

    database_path: Path
    busy_timeout_ms: int = 5_000
    enable_local_embeddings: bool = True
    local_embedding_dimensions: int = 256

    @classmethod
    def local(cls, database_path: str | Path) -> MemoryGraphConfig:
        return cls(database_path=Path(database_path).expanduser().resolve())
