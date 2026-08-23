"""Seed a project bank with a small, explicit, reviewable operating contract."""

from __future__ import annotations

from dataclasses import dataclass

from memorygraph import MemoryGraph


@dataclass(frozen=True, slots=True)
class DogfoodBootstrapReport:
    bank_id: str
    bank_slug: str
    observation_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    procedural_episode_id: str


_PROJECT_MEMORIES = (
    (
        "architecture_contract",
        "08-memorygraph-project-spec-v2.md",
        "pointer",
        "The approved implementation contract is 08-memorygraph-project-spec-v2.md; re-read "
        "the file before relying on details that may have changed.",
    ),
    (
        "test_command",
        ".venv/bin/python -m pytest -q",
        "pointer",
        "The local full-suite command is .venv/bin/python -m pytest -q; verify the current "
        "project environment before executing it.",
    ),
    (
        "product_wedge",
        "long-running coding agents",
        "timeless",
        "MemoryGraph's initial product wedge is long-running coding agents that need auditable "
        "revision rather than another timeless fact store.",
    ),
    (
        "dream_commit_policy",
        "models propose; deterministic policy validates and commits",
        "timeless",
        "Dream providers may propose candidates, but deterministic validation and atomic policy "
        "commit every accepted mutation.",
    ),
    (
        "authority_model",
        "raw observations and append-only events are authoritative; graphs and notes are derived",
        "timeless",
        "Raw observations and append-only events are authoritative. The temporal graph, indexes, "
        "artifacts, and Obsidian notes are derived projections.",
    ),
    (
        "mcp_tool_surface",
        "recall, record, explain, correct, forget",
        "timeless",
        "The agent-facing MCP surface is exactly five tools: recall, record, explain, correct, "
        "and forget.",
    ),
    (
        "alpha_benchmark",
        "12/12 public retrieval, 7/7 chaos, strongest simple baseline 7/12",
        "snapshot",
        "At the 2026-08-22 alpha checkpoint MemoryGraph passed 12/12 public retrieval cases and "
        "7/7 chaos cases; the strongest simple retrieval baseline passed 7/12.",
    ),
)


def bootstrap_project_memory(
    memory: MemoryGraph,
    *,
    bank_slug: str = "project:memorygraph",
    workspace: str = "agent-memory-research",
) -> DogfoodBootstrapReport:
    """Idempotently seed explicit project facts and the first procedural success."""

    bank = memory.create_bank(
        bank_slug,
        name="MemoryGraph self-dogfood",
        mission="Prevent repeated mistakes and stale-state failures during MemoryGraph work.",
    )
    observation_ids: list[str] = []
    claim_ids: list[str] = []
    for predicate, value, freshness_form, content in _PROJECT_MEMORIES:
        memory.define_predicate(
            predicate,
            bank=bank.id,
            cardinality="one",
            volatility="volatile" if freshness_form == "snapshot" else "durable",
        )
        observation = memory.observe(
            content,
            bank=bank.id,
            source_key=f"dogfood:bootstrap:{predicate}",
            kind="explicit_assertion",
            trust_class="owner_explicit",
            metadata={
                "workspace": workspace,
                "freshness_form": freshness_form,
                "dogfood_bootstrap": True,
            },
        )
        claim = memory.assert_claim(
            bank=bank.id,
            subject="MemoryGraph project",
            predicate=predicate,
            object=value,
            object_kind="string",
            observation_id=observation.id,
            excerpt=content,
        )
        observation_ids.append(observation.id)
        claim_ids.append(claim.id)

    attempt = memory.record_attempt(
        bank=bank.id,
        source_key="dogfood:bootstrap:alpha-mvp",
        task_key="build MemoryGraph alpha MVP",
        strategy=(
            "Freeze the authority model, implement deterministic mutation gates, then validate "
            "the installed package and real newline-delimited MCP handshake."
        ),
        outcome="success",
        applicability={"project": "memorygraph", "phase": "alpha"},
        environment={"storage": "sqlite", "transport": "stdio-mcp"},
        workspace=workspace,
    )
    return DogfoodBootstrapReport(
        bank_id=bank.id,
        bank_slug=bank.slug,
        observation_ids=tuple(observation_ids),
        claim_ids=tuple(claim_ids),
        procedural_episode_id=attempt.id,
    )
