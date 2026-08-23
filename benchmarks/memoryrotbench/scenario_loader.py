"""Standard-library scenario loading and validation for MemoryRotBench."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "memoryrotbench.scenario/v1"
ALLOWED_CATEGORIES = {
    "state_replacement",
    "historical_mention_trap",
    "multi_valued_truth",
    "negation_and_correction",
    "delayed_reporting",
    "conflicting_sources",
    "stable_facts",
    "expiring_state",
    "duplicate_paraphrase",
    "entity_ambiguity",
    "procedural_learning",
    "premise_awareness",
    "abstention",
    "summary_drift",
    "poisoning_and_injection",
    "isolation",
    "deletion_and_revocation",
}
ALLOWED_OUTCOME_TYPES = {"answer", "abstain", "contested"}
ALLOWED_EVENT_ACTIONS = {
    "assert",
    "confirm",
    "supersede",
    "correction",
    "historical_reference",
    "coexists",
    "contest",
    "expire",
    "store_untrusted_content",
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario document fails validation."""


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScenarioValidationError(f"{field_name} is not valid ISO-8601 UTC: {value}") from exc


def _expect_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioValidationError(f"{field_name} must be an object")
    return value


def _expect_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScenarioValidationError(f"{field_name} must be an array")
    return value


def _expect_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScenarioValidationError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ScenarioEntity:
    entity_id: str
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioClaim:
    claim_id: str
    subject: str
    predicate: str
    object_kind: str
    object_value: str
    cardinality: str
    stability: str
    valid_from: str | None
    valid_to: str | None
    lifecycle: str
    summary: str
    evidence_event_ids: tuple[str, ...]
    source_authority: str


@dataclass(frozen=True)
class ScenarioEvent:
    event_id: str
    at: str
    effective_at: str | None
    kind: str
    actor: str
    source_type: str
    trust: str
    authority: str
    content: str
    expected_outcomes: tuple[dict[str, str], ...]

    @property
    def timestamp(self) -> datetime:
        return _parse_timestamp(self.at, f"event {self.event_id}.at")


@dataclass(frozen=True)
class ScenarioQuery:
    query_id: str
    after_event: str
    question: str
    outcome_type: str
    answer: str | None
    acceptable_answers: tuple[str, ...]
    required_claim_ids: tuple[str, ...]
    forbidden_current_claim_ids: tuple[str, ...]
    required_evidence_event_ids: tuple[str, ...]
    forbidden_bank_ids: tuple[str, ...]
    forbidden_answer_fragments: tuple[str, ...]


@dataclass(frozen=True)
class Scenario:
    schema_version: str
    scenario_id: str
    title: str
    category: str
    bank_id: str
    description: str
    acceptance_cases: tuple[int, ...]
    entities: tuple[ScenarioEntity, ...]
    claims: tuple[ScenarioClaim, ...]
    events: tuple[ScenarioEvent, ...]
    queries: tuple[ScenarioQuery, ...]
    raw: dict[str, Any]

    def event_by_id(self, event_id: str) -> ScenarioEvent:
        for event in self.events:
            if event.event_id == event_id:
                return event
        raise KeyError(event_id)

    def query_by_id(self, query_id: str) -> ScenarioQuery:
        for query in self.queries:
            if query.query_id == query_id:
                return query
        raise KeyError(query_id)

    def checkpoint_time(self, query: ScenarioQuery) -> datetime:
        return self.event_by_id(query.after_event).timestamp


def discover_scenario_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    return sorted(path for path in root_path.glob("*.json") if not path.name.startswith("."))


def load_scenarios(root: str | Path) -> list[Scenario]:
    return [load_scenario(path) for path in discover_scenario_files(root)]


def load_scenario(path: str | Path) -> Scenario:
    path_obj = Path(path)
    with path_obj.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    validate_scenario_document(document)
    return _build_scenario(document)


