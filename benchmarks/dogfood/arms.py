"""Treatment-arm runtimes for deterministic dogfood evaluation."""

from __future__ import annotations

import json
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from memorygraph import MemoryGraph
from memorygraph.dream import DreamRunMode

from .manifest import (
    AttemptStep,
    DogfoodManifest,
    ObservationStep,
    PredicateDefinition,
    QueryStep,
)


@dataclass(frozen=True, slots=True)
class ContextItem:
    context_id: str
    bank_id: str
    at: str
    content: str
    metadata: dict[str, Any]
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RecallTrace:
    items: tuple[ContextItem, ...]
    latency_ms: float
    token_estimate: int
    tool_calls: int
    retries: int
    estimated_cost_usd: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DreamTrace:
    runs: int = 0
    proposals: int = 0
    pending_reviews: int = 0
    latency_ms: float = 0.0
    token_estimate: int = 0
    tool_calls: int = 0
    estimated_cost_usd: float = 0.0


class ArmRuntime(ABC):
    def __init__(self, manifest: DogfoodManifest, *, max_items: int, max_tokens: int) -> None:
        self.manifest = manifest
        self.max_items = max_items
        self.max_tokens = max_tokens

    @property
    @abstractmethod
    def arm_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def setup(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_observation(self, step: ObservationStep) -> DreamTrace:
        raise NotImplementedError

    @abstractmethod
    def run_query(self, step: QueryStep) -> RecallTrace:
        raise NotImplementedError

    @abstractmethod
    def run_attempt_query(self, step: AttemptStep) -> RecallTrace:
        raise NotImplementedError

    @abstractmethod
    def record_attempt(self, step: AttemptStep) -> None:
        raise NotImplementedError

    def config(self) -> dict[str, Any]:
        return {
            "arm_name": self.arm_name,
            "max_items": self.max_items,
            "max_tokens": self.max_tokens,
        }

    def _effective_limits(self, *, max_items: int, max_tokens: int) -> tuple[int, int]:
        return min(max_items, self.max_items), min(max_tokens, self.max_tokens)


class NoMemoryRuntime(ArmRuntime):
    arm_name = "no_memory"

    def setup(self) -> None:
        return None

    def close(self) -> None:
        return None

    def apply_observation(self, step: ObservationStep) -> DreamTrace:
        del step
        return DreamTrace()

    def run_query(self, step: QueryStep) -> RecallTrace:
        return RecallTrace(
            (),
            0.0,
            0,
            step.tool_calls,
            step.retries,
            step.estimated_cost_usd,
        )

    def run_attempt_query(self, step: AttemptStep) -> RecallTrace:
        return RecallTrace(
            (),
            0.0,
            0,
            step.tool_calls,
            step.retries,
            step.estimated_cost_usd,
        )

    def record_attempt(self, step: AttemptStep) -> None:
        del step


class MarkdownRuntime(ArmRuntime):
    arm_name = "markdown"

    def __init__(self, manifest: DogfoodManifest, *, max_items: int, max_tokens: int) -> None:
        super().__init__(manifest, max_items=max_items, max_tokens=max_tokens)
        self._items: list[ContextItem] = []

    def setup(self) -> None:
        return None

    def close(self) -> None:
        return None

    def apply_observation(self, step: ObservationStep) -> DreamTrace:
        self._items.append(
            ContextItem(
                context_id=step.source_key,
                bank_id=step.bank_id,
                at=step.at,
                content=step.content,
                metadata={"kind": "observation", "trust_class": step.trust_class},
            )
        )
        return DreamTrace()

    def run_query(self, step: QueryStep) -> RecallTrace:
        max_items, max_tokens = self._effective_limits(
            max_items=step.max_items,
            max_tokens=step.max_tokens,
        )
        return self._search(
            bank_id=step.bank_id,
            as_of=step.at,
            question=step.question,
            max_items=max_items,
            max_tokens=max_tokens,
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
        )

    def run_attempt_query(self, step: AttemptStep) -> RecallTrace:
        if not step.query:
            return RecallTrace(
                (),
                0.0,
                0,
                step.tool_calls,
                step.retries,
                step.estimated_cost_usd,
            )
        max_items, max_tokens = self._effective_limits(
            max_items=step.max_items,
            max_tokens=step.max_tokens,
        )
        return self._search(
            bank_id=step.bank_id,
            as_of=step.at,
            question=step.query,
            max_items=max_items,
            max_tokens=max_tokens,
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
        )

    def record_attempt(self, step: AttemptStep) -> None:
        payload = "\n".join(
            line
            for line in (
                f"Task: {step.task_key}",
                f"Strategy: {step.strategy}",
                f"Outcome: {step.outcome}",
                None if step.failure is None else f"Failure: {step.failure}",
                f"Applicability: {json.dumps(step.applicability or {}, sort_keys=True)}",
                f"Environment: {json.dumps(step.environment or {}, sort_keys=True)}",
            )
            if line is not None
        )
        self._items.append(
            ContextItem(
                context_id=step.source_key,
                bank_id=step.bank_id,
                at=step.at,
                content=payload,
                metadata={"kind": "attempt", "task_key": step.task_key},
            )
        )

    def _search(
        self,
        *,
        bank_id: str,
        as_of: str,
        question: str,
        max_items: int,
        max_tokens: int,
        tool_calls: int,
        retries: int,
        estimated_cost_usd: float,
    ) -> RecallTrace:
        started = perf_counter()
        scored: list[tuple[int, ContextItem]] = []
        question_terms = _terms(question)
        for item in self._items:
            if item.bank_id != bank_id or item.at > as_of:
                continue
            score = len(question_terms & _terms(item.content))
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda item: (-item[0], item[1].at, item[1].context_id))
        selected = _select_items(
            [item for _, item in scored],
            max_items=max_items,
            max_tokens=max_tokens,
        )
        latency_ms = (perf_counter() - started) * 1000.0
        return RecallTrace(
            items=selected,
            latency_ms=latency_ms,
            token_estimate=sum(len(item.content.split()) for item in selected),
            tool_calls=tool_calls,
            retries=retries,
            estimated_cost_usd=estimated_cost_usd,
        )


