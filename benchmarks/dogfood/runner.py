"""Run all treatment arms over one manifest and produce paired summaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .arms import make_arm_runtime
from .manifest import AttemptStep, DogfoodManifest, ObservationStep, QueryStep
from .results import (
    ArmSummary,
    DogfoodRunResult,
    PairwiseComparison,
    PairwiseSummary,
    RunSummary,
    StepMetrics,
    StepResult,
    TaskResult,
    make_run_id,
)

DEFAULT_ARMS = (
    "no_memory",
    "markdown",
    "memorygraph_graph_only",
    "memorygraph_gated_dream",
    "memorygraph_always_dream",
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    arms: tuple[str, ...] = DEFAULT_ARMS
    max_items: int = 5
    max_tokens: int = 512
    external_command: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.arms:
            raise ValueError("at least one treatment arm is required")
        if len(set(self.arms)) != len(self.arms):
            raise ValueError("treatment arms must be unique")
        if self.max_items <= 0 or self.max_tokens <= 0:
            raise ValueError("run max_items and max_tokens must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "arms": list(self.arms),
            "max_items": self.max_items,
            "max_tokens": self.max_tokens,
            "external_command": list(self.external_command) if self.external_command else None,
        }


class DogfoodRunner:
    def __init__(self, manifest: DogfoodManifest) -> None:
        self.manifest = manifest

    def run(self, config: RunConfig, *, run_id: str | None = None) -> DogfoodRunResult:
        active_run_id = run_id or make_run_id("dogfood")
        arm_task_results: dict[str, tuple[TaskResult, ...]] = {}
        for arm_name in config.arms:
            runtime = make_arm_runtime(
                arm_name,
                self.manifest,
                max_items=config.max_items,
                max_tokens=config.max_tokens,
                external_command=config.external_command,
            )
            runtime.setup()
            try:
                task_results = tuple(self._run_task(runtime, task) for task in self.manifest.tasks)
            finally:
                runtime.close()
            arm_task_results[arm_name] = task_results
        summary = RunSummary(
            run_id=active_run_id,
            arm_summaries=tuple(
                _summarize_arm(name, arm_task_results[name]) for name in config.arms
            ),
            pairwise=_pairwise_summary(config.arms, arm_task_results),
        )
        return DogfoodRunResult(
            run_id=active_run_id,
            manifest_id=self.manifest.manifest_id,
            arm_task_results=arm_task_results,
            summary=summary,
        )

    def _run_task(self, runtime: Any, task: Any) -> TaskResult:
        step_results: list[StepResult] = []
        mistake_counts: dict[str, int] = {}
        for step in task.steps:
            if isinstance(step, ObservationStep):
                dream = runtime.apply_observation(step)
                step_results.append(
                    StepResult(
                        arm_name=runtime.arm_name,
                        task_id=task.task_id,
                        step_id=step.step_id,
                        kind=step.kind,
                        passed=True,
                        retrieved_context_ids=(),
                        metrics=StepMetrics(
                            useful_recall_hits=0,
                            useful_recall_precision=1.0,
                            forbidden_recall_hits=0,
                            forbidden_fragment_hits=0,
                            repeated_mistake=False,
                            latency_ms=dream.latency_ms,
                            token_estimate=dream.token_estimate,
                            tool_calls=dream.tool_calls,
                            retries=0,
                            estimated_cost_usd=dream.estimated_cost_usd,
                            dream_runs=dream.runs,
                            dream_proposals=dream.proposals,
                            pending_reviews=dream.pending_reviews,
                        ),
                    )
                )
                continue
            if isinstance(step, QueryStep):
                trace = runtime.run_query(step)
                step_results.append(
                    self._grade_trace(
                        runtime.arm_name,
                        task.task_id,
                        step,
                        trace,
                        False,
                    )
                )
                continue
            trace = runtime.run_attempt_query(step)
            repeated = _is_repeated_mistake(step, mistake_counts)
            step_results.append(
                self._grade_trace(
                    runtime.arm_name,
                    task.task_id,
                    step,
                    trace,
                    repeated,
                )
            )
            runtime.record_attempt(step)
            if step.mistake_key and step.outcome == "failure":
                mistake_counts[step.mistake_key] = mistake_counts.get(step.mistake_key, 0) + 1
        return TaskResult(
            arm_name=runtime.arm_name,
            task_id=task.task_id,
            title=task.title,
            passed=all(step.passed for step in step_results),
            step_results=tuple(step_results),
        )

    def _grade_trace(
        self,
        arm_name: str,
        task_id: str,
        step: QueryStep | AttemptStep,
        trace: Any,
        repeated_mistake: bool,
    ) -> StepResult:
        required = set(step.expectations.required_context_ids)
        forbidden = set(step.expectations.forbidden_context_ids)
        retrieved_ids = tuple(item.context_id for item in trace.items)
        useful_hits = len(required & set(retrieved_ids))
        forbidden_hits = len(forbidden & set(retrieved_ids))
        forbidden_fragment_hits = sum(
            1
            for item in trace.items
            for fragment in step.expectations.forbidden_fragments
            if fragment.lower() in item.content.lower()
        )
        precision = (
            useful_hits / len(trace.items) if trace.items else (1.0 if not required else 0.0)
        )
        passed = (
            useful_hits >= step.expectations.min_hits
            and forbidden_hits == 0
            and forbidden_fragment_hits == 0
            and (
                step.expectations.max_hits is None or len(trace.items) <= step.expectations.max_hits
            )
        )
        return StepResult(
            arm_name=arm_name,
            task_id=task_id,
            step_id=step.step_id,
            kind=step.kind,
            passed=passed,
            retrieved_context_ids=retrieved_ids,
            metrics=StepMetrics(
                useful_recall_hits=useful_hits,
                useful_recall_precision=precision,
                forbidden_recall_hits=forbidden_hits,
                forbidden_fragment_hits=forbidden_fragment_hits,
                repeated_mistake=repeated_mistake,
                latency_ms=trace.latency_ms,
                token_estimate=trace.token_estimate,
                tool_calls=trace.tool_calls,
                retries=trace.retries,
                estimated_cost_usd=trace.estimated_cost_usd,
            ),
            notes=trace.notes,
        )


def _is_repeated_mistake(step: AttemptStep, mistake_counts: dict[str, int]) -> bool:
    if not step.mistake_key or step.outcome != "failure":
        return False
    return mistake_counts.get(step.mistake_key, 0) > 0


def _summarize_arm(arm_name: str, task_results: Sequence[TaskResult]) -> ArmSummary:
    step_results = [step for task in task_results for step in task.step_results]
    query_steps = [step for step in step_results if step.kind in {"query", "attempt"}]
    precision_values = [step.metrics.useful_recall_precision for step in query_steps]
    return ArmSummary(
        arm_name=arm_name,
        task_count=len(task_results),
        passed_tasks=sum(1 for task in task_results if task.passed),
        failed_tasks=sum(1 for task in task_results if not task.passed),
        query_steps=len(query_steps),
        useful_recall_precision=(
            (sum(precision_values) / len(precision_values)) if precision_values else 1.0
        ),
        forbidden_recall_hits=sum(step.metrics.forbidden_recall_hits for step in step_results),
        forbidden_fragment_hits=sum(step.metrics.forbidden_fragment_hits for step in step_results),
        repeated_mistakes=sum(1 for step in step_results if step.metrics.repeated_mistake),
        total_latency_ms=sum(step.metrics.latency_ms for step in step_results),
        total_tokens=sum(step.metrics.token_estimate for step in step_results),
        total_tool_calls=sum(step.metrics.tool_calls for step in step_results),
        total_retries=sum(step.metrics.retries for step in step_results),
        total_estimated_cost_usd=sum(step.metrics.estimated_cost_usd for step in step_results),
        dream_runs=sum(step.metrics.dream_runs for step in step_results),
        dream_proposals=sum(step.metrics.dream_proposals for step in step_results),
        pending_reviews=sum(step.metrics.pending_reviews for step in step_results),
    )


def _pairwise_summary(
    arm_names: Sequence[str],
    arm_task_results: dict[str, tuple[TaskResult, ...]],
) -> PairwiseSummary:
    comparisons: list[PairwiseComparison] = []
    for left_index, left_arm in enumerate(arm_names):
        left_tasks = {task.task_id: task for task in arm_task_results[left_arm]}
        for right_arm in arm_names[left_index + 1 :]:
            right_tasks = {task.task_id: task for task in arm_task_results[right_arm]}
            wins = losses = ties = 0
            for task_id, left_task in left_tasks.items():
                right_task = right_tasks[task_id]
                if left_task.passed and not right_task.passed:
                    wins += 1
                elif right_task.passed and not left_task.passed:
                    losses += 1
                else:
                    ties += 1
            left_summary = _summarize_arm(left_arm, arm_task_results[left_arm])
            right_summary = _summarize_arm(right_arm, arm_task_results[right_arm])
            comparisons.append(
                PairwiseComparison(
                    left_arm=left_arm,
                    right_arm=right_arm,
                    task_wins=wins,
                    task_losses=losses,
                    task_ties=ties,
                    passed_task_delta=left_summary.passed_tasks - right_summary.passed_tasks,
                    useful_precision_delta=left_summary.useful_recall_precision
                    - right_summary.useful_recall_precision,
                    repeated_mistake_delta=left_summary.repeated_mistakes
                    - right_summary.repeated_mistakes,
                    forbidden_recall_delta=left_summary.forbidden_recall_hits
                    - right_summary.forbidden_recall_hits,
                    forbidden_fragment_delta=left_summary.forbidden_fragment_hits
                    - right_summary.forbidden_fragment_hits,
                    latency_delta_ms=left_summary.total_latency_ms - right_summary.total_latency_ms,
                )
            )
            comparisons.append(
                PairwiseComparison(
                    left_arm=right_arm,
                    right_arm=left_arm,
                    task_wins=losses,
                    task_losses=wins,
                    task_ties=ties,
                    passed_task_delta=right_summary.passed_tasks - left_summary.passed_tasks,
                    useful_precision_delta=right_summary.useful_recall_precision
                    - left_summary.useful_recall_precision,
                    repeated_mistake_delta=right_summary.repeated_mistakes
                    - left_summary.repeated_mistakes,
                    forbidden_recall_delta=right_summary.forbidden_recall_hits
                    - left_summary.forbidden_recall_hits,
                    forbidden_fragment_delta=right_summary.forbidden_fragment_hits
                    - left_summary.forbidden_fragment_hits,
                    latency_delta_ms=right_summary.total_latency_ms - left_summary.total_latency_ms,
                )
            )
    return PairwiseSummary(comparisons=tuple(comparisons))
