from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from memorygraph import MemoryGraph, ValidationError


def test_acme_to_stripe_is_current_recall_with_auditable_history() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("personal:founder")
        memory.define_predicate(
            "works_at",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
            subject_type="person",
            object_type="organization",
        )

        acme_observation = memory.observe(
            "Abrar works at Acme.",
            bank=bank.id,
            source_key="event:employment:acme",
            observed_at="2026-01-01T12:00:00Z",
        )
        acme_claim = memory.assert_claim(
            bank=bank.id,
            subject="Abrar",
            subject_type="person",
            predicate="works_at",
            object="Acme",
            object_type="organization",
            observation_id=acme_observation.id,
            valid_from="2026-01-01T12:00:00Z",
            known_at="2026-01-01T12:00:00Z",
        )

        stripe_observation = memory.observe(
            "Update: Abrar now works at Stripe.",
            bank=bank.id,
            source_key="event:employment:stripe",
            observed_at="2026-06-01T12:00:00Z",
        )
        stripe_claim = memory.supersede_claim(
            acme_claim.id,
            bank=bank.id,
            object="Stripe",
            object_type="organization",
            observation_id=stripe_observation.id,
            valid_from="2026-06-01T12:00:00Z",
            known_at="2026-06-01T12:00:00Z",
            rationale="owner explicitly reported a new employer",
        )

        current = memory.recall(
            bank_id=bank.id,
            query_text="Where does Abrar work?",
            as_of="2026-08-01T12:00:00Z",
            max_items=10,
            max_tokens=100,
        )
        historical = memory.recall(
            bank_id=bank.id,
            query_text="Where did Abrar work?",
            as_of="2026-03-01T12:00:00Z",
            max_items=10,
            max_tokens=100,
        )
        history = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
        )
        explanation = memory.explain(stripe_claim.id, bank=bank.id)

    assert [hit.event_id for hit in current] == ["event:employment:stripe"]
    assert [hit.event_id for hit in historical] == ["event:employment:acme"]
    assert [(item.object, item.claim.lifecycle) for item in history] == [
        ("Acme", "active"),
        ("Acme", "superseded"),
        ("Stripe", "active"),
    ]
    assert explanation.object == "Stripe"
    assert explanation.evidence[0].excerpt == "Update: Abrar now works at Stripe."
    assert explanation.relations[0].relation == "supersedes"


def test_recall_never_crosses_bank_boundaries() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        alpha = memory.create_bank("project:alpha")
        beta = memory.create_bank("project:beta")
        alpha_observation = memory.observe(
            "Orchid is Alpha's launch codename.",
            bank=alpha.id,
            source_key="alpha:codename",
        )
        beta_observation = memory.observe(
            "Orchid is forbidden in Beta.",
            bank=beta.id,
            source_key="beta:directive",
        )
        memory.assert_claim(
            bank=alpha.id,
            subject="Alpha",
            predicate="codename",
            object="Orchid",
            object_kind="string",
            observation_id=alpha_observation.id,
        )
        memory.assert_claim(
            bank=beta.id,
            subject="Beta",
            predicate="forbidden_term",
            object="Orchid",
            object_kind="string",
            observation_id=beta_observation.id,
        )

        hits = memory.recall(
            bank_id=alpha.id,
            query_text="Orchid codename",
            max_items=10,
            max_tokens=100,
        )

    assert [hit.event_id for hit in hits] == ["alpha:codename"]
    assert all(hit.bank_id == alpha.slug for hit in hits)


def test_confirmation_deduplicates_belief_and_conflict_preserves_both_sides() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:mercury")
        memory.define_predicate(
            "build_backend",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
        )
        first = memory.observe(
            "pyproject.toml selects Poetry.",
            bank=bank.id,
            source_key="event:poetry:1",
            observed_at="2026-03-01T12:00:00Z",
        )
        confirmation = memory.observe(
            "The lockfile also names Poetry.",
            bank=bank.id,
            source_key="event:poetry:2",
            observed_at="2026-03-01T12:01:00Z",
        )
        contradiction = memory.observe(
            "docs/build.md says the backend is Hatchling.",
            bank=bank.id,
            source_key="event:hatchling",
            observed_at="2026-03-01T12:05:00Z",
        )
        poetry = memory.assert_claim(
            bank=bank.id,
            subject="Mercury",
            predicate="build_backend",
            object="Poetry",
            object_kind="string",
            observation_id=first.id,
            known_at="2026-03-01T12:00:00Z",
        )
        memory.confirm_claim(
            poetry.id,
            bank=bank.id,
            observation_id=confirmation.id,
            known_at="2026-03-01T12:01:00Z",
        )
        hatchling = memory.contradict_claim(
            poetry.id,
            bank=bank.id,
            object="Hatchling",
            object_kind="string",
            observation_id=contradiction.id,
            known_at="2026-03-01T12:05:00Z",
        )

        hits = memory.recall(
            bank_id=bank.id,
            query_text="Mercury build backend",
            as_of="2026-03-01T12:06:00Z",
            max_items=10,
            max_tokens=100,
        )
        history = memory.history(
            bank=bank.id,
            subject="Mercury",
            predicate="build_backend",
            current_versions_only=True,
        )
        explanation = memory.explain(hatchling.id, bank=bank.id)

    assert {hit.event_id for hit in hits} == {
        "event:poetry:1",
        "event:poetry:2",
        "event:hatchling",
    }
    assert {item.object for item in history} == {"Poetry", "Hatchling"}
    assert {item.claim.lifecycle for item in history} == {"contested"}
    assert explanation.relations[0].relation == "contradicts"