class MemoryGraphRuntime(ArmRuntime):
    def __init__(
        self,
        manifest: DogfoodManifest,
        *,
        max_items: int,
        max_tokens: int,
        dream_mode: DreamRunMode | None,
        arm_name: str,
    ) -> None:
        super().__init__(manifest, max_items=max_items, max_tokens=max_tokens)
        self._arm_name = arm_name
        self._dream_mode = dream_mode
        self._tempdir = tempfile.TemporaryDirectory(prefix=f"{arm_name}-")
        self._db_path = Path(self._tempdir.name) / "memory.db"
        self.memory = MemoryGraph.open(self._db_path)
        self._banks_ready: set[str] = set()

    @property
    def arm_name(self) -> str:
        return self._arm_name

    def setup(self) -> None:
        return None

    def close(self) -> None:
        self.memory.close()
        self._tempdir.cleanup()

    def apply_observation(self, step: ObservationStep) -> DreamTrace:
        self._ensure_bank(step.bank_id)
        observation = self.memory.observe(
            step.content,
            bank=step.bank_id,
            source_key=step.source_key,
            trust_class=step.trust_class,
            actor_type=step.actor_type,
            metadata=step.metadata,
            observed_at=step.at,
        )
        if self._dream_mode is None:
            return DreamTrace()
        started = perf_counter()
        report = self.memory.run_dream(
            bank=step.bank_id,
            mode=self._dream_mode,
            observation_ids=(observation.id,),
        )
        latency_ms = (perf_counter() - started) * 1000.0
        return DreamTrace(
            runs=1,
            proposals=report.metrics.proposals_total,
            pending_reviews=report.metrics.review_required,
            latency_ms=latency_ms,
            token_estimate=sum(
                trace.usage.total_tokens
                for trace in report.provider_calls
                if trace.usage is not None
            ),
            tool_calls=report.metrics.provider_calls,
            estimated_cost_usd=sum(
                trace.usage.estimated_cost_usd or 0.0
                for trace in report.provider_calls
                if trace.usage is not None
            ),
        )

    def run_query(self, step: QueryStep) -> RecallTrace:
        self._ensure_bank(step.bank_id)
        max_items, max_tokens = self._effective_limits(
            max_items=step.max_items,
            max_tokens=step.max_tokens,
        )
        started = perf_counter()
        hits = self.memory.recall(
            bank_id=step.bank_id,
            query_text=step.question,
            as_of=step.at,
            max_items=max_items,
            max_tokens=max_tokens,
        )
        latency_ms = (perf_counter() - started) * 1000.0
        items = tuple(
            ContextItem(
                context_id=hit.event_id,
                bank_id=hit.bank_id,
                at=hit.at,
                content=hit.content,
                metadata=hit.metadata or {},
                score=hit.score,
            )
            for hit in hits
        )
        return RecallTrace(
            items=items,
            latency_ms=latency_ms,
            token_estimate=sum(len(item.content.split()) for item in items),
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
            notes=(),
        )

    def run_attempt_query(self, step: AttemptStep) -> RecallTrace:
        if not step.query:
            return RecallTrace(
                (),
                0.0,
                0,
                step.tool_calls,
                step.retries,
                step.estimated_cost_usd,
            )
        query_step = QueryStep(
            step_id=step.step_id,
            kind="query",
            at=step.at,
            bank_id=step.bank_id,
            title=step.title,
            question=step.query,
            expectations=step.expectations,
            max_items=step.max_items,
            max_tokens=step.max_tokens,
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
        )
        return self.run_query(query_step)

    def record_attempt(self, step: AttemptStep) -> None:
        self._ensure_bank(step.bank_id)
        self.memory.record_attempt(
            bank=step.bank_id,
            source_key=step.source_key,
            task_key=step.task_key,
            strategy=step.strategy,
            outcome=step.outcome,
            failure=step.failure,
            applicability=step.applicability or {},
            environment=step.environment or {},
            completed_at=step.at,
        )

    def _ensure_bank(self, bank_id: str) -> None:
        if bank_id in self._banks_ready:
            return
        self.memory.create_bank(bank_id, name=bank_id)
        for predicate in self.manifest.predicate_definitions:
            self._define_predicate(bank_id, predicate)
        self._banks_ready.add(bank_id)

    def _define_predicate(self, bank_id: str, predicate: PredicateDefinition) -> None:
        if self.memory.predicates.resolve(bank_id, predicate.name) is not None:
            return
        self.memory.define_predicate(
            predicate.name,
            bank=bank_id,
            cardinality=predicate.cardinality,
            volatility=predicate.volatility,
            subject_type=predicate.subject_type,
            object_type=predicate.object_type,
        )


