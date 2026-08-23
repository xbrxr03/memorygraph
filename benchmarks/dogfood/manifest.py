"""Strict manifest loader for deterministic real-task dogfood runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "memorygraph.dogfood.manifest/v1"
STEP_KINDS = {"observation", "query", "attempt"}
OUTCOMES = {"success", "failure", "partial", "unknown"}
TRUST_CLASSES = {
    "owner_explicit",
    "authoritative_source",
    "direct_observation",
    "untrusted",
}


class ManifestValidationError(ValueError):
    """Raised when a dogfood manifest is invalid."""


def _expect_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field_name} must be an object")
    return value


def _expect_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{field_name} must be an array")
    return value


def _expect_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{field_name} must be a non-empty string")
    return value


def _expect_timestamp(value: Any, field_name: str) -> str:
    text = _expect_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - exercised through validation tests
        raise ManifestValidationError(f"{field_name} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ManifestValidationError(f"{field_name} must be ISO-8601 UTC")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _expect_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    items = _expect_list(value or [], field_name)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ManifestValidationError(f"{field_name} must contain non-empty strings")
    return tuple(str(item) for item in items)


def _expect_int(value: Any, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestValidationError(f"{field_name} must be an integer")
    if value < minimum:
        comparator = "positive" if minimum == 1 else f">= {minimum}"
        raise ManifestValidationError(f"{field_name} must be {comparator}")
    return value


def _expect_float(value: Any, field_name: str, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestValidationError(f"{field_name} must be a number")
    result = float(value)
    if result < minimum:
        raise ManifestValidationError(f"{field_name} must be >= {minimum}")
    return result


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    name: str
    cardinality: str
    volatility: str = "durable"
    subject_type: str = "entity"
    object_type: str = "value"


@dataclass(frozen=True, slots=True)
class QueryExpectations:
    required_context_ids: tuple[str, ...] = ()
    forbidden_context_ids: tuple[str, ...] = ()
    forbidden_fragments: tuple[str, ...] = ()
    min_hits: int = 0
    max_hits: int | None = None


@dataclass(frozen=True, slots=True)
class StepBase:
    step_id: str
    kind: Literal["observation", "query", "attempt"]
    at: str
    bank_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationStep(StepBase):
    source_key: str = ""
    content: str = ""
    trust_class: str = "owner_explicit"
    actor_type: str = "user"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class QueryStep(StepBase):
    question: str = ""
    expectations: QueryExpectations = QueryExpectations()
    max_items: int = 5
    max_tokens: int = 512
    tool_calls: int = 1
    retries: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class AttemptStep(StepBase):
    source_key: str = ""
    task_key: str = ""
    strategy: str = ""
    outcome: str = "unknown"
    failure: str | None = None
    applicability: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    query: str | None = None
    expectations: QueryExpectations = QueryExpectations()
    mistake_key: str | None = None
    max_items: int = 5
    max_tokens: int = 512
    tool_calls: int = 1
    retries: int = 0
    estimated_cost_usd: float = 0.0


ManifestStep = ObservationStep | QueryStep | AttemptStep


@dataclass(frozen=True, slots=True)
class TaskManifest:
    task_id: str
    title: str
    bank_id: str
    steps: tuple[ManifestStep, ...]


@dataclass(frozen=True, slots=True)
class DogfoodManifest:
    schema_version: str
    manifest_id: str
    title: str
    description: str
    predicate_definitions: tuple[PredicateDefinition, ...]
    tasks: tuple[TaskManifest, ...]
    raw: dict[str, Any]


def load_manifest(path: str | Path) -> DogfoodManifest:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_manifest(document)


def build_manifest(document: dict[str, Any]) -> DogfoodManifest:
    top = _expect_mapping(document, "manifest")
    schema_version = _expect_string(top.get("schema_version"), "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ManifestValidationError(
            f"schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        )
    manifest_id = _expect_string(top.get("manifest_id"), "manifest_id")
    title = _expect_string(top.get("title"), "title")
    description = _expect_string(top.get("description"), "description")
    predicate_definitions = tuple(
        _build_predicate(item, index)
        for index, item in enumerate(
            _expect_list(top.get("predicate_definitions", []), "predicate_definitions")
        )
    )
    tasks = tuple(
        _build_task(item, index)
        for index, item in enumerate(_expect_list(top.get("tasks"), "tasks"))
    )
    task_ids = {task.task_id for task in tasks}
    if len(task_ids) != len(tasks):
        raise ManifestValidationError("task_ids must be unique")
    return DogfoodManifest(
        schema_version=schema_version,
        manifest_id=manifest_id,
        title=title,
        description=description,
        predicate_definitions=predicate_definitions,
        tasks=tasks,
        raw=document,
    )


def _build_predicate(document: Any, index: int) -> PredicateDefinition:
    mapping = _expect_mapping(document, f"predicate_definitions[{index}]")
    return PredicateDefinition(
        name=_expect_string(mapping.get("name"), f"predicate_definitions[{index}].name"),
        cardinality=_expect_string(
            mapping.get("cardinality"),
            f"predicate_definitions[{index}].cardinality",
        ),
        volatility=str(mapping.get("volatility", "durable")),
        subject_type=str(mapping.get("subject_type", "entity")),
        object_type=str(mapping.get("object_type", "value")),
    )


def _build_task(document: Any, index: int) -> TaskManifest:
    mapping = _expect_mapping(document, f"tasks[{index}]")
    bank_id = _expect_string(mapping.get("bank_id"), f"tasks[{index}].bank_id")
    steps = tuple(
        _build_step(item, task_index=index, step_index=step_index, bank_id=bank_id)
        for step_index, item in enumerate(
            _expect_list(mapping.get("steps"), f"tasks[{index}].steps")
        )
    )
    step_ids = {step.step_id for step in steps}
    if len(step_ids) != len(steps):
        raise ManifestValidationError(f"tasks[{index}] step_ids must be unique")
    previous_at: datetime | None = None
    for step in steps:
        current_at = datetime.fromisoformat(step.at.replace("Z", "+00:00"))
        if previous_at is not None and current_at < previous_at:
            raise ManifestValidationError(
                f"tasks[{index}] steps must be sorted by non-decreasing `at` timestamps"
            )
        previous_at = current_at
    return TaskManifest(
        task_id=_expect_string(mapping.get("task_id"), f"tasks[{index}].task_id"),
        title=_expect_string(mapping.get("title"), f"tasks[{index}].title"),
        bank_id=bank_id,
        steps=steps,
    )


def _build_step(document: Any, *, task_index: int, step_index: int, bank_id: str) -> ManifestStep:
    mapping = _expect_mapping(document, f"tasks[{task_index}].steps[{step_index}]")
    kind = _expect_string(mapping.get("kind"), f"tasks[{task_index}].steps[{step_index}].kind")
    if kind not in STEP_KINDS:
        raise ManifestValidationError(f"unsupported step kind: {kind}")
    common = {
        "step_id": _expect_string(
            mapping.get("step_id"),
            f"tasks[{task_index}].steps[{step_index}].step_id",
        ),
        "kind": kind,
        "at": _expect_timestamp(mapping.get("at"), f"tasks[{task_index}].steps[{step_index}].at"),
        "bank_id": bank_id,
        "title": str(mapping["title"]) if "title" in mapping and mapping["title"] else None,
    }
    expectations = _build_expectations(
        mapping.get("expectations", {}),
        f"tasks[{task_index}].steps[{step_index}].expectations",
    )
    if kind == "observation":
        trust_class = str(mapping.get("trust_class", "owner_explicit"))
        if trust_class not in TRUST_CLASSES:
            raise ManifestValidationError(f"unsupported trust_class: {trust_class}")
        return ObservationStep(
            **common,
            source_key=_expect_string(
                mapping.get("source_key"),
                f"tasks[{task_index}].steps[{step_index}].source_key",
            ),
            content=_expect_string(
                mapping.get("content"),
                f"tasks[{task_index}].steps[{step_index}].content",
            ),
            trust_class=trust_class,
            actor_type=str(mapping.get("actor_type", "user")),
            metadata=_expect_mapping(
                mapping.get("metadata", {}),
                f"tasks[{task_index}].steps[{step_index}].metadata",
            )
            if "metadata" in mapping
            else None,
        )
    if kind == "query":
        return QueryStep(
            **common,
            question=_expect_string(
                mapping.get("question"),
                f"tasks[{task_index}].steps[{step_index}].question",
            ),
            expectations=expectations,
            max_items=_expect_int(
                mapping.get("max_items", 5),
                f"tasks[{task_index}].steps[{step_index}].max_items",
                minimum=1,
            ),
            max_tokens=_expect_int(
                mapping.get("max_tokens", 512),
                f"tasks[{task_index}].steps[{step_index}].max_tokens",
                minimum=1,
            ),
            tool_calls=_expect_int(
                mapping.get("tool_calls", 1),
                f"tasks[{task_index}].steps[{step_index}].tool_calls",
                minimum=0,
            ),
            retries=_expect_int(
                mapping.get("retries", 0),
                f"tasks[{task_index}].steps[{step_index}].retries",
                minimum=0,
            ),
            estimated_cost_usd=_expect_float(
                mapping.get("estimated_cost_usd", 0.0),
                f"tasks[{task_index}].steps[{step_index}].estimated_cost_usd",
                minimum=0.0,
            ),
        )
    outcome = str(mapping.get("outcome", "unknown"))
    if outcome not in OUTCOMES:
        raise ManifestValidationError(f"unsupported attempt outcome: {outcome}")
    query = mapping.get("query")
    if query is not None:
        query = _expect_string(query, f"tasks[{task_index}].steps[{step_index}].query")
    return AttemptStep(
        **common,
        source_key=_expect_string(
            mapping.get("source_key"),
            f"tasks[{task_index}].steps[{step_index}].source_key",
        ),
        task_key=_expect_string(
            mapping.get("task_key"),
            f"tasks[{task_index}].steps[{step_index}].task_key",
        ),
        strategy=_expect_string(
            mapping.get("strategy"),
            f"tasks[{task_index}].steps[{step_index}].strategy",
        ),
        outcome=outcome,
        failure=str(mapping["failure"]) if "failure" in mapping and mapping["failure"] else None,
        applicability=_expect_mapping(
            mapping.get("applicability", {}),
            f"tasks[{task_index}].steps[{step_index}].applicability",
        )
        if "applicability" in mapping
        else None,
        environment=_expect_mapping(
            mapping.get("environment", {}),
            f"tasks[{task_index}].steps[{step_index}].environment",
        )
        if "environment" in mapping
        else None,
        query=query,
        expectations=expectations,
        mistake_key=str(mapping["mistake_key"]) if "mistake_key" in mapping else None,
        max_items=_expect_int(
            mapping.get("max_items", 5),
            f"tasks[{task_index}].steps[{step_index}].max_items",
            minimum=1,
        ),
        max_tokens=_expect_int(
            mapping.get("max_tokens", 512),
            f"tasks[{task_index}].steps[{step_index}].max_tokens",
            minimum=1,
        ),
        tool_calls=_expect_int(
            mapping.get("tool_calls", 1),
            f"tasks[{task_index}].steps[{step_index}].tool_calls",
            minimum=0,
        ),
        retries=_expect_int(
            mapping.get("retries", 0),
            f"tasks[{task_index}].steps[{step_index}].retries",
            minimum=0,
        ),
        estimated_cost_usd=_expect_float(
            mapping.get("estimated_cost_usd", 0.0),
            f"tasks[{task_index}].steps[{step_index}].estimated_cost_usd",
            minimum=0.0,
        ),
    )


def _build_expectations(document: Any, field_name: str) -> QueryExpectations:
    mapping = _expect_mapping(document, field_name)
    max_hits = mapping.get("max_hits")
    if max_hits is not None:
        max_hits = _expect_int(max_hits, f"{field_name}.max_hits", minimum=0)
    min_hits = _expect_int(mapping.get("min_hits", 0), f"{field_name}.min_hits", minimum=0)
    if max_hits is not None and max_hits < min_hits:
        raise ManifestValidationError(f"{field_name}.max_hits must be >= min_hits")
    return QueryExpectations(
        required_context_ids=_expect_string_list(
            mapping.get("required_context_ids", []),
            f"{field_name}.required_context_ids",
        ),
        forbidden_context_ids=_expect_string_list(
            mapping.get("forbidden_context_ids", []),
            f"{field_name}.forbidden_context_ids",
        ),
        forbidden_fragments=_expect_string_list(
            mapping.get("forbidden_fragments", []),
            f"{field_name}.forbidden_fragments",
        ),
        min_hits=min_hits,
        max_hits=max_hits,
    )
