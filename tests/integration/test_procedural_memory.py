from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memorygraph import MemoryGraph


def test_attempts_are_idempotent_searchable_and_keep_applicability(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        first = memory.record_attempt(
            bank=bank.id,
            source_key="codex:session-1:attempt-1",
            task_key="repair flaky sqlite worker test",
            strategy="increase sleep before checking the lease",
            outcome="failure",
            failure="timing remained nondeterministic",
            applicability={"os": "macos", "database": "sqlite"},
            environment={"python": "3.13", "wal": True},
        )
        replay = memory.record_attempt(
            bank=bank.id,
            source_key="codex:session-1:attempt-1",
            task_key="repair flaky sqlite worker test",
            strategy="increase sleep before checking the lease",
            outcome="failure",
            failure="timing remained nondeterministic",
            applicability={"os": "macos", "database": "sqlite"},
            environment={"python": "3.13", "wal": True},
        )

        attempts = memory.recall_attempts(
            bank=bank.id,
            query_text="sqlite lease timing failure",
        )
        recalled = memory.recall(
            bank_id=bank.id,
            query_text="sqlite lease timing failure",
            max_items=3,
        )

        assert replay.id == first.id
        assert len(attempts) == 1
        assert attempts[0].episode.outcome == "failure"
        assert attempts[0].episode.applicability == {"database": "sqlite", "os": "macos"}
        assert len(recalled) == 1
        assert recalled[0].metadata is not None
        assert recalled[0].metadata["memory_kind"] == "attempt"
        assert recalled[0].metadata["outcome"] == "failure"
        assert recalled[0].metadata["freshness_form"] == "snapshot"


def test_attempt_source_deletion_redacts_procedural_prose(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        episode = memory.record_attempt(
            bank=bank.id,
            source_key="codex:session-1:attempt-secret",
            task_key="deploy acme",
            strategy="use private token ORBIT-9284",
            outcome="failure",
            failure="token was revoked",
        )

        result = memory.delete_observation(episode.source_observation_id, bank=bank.id)
        redacted = memory.procedural_episodes.get(bank.id, episode.id)

        assert result.residue_issues == ()
        assert redacted is not None
        assert redacted.strategy.startswith("[redacted procedural source:")
        assert redacted.failure == redacted.strategy
        assert memory.recall_attempts(bank=bank.id, query_text="ORBIT-9284") == ()


def test_relevant_attempt_gets_a_reserved_recall_slot_and_respects_known_time(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:acme")
        for index in range(4):
            observation = memory.observe(
                f"MCP validation claim number {index}",
                bank=bank.id,
                source_key=f"claim:{index}",
            )
            memory.assert_claim(
                bank=bank.id,
                subject=f"Component {index}",
                predicate="validation_note",
                object=f"MCP validation {index}",
                object_kind="string",
                observation_id=observation.id,
            )
        future = datetime.now(UTC) + timedelta(days=2)
        episode = memory.record_attempt(
            bank=bank.id,
            source_key="attempt:mcp",
            task_key="validate MCP transport",
            strategy="Use a newline-delimited subprocess handshake.",
            outcome="success",
            completed_at=future.isoformat(),
        )

        before = memory.recall(
            bank_id=bank.id,
            query_text="How was MCP validated?",
            as_of=datetime.now(UTC).isoformat(),
            max_items=3,
        )
        after = memory.recall(
            bank_id=bank.id,
            query_text="How was MCP validated?",
            as_of=(future + timedelta(seconds=1)).isoformat(),
            max_items=3,
        )

        assert all((hit.metadata or {}).get("episode_id") != episode.id for hit in before)
        assert len(after) == 3
        assert any((hit.metadata or {}).get("episode_id") == episode.id for hit in after)
