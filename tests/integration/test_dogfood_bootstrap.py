from __future__ import annotations

import tempfile
from pathlib import Path

from memorygraph import MemoryGraph
from memorygraph.dogfood import bootstrap_project_memory


def test_dogfood_bootstrap_is_idempotent_and_recallable() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        first = bootstrap_project_memory(memory)
        second = bootstrap_project_memory(memory)

        assert first == second
        assert len(first.claim_ids) == 7
        recalled = memory.recall(
            bank_id=first.bank_slug,
            query_text="How should Dream provider candidates get committed?",
            max_items=5,
            max_tokens=300,
        )
        assert any("deterministic validation" in hit.content for hit in recalled)
        attempts = memory.recall_attempts(
            bank=first.bank_slug,
            query_text="alpha MCP handshake",
        )
        assert len(attempts) == 1
