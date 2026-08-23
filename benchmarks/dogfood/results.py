"""Result models and summaries for dogfood treatment runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class StepMetrics:
    useful_recall_hits: int
    useful_recall_precision: float
    forbidden_recall_hits: int
    forbidden_fragment_hits: int
    repeated_mistake: bool
    latency_ms: float
    token_estimate: int
    tool_calls: int
    retries: int
    estimated_cost_usd: float
    dream_runs: int = 0
    dream_proposals: int = 0
    pending_reviews: int = 0


@dataclass(frozen=True, slots=True)
class StepResult:
    arm_name: str
    task_id: str
    step_id: str
    kind: str
    passed: bool
    retrieved_context_ids: tuple[str, ...]
    metrics: StepMetrics
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["retrieved_context_ids"] = list(self.retrieved_context_ids)
        payload["notes"] = list(self.notes)
        return payload


@dataclass(frozen=True, slots=True)
class TaskResult:
    arm_name: str
    task_id: str
    title: str
    passed: bool
    step_results: tuple[StepResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_name": self.arm_name,
            "task_id": self.task_id,
            "title": self.title,
            "passed": self.passed,
            "step_results": [step.to_dict() for step in self.step_results],
        }


@dataclass(frozen=True, slots=True)
class ArmSummary:
    arm_name: str
    task_count: int
    passed_tasks: int
    failed_tasks: int
    query_steps: int
    useful_recall_precision: float
    forbidden_recall_hits: int
    forbidden_fragment_hits: int
    repeated_mistakes: int
    total_latency_ms: float
    total_tokens: int
    total_tool_calls: int
    total_retries: int
    total_estimated_cost_usd: float
    dream_runs: int
    dream_proposals: int
    pending_reviews: int


@dataclass(frozen=True, slots=True)
class PairwiseComparison:
    left_arm: str
    right_arm: str
    task_wins: int
    task_losses: int
    task_ties: int
    passed_task_delta: int
    useful_precision_delta: float
    repeated_mistake_delta: int
    forbidden_recall_delta: int
    forbidden_fragment_delta: int
    latency_delta_ms: float


@dataclass(frozen=True, slots=True)
class PairwiseSummary:
    comparisons: tuple[PairwiseComparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"comparisons": [asdict(item) for item in self.comparisons]}


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    arm_summaries: tuple[ArmSummary, ...]
    pairwise: PairwiseSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "arm_summaries": [asdict(item) for item in self.arm_summaries],
            "pairwise": self.pairwise.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DogfoodRunResult:
    run_id: str
    manifest_id: str
    arm_task_results: dict[str, tuple[TaskResult, ...]]
    summary: RunSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "manifest_id": self.manifest_id,
            "arm_task_results": {
                arm_name: [item.to_dict() for item in task_results]
                for arm_name, task_results in self.arm_task_results.items()
            },
            "summary": self.summary.to_dict(),
        }

    def write_json_report(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )


def make_run_id(prefix: str = "dogfood") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