def validate_scenario_document(document: dict[str, Any]) -> None:
    top = _expect_mapping(document, "scenario")
    schema_version = _expect_str(top.get("schema_version"), "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ScenarioValidationError(
            f"schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        )

    _expect_str(top.get("scenario_id"), "scenario_id")
    _expect_str(top.get("title"), "title")
    category = _expect_str(top.get("category"), "category")
    if category not in ALLOWED_CATEGORIES:
        raise ScenarioValidationError(f"category is not allowed: {category}")
    _expect_str(top.get("bank_id"), "bank_id")
    _expect_str(top.get("description"), "description")

    acceptance_cases = _expect_list(top.get("acceptance_cases"), "acceptance_cases")
    if not acceptance_cases or not all(isinstance(item, int) for item in acceptance_cases):
        raise ScenarioValidationError("acceptance_cases must be a non-empty array of integers")

    entities = _expect_list(top.get("entities"), "entities")
    claims = _expect_list(top.get("claims"), "claims")
    events = _expect_list(top.get("events"), "events")
    queries = _expect_list(top.get("queries"), "queries")

    entity_ids: set[str] = set()
    for index, entity in enumerate(entities):
        entity_map = _expect_mapping(entity, f"entities[{index}]")
        entity_id = _expect_str(entity_map.get("entity_id"), f"entities[{index}].entity_id")
        if entity_id in entity_ids:
            raise ScenarioValidationError(f"duplicate entity_id: {entity_id}")
        entity_ids.add(entity_id)
        _expect_str(entity_map.get("canonical_name"), f"entities[{index}].canonical_name")
        _expect_str(entity_map.get("type"), f"entities[{index}].type")
        aliases = _expect_list(entity_map.get("aliases", []), f"entities[{index}].aliases")
        if not all(isinstance(alias, str) and alias for alias in aliases):
            raise ScenarioValidationError(f"entities[{index}].aliases must contain strings")

    event_ids: set[str] = set()
    prior_event_time: datetime | None = None
    for index, event in enumerate(events):
        event_map = _expect_mapping(event, f"events[{index}]")
        event_id = _expect_str(event_map.get("event_id"), f"events[{index}].event_id")
        if event_id in event_ids:
            raise ScenarioValidationError(f"duplicate event_id: {event_id}")
        event_ids.add(event_id)
        event_time = _parse_timestamp(
            _expect_str(event_map.get("at"), f"events[{index}].at"), f"events[{index}].at"
        )
        if prior_event_time and event_time < prior_event_time:
            raise ScenarioValidationError("events must be chronological")
        prior_event_time = event_time
        effective_at = event_map.get("effective_at")
        if effective_at is not None:
            _parse_timestamp(
                _expect_str(effective_at, f"events[{index}].effective_at"),
                f"events[{index}].effective_at",
            )
        _expect_str(event_map.get("kind"), f"events[{index}].kind")
        _expect_str(event_map.get("actor"), f"events[{index}].actor")
        _expect_str(event_map.get("source_type"), f"events[{index}].source_type")
        _expect_str(event_map.get("trust"), f"events[{index}].trust")
        _expect_str(event_map.get("authority"), f"events[{index}].authority")
        _expect_str(event_map.get("content"), f"events[{index}].content")
        expected_outcomes = _expect_list(
            event_map.get("expected_outcomes", []),
            f"events[{index}].expected_outcomes",
        )
        for outcome_index, expected in enumerate(expected_outcomes):
            expected_map = _expect_mapping(
                expected, f"events[{index}].expected_outcomes[{outcome_index}]"
            )
            action = _expect_str(
                expected_map.get("action"),
                f"events[{index}].expected_outcomes[{outcome_index}].action",
            )
            if action not in ALLOWED_EVENT_ACTIONS:
                raise ScenarioValidationError(f"unsupported event action: {action}")
            claim_id = expected_map.get("claim_id")
            if claim_id is not None:
                _expect_str(
                    claim_id,
                    f"events[{index}].expected_outcomes[{outcome_index}].claim_id",
                )

    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        claim_map = _expect_mapping(claim, f"claims[{index}]")
        claim_id = _expect_str(claim_map.get("claim_id"), f"claims[{index}].claim_id")
        if claim_id in claim_ids:
            raise ScenarioValidationError(f"duplicate claim_id: {claim_id}")
        claim_ids.add(claim_id)
        subject = _expect_str(claim_map.get("subject"), f"claims[{index}].subject")
        if subject not in entity_ids:
            raise ScenarioValidationError(f"claim subject does not reference an entity: {subject}")
        _expect_str(claim_map.get("predicate"), f"claims[{index}].predicate")
        object_map = _expect_mapping(claim_map.get("object"), f"claims[{index}].object")
        _expect_str(object_map.get("kind"), f"claims[{index}].object.kind")
        object_value = _expect_str(object_map.get("value"), f"claims[{index}].object.value")
        if object_map["kind"] == "entity" and object_value not in entity_ids:
            raise ScenarioValidationError(
                f"entity-valued claim object does not reference an entity: {object_value}"
            )
        cardinality = _expect_str(claim_map.get("cardinality"), f"claims[{index}].cardinality")
        if cardinality not in {"one", "many"}:
            raise ScenarioValidationError("claim cardinality must be one or many")
        stability = _expect_str(claim_map.get("stability"), f"claims[{index}].stability")
        if stability not in {"immutable", "durable", "volatile", "ephemeral"}:
            raise ScenarioValidationError(f"unsupported claim stability: {stability}")
        valid_from = claim_map.get("valid_from")
        valid_to = claim_map.get("valid_to")
        if valid_from is not None:
            _parse_timestamp(
                _expect_str(valid_from, f"claims[{index}].valid_from"),
                f"claims[{index}].valid_from",
            )
        if valid_to is not None:
            _parse_timestamp(
                _expect_str(valid_to, f"claims[{index}].valid_to"), f"claims[{index}].valid_to"
            )
        lifecycle = _expect_str(claim_map.get("lifecycle"), f"claims[{index}].lifecycle")
        if lifecycle not in {"current", "historical", "contested", "expired", "retracted"}:
            raise ScenarioValidationError(f"unsupported claim lifecycle: {lifecycle}")
        _expect_str(claim_map.get("summary"), f"claims[{index}].summary")
        evidence_event_ids = _expect_list(
            claim_map.get("evidence_event_ids"),
            f"claims[{index}].evidence_event_ids",
        )
        if not evidence_event_ids or not all(
            isinstance(event_id, str) for event_id in evidence_event_ids
        ):
            raise ScenarioValidationError(
                f"claims[{index}].evidence_event_ids must contain event IDs"
            )
        unknown_event_ids = sorted(set(evidence_event_ids) - event_ids)
        if unknown_event_ids:
            raise ScenarioValidationError(
                f"claim references unknown evidence events: {', '.join(unknown_event_ids)}"
            )
        _expect_str(claim_map.get("source_authority"), f"claims[{index}].source_authority")

    for index, query in enumerate(queries):
        query_map = _expect_mapping(query, f"queries[{index}]")
        _expect_str(query_map.get("query_id"), f"queries[{index}].query_id")
        after_event = _expect_str(query_map.get("after_event"), f"queries[{index}].after_event")
        if after_event not in event_ids:
            raise ScenarioValidationError(f"query references unknown after_event: {after_event}")
        _expect_str(query_map.get("question"), f"queries[{index}].question")
        outcome_map = _expect_mapping(query_map.get("outcome"), f"queries[{index}].outcome")
        outcome_type = _expect_str(outcome_map.get("type"), f"queries[{index}].outcome.type")
        if outcome_type not in ALLOWED_OUTCOME_TYPES:
            raise ScenarioValidationError(f"unsupported query outcome type: {outcome_type}")
        if outcome_type == "answer":
            _expect_str(outcome_map.get("answer"), f"queries[{index}].outcome.answer")
        for field_name in (
            "acceptable_answers",
            "required_claim_ids",
            "forbidden_current_claim_ids",
            "required_evidence_event_ids",
            "forbidden_bank_ids",
            "forbidden_answer_fragments",
        ):
            values = _expect_list(query_map.get(field_name, []), f"queries[{index}].{field_name}")
            if not all(isinstance(value, str) and value for value in values):
                raise ScenarioValidationError(f"queries[{index}].{field_name} must contain strings")

        claim_refs = set(query_map.get("required_claim_ids", [])) | set(
            query_map.get("forbidden_current_claim_ids", [])
        )
        unknown_claims = sorted(claim_refs - claim_ids)
        if unknown_claims:
            raise ScenarioValidationError(
                f"query references unknown claims: {', '.join(unknown_claims)}"
            )
        unknown_evidence = sorted(set(query_map.get("required_evidence_event_ids", [])) - event_ids)
        if unknown_evidence:
            raise ScenarioValidationError(
                f"query references unknown evidence events: {', '.join(unknown_evidence)}"
            )


def _build_scenario(document: dict[str, Any]) -> Scenario:
    entities = tuple(
        ScenarioEntity(
            entity_id=entity["entity_id"],
            canonical_name=entity["canonical_name"],
            entity_type=entity["type"],
            aliases=tuple(entity.get("aliases", [])),
        )
        for entity in document["entities"]
    )
    claims = tuple(
        ScenarioClaim(
            claim_id=claim["claim_id"],
            subject=claim["subject"],
            predicate=claim["predicate"],
            object_kind=claim["object"]["kind"],
            object_value=claim["object"]["value"],
            cardinality=claim["cardinality"],
            stability=claim["stability"],
            valid_from=claim.get("valid_from"),
            valid_to=claim.get("valid_to"),
            lifecycle=claim["lifecycle"],
            summary=claim["summary"],
            evidence_event_ids=tuple(claim["evidence_event_ids"]),
            source_authority=claim["source_authority"],
        )
        for claim in document["claims"]
    )
    events = tuple(
        ScenarioEvent(
            event_id=event["event_id"],
            at=event["at"],
            effective_at=event.get("effective_at"),
            kind=event["kind"],
            actor=event["actor"],
            source_type=event["source_type"],
            trust=event["trust"],
            authority=event["authority"],
            content=event["content"],
            expected_outcomes=tuple(event.get("expected_outcomes", [])),
        )
        for event in document["events"]
    )
    queries = tuple(
        ScenarioQuery(
            query_id=query["query_id"],
            after_event=query["after_event"],
            question=query["question"],
            outcome_type=query["outcome"]["type"],
            answer=query["outcome"].get("answer"),
            acceptable_answers=tuple(query.get("acceptable_answers", [])),
            required_claim_ids=tuple(query.get("required_claim_ids", [])),
            forbidden_current_claim_ids=tuple(query.get("forbidden_current_claim_ids", [])),
            required_evidence_event_ids=tuple(query.get("required_evidence_event_ids", [])),
            forbidden_bank_ids=tuple(query.get("forbidden_bank_ids", [])),
            forbidden_answer_fragments=tuple(query.get("forbidden_answer_fragments", [])),
        )
        for query in document["queries"]
    )
    return Scenario(
        schema_version=document["schema_version"],
        scenario_id=document["scenario_id"],
        title=document["title"],
        category=document["category"],
        bank_id=document["bank_id"],
        description=document["description"],
        acceptance_cases=tuple(document["acceptance_cases"]),
        entities=entities,
        claims=claims,
        events=events,
        queries=queries,
        raw=document,
    )