class ExternalGraphifyRuntime(MarkdownRuntime):
    arm_name = "graphify_compatible"

    def __init__(
        self,
        manifest: DogfoodManifest,
        *,
        max_items: int,
        max_tokens: int,
        command: Sequence[str],
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(manifest, max_items=max_items, max_tokens=max_tokens)
        if not command:
            raise ValueError("external command cannot be empty")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds

    def config(self) -> dict[str, Any]:
        config = super().config()
        config["command"] = list(self.command)
        return config

    def run_query(self, step: QueryStep) -> RecallTrace:
        max_items, max_tokens = self._effective_limits(
            max_items=step.max_items,
            max_tokens=step.max_tokens,
        )
        return self._subprocess_search(
            bank_id=step.bank_id,
            as_of=step.at,
            question=step.question,
            max_items=max_items,
            max_tokens=max_tokens,
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
        )

    def run_attempt_query(self, step: AttemptStep) -> RecallTrace:
        if not step.query:
            return RecallTrace(
                (),
                0.0,
                0,
                step.tool_calls,
                step.retries,
                step.estimated_cost_usd,
            )
        max_items, max_tokens = self._effective_limits(
            max_items=step.max_items,
            max_tokens=step.max_tokens,
        )
        return self._subprocess_search(
            bank_id=step.bank_id,
            as_of=step.at,
            question=step.query,
            max_items=max_items,
            max_tokens=max_tokens,
            tool_calls=step.tool_calls,
            retries=step.retries,
            estimated_cost_usd=step.estimated_cost_usd,
        )

    def _subprocess_search(
        self,
        *,
        bank_id: str,
        as_of: str,
        question: str,
        max_items: int,
        max_tokens: int,
        tool_calls: int,
        retries: int,
        estimated_cost_usd: float,
    ) -> RecallTrace:
        visible = [item for item in self._items if item.bank_id == bank_id and item.at <= as_of]
        by_id = {item.context_id: item for item in visible}
        request = {
            "protocol": "memorygraph.dogfood.external/v1",
            "bank_id": bank_id,
            "as_of": as_of,
            "query": question,
            "max_items": max_items,
            "events": [
                {
                    "event_id": item.context_id,
                    "kind": item.metadata.get("kind", "note"),
                    "at": item.at,
                    "content": item.content,
                    "metadata": item.metadata,
                }
                for item in visible
            ],
        }
        started = perf_counter()
        completed = subprocess.run(
            self.command,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        latency_ms = (perf_counter() - started) * 1000.0
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise RuntimeError(f"graphify-compatible bridge failed: {detail}")
        response = json.loads(completed.stdout)
        event_ids = response.get("event_ids", [])
        if not isinstance(event_ids, list) or not all(isinstance(item, str) for item in event_ids):
            raise RuntimeError("graphify-compatible bridge returned invalid event_ids")
        selected = _select_items(
            [by_id[event_id] for event_id in event_ids if event_id in by_id],
            max_items=max_items,
            max_tokens=max_tokens,
        )
        return RecallTrace(
            items=selected,
            latency_ms=latency_ms,
            token_estimate=sum(len(item.content.split()) for item in selected),
            tool_calls=tool_calls,
            retries=retries,
            estimated_cost_usd=estimated_cost_usd,
        )


def make_arm_runtime(
    arm_name: str,
    manifest: DogfoodManifest,
    *,
    max_items: int,
    max_tokens: int,
    external_command: Sequence[str] | None = None,
) -> ArmRuntime:
    if arm_name == "no_memory":
        return NoMemoryRuntime(manifest, max_items=max_items, max_tokens=max_tokens)
    if arm_name == "markdown":
        return MarkdownRuntime(manifest, max_items=max_items, max_tokens=max_tokens)
    if arm_name == "memorygraph_graph_only":
        return MemoryGraphRuntime(
            manifest,
            max_items=max_items,
            max_tokens=max_tokens,
            dream_mode=None,
            arm_name=arm_name,
        )
    if arm_name == "memorygraph_gated_dream":
        return MemoryGraphRuntime(
            manifest,
            max_items=max_items,
            max_tokens=max_tokens,
            dream_mode=DreamRunMode.REVIEW_ONLY,
            arm_name=arm_name,
        )
    if arm_name == "memorygraph_always_dream":
        return MemoryGraphRuntime(
            manifest,
            max_items=max_items,
            max_tokens=max_tokens,
            dream_mode=DreamRunMode.APPLY,
            arm_name=arm_name,
        )
    if arm_name == "graphify_compatible":
        if not external_command:
            raise ValueError("graphify_compatible arm requires external_command")
        return ExternalGraphifyRuntime(
            manifest,
            max_items=max_items,
            max_tokens=max_tokens,
            command=external_command,
        )
    raise ValueError(f"unsupported arm: {arm_name}")


def _terms(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {token for token in normalized.split() if token}


def _select_items(
    items: Sequence[ContextItem],
    *,
    max_items: int,
    max_tokens: int,
) -> tuple[ContextItem, ...]:
    selected: list[ContextItem] = []
    used_tokens = 0
    for item in items[:max_items]:
        token_count = max(1, len(item.content.split()))
        if token_count > max_tokens - used_tokens:
            continue
        selected.append(item)
        used_tokens += token_count
    return tuple(selected)
