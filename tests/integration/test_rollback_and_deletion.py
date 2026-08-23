from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from memorygraph import MemoryGraph


def _dream_metadata(*, object_name: str) -> dict[str, object]:
    return {
        "memorygraph": {
            "entities": [
                {"local_id": "subject", "name": "Abrar", "type": "person"},
                {"local_id": "employer", "name": object_name, "type": "organization"},
            ],
            "claims": [
                {
                    "local_id": "claim-1",
                    "subject": "subject",
                    "predicate": "works_at",
                    "object": {"kind": "entity", "value": "employer"},
                    "confidence": 1.0,
                }
            ],
        }
    }


def _require_hook(memory: MemoryGraph, name: str) -> Any:
    hook = getattr(memory, name, None)
    if hook is None:
        pytest.xfail(f"MemoryGraph.{name}() is not implemented yet")
    return hook


def test_rollback_of_dream_assert_removes_current_claim_but_preserves_history() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:rollback-dream-assert")
        memory.define_predicate(
            "works_at",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
            subject_type="person",
            object_type="organization",
        )
        observation = memory.observe(
            "Abrar works at Acme.",
            bank=bank.id,
            source_key="dream:acme",
            observed_at="2026-08-20T12:00:00Z",
            metadata=_dream_metadata(object_name="Acme"),
        )

        report = memory.run_dream(bank=bank.id, observation_ids=(observation.id,))
        rollback = _require_hook(memory, "rollback")

        current_before = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        result = rollback(report.task.run_id, bank=bank.id)
        history_after = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
        )
        current_hits_after = memory.recall(
            bank_id=bank.id,
            query_text="Where does Abrar work?",
            max_items=10,
            max_tokens=100,
        )

    assert [item.object for item in current_before if item.claim.lifecycle == "active"] == ["Acme"]
    assert result.original_run_id == report.task.run_id
    assert result.restored_claim_ids == ()
    assert result.retracted_claim_ids
    assert current_hits_after == ()
    assert {item.object for item in history_after} == {"Acme"}
    assert any(item.claim.lifecycle == "retracted" for item in history_after)


def test_rollback_of_dream_supersede_restores_prior_current_view_and_keeps_stripe_history() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:rollback-dream-supersede")
        memory.define_predicate(
            "works_at",
            bank=bank.id,
            cardinality="one",
            volatility="volatile",
            subject_type="person",
            object_type="organization",
        )
        acme = memory.observe(
            "Abrar works at Acme.",
            bank=bank.id,
            source_key="dream:acme",
            observed_at="2026-08-20T12:00:00Z",
            metadata=_dream_metadata(object_name="Acme"),
        )
        first_report = memory.run_dream(bank=bank.id, observation_ids=(acme.id,))
        stripe = memory.observe(
            "Abrar now works at Stripe.",
            bank=bank.id,
            source_key="dream:stripe",
            observed_at="2026-08-21T09:00:00Z",
            metadata=_dream_metadata(object_name="Stripe"),
        )
        second_report = memory.run_dream(bank=bank.id, observation_ids=(stripe.id,))
        rollback = _require_hook(memory, "rollback")

        current_after_second = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
            current_versions_only=True,
        )
        stripe_claim_id = next(
            item.claim.id
            for item in current_after_second
            if item.object == "Stripe" and item.claim.lifecycle == "active"
        )
        result = rollback(second_report.task.run_id, bank=bank.id)
        current_hits_after = memory.recall(
            bank_id=bank.id,
            query_text="Where does Abrar work?",
            max_items=10,
            max_tokens=100,
        )
        history_after = memory.history(
            bank=bank.id,
            subject="Abrar",
            predicate="works_at",
        )
        stripe_after = memory.explain(stripe_claim_id, bank=bank.id)

    assert result.original_run_id == second_report.task.run_id
    assert result.rollback_run_id != second_report.task.run_id
    assert result.restored_claim_ids
    assert current_hits_after
    assert [hit.event_id for hit in current_hits_after] == ["dream:acme"]
    assert any(item.object == "Acme" and item.claim.lifecycle == "active" for item in history_after)
    assert any(item.object == "Stripe" for item in history_after)
    assert stripe_after.object == "Stripe"
    assert first_report.task.run_id != second_report.task.run_id


