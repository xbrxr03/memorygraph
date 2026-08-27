"""MemoryGraph CLI entry point."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePath
from typing import Annotated

import typer

from memorygraph import MemoryGraph, __version__
from memorygraph.application import DurableWorkerService
from memorygraph.dogfood import (
    LiveSessionEvent,
    LiveSessionLedger,
    bootstrap_project_memory,
    evaluate_live_sessions,
)
from memorygraph.integrations.codex import CodexSessionAdapter, probe_codex_mcp, report_to_dict
from memorygraph.providers import OpenAICompatibleConfig, OpenAICompatibleDreamProvider
from memorygraph.storage.database import MIGRATION_NAME_PATTERN, MigrationRunner

app = typer.Typer(
    name="memorygraph",
    help="Evidence-backed temporal belief revision for AI agents.",
    no_args_is_help=True,
)
bank_app = typer.Typer(help="Manage isolated memory banks.")
predicate_app = typer.Typer(help="Manage typed graph predicates.")
claim_app = typer.Typer(help="Create and inspect evidence-backed claims.")
dream_app = typer.Typer(help="Run and inspect durable dream cycles.")
observation_app = typer.Typer(help="Manage source observation lifecycle.")
dogfood_app = typer.Typer(help="Run and inspect real-project dogfood experiments.")
app.add_typer(bank_app, name="bank")
app.add_typer(predicate_app, name="predicate")
app.add_typer(claim_app, name="claim")
app.add_typer(dream_app, name="dream")
app.add_typer(observation_app, name="observation")
app.add_typer(dogfood_app, name="dogfood")

DEFAULT_DATABASE = Path(".memorygraph/memory.db")
DEFAULT_PROJECT_DIRECTORY = Path.cwd()
SUPPORTED_PYTHON = ((3, 11), (3, 13))


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    action: str | None = None


def main() -> None:
    """Console-script entry point."""

    app()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"memorygraph {__version__}")
        raise typer.Exit


@app.callback()
def app_main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run MemoryGraph commands."""


@app.command("init")
def initialize(
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Initialize or migrate a local MemoryGraph database."""

    resolved = database.expanduser().resolve()
    existed_before = resolved.exists()
    with MemoryGraph.open(database):
        pass
    if existed_before:
        typer.echo(f"MemoryGraph ready at {resolved}")
    else:
        typer.echo(f"Initialized MemoryGraph at {resolved}")


@app.command("doctor")
def doctor(
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path to inspect."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Run environment and database diagnostics."""

    resolved = database.expanduser().resolve()
    checks = _doctor_checks(resolved)
    failing = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]

    typer.echo(f"memorygraph {__version__}")
    typer.echo(f"database: {resolved}")
    for check in checks:
        typer.echo(f"[{check.status}] {check.name}: {check.detail}")
        if check.action is not None:
            typer.echo(f"  action: {check.action}")

    typer.echo(
        f"summary: {len(checks) - len(failing) - len(warnings)} ok, "
        f"{len(warnings)} warnings, {len(failing)} failures"
    )
    if failing:
        raise typer.Exit(code=1)