def test_claim_values_and_evidence_spans_are_validated_before_mutation() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:validation")
        observation = memory.observe(
            "The retry count is three.",
            bank=bank.id,
            source_key="event:retry",
        )

        try:
            memory.assert_claim(
                bank=bank.id,
                subject="Worker",
                predicate="retry_count",
                object="three",
                object_kind="number",
                observation_id=observation.id,
            )
        except ValidationError:
            pass
        else:  # pragma: no cover - assertion documents the required guard
            raise AssertionError("invalid typed values must be rejected")

        try:
            memory.assert_claim(
                bank=bank.id,
                subject="Worker",
                predicate="retry_count",
                object=3,
                object_kind="number",
                observation_id=observation.id,
                excerpt="not in the source",
            )
        except ValidationError:
            pass
        else:  # pragma: no cover - assertion documents the required guard
            raise AssertionError("invented evidence spans must be rejected")

        assert (
            memory.history(
                bank=bank.id,
                subject="Worker",
                predicate="retry_count",
            )
            == ()
        )


def test_retraction_removes_current_recall_but_preserves_historical_evidence() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:retraction")
        assertion = memory.observe(
            "The launch date is September 5.",
            bank=bank.id,
            source_key="event:launch-date",
            observed_at="2026-06-01T10:00:00Z",
        )
        correction = memory.observe(
            "Withdraw the September 5 launch date.",
            bank=bank.id,
            source_key="event:withdrawal",
            observed_at="2026-06-02T10:00:00Z",
        )
        claim = memory.assert_claim(
            bank=bank.id,
            subject="Launch",
            predicate="date",
            object="September 5",
            object_kind="string",
            observation_id=assertion.id,
            valid_from="2026-06-01T10:00:00Z",
            known_at="2026-06-01T10:00:00Z",
        )
        retracted = memory.retract_claim(
            claim.id,
            bank=bank.id,
            observation_id=correction.id,
            effective_at="2026-06-02T10:00:00Z",
            known_at="2026-06-02T10:00:00Z",
        )

        current = memory.recall(
            bank_id=bank.id,
            query_text="What is the Launch date?",
            as_of="2026-06-03T10:00:00Z",
        )
        historical = memory.recall(
            bank_id=bank.id,
            query_text="What was the Launch date?",
            as_of="2026-06-01T12:00:00Z",
        )
        explanation = memory.explain(retracted.id, bank=bank.id)

    assert current == ()
    assert [hit.event_id for hit in historical] == ["event:launch-date"]
    assert explanation.claim.lifecycle == "retracted"
    assert {item.stance for item in explanation.evidence} == {"supports", "contradicts"}


def test_failed_supersession_rolls_back_claims_relations_and_search_view() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:rollback")
        memory.define_predicate(
            "works_at",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
        )
        first = memory.observe(
            "Mira works at Acme.",
            bank=bank.id,
            source_key="event:acme",
        )
        replacement = memory.observe(
            "Mira now works at Stripe.",
            bank=bank.id,
            source_key="event:stripe",
        )
        claim = memory.assert_claim(
            bank=bank.id,
            subject="Mira",
            predicate="works_at",
            object="Acme",
            observation_id=first.id,
        )

        original_record_event = memory._record_event

        def fail_commit(**_: object) -> None:
            raise RuntimeError("injected event-log failure")

        memory._record_event = fail_commit  # type: ignore[method-assign]
        try:
            memory.supersede_claim(
                claim.id,
                bank=bank.id,
                object="Stripe",
                observation_id=replacement.id,
            )
        except RuntimeError as error:
            assert str(error) == "injected event-log failure"
        else:  # pragma: no cover - failure is intentionally injected
            raise AssertionError("supersession should have failed")
        finally:
            memory._record_event = original_record_event  # type: ignore[method-assign]

        current = memory.recall(
            bank_id=bank.id,
            query_text="Where does Mira work?",
        )
        history = memory.history(bank=bank.id, subject="Mira", predicate="works_at")
        relation_count = memory.connection.execute(
            "SELECT COUNT(*) FROM claim_relations WHERE bank_id = ?",
            (bank.id,),
        ).fetchone()[0]

    assert [hit.event_id for hit in current] == ["event:acme"]
    assert [(item.object, item.claim.lifecycle) for item in history] == [("Acme", "active")]
    assert relation_count == 0


def test_implicit_supersession_advances_system_time_when_clock_does_not() -> None:
    frozen = datetime(2026, 8, 27, 12, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return frozen if tz is not None else frozen.replace(tzinfo=None)

    with (
        tempfile.TemporaryDirectory() as directory,
        patch("memorygraph.api.datetime", FrozenDatetime),
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:coarse-clock")
        first = memory.observe("Mira works at Acme.", bank=bank.id, source_key="event:acme")
        second = memory.observe("Mira works at Stripe.", bank=bank.id, source_key="event:stripe")
        claim = memory.assert_claim(
            bank=bank.id,
            subject="Mira",
            predicate="works_at",
            object="Acme",
            observation_id=first.id,
        )

        replacement = memory.supersede_claim(
            claim.id,
            bank=bank.id,
            object="Stripe",
            observation_id=second.id,
        )

    assert replacement.system_from > claim.system_from