def test_delete_observation_retracts_sole_supported_claim_and_keeps_independent_claim() -> None:
    with (
        tempfile.TemporaryDirectory() as directory,
        MemoryGraph.open(Path(directory) / "memory.db") as memory,
    ):
        bank = memory.create_bank("project:deletion-recompute")
        memory.define_predicate(
            "deploy_command",
            bank=bank.id,
            cardinality="one",
            volatility="durable",
        )
        memory.define_predicate(
            "webhook_provider",
            bank=bank.id,
            cardinality="one",
            volatility="durable",
        )
        deleted_source = memory.observe(
            "Deploy Delta with ./scripts/deploy.sh --prod. Stripe handles webhooks.",
            bank=bank.id,
            source_key="ops:deploy-and-webhooks",
            observed_at="2026-08-21T08:00:00Z",
        )
        confirming_source = memory.observe(
            "Stripe remains the webhook provider for Delta.",
            bank=bank.id,
            source_key="ops:webhooks-confirmation",
            observed_at="2026-08-21T08:05:00Z",
        )
        deploy_claim = memory.assert_claim(
            bank=bank.id,
            subject="Delta",
            predicate="deploy_command",
            object="./scripts/deploy.sh --prod",
            object_kind="string",
            observation_id=deleted_source.id,
            excerpt="./scripts/deploy.sh --prod",
        )
        webhook_claim = memory.assert_claim(
            bank=bank.id,
            subject="Delta",
            predicate="webhook_provider",
            object="Stripe",
            object_kind="string",
            observation_id=deleted_source.id,
            excerpt="Stripe",
        )
        memory.confirm_claim(
            webhook_claim.id,
            bank=bank.id,
            observation_id=confirming_source.id,
            excerpt="Stripe",
        )
        delete_observation = _require_hook(memory, "delete_observation")

        result = delete_observation(deleted_source.id, bank=bank.id)
        deleted_record = memory.observations.get(bank.id, deleted_source.id)
        deploy_history = memory.history(
            bank=bank.id,
            subject="Delta",
            predicate="deploy_command",
        )
        deploy_current = memory.history(
            bank=bank.id,
            subject="Delta",
            predicate="deploy_command",
            current_versions_only=True,
        )
        webhook_current = memory.history(
            bank=bank.id,
            subject="Delta",
            predicate="webhook_provider",
            current_versions_only=True,
        )
        webhook_after = memory.explain(webhook_claim.id, bank=bank.id)
        deleted_evidence = memory.evidence.list_for_observation(bank.id, deleted_source.id)
        deploy_retracted_ids = {
            item.claim.id for item in deploy_history if item.claim.lifecycle == "retracted"
        }

    assert result.observation_id == deleted_source.id
    assert set(result.affected_claim_ids) >= {deploy_claim.id, webhook_claim.id}
    assert set(result.retracted_claim_ids) == deploy_retracted_ids
    assert deleted_record is not None
    assert deleted_record.ingestion_state == "deleted"
    assert deleted_record.content.startswith("[deleted:")
    assert "./scripts/deploy.sh --prod" not in deleted_record.content
    assert "Stripe handles webhooks" not in deleted_record.content
    assert deleted_record.chunks
    assert deleted_record.chunks[0].content.startswith("[deleted:")
    assert deleted_evidence == ()
    assert any(item.object == "./scripts/deploy.sh --prod" for item in deploy_history)
    assert not any(item.claim.lifecycle in {"active", "contested"} for item in deploy_current)
    assert any(
        item.object == "Stripe" and item.claim.lifecycle == "active" for item in webhook_current
    )
    assert len(webhook_after.evidence) == 1
    assert webhook_after.evidence[0].observation_id == confirming_source.id