@bank_app.command("create")
def create_bank(
    slug: Annotated[str, typer.Argument(help="Stable bank slug, e.g. project:my-app.")],
    name: Annotated[str | None, typer.Option("--name", help="Human-readable name.")] = None,
    mission: Annotated[
        str | None,
        typer.Option("--mission", help="Optional future extraction guidance."),
    ] = None,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Create an isolated memory bank; safe to rerun."""

    with MemoryGraph.open(database) as memory:
        bank = memory.create_bank(slug, name=name, mission=mission)
    typer.echo(f"{bank.slug}\t{bank.id}")


@app.command("observe")
def observe(
    content: Annotated[str, typer.Argument(help="Exact source observation text.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    source_key: Annotated[
        str,
        typer.Option("--source-key", help="Stable external source identifier."),
    ],
    kind: Annotated[
        str,
        typer.Option("--kind", help="Observation kind."),
    ] = "explicit_assertion",
    trust_class: Annotated[
        str,
        typer.Option("--trust", help="Source trust class used by dream policy."),
    ] = "owner_explicit",
    metadata_json: Annotated[
        str | None,
        typer.Option("--metadata-json", help="JSON metadata, including extraction candidates."),
    ] = None,
    metadata_file: Annotated[
        Path | None,
        typer.Option("--metadata-file", help="Path to a JSON metadata document."),
    ] = None,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Record an immutable source observation."""

    with MemoryGraph.open(database) as memory:
        observation = memory.observe(
            content,
            bank=bank,
            source_key=source_key,
            kind=kind,
            trust_class=trust_class,
            metadata=_load_metadata(metadata_json, metadata_file),
        )
    typer.echo(observation.id)


@app.command("record-attempt")
def record_attempt(
    strategy: Annotated[str, typer.Argument(help="Exact strategy that was attempted.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    source_key: Annotated[
        str,
        typer.Option("--source-key", help="Stable external attempt identifier."),
    ],
    task_key: Annotated[
        str,
        typer.Option("--task", help="Stable task or problem family."),
    ],
    outcome: Annotated[
        str,
        typer.Option("--outcome", help="success, failure, partial, or unknown."),
    ],
    failure: Annotated[
        str | None,
        typer.Option("--failure", help="Observed failure, not a generalized rule."),
    ] = None,
    applicability_json: Annotated[
        str | None,
        typer.Option("--applicability-json", help="JSON object bounding reuse."),
    ] = None,
    environment_json: Annotated[
        str | None,
        typer.Option("--environment-json", help="JSON object describing the environment."),
    ] = None,
    started_at: Annotated[
        str | None,
        typer.Option("--started-at", help="Optional ISO-8601 start time."),
    ] = None,
    completed_at: Annotated[
        str | None,
        typer.Option("--completed-at", help="Optional ISO-8601 completion time."),
    ] = None,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Record a reusable success or failure with explicit applicability bounds."""

    with MemoryGraph.open(database) as memory:
        episode = memory.record_attempt(
            bank=bank,
            source_key=source_key,
            task_key=task_key,
            strategy=strategy,
            outcome=outcome,
            failure=failure,
            applicability=_load_json_object(applicability_json, "applicability"),
            environment=_load_json_object(environment_json, "environment"),
            started_at=started_at,
            completed_at=completed_at,
        )
    typer.echo(episode.id)


@dream_app.command("run")
def run_dream(
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    mode: Annotated[
        str,
        typer.Option("--mode", help="apply, dry_run, or review_only."),
    ] = "apply",
    observation_ids: Annotated[
        list[str] | None,
        typer.Option("--observation", help="Limit the run to a pending observation UUID."),
    ] = None,
    provider_model: Annotated[
        str | None,
        typer.Option("--provider-model", help="OpenAI-compatible Responses model name."),
    ] = None,
    provider_endpoint: Annotated[
        str,
        typer.Option("--provider-endpoint", help="OpenAI-compatible Responses endpoint."),
    ] = "https://api.openai.com/v1/responses",
    api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="Environment variable containing the provider key."),
    ] = "OPENAI_API_KEY",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Execute one bounded dream cycle using the deterministic metadata provider."""

    with MemoryGraph.open(database) as memory:
        report = memory.run_dream(
            bank=bank,
            provider=_dream_provider(provider_model, provider_endpoint, api_key_env),
            mode=mode,
            observation_ids=tuple(observation_ids or ()),
        )
    typer.echo(
        json.dumps(
            {
                "run_id": report.task.run_id,
                "task_id": report.task.task_id,
                "status": report.status.value,
                "failure_stage": report.failure_stage,
                "error": report.error_message,
                "metrics": {
                    "selected_observations": report.metrics.selected_observations,
                    "extracted_entities": report.metrics.extracted_entities,
                    "extracted_claims": report.metrics.extracted_claims,
                    "proposals_total": report.metrics.proposals_total,
                    "auto_eligible": report.metrics.auto_eligible,
                    "review_required": report.metrics.review_required,
                    "rejected": report.metrics.rejected,
                    "stale": report.metrics.stale,
                    "committed": report.metrics.committed,
                    "replayed": report.metrics.replayed,
                    "provider_calls": report.metrics.provider_calls,
                },
            },
            sort_keys=True,
        )
    )


@dream_app.command("status")
def dream_status(
    run_id: Annotated[str, typer.Argument(help="Dream run UUID.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Show durable run state, metrics, and proposal dispositions."""

    with MemoryGraph.open(database) as memory:
        bank_record = memory.get_bank(bank)
        run = memory.dream_runs.get(bank_record.id, run_id)
        if run is None:
            raise typer.BadParameter(f"Unknown dream run {run_id!r}")
        proposals = memory.dream_proposals.list_for_run(bank_record.id, run_id)
    typer.echo(
        json.dumps(
            {
                "run_id": run.id,
                "state": run.state,
                "mode": run.mode,
                "input_watermark": run.input_watermark,
                "usage": run.usage,
                "error": run.error,
                "proposals": [
                    {
                        "id": proposal.id,
                        "type": proposal.proposal_type,
                        "disposition": proposal.disposition,
                    }
                    for proposal in proposals
                ],
            },
            sort_keys=True,
        )
    )


@dream_app.command("reviews")
def dream_reviews(
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum review items returned.")] = 100,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """List proposals waiting for a human decision."""

    with MemoryGraph.open(database) as memory:
        reviews = memory.pending_reviews(bank=bank, limit=limit)
    for review in reviews:
        typer.echo(
            json.dumps(
                {
                    "review_id": review.id,
                    "proposal_id": review.proposal_id,
                    "reason": review.reason,
                    "state": review.state,
                    "created_at": review.created_at,
                },
                sort_keys=True,
            )
        )


@dream_app.command("rollback")
def rollback_dream(
    run_id: Annotated[str, typer.Argument(help="Committed dream run UUID.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Apply a safe compensating rollback while preserving history."""

    with MemoryGraph.open(database) as memory:
        result = memory.rollback(run_id, bank=bank)
    typer.echo(
        json.dumps(
            {
                "original_run_id": result.original_run_id,
                "rollback_run_id": result.rollback_run_id,
                "retracted_claim_ids": list(result.retracted_claim_ids),
                "restored_claim_ids": list(result.restored_claim_ids),
                "removed_evidence_ids": list(result.removed_evidence_ids),
            },
            sort_keys=True,
        )
    )


@observation_app.command("delete")
def delete_observation(
    observation_id: Annotated[str, typer.Argument(help="Observation UUID to privacy-delete.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Erase source content and recompute directly dependent beliefs."""

    with MemoryGraph.open(database) as memory:
        result = memory.delete_observation(observation_id, bank=bank)
    typer.echo(
        json.dumps(
            {
                "observation_id": result.observation_id,
                "affected_claim_ids": list(result.affected_claim_ids),
                "retracted_claim_ids": list(result.retracted_claim_ids),
                "stale_artifact_ids": list(result.stale_artifact_ids),
                "residue_issues": list(result.residue_issues),
            },
            sort_keys=True,
        )
    )


@predicate_app.command("define")
def define_predicate(
    name: Annotated[str, typer.Argument(help="Predicate name, e.g. works_at.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    cardinality: Annotated[
        str,
        typer.Option("--cardinality", help="one, many, or event."),
    ] = "many",
    volatility: Annotated[
        str,
        typer.Option("--volatility", help="immutable, durable, volatile, or ephemeral."),
    ] = "durable",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Define the semantics for a class of graph edge."""

    with MemoryGraph.open(database) as memory:
        predicate = memory.define_predicate(
            name,
            bank=bank,
            cardinality=cardinality,
            volatility=volatility,
        )
    typer.echo(predicate.name)


@claim_app.command("assert")
def assert_claim(
    subject: Annotated[str, typer.Argument(help="Subject entity name.")],
    predicate: Annotated[str, typer.Argument(help="Predicate name.")],
    object_value: Annotated[str, typer.Argument(help="Entity name or literal value.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    observation_id: Annotated[
        str,
        typer.Option("--observation", help="Grounding observation UUID."),
    ],
    object_kind: Annotated[
        str,
        typer.Option("--object-kind", help="entity, string, number, boolean, datetime, or json."),
    ] = "entity",
    valid_from: Annotated[
        str | None,
        typer.Option("--valid-from", help="Optional ISO-8601 valid-time start."),
    ] = None,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Commit one claim grounded in an exact observation."""

    parsed_object = _parse_cli_object(object_value, object_kind)
    with MemoryGraph.open(database) as memory:
        claim = memory.assert_claim(
            bank=bank,
            subject=subject,
            predicate=predicate,
            object=parsed_object,
            object_kind=object_kind,
            observation_id=observation_id,
            valid_from=valid_from,
        )
    typer.echo(claim.id)


@claim_app.command("supersede")
def supersede_claim(
    claim_id: Annotated[str, typer.Argument(help="Current claim UUID.")],
    object_value: Annotated[str, typer.Argument(help="Replacement entity name or literal.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    observation_id: Annotated[
        str,
        typer.Option("--observation", help="Grounding observation UUID."),
    ],
    object_kind: Annotated[
        str | None,
        typer.Option("--object-kind", help="Defaults to the current claim's object kind."),
    ] = None,
    valid_from: Annotated[
        str | None,
        typer.Option("--valid-from", help="Optional ISO-8601 valid-time start."),
    ] = None,
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Why the prior claim is being replaced."),
    ] = "explicit correction",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Atomically replace a current claim while retaining its history."""

    with MemoryGraph.open(database) as memory:
        parsed_object = _parse_cli_object(
            object_value,
            _claim_object_kind(memory, bank, claim_id, object_kind),
        )
        claim = memory.supersede_claim(
            claim_id,
            bank=bank,
            object=parsed_object,
            object_kind=object_kind,
            observation_id=observation_id,
            valid_from=valid_from,
            rationale=rationale,
        )
    typer.echo(claim.id)


@claim_app.command("confirm")
def confirm_claim(
    claim_id: Annotated[str, typer.Argument(help="Current claim UUID.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    observation_id: Annotated[
        str,
        typer.Option("--observation", help="Additional supporting observation UUID."),
    ],
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Add supporting evidence without creating a duplicate belief."""

    with MemoryGraph.open(database) as memory:
        claim = memory.confirm_claim(
            claim_id,
            bank=bank,
            observation_id=observation_id,
        )
    typer.echo(claim.id)


@claim_app.command("contradict")
def contradict_claim(
    claim_id: Annotated[str, typer.Argument(help="Current claim UUID.")],
    object_value: Annotated[str, typer.Argument(help="Conflicting entity name or literal.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    observation_id: Annotated[
        str,
        typer.Option("--observation", help="Grounding observation UUID."),
    ],
    object_kind: Annotated[
        str | None,
        typer.Option("--object-kind", help="Defaults to the current claim's object kind."),
    ] = None,
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Why the sources remain unresolved."),
    ] = "equal-authority evidence conflicts",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Preserve both sides of an unresolved contradiction."""

    with MemoryGraph.open(database) as memory:
        parsed_object = _parse_cli_object(
            object_value,
            _claim_object_kind(memory, bank, claim_id, object_kind),
        )
        claim = memory.contradict_claim(
            claim_id,
            bank=bank,
            object=parsed_object,
            object_kind=object_kind,
            observation_id=observation_id,
            rationale=rationale,
        )
    typer.echo(claim.id)


@claim_app.command("history")
def claim_history(
    subject: Annotated[str, typer.Argument(help="Subject entity name.")],
    predicate: Annotated[str, typer.Argument(help="Predicate name.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    current_only: Annotated[
        bool,
        typer.Option("--current-only", help="Show only versions known now."),
    ] = False,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Print the bitemporal history for a graph slot as JSON Lines."""

    with MemoryGraph.open(database) as memory:
        items = memory.history(
            bank=bank,
            subject=subject,
            predicate=predicate,
            current_versions_only=current_only,
        )
    for item in items:
        typer.echo(
            json.dumps(
                {
                    "claim_id": item.claim.id,
                    "subject": item.subject,
                    "predicate": item.claim.predicate,
                    "object": item.object,
                    "lifecycle": item.claim.lifecycle,
                    "valid_from": item.claim.valid_from,
                    "valid_to": item.claim.valid_to,
                    "system_from": item.claim.system_from,
                    "system_to": item.claim.system_to,
                    "evidence_ids": [evidence.id for evidence in item.evidence],
                },
                sort_keys=True,
            )
        )


@claim_app.command("retract")
def retract_claim(
    claim_id: Annotated[str, typer.Argument(help="Current claim UUID.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    observation_id: Annotated[
        str | None,
        typer.Option("--observation", help="Optional retraction evidence observation UUID."),
    ] = None,
    effective_at: Annotated[
        str | None,
        typer.Option("--effective-at", help="Optional ISO-8601 end of world validity."),
    ] = None,
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the claim is being retracted."),
    ] = "explicit retraction",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Retract a current claim while preserving its complete audit trail."""

    with MemoryGraph.open(database) as memory:
        claim = memory.retract_claim(
            claim_id,
            bank=bank,
            observation_id=observation_id,
            effective_at=effective_at,
            reason=reason,
        )
    typer.echo(claim.id)


@claim_app.command("explain")
def explain_claim(
    claim_id: Annotated[str, typer.Argument(help="Claim UUID.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Show evidence and relations behind one claim."""

    with MemoryGraph.open(database) as memory:
        explanation = memory.explain(claim_id, bank=bank)
    typer.echo(
        json.dumps(
            {
                "claim_id": explanation.claim.id,
                "subject": explanation.subject,
                "predicate": explanation.claim.predicate,
                "object": explanation.object,
                "lifecycle": explanation.claim.lifecycle,
                "evidence": [
                    {
                        "id": evidence.id,
                        "observation_id": evidence.observation_id,
                        "excerpt": evidence.excerpt,
                        "stance": evidence.stance,
                    }
                    for evidence in explanation.evidence
                ],
                "relations": [
                    {
                        "kind": relation.relation,
                        "from": relation.from_claim_id,
                        "to": relation.to_claim_id,
                        "rationale": relation.rationale,
                    }
                    for relation in explanation.relations
                ],
                "warnings": list(explanation.warnings),
            },
            sort_keys=True,
        )
    )


@app.command("recall")
def recall(
    query: Annotated[str, typer.Argument(help="Natural-language recall query.")],
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Optional known/valid time in ISO-8601 form."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum observations returned.")] = 10,
    max_tokens: Annotated[
        int,
        typer.Option("--max-tokens", help="Maximum approximate words returned."),
    ] = 2_000,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Recall evidence selected through current temporal claims."""

    with MemoryGraph.open(database) as memory:
        hits = memory.recall(
            bank_id=bank,
            query_text=query,
            as_of=as_of,
            max_items=limit,
            max_tokens=max_tokens,
        )
    for hit in hits:
        typer.echo(
            json.dumps(
                {
                    "event_id": hit.event_id,
                    "at": hit.at,
                    "content": hit.content,
                    "bank_id": hit.bank_id,
                    "score": hit.score,
                    "metadata": hit.metadata,
                },
                sort_keys=True,
            )
        )


@dream_app.command("queue")
def queue_dream(
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    mode: Annotated[
        str,
        typer.Option("--mode", help="apply, dry_run, or review_only."),
    ] = "apply",
    observation_ids: Annotated[
        list[str] | None,
        typer.Option("--observation", help="Limit the run to a pending observation UUID."),
    ] = None,
    provider_model: Annotated[
        str | None,
        typer.Option("--provider-model", help="OpenAI-compatible Responses model name."),
    ] = None,
    provider_endpoint: Annotated[
        str,
        typer.Option("--provider-endpoint", help="OpenAI-compatible Responses endpoint."),
    ] = "https://api.openai.com/v1/responses",
    api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="Environment variable containing the provider key."),
    ] = "OPENAI_API_KEY",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Queue a Dream cycle for crash-recoverable worker execution."""

    with MemoryGraph.open(database) as memory:
        run, task = memory.queue_dream(
            bank=bank,
            provider=_dream_provider(provider_model, provider_endpoint, api_key_env),
            mode=mode,
            observation_ids=tuple(observation_ids or ()),
        )
    typer.echo(
        json.dumps(
            {"run_id": run.id, "task_id": task.id, "state": run.state},
            sort_keys=True,
        )
    )


@dream_app.command("worker")
def dream_worker(
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Poll instead of processing at most one queued run."),
    ] = False,
    stop_when_idle: Annotated[
        bool,
        typer.Option("--stop-when-idle", help="Exit watch mode after the first idle cycle."),
    ] = False,
    max_idle_cycles: Annotated[
        int | None,
        typer.Option("--max-idle-cycles", help="Optional watch-mode idle bound."),
    ] = None,
    provider_model: Annotated[
        str | None,
        typer.Option("--provider-model", help="OpenAI-compatible Responses model name."),
    ] = None,
    provider_endpoint: Annotated[
        str,
        typer.Option("--provider-endpoint", help="OpenAI-compatible Responses endpoint."),
    ] = "https://api.openai.com/v1/responses",
    api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="Environment variable containing the provider key."),
    ] = "OPENAI_API_KEY",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Process queued Dream cycles with leases, retries, heartbeat, and recovery."""

    provider = _dream_provider(provider_model, provider_endpoint, api_key_env)
    with MemoryGraph.open(database) as memory:
        service = DurableWorkerService(memory)
        if watch:
            poll_result = service.poll(
                bank=bank,
                provider=provider,
                stop_when_idle=stop_when_idle,
                max_idle_cycles=max_idle_cycles,
            )
            results = poll_result.processed
            idle_cycles = poll_result.idle_cycles
        else:
            result = service.process_next(bank=bank, provider=provider)
            results = () if result is None else (result,)
            idle_cycles = 1 if result is None else 0
    typer.echo(
        json.dumps(
            {
                "processed": [
                    {
                        "run_id": result.run_id,
                        "task_id": result.task_id,
                        "state": result.state,
                        "attempt_count": result.attempt_count,
                        "retried": result.retried,
                    }
                    for result in results
                ],
                "idle_cycles": idle_cycles,
            },
            sort_keys=True,
        )
    )


@app.command("project-obsidian")
def project_obsidian(
    bank: Annotated[str, typer.Option("--bank", help="Bank slug or UUID.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Directory for generated Markdown."),
    ] = Path(".memorygraph/obsidian"),
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Regenerate the Obsidian-compatible review projection."""

    with MemoryGraph.open(database) as memory:
        result = memory.project_obsidian(bank=bank, output_directory=output)
    typer.echo(
        json.dumps(
            {
                "bank": result.bank_slug,
                "output": str(result.output_directory),
                "source_watermark": result.source_watermark,
                "files_written": len(result.files_written),
                "stale_files_removed": len(result.stale_files_removed),
            },
            sort_keys=True,
        )
    )


@app.command("ingest-codex")
def ingest_codex(
    jsonl: Annotated[Path, typer.Argument(help="User-approved Codex JSONL export.")],
    allow_unapproved: Annotated[
        bool,
        typer.Option("--allow-unapproved", help="Import records without an approval flag."),
    ] = False,
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Import explicit Codex session records without scraping private app state."""

    with MemoryGraph.open(database) as memory:
        report = CodexSessionAdapter(memory).ingest_jsonl(
            jsonl,
            require_approval=not allow_unapproved,
        )
    typer.echo(
        json.dumps(
            {
                "imported": report.imported_count,
                "skipped": report.skipped_count,
                "observation_ids": list(report.imported_observation_ids),
            },
            sort_keys=True,
        )
    )


@app.command("install-codex")
def install_codex(
    project_directory: Annotated[
        Path,
        typer.Option("--project", help="Trusted project directory to configure."),
    ] = DEFAULT_PROJECT_DIRECTORY,
    database_argument: Annotated[
        str,
        typer.Option("--database-argument", help="Database argument used by the MCP server."),
    ] = ".memorygraph/memory.db",
) -> None:
    """Create or repair project-scoped Codex MCP configuration."""

    project_path = project_directory.expanduser().resolve()
    config_path, changed = _install_codex_project_config(
        project_path,
        database_argument=database_argument,
    )
    if not changed:
        typer.echo(f"MemoryGraph MCP already configured in {config_path}")
        return
    typer.echo(f"Configured MemoryGraph MCP in {config_path}")


@app.command("onboard-codex")
def onboard_codex(
    project_directory: Annotated[
        Path,
        typer.Option("--project", help="Trusted project directory to configure."),
    ] = DEFAULT_PROJECT_DIRECTORY,
    bank: Annotated[
        str | None,
        typer.Option("--bank", help="Project bank slug; defaults from the directory name."),
    ] = None,
    database: Annotated[
        Path | None,
        typer.Option(
            "--database",
            "-d",
            help="Database path; relative paths resolve inside the project.",
        ),
    ] = None,
) -> None:
    """Initialize and live-verify MemoryGraph for Codex in one command."""

    project_path = project_directory.expanduser().resolve()
    if not project_path.is_dir():
        typer.echo(f"[FAIL] project: directory does not exist: {project_path}", err=True)
        typer.echo(
            "  action: create the directory or pass --project with a trusted repository",
            err=True,
        )
        raise typer.Exit(code=1)

    database_path = _onboarding_database_path(project_path, database)
    bank_slug = bank or _default_project_bank(project_path)
    typer.echo(f"MemoryGraph Codex onboarding for {project_path}")

    try:
        existed_before = database_path.exists()
        with MemoryGraph.open(database_path) as memory:
            bank_record = memory.create_bank(bank_slug, name=project_path.name)
    except Exception as error:  # noqa: BLE001 - CLI boundary must produce recovery guidance
        typer.echo(f"[FAIL] database/bank: {error}", err=True)
        typer.echo(f"  action: check write access to {database_path.parent}", err=True)
        raise typer.Exit(code=1) from error
    database_status = "opened" if existed_before else "initialized"
    typer.echo(f"[OK] database: {database_status} {database_path}")
    typer.echo(f"[OK] bank: selected {bank_record.slug} ({bank_record.id})")

    database_argument = _database_argument_for_project(project_path, database_path)
    try:
        config_path, changed = _install_codex_project_config(
            project_path,
            database_argument=database_argument,
        )
    except OSError as error:
        typer.echo(f"[FAIL] Codex config: {error}", err=True)
        typer.echo(f"  action: check write access to {project_path / '.codex'}", err=True)
        raise typer.Exit(code=1) from error
    config_status = "configured" if changed else "already configured"
    typer.echo(f"[OK] Codex config: {config_status} at {config_path}")

    try:
        report = probe_codex_mcp(
            project_path,
            database_mode="project",
            launch_sources=("config",),
        )
    except Exception as error:  # noqa: BLE001 - convert probe crashes into safe diagnostics
        typer.echo(f"[FAIL] live MCP probe: {error}", err=True)
        typer.echo(
            f"  action: run `memorygraph probe-codex --project {project_path} "
            "--project-database --configured-only` after fixing the reported environment issue",
            err=True,
        )
        typer.echo(
            "  safe state: the database and optional MCP config remain local; "
            "Codex is configured with required=false",
            err=True,
        )
        raise typer.Exit(code=1) from error
    if not report.ok:
        typer.echo("[FAIL] live MCP probe: configured server did not pass", err=True)
        launch_issues = tuple(issue for launch in report.launches for issue in launch.issues)
        for issue in (*report.configuration.issues, *launch_issues):
            typer.echo(f"  {issue.code}: {issue.message}", err=True)
            if issue.action:
                typer.echo(f"  action: {issue.action}", err=True)
        typer.echo(
            "  safe state: the database and optional MCP config remain local; "
            "Codex is configured with required=false",
            err=True,
        )
        raise typer.Exit(code=1)

    launch = report.launches[0]
    typer.echo(
        "[OK] live MCP probe: initialize, five tools, record, recall, and forget "
        f"passed ({launch.protocol_version})"
    )
    typer.echo(f"READY: bank={bank_record.slug} database={database_path}")
    typer.echo(
        "Next: open a fresh Codex task in this project; "
        "MemoryGraph fails open if unavailable."
    )


def _install_codex_project_config(
    project_path: Path,
    *,
    database_argument: str,
) -> tuple[Path, bool]:
    config_path = project_path / ".codex" / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    block = (
        "[mcp_servers.memorygraph]\n"
        f"command = {json.dumps(sys.executable)}\n"
        f"args = [\"-m\", \"memorygraph.mcp\", {json.dumps(database_argument)}]\n"
        f"cwd = {json.dumps(str(project_path))}\n"
        "required = false\n"
        "startup_timeout_sec = 15\n"
        "tool_timeout_sec = 60\n"
        'default_tools_approval_mode = "writes"\n'
    )
    updated = _upsert_toml_table(existing, "mcp_servers.memorygraph", block)
    if updated == existing:
        return config_path, False
    config_path.write_text(updated, encoding="utf-8")
    return config_path, True


@app.command("probe-codex")
def probe_codex(
    project_directory: Annotated[
        Path,
        typer.Option("--project", help="Trusted project directory to probe."),
    ] = DEFAULT_PROJECT_DIRECTORY,
    project_database: Annotated[
        bool,
        typer.Option(
            "--project-database",
            help="Exercise the configured project DB instead of disposable probe DBs.",
        ),
    ] = False,
    configured_only: Annotated[
        bool,
        typer.Option("--configured-only", help="Skip the local-module fallback probe."),
    ] = False,
) -> None:
    """Validate config and exercise a real Codex MCP subprocess lifecycle."""

    report = probe_codex_mcp(
        project_directory,
        database_mode="project" if project_database else "temporary",
        launch_sources=("config",) if configured_only else ("config", "local_module"),
    )
    typer.echo(json.dumps(report_to_dict(report), sort_keys=True))
    if not report.ok:
        raise typer.Exit(code=1)


@dogfood_app.command("bootstrap")
def dogfood_bootstrap(
    bank: Annotated[
        str,
        typer.Option("--bank", help="Project bank slug."),
    ] = "project:memorygraph",
    workspace: Annotated[
        str,
        typer.Option("--workspace", help="Workspace label for isolation."),
    ] = "agent-memory-research",
    database: Annotated[
        Path,
        typer.Option("--database", "-d", help="SQLite database path."),
    ] = DEFAULT_DATABASE,
) -> None:
    """Seed the explicit operating contract used by self-dogfood tasks."""

    with MemoryGraph.open(database) as memory:
        report = bootstrap_project_memory(
            memory,
            bank_slug=bank,
            workspace=workspace,
        )
    typer.echo(
        json.dumps(
            {
                "bank": report.bank_slug,
                "bank_id": report.bank_id,
                "observations": len(report.observation_ids),
                "claims": len(report.claim_ids),
                "procedural_episode_id": report.procedural_episode_id,
            },
            sort_keys=True,
        )
    )


@dogfood_app.command("capture")
def dogfood_capture(
    event_json: Annotated[
        str,
        typer.Argument(help="Explicitly approved live-session event JSON object."),
    ],
    ledger: Annotated[
        Path,
        typer.Option("--ledger", help="Append-only live session event ledger."),
    ] = Path(".memorygraph/dogfood/live-sessions.jsonl"),
) -> None:
    """Append one approved coding-session metric event without scraping host state."""

    try:
        payload = json.loads(event_json)
        if not isinstance(payload, dict):
            raise ValueError("event JSON must be an object")
        event = LiveSessionEvent.from_mapping(payload)
        LiveSessionLedger(ledger).append(event)
    except (json.JSONDecodeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps({"captured": True, "ledger": str(ledger)}, sort_keys=True))


@dogfood_app.command("evaluate-live")
def dogfood_evaluate_live(
    ledger: Annotated[
        Path,
        typer.Option("--ledger", help="Append-only live session event ledger."),
    ] = Path(".memorygraph/dogfood/live-sessions.jsonl"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional JSON report output path."),
    ] = None,
) -> None:
    """Score approved live coding sessions using the Beta quality metrics."""

    try:
        report = evaluate_live_sessions(LiveSessionLedger(ledger).read())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(report, sort_keys=True))


def _dream_provider(model: str | None, endpoint: str, api_key_env: str):
    if model is None:
        return None
    return OpenAICompatibleDreamProvider(
        OpenAICompatibleConfig(
            model=model,
            endpoint=endpoint,
            api_key_env=api_key_env,
        )
    )


def _upsert_toml_table(existing: str, table_name: str, block: str) -> str:
    normalized_block = block.rstrip() + "\n"
    table_pattern = re.compile(
        rf"(?ms)^\[{re.escape(table_name)}\][ \t]*\n.*?(?=^\[|\Z)"
    )
    match = table_pattern.search(existing)
    if match is not None:
        before = existing[: match.start()].rstrip()
        after = existing[match.end() :].lstrip("\n")
        pieces = [piece for piece in (before, normalized_block.rstrip(), after.rstrip()) if piece]
        return "\n\n".join(pieces) + "\n"
    prefix = existing.rstrip()
    return f"{prefix}\n\n{normalized_block}" if prefix else normalized_block


def _onboarding_database_path(project_path: Path, database: Path | None) -> Path:
    requested = database or Path(".memorygraph/memory.db")
    expanded = requested.expanduser()
    if not expanded.is_absolute():
        expanded = project_path / expanded
    return expanded.resolve()


def _database_argument_for_project(
    project_path: PurePath,
    database_path: PurePath,
) -> str:
    try:
        return database_path.relative_to(project_path).as_posix()
    except ValueError:
        return str(database_path)


def _default_project_bank(project_path: Path) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", project_path.name.lower()).strip("-.")
    return f"project:{normalized or 'default'}"


def _parse_cli_object(value: str, object_kind: str) -> object:
    if object_kind in {"number", "boolean", "json"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise typer.BadParameter(f"Invalid {object_kind} JSON value: {value!r}") from error
    return value


def _load_metadata(inline: str | None, path: Path | None) -> dict[str, object] | None:
    if inline is not None and path is not None:
        raise typer.BadParameter("Use only one of --metadata-json or --metadata-file.")
    if inline is None and path is None:
        return None
    try:
        if inline is not None:
            raw = inline
        else:
            assert path is not None
            raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (json.JSONDecodeError, OSError) as error:
        raise typer.BadParameter(f"Could not load observation metadata: {error}") from error
    if not isinstance(value, dict):
        raise typer.BadParameter("Observation metadata must be a JSON object.")
    return value


def _load_json_object(inline: str | None, field: str) -> dict[str, object]:
    if inline is None:
        return {}
    try:
        value = json.loads(inline)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"Invalid {field} JSON: {error}") from error
    if not isinstance(value, dict):
        raise typer.BadParameter(f"{field} must be a JSON object.")
    return value


def _claim_object_kind(
    memory: MemoryGraph,
    bank: str,
    claim_id: str,
    requested_kind: str | None,
) -> str:
    if requested_kind is not None:
        return requested_kind
    bank_record = memory.get_bank(bank)
    claim = memory.claims.get(bank_record.id, claim_id)
    return "entity" if claim is None else claim.object_kind


def _doctor_checks(database: Path) -> list[DoctorCheck]:
    checks = [
        _check_python_runtime(),
        _check_migration_resources(),
        _check_database_parent(database),
    ]

    if database.exists():
        checks.extend(_check_existing_database(database))
    else:
        checks.append(
            DoctorCheck(
                name="database file",
                status="WARN",
                detail="database does not exist yet",
                action=f"run `memorygraph init --database {database}` to create it",
            )
        )
    return checks


def _check_python_runtime() -> DoctorCheck:
    version = sys.version_info
    detail = f"Python {version.major}.{version.minor}.{version.micro}"
    min_supported, max_supported = SUPPORTED_PYTHON
    if version < min_supported:
        return DoctorCheck(
            name="python runtime",
            status="FAIL",
            detail=(
                f"{detail} is below the supported floor of {min_supported[0]}.{min_supported[1]}"
            ),
            action="install Python 3.11 or newer",
        )
    if version[:2] > max_supported:
        return DoctorCheck(
            name="python runtime",
            status="WARN",
            detail=(
                f"{detail} is newer than the tested range through "
                f"{max_supported[0]}.{max_supported[1]}"
            ),
            action="run the test suite on this interpreter before shipping it",
        )
    return DoctorCheck(name="python runtime", status="OK", detail=detail)


def _check_migration_resources() -> DoctorCheck:
    try:
        migration_root = resources.files("memorygraph.storage.migrations")
        names = sorted(
            item.name
            for item in migration_root.iterdir()
            if MIGRATION_NAME_PATTERN.match(item.name)
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as error:
        return DoctorCheck(
            name="migration resources",
            status="FAIL",
            detail=f"could not load packaged migrations: {error}",
            action="reinstall the package or verify wheel/sdist contents",
        )
    if not names:
        return DoctorCheck(
            name="migration resources",
            status="FAIL",
            detail="no SQL migrations were packaged",
            action="include src/memorygraph/storage/migrations/*.sql in build artifacts",
        )
    return DoctorCheck(
        name="migration resources",
        status="OK",
        detail=f"found {len(names)} packaged migration file(s); latest is {names[-1]}",
    )


def _check_database_parent(database: Path) -> DoctorCheck:
    parent = database.parent
    if parent.exists():
        if parent.is_dir() and _directory_is_writable(parent):
            return DoctorCheck(
                name="database path",
                status="OK",
                detail=f"parent directory is writable: {parent}",
            )
        return DoctorCheck(
            name="database path",
            status="FAIL",
            detail=f"parent path is not writable: {parent}",
            action="choose a writable --database path",
        )
    ancestor = _first_existing_ancestor(parent)
    if ancestor is None:
        return DoctorCheck(
            name="database path",
            status="FAIL",
            detail=f"no existing ancestor found for {parent}",
            action="choose a simpler writable --database path",
        )
    if not _directory_is_writable(ancestor):
        return DoctorCheck(
            name="database path",
            status="FAIL",
            detail=f"cannot create {parent} because {ancestor} is not writable",
            action="choose a writable --database path",
        )
    return DoctorCheck(
        name="database path",
        status="OK",
        detail=f"parent directory can be created under {ancestor}",
    )


def _check_existing_database(database: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if not database.is_file():
        return [
            DoctorCheck(
                name="database file",
                status="FAIL",
                detail=f"path exists but is not a file: {database}",
                action="point --database at a SQLite file path",
            )
        ]

    try:
        connection = sqlite3.connect(str(database))
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        return [
            DoctorCheck(
                name="database file",
                status="FAIL",
                detail=f"could not open SQLite database: {error}",
                action="restore the file from backup or re-run `memorygraph init` on a new path",
            )
        ]

    try:
        checks.append(_check_sqlite_features(connection))
        checks.append(_check_schema_version(connection))
        checks.append(_check_database_open(database))
    finally:
        connection.close()
    return checks


def _check_sqlite_features(connection: sqlite3.Connection) -> DoctorCheck:
    try:
        sqlite_version = connection.execute("SELECT sqlite_version()").fetchone()[0]
        connection.execute("CREATE VIRTUAL TABLE temp.memorygraph_doctor_fts USING fts5(content)")
        connection.execute("DROP TABLE temp.memorygraph_doctor_fts")
        connection.execute("SELECT json_valid('{\"ok\": 1}')").fetchone()
    except sqlite3.Error as error:
        return DoctorCheck(
            name="sqlite features",
            status="FAIL",
            detail=f"required SQLite feature check failed: {error}",
            action="use a Python build with SQLite JSON1 and FTS5 support",
        )
    return DoctorCheck(
        name="sqlite features",
        status="OK",
        detail=f"SQLite {sqlite_version} with JSON1 and FTS5 available",
    )


def _check_schema_version(connection: sqlite3.Connection) -> DoctorCheck:
    try:
        current = MigrationRunner(connection).current_version()
        available = MigrationRunner(connection).migrations()
    except Exception as error:
        return DoctorCheck(
            name="schema version",
            status="FAIL",
            detail=f"could not inspect schema version: {error}",
            action="re-run `memorygraph init` or inspect the packaged migrations",
        )
    latest = available[-1].version if available else 0
    database_path = connection.execute("PRAGMA database_list").fetchone()[2]
    if current == 0:
        return DoctorCheck(
            name="schema version",
            status="WARN",
            detail="database exists but has not been initialized by MemoryGraph",
            action=f"run `memorygraph init --database {database_path}`",
        )
    if current < latest:
        return DoctorCheck(
            name="schema version",
            status="WARN",
            detail=f"database schema is at {current}; latest packaged schema is {latest}",
            action=f"run `memorygraph init --database {database_path}` to migrate it",
        )
    return DoctorCheck(
        name="schema version",
        status="OK",
        detail=f"schema version {current} matches packaged migrations",
    )


def _check_database_open(database: Path) -> DoctorCheck:
    try:
        with tempfile.TemporaryDirectory(prefix="memorygraph-doctor-") as tempdir:
            probe_path = Path(tempdir) / "probe.db"
            with (
                closing(sqlite3.connect(str(database))) as source,
                closing(sqlite3.connect(str(probe_path))) as probe,
            ):
                source.backup(probe)
            with MemoryGraph.open(probe_path):
                pass
    except Exception as error:
        return DoctorCheck(
            name="memorygraph open",
            status="FAIL",
            detail=f"MemoryGraph.open failed: {error}",
            action="inspect the database file and rerun `memorygraph init` if needed",
        )
    return DoctorCheck(
        name="memorygraph open",
        status="OK",
        detail="database opens and migrations apply cleanly",
    )


def _directory_is_writable(path: Path) -> bool:
    return path.is_dir() and path.exists() and os.access(path, os.W_OK)


def _first_existing_ancestor(path: Path) -> Path | None:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return current


if __name__ == "__main__":
    app()
