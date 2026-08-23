from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.dogfood import DogfoodRunner, RunConfig, load_manifest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "benchmarks" / "dogfood" / "fixtures" / "offline-mvp.json"
BRIDGE = ROOT / "tests" / "dogfood" / "fixtures" / "graphify_bridge.py"


def test_runner_executes_all_treatment_arms() -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(
            arms=(
                "no_memory",
                "markdown",
                "memorygraph_graph_only",
                "memorygraph_gated_dream",
                "memorygraph_always_dream",
                "graphify_compatible",
            ),
            external_command=("python3", str(BRIDGE)),
        ),
        run_id="dogfood-fixture",
    )

    summaries = {item.arm_name: item for item in result.summary.arm_summaries}

    assert summaries["memorygraph_always_dream"].passed_tasks == 3
    assert summaries["memorygraph_always_dream"].passed_tasks > summaries["no_memory"].passed_tasks
    assert summaries["memorygraph_always_dream"].forbidden_fragment_hits == 0
    assert summaries["markdown"].forbidden_fragment_hits >= 1
    assert summaries["memorygraph_graph_only"].passed_tasks >= summaries["no_memory"].passed_tasks
    assert summaries["memorygraph_gated_dream"].pending_reviews == 0
    assert summaries["graphify_compatible"].task_count == 3

    comparisons = {
        (item.left_arm, item.right_arm): item for item in result.summary.pairwise.comparisons
    }
    assert comparisons[("no_memory", "memorygraph_always_dream")].passed_task_delta < 0


def test_graph_only_keeps_procedural_recall_without_dream_claims() -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(arms=("memorygraph_graph_only",), external_command=None),
        run_id="dogfood-graph-only",
    )
    task_results = result.arm_task_results["memorygraph_graph_only"]
    procedural = next(task for task in task_results if task.task_id == "procedural-memory")
    assert procedural.passed is True
    release = next(task for task in task_results if task.task_id == "release-hygiene")
    assert release.passed is False


def test_run_config_strictly_clamps_query_limits_across_arms() -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(
            arms=("markdown", "memorygraph_graph_only", "graphify_compatible"),
            max_items=1,
            max_tokens=1,
            external_command=("python3", str(BRIDGE)),
        ),
        run_id="dogfood-clamped",
    )

    for arm_name, task_results in result.arm_task_results.items():
        for task in task_results:
            for step in task.step_results:
                if step.kind not in {"query", "attempt"}:
                    continue
                assert len(step.retrieved_context_ids) <= 1, (arm_name, step.step_id)
                assert step.metrics.token_estimate <= 1, (arm_name, step.step_id)


def test_always_dream_observations_report_provider_work_in_metrics() -> None:
    manifest = load_manifest(FIXTURE)
    result = DogfoodRunner(manifest).run(
        RunConfig(arms=("memorygraph_always_dream",), external_command=None),
        run_id="dogfood-dream-metrics",
    )

    observation_steps = [
        step
        for task in result.arm_task_results["memorygraph_always_dream"]
        for step in task.step_results
        if step.kind == "observation"
    ]

    assert observation_steps
    assert all(step.metrics.dream_runs == 1 for step in observation_steps)
    assert all(step.metrics.tool_calls >= 1 for step in observation_steps)
    assert all(step.metrics.latency_ms > 0.0 for step in observation_steps)


def test_run_config_rejects_empty_duplicate_or_unbounded_arms() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RunConfig(arms=())
    with pytest.raises(ValueError, match="unique"):
        RunConfig(arms=("no_memory", "no_memory"))
    with pytest.raises(ValueError, match="positive"):
        RunConfig(arms=("no_memory",), max_tokens=0)
