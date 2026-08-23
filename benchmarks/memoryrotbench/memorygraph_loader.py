"""Seed declarative MemoryRotBench fixtures into a real MemoryGraph instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memorygraph import MemoryGraph
    from memorygraph.storage import ClaimRecord, ObservationRecord
else:
    MemoryGraph = Any
    ClaimRecord = Any
    ObservationRecord = Any

from .scenario_loader import Scenario, ScenarioClaim, ScenarioEntity, ScenarioEvent


@dataclass(frozen=True)
class SeededScenario:
    scenario: Scenario
    claim_ids: dict[str, str]
    observation_ids: dict[str, str]


class MemoryGraphScenarioLoader:
    """Replay benchmark-declared mutations through the public embedded API."""

    def __init__(self, memory: MemoryGraph) -> None:
        self.memory = memory

    def seed_all(self, scenarios: list[Scenario]) -> tuple[SeededScenario, ...]:
        return tuple(self.seed(scenario) for scenario in scenarios)

    def seed(self, scenario: Scenario) -> SeededScenario:
        bank = self.memory.create_bank(scenario.bank_id, name=scenario.title)
        entities = {entity.entity_id: entity for entity in scenario.entities}
        claim_specs = {claim.claim_id: claim for claim in scenario.claims}
        observations = {
            event.event_id: self._observe(bank.id, scenario, event) for event in scenario.events
        }

        for claim in scenario.claims:
            if self.memory.predicates.resolve(bank.id, claim.predicate) is None:
                self.memory.define_predicate(
                    claim.predicate,
                    bank=bank.id,
                    cardinality=claim.cardinality,
                    volatility=claim.stability,
                )

        stored_claims: dict[str, ClaimRecord] = {}
        current_by_slot: dict[tuple[str, str], ClaimRecord] = {}
        for event in scenario.events:
            for expected in event.expected_outcomes:
                action = expected["action"]
                declared_claim_id = expected.get("claim_id")
                if action in {"historical_reference", "expire", "store_untrusted_content"}:
                    continue
                if declared_claim_id is None:
                    continue
                claim = claim_specs[declared_claim_id]
                observation = observations[event.event_id]
                slot = (claim.subject, claim.predicate)

                if action in {"assert", "coexists"}:
                    stored = self._assert(
                        bank.id,
                        claim,
                        entities,
                        observation,
                        event,
                    )
                elif action == "confirm":
                    stored = stored_claims[declared_claim_id]
                    self.memory.confirm_claim(
                        stored.id,
                        bank=bank.id,
                        observation_id=observation.id,
                        known_at=event.at,
                    )
                elif action in {"supersede", "correction"}:
                    prior = current_by_slot[slot]
                    object_value, object_type = _object_value(claim, entities)
                    stored = self.memory.supersede_claim(
                        prior.id,
                        bank=bank.id,
                        object=object_value,
                        object_kind=claim.object_kind,
                        object_type=object_type,
                        observation_id=observation.id,
                        valid_from=claim.valid_from,
                        valid_to=claim.valid_to,
                        known_at=event.at,
                        rationale=f"MemoryRotBench {action}",
                    )
                elif action == "contest":
                    prior = current_by_slot[slot]
                    object_value, object_type = _object_value(claim, entities)
                    stored = self.memory.contradict_claim(
                        prior.id,
                        bank=bank.id,
                        object=object_value,
                        object_kind=claim.object_kind,
                        object_type=object_type,
                        observation_id=observation.id,
                        valid_from=claim.valid_from,
                        valid_to=claim.valid_to,
                        known_at=event.at,
                        rationale="MemoryRotBench equal-authority conflict",
                    )
                else:  # pragma: no cover - loader schema rejects unknown actions
                    raise ValueError(f"Unsupported benchmark action: {action}")

                stored_claims[declared_claim_id] = stored
                current_by_slot[slot] = stored

        return SeededScenario(
            scenario=scenario,
            claim_ids={key: value.id for key, value in stored_claims.items()},
            observation_ids={key: value.id for key, value in observations.items()},
        )

    def _observe(
        self,
        bank_id: str,
        scenario: Scenario,
        event: ScenarioEvent,
    ) -> ObservationRecord:
        return self.memory.observe(
            event.content,
            bank=bank_id,
            source_key=event.event_id,
            kind="message" if event.kind == "message" else "file",
            actor_type=event.actor if event.actor in {"user", "tool"} else "external",
            effective_at=event.effective_at,
            trust_class=_trust_class(event),
            metadata={"scenario_id": scenario.scenario_id},
            observed_at=event.at,
        )

    def _assert(
        self,
        bank_id: str,
        claim: ScenarioClaim,
        entities: dict[str, ScenarioEntity],
        observation: ObservationRecord,
        event: ScenarioEvent,
    ) -> ClaimRecord:
        subject = entities[claim.subject]
        object_value, object_type = _object_value(claim, entities)
        initial_valid_to = (
            None if claim.lifecycle in {"historical", "retracted"} else claim.valid_to
        )
        return self.memory.assert_claim(
            bank=bank_id,
            subject=subject.canonical_name,
            subject_type=subject.entity_type,
            predicate=claim.predicate,
            object=object_value,
            object_kind=claim.object_kind,
            object_type=object_type,
            observation_id=observation.id,
            valid_from=claim.valid_from,
            valid_to=initial_valid_to,
            known_at=event.at,
        )


def _object_value(
    claim: ScenarioClaim,
    entities: dict[str, ScenarioEntity],
) -> tuple[str, str]:
    if claim.object_kind != "entity":
        return claim.object_value, "value"
    entity = entities[claim.object_value]
    return entity.canonical_name, entity.entity_type


def _trust_class(event: ScenarioEvent) -> str:
    if event.trust == "untrusted":
        return "untrusted"
    if event.actor == "user":
        return "owner_explicit"
    return "authoritative_source"
