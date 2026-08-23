from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memorygraph import MemoryGraph
from memorygraph.integrations.codex import CodexSessionAdapter


def test_ingest_records_requires_approval_and_is_idempotent() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:codex-adapter")
        adapter = CodexSessionAdapter(memory)

        records = [
            {
                "bank": bank.id,
                "session_id": "thread-1",
                "turn_id": "turn-1",
                "role": "user",
                "content": "We run tests with pytest -q.",
                "approved": True,
                "workspace": "repo-a",
                "created_at": "2026-08-22T10:00:00Z",
            },
            {
                "bank": bank.id,
                "session_id": "thread-1",
                "turn_id": "turn-2",
                "role": "assistant",
                "content": "Unapproved internal note.",
                "approved": False,
                "workspace": "repo-a",
                "created_at": "2026-08-22T10:01:00Z",
            },
        ]

        first = adapter.ingest_records(records)
        second = adapter.ingest_records(records)

        assert first.imported_count == 1
        assert first.skipped_records == ("thread-1:turn-2",)
        assert second.imported_observation_ids == first.imported_observation_ids

        stored = memory.observations.list_by_source_key(bank.id, "codex:thread-1:turn-1:user")
        assert len(stored) == 1
        assert stored[0].metadata_json["workspace"] == "repo-a"
        assert stored[0].metadata_json["codex"]["approved"] is True


def test_ingest_jsonl_reads_explicit_export_without_private_state_scraping() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:codex-jsonl")
        path = Path(directory) / "session.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "bank": bank.slug,
                            "thread_id": "thread-9",
                            "message_id": "msg-1",
                            "role": "user",
                            "content": "Stripe is now our webhook provider.",
                            "approval": "approved",
                            "workspace": "repo-b",
                            "observed_at": "2026-08-22T12:00:00Z",
                        }
                    ),
                    json.dumps(
                        {
                            "bank": bank.slug,
                            "thread_id": "thread-9",
                            "message_id": "msg-2",
                            "role": "assistant",
                            "content": "Draft reply",
                            "approval": "pending",
                            "workspace": "repo-b",
                            "observed_at": "2026-08-22T12:01:00Z",
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        adapter = CodexSessionAdapter(memory)

        report = adapter.ingest_jsonl(path)

        assert report.imported_count == 1
        assert report.skipped_records == ("thread-9:msg-2",)
        observations = memory.observations.list_by_source_key(bank.id, "codex:thread-9:msg-1:user")
        assert len(observations) == 1
        assert observations[0].metadata_json["codex"]["session_id"] == "thread-9"
