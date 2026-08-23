"""Versioned loader for DreamRuntime chaos/acceptance fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dream_contracts import DreamProposal, EvidenceSpan

CHAOS_SCHEMA_VERSION = "memoryrotbench.chaos_case/v1"
ALLOWED_STEP_TYPES = {
    "capture_snapshot",
    "process_proposal",
    "rollback",
    "delete_evidence",
    "refresh_artifact",
    "assert_snapshot",
}


class ChaosCaseValidationError(ValueError):
    """Raised when a chaos-case document fails validation."""


def _expect_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChaosCaseValidationError(f"{field_name} must be an object")
    return value


def _expect_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ChaosCaseValidationError(f"{field_name} must be an array")
    return value


def _expect_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ChaosCaseValidationError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ChaosObservation:
    observation_id: str
    content: str


@dataclass(frozen=True)
class ChaosStep:
    step_id: str
    step_type: str
    label: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ChaosSnapshotExpectation:
    version: int | None
    active_claim_ids: tuple[str, ...] | None
    evidence_ids: tuple[str, ...] | None
    current_view: tuple[tuple[str, str], ...] | None
    artifact_ids: tuple[str, ...] | None
    committed_run_ids: tuple[str, ...] | None
    history_claim_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ChaosCase:
    schema_version: str
    case_id: str
    title: str
    acceptance_case: int
    description: str
    observations: tuple[ChaosObservation, ...]
    steps: tuple[ChaosStep, ...]
    final_assertion: ChaosSnapshotExpectation | None
    raw: dict[str, Any]


def discover_chaos_case_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    return sorted(path for path in root_path.glob("*.json") if not path.name.startswith("."))


def load_chaos_cases(root: str | Path) -> list[ChaosCase]:
    return [load_chaos_case(path) for path in discover_chaos_case_files(root)]


def load_chaos_case(path: str | Path) -> ChaosCase:
    path_obj = Path(path)
    with path_obj.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    validate_chaos_case_document(document)
    return _build_case(document)


def validate_chaos_case_document(document: dict[str, Any]) -> None:
    top = _expect_mapping(document, "chaos_case")
    schema_version = _expect_str(top.get("schema_version"), "schema_version")
    if schema_version != CHAOS_SCHEMA_VERSION:
        raise ChaosCaseValidationError(
            f"schema_version must be {CHAOS_SCHEMA_VERSION}, got {schema_version}"
        )
    _expect_str(top.get("case_id"), "case_id")
    _expect_str(top.get("title"), "title")
    acceptance_case = top.get("acceptance_case")
    if not isinstance(acceptance_case, int) or not 9 <= acceptance_case <= 15:
        raise ChaosCaseValidationError("acceptance_case must be an integer from 9 to 15")
    _expect_str(top.get("description"), "description")

    observations = _expect_list(top.get("observations"), "observations")
    seen_observations: set[str] = set()
    for index, observation in enumerate(observations):
        obs_map = _expect_mapping(observation, f"observations[{index}]")
        observation_id = _expect_str(
            obs_map.get("observation_id"), f"observations[{index}].observation_id"
        )
        if observation_id in seen_observations:
            raise ChaosCaseValidationError(f"duplicate observation_id: {observation_id}")
        seen_observations.add(observation_id)
        _expect_str(obs_map.get("content"), f"observations[{index}].content")

    steps = _expect_list(top.get("steps"), "steps")
    if not steps:
        raise ChaosCaseValidationError("steps must not be empty")
    seen_step_ids: set[str] = set()
    seen_labels: set[str] = set()
    for index, step in enumerate(steps):
        step_map = _expect_mapping(step, f"steps[{index}]")
        step_id = _expect_str(step_map.get("step_id"), f"steps[{index}].step_id")
        if step_id in seen_step_ids:
            raise ChaosCaseValidationError(f"duplicate step_id: {step_id}")
        seen_step_ids.add(step_id)
        step_type = _expect_str(step_map.get("type"), f"steps[{index}].type")
        if step_type not in ALLOWED_STEP_TYPES:
            raise ChaosCaseValidationError(f"unsupported step type: {step_type}")
        label = step_map.get("label")
        if label is not None:
            label = _expect_str(label, f"steps[{index}].label")
            if label in seen_labels:
                raise ChaosCaseValidationError(f"duplicate label: {label}")
            seen_labels.add(label)
        if step_type == "process_proposal":
            proposal = _expect_mapping(step_map.get("proposal"), f"steps[{index}].proposal")
            _expect_str(proposal.get("proposal_id"), f"steps[{index}].proposal.proposal_id")
            _expect_str(
                proposal.get("idempotency_key"),
                f"steps[{index}].proposal.idempotency_key",
            )
            _expect_str(proposal.get("claim_id"), f"steps[{index}].proposal.claim_id")
            _expect_str(proposal.get("subject"), f"steps[{index}].proposal.subject")
            _expect_str(proposal.get("predicate"), f"steps[{index}].proposal.predicate")
            _expect_str(proposal.get("object_value"), f"steps[{index}].proposal.object_value")
            spans = _expect_list(
                proposal.get("evidence_spans"), f"steps[{index}].proposal.evidence_spans"
            )
            if not spans:
                raise ChaosCaseValidationError("proposal.evidence_spans must not be empty")
            for span_index, span in enumerate(spans):
                span_map = _expect_mapping(
                    span, f"steps[{index}].proposal.evidence_spans[{span_index}]"
                )
                obs_id = _expect_str(
                    span_map.get("observation_id"),
                    f"steps[{index}].proposal.evidence_spans[{span_index}].observation_id",
                )
                if obs_id not in seen_observations:
                    raise ChaosCaseValidationError(
                        f"proposal references unknown observation: {obs_id}"
                    )
                if not isinstance(span_map.get("start"), int) or not isinstance(
                    span_map.get("end"), int
                ):
                    raise ChaosCaseValidationError("evidence span start/end must be integers")
            _validate_expected(step_map, index)
        elif step_type == "rollback":
            if "run_ref" not in step_map and "run_id" not in step_map:
                raise ChaosCaseValidationError("rollback step requires run_ref or run_id")
            _validate_expected(step_map, index)
        elif step_type == "delete_evidence":
            observation_id = _expect_str(
                step_map.get("observation_id"), f"steps[{index}].observation_id"
            )
            if observation_id not in seen_observations:
                raise ChaosCaseValidationError(f"unknown observation_id: {observation_id}")
        elif step_type == "refresh_artifact":
            _expect_str(step_map.get("artifact_id"), f"steps[{index}].artifact_id")
            _expect_str(step_map.get("body"), f"steps[{index}].body")
            source_claim_ids = _expect_list(
                step_map.get("source_claim_ids", []), f"steps[{index}].source_claim_ids"
            )
            if not all(isinstance(item, str) and item for item in source_claim_ids):
                raise ChaosCaseValidationError("source_claim_ids must contain strings")
            source_artifact_refs = _expect_list(
                step_map.get("source_artifact_refs", []),
                f"steps[{index}].source_artifact_refs",
            )
            if not all(isinstance(item, str) and item for item in source_artifact_refs):
                raise ChaosCaseValidationError("source_artifact_refs must contain strings")
            _validate_expected(step_map, index)
        elif step_type == "assert_snapshot":
            _validate_snapshot_expectation(step_map.get("expect"), f"steps[{index}].expect")

    final_assertion = top.get("final_assertion")
    if final_assertion is not None:
        _validate_snapshot_expectation(final_assertion, "final_assertion")


def _validate_expected(step_map: dict[str, Any], index: int) -> None:
    expected = _expect_mapping(step_map.get("expect"), f"steps[{index}].expect")
    status = expected.get("status")
    exception = expected.get("exception")
    if status is None and exception is None:
        raise ChaosCaseValidationError("expect must include status or exception")
    if status is not None:
        _expect_str(status, f"steps[{index}].expect.status")
    if exception is not None:
        _expect_str(exception, f"steps[{index}].expect.exception")


def _validate_snapshot_expectation(value: Any, field_name: str) -> None:
    expect = _expect_mapping(value, field_name)
    for key in (
        "active_claim_ids",
        "evidence_ids",
        "artifact_ids",
        "committed_run_ids",
        "history_claim_ids",
    ):
        if key in expect:
            values = _expect_list(expect[key], f"{field_name}.{key}")
            if not all(isinstance(item, str) and item for item in values):
                raise ChaosCaseValidationError(f"{field_name}.{key} must contain strings")
    if "current_view" in expect:
        current_view = _expect_list(expect["current_view"], f"{field_name}.current_view")
        for index, item in enumerate(current_view):
            pair = _expect_list(item, f"{field_name}.current_view[{index}]")
            if len(pair) != 2 or not all(isinstance(v, str) and v for v in pair):
                raise ChaosCaseValidationError(
                    f"{field_name}.current_view entries must be [slot, claim_id]"
                )
    if "version" in expect and not isinstance(expect["version"], int):
        raise ChaosCaseValidationError(f"{field_name}.version must be an integer")


def _build_case(document: dict[str, Any]) -> ChaosCase:
    return ChaosCase(
        schema_version=document["schema_version"],
        case_id=document["case_id"],
        title=document["title"],
        acceptance_case=document["acceptance_case"],
        description=document["description"],
        observations=tuple(
            ChaosObservation(
                observation_id=observation["observation_id"],
                content=observation["content"],
            )
            for observation in document["observations"]
        ),
        steps=tuple(
            ChaosStep(
                step_id=step["step_id"],
                step_type=step["type"],
                label=step.get("label"),
                raw=step,
            )
            for step in document["steps"]
        ),
        final_assertion=_build_snapshot_expectation(document.get("final_assertion")),
        raw=document,
    )


def _build_snapshot_expectation(
    value: dict[str, Any] | None,
) -> ChaosSnapshotExpectation | None:
    if value is None:
        return None
    return ChaosSnapshotExpectation(
        version=value.get("version"),
        active_claim_ids=_tuple_or_none(value.get("active_claim_ids")),
        evidence_ids=_tuple_or_none(value.get("evidence_ids")),
        current_view=tuple(tuple(item) for item in value["current_view"])
        if "current_view" in value
        else None,
        artifact_ids=_tuple_or_none(value.get("artifact_ids")),
        committed_run_ids=_tuple_or_none(value.get("committed_run_ids")),
        history_claim_ids=_tuple_or_none(value.get("history_claim_ids")),
    )


def _tuple_or_none(value: list[str] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(value)


def build_proposal(step: ChaosStep) -> DreamProposal:
    proposal = step.raw["proposal"]
    return DreamProposal(
        proposal_id=proposal["proposal_id"],
        idempotency_key=proposal["idempotency_key"],
        claim_id=proposal["claim_id"],
        subject=proposal["subject"],
        predicate=proposal["predicate"],
        object_value=proposal["object_value"],
        evidence_spans=tuple(
            EvidenceSpan(
                observation_id=span["observation_id"],
                start=span["start"],
                end=span["end"],
            )
            for span in proposal["evidence_spans"]
        ),
        precondition_version=proposal.get("precondition_version"),
        replaces_claim_id=proposal.get("replaces_claim_id"),
    )
