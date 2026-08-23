from __future__ import annotations

from memorygraph import MemoryGraph


def test_recall_never_exceeds_max_tokens_even_for_first_hit(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:falsify")
        observation = memory.observe(
            "word " * 120,
            bank=bank.id,
            source_key="session:oversized",
        )
        memory.define_predicate(
            "note",
            bank=bank.id,
            cardinality="many",
            volatility="durable",
        )
        memory.assert_claim(
            bank=bank.id,
            subject="Acme",
            predicate="note",
            object="oversized payload",
            object_kind="string",
            observation_id=observation.id,
        )

        hits = memory.recall(
            bank_id=bank.id,
            query_text="word oversized payload",
            max_items=3,
            max_tokens=20,
        )

    assert hits == ()


def test_procedural_recall_respects_as_of_cutoff(tmp_path) -> None:
    with MemoryGraph.open(tmp_path / "memory.db") as memory:
        bank = memory.create_bank("project:falsify")
        memory.record_attempt(
            bank=bank.id,
            source_key="codex:attempt-1",
            task_key="repair deploy worker",
            strategy="old strategy",
            outcome="failure",
            completed_at="2026-08-20T10:00:00Z",
        )
        memory.record_attempt(
            bank=bank.id,
            source_key="codex:attempt-2",
            task_key="repair deploy worker",
            strategy="new strategy",
            outcome="success",
            completed_at="2026-08-22T10:00:00Z",
        )

        attempts = memory.recall_attempts(
            bank=bank.id,
            query_text="repair deploy worker",
            as_of="2026-08-21T00:00:00Z",
        )
        hits = memory.recall(
            bank_id=bank.id,
            query_text="repair deploy worker",
            as_of="2026-08-21T00:00:00Z",
            max_items=5,
            max_tokens=100,
        )

    assert [attempt.episode.strategy for attempt in attempts] == ["old strategy"]
    assert [hit.metadata["memory_kind"] for hit in hits] == ["attempt"]
    assert [hit.content.splitlines()[1] for hit in hits] == ["Strategy: old strategy"]
