"""Durable embedded orchestration for the provider-neutral dream runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from memorygraph.adapters.storage_reader import StorageDomainReader
from memorygraph.domain import (
    ClaimHandle,
    ClaimTemplate,
    DraftClaim,
    PlannedEvidenceAttachment,
    TransitionPlan,
    plan_confirm,
    plan_contradict,
    plan_supersede,
)
from memorygraph.dream import (
    BatchCommitResult,
    ChallengeRequest,
    ChallengeResult,
    ClaimObjectCandidate,
    ClaimStatePrecondition,
    ClaimVersionToken,
    DreamAction,
    DreamActionKind,
    DreamProposal,
    DreamProposalValidator,
    DreamProvider,
    DreamRunMode,
    DreamRunReport,
    DreamRuntime,
    DreamTask,
    EvidenceSpanCandidate,
    EvidenceSpanCheck,
    ExistingIdempotencyRecord,
    ExtractedClaimCandidate,
    ExtractedEntityCandidate,
    ExtractionCandidateBatch,
    ExtractionResult,
    IdempotencyPrecondition,
    IdempotencyRecordState,
    ProposalCommitOutcome,
    ProposalCommitStatus,
    ProposalDisposition,
    ProposalPreconditions,
    ProviderCallTrace,
    ProviderOperation,
    SourceBundle,
    SourceChunk,
    SourceObservation,
    ValidatedProposal,
    ValidationContext,
    fingerprint_for_value,
)
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    EvidenceExplicitness,
    PredicateDefinition,
)
from memorygraph.storage import DreamRunRecord, DreamTaskRecord, ObservationRecord, transaction

if TYPE_CHECKING:
    from memorygraph.api import MemoryGraph


@dataclass(frozen=True, slots=True)
class ProposalMaterial:
    claim_candidate: ExtractedClaimCandidate
    subject: ExtractedEntityCandidate
    object_entity: ExtractedEntityCandidate | None
    evidence_by_id: Mapping[str, EvidenceSpanCandidate]
    observation_by_evidence_id: Mapping[str, SourceObservation]
    object_value: Any
    object_type: str


class MetadataDreamProvider(DreamProvider):
    """Deterministic reference provider reading candidates from observation metadata.

    It exists to exercise the complete runtime without network calls. Real model adapters
    produce the same schema and remain outside the trusted write path.
    """

    provider_name = "memorygraph.metadata"

    def extract(self, source_bundle: SourceBundle) -> ExtractionResult:
        entities: list[ExtractedEntityCandidate] = []
        claims: list[ExtractedClaimCandidate] = []
        warnings: list[str] = []
        for observation in source_bundle.observations:
            payload = observation.metadata.get("memorygraph")
            if not isinstance(payload, Mapping):
                continue
            local_entities: dict[str, ExtractedEntityCandidate] = {}
            for raw_entity in _mapping_items(payload.get("entities")):
                local_id = str(raw_entity["local_id"])
                name = str(raw_entity["name"])
                start, end = _span_or_find(raw_entity.get("evidence"), observation.content, name)
                entity = ExtractedEntityCandidate(
                    local_id=f"{observation.observation_id}:{local_id}",
                    name=name,
                    entity_type=str(raw_entity.get("type", "entity")),
                    description=_optional_string(raw_entity.get("description")),
                    evidence_span=EvidenceSpanCandidate(
                        candidate_id=f"{observation.observation_id}:entity:{local_id}",
                        observation_id=observation.observation_id,
                        start_offset=start,
                        end_offset=end,
                        excerpt=observation.content[start:end],
                    ),
                )
                entities.append(entity)
                local_entities[local_id] = entity

            for raw_claim in _mapping_items(payload.get("claims")):
                local_id = str(raw_claim["local_id"])
                subject_local_id = str(raw_claim["subject"])
                if subject_local_id not in local_entities:
                    warnings.append(
                        f"claim {local_id!r} references unknown local subject {subject_local_id!r}"
                    )
                    continue
                evidence_payloads = raw_claim.get("evidence")
                if not isinstance(evidence_payloads, list) or not evidence_payloads:
                    evidence_payloads = [{"start": 0, "end": len(observation.content)}]
                evidence: list[EvidenceSpanCandidate] = []
                for ordinal, raw_span in enumerate(evidence_payloads):
                    start, end = _span_or_find(raw_span, observation.content, None)
                    evidence.append(
                        EvidenceSpanCandidate(
                            candidate_id=(
                                f"{observation.observation_id}:claim:{local_id}:evidence:{ordinal}"
                            ),
                            observation_id=observation.observation_id,
                            start_offset=start,
                            end_offset=end,
                            excerpt=observation.content[start:end],
                        )
                    )
                raw_object = raw_claim["object"]
                if not isinstance(raw_object, Mapping):
                    raise ValueError(f"claim {local_id!r} object must be a mapping")
                object_kind = ClaimObjectKind(str(raw_object.get("kind", "string")))
                object_value = raw_object.get("value")
                if object_kind is ClaimObjectKind.ENTITY and str(object_value) in local_entities:
                    object_value = local_entities[str(object_value)].local_id
                claims.append(
                    ExtractedClaimCandidate(
                        local_id=f"{observation.observation_id}:{local_id}",
                        subject_local_id=local_entities[subject_local_id].local_id,
                        predicate=str(raw_claim["predicate"]),
                        object_candidate=ClaimObjectCandidate(
                            kind=object_kind,
                            value=object_value,
                        ),
                        polarity=ClaimPolarity(str(raw_claim.get("polarity", "positive"))),
                        explicitness=EvidenceExplicitness(
                            str(raw_claim.get("explicitness", "explicit"))
                        ),
                        evidence_spans=tuple(evidence),
                        extraction_confidence=float(raw_claim.get("confidence", 1.0)),
                        valid_from=_optional_datetime(raw_claim.get("valid_from")),
                        valid_to=_optional_datetime(raw_claim.get("valid_to")),
                    )
                )
        return ExtractionResult(
            candidates=ExtractionCandidateBatch(
                entities=tuple(entities),
                claims=tuple(claims),
                warnings=tuple(warnings),
            ),
            trace=ProviderCallTrace(
                operation=ProviderOperation.EXTRACT,
                provider_name=self.provider_name,
                provider_version="1",
                prompt_version="metadata-v1",
                metadata={"bundle_id": source_bundle.bundle_id},
            ),
        )

    def challenge(self, request: ChallengeRequest) -> ChallengeResult:
        return ChallengeResult(
            objections=(),
            trace=ProviderCallTrace(
                operation=ProviderOperation.CHALLENGE,
                provider_name=self.provider_name,
                provider_version="1",
                prompt_version="metadata-v1",
                metadata={"proposal_id": request.proposal_id},
            ),
        )


class EmbeddedDreamComponents:
    """Storage-backed context, proposal pipeline, and atomic committer."""

    def __init__(self, memory: MemoryGraph) -> None:
        self.memory = memory
        self.reader = StorageDomainReader(memory.connection)
        self.materials: dict[str, ProposalMaterial] = {}
        self.bundles: dict[str, SourceBundle] = {}

    def build_source_bundle(self, task: DreamTask) -> SourceBundle:
        records = self._observations_for_task(task)
        observations = tuple(_source_observation(record) for record in records)
        bank = self.memory.get_bank(task.bank_id)
        bundle = SourceBundle(
            bundle_id=f"bundle:{task.task_id}",
            bank_id=bank.id,
            reason=task.reason,
            priority=task.priority,
            observations=observations,
            mission=bank.mission,
            metadata={"run_id": task.run_id, "task_id": task.task_id},
        )
        self.bundles[bundle.bundle_id] = bundle
        return bundle

    def proposals_from_extraction(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        extraction: ExtractionResult,
    ) -> Sequence[DreamProposal]:
        entities = {item.local_id: item for item in extraction.candidates.entities}
        observations = {
            item.observation_id: item for item in source_bundle.observations
        }
        proposals: list[DreamProposal] = []
        for candidate in extraction.candidates.claims:
            subject = entities.get(candidate.subject_local_id)
            if subject is None:
                continue
            object_entity = None
            object_value = candidate.object_candidate.value
            object_type = "value"
            if candidate.object_candidate.kind is ClaimObjectKind.ENTITY:
                object_entity = entities.get(str(object_value))
                if object_entity is None:
                    continue
                object_value = object_entity.name
                object_type = object_entity.entity_type
            evidence_by_id = {item.candidate_id: item for item in candidate.evidence_spans}
            observation_by_evidence_id = {
                item.candidate_id: observations[item.observation_id]
                for item in candidate.evidence_spans
                if item.observation_id in observations
            }
            if len(observation_by_evidence_id) != len(evidence_by_id):
                continue
            proposal = self._build_proposal(
                task=task,
                source_bundle=source_bundle,
                candidate=candidate,
                subject=subject,
                object_entity=object_entity,
                object_value=object_value,
                object_type=object_type,
                evidence_by_id=evidence_by_id,
                observation_by_evidence_id=observation_by_evidence_id,
            )
            self.materials[proposal.id] = ProposalMaterial(
                claim_candidate=candidate,
                subject=subject,
                object_entity=object_entity,
                evidence_by_id=evidence_by_id,
                observation_by_evidence_id=observation_by_evidence_id,
                object_value=object_value,
                object_type=object_type,
            )
            proposals.append(proposal)
        proposals = self._route_ambiguous_slots_to_review(proposals)
        for proposal in proposals:
            self._persist_proposal(task, proposal)
        return proposals

    def should_challenge(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> bool:
        del task, source_bundle
        material = self.materials[proposal.id]
        return any(
            observation.trust_class in {"untrusted", "model_generated"}
            for observation in material.observation_by_evidence_id.values()
        ) or proposal.action.predicate_definition.sensitivity == "protected"

    def build_challenge_request(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> ChallengeRequest:
        del task
        return ChallengeRequest(
            proposal_id=proposal.id,
            bank_id=proposal.bank_id,
            source_bundle_id=source_bundle.bundle_id,
            proposal=proposal,
            evidence_candidate_ids=proposal.action.referenced_evidence_ids(),
        )

    def apply_challenge_result(
        self,
        proposal: DreamProposal,
        challenge_result: ChallengeResult,
    ) -> DreamProposal:
        return replace(proposal, challenger_objections=challenge_result.objections)

    def build_validation_context(
        self,
        task: DreamTask,
        proposal: DreamProposal,
    ) -> ValidationContext:
        del task
        material = self.materials[proposal.id]
        evidence_checks = {
            candidate_id: _validate_evidence_candidate(
                proposal.bank_id,
                span,
                material.observation_by_evidence_id[candidate_id],
            )
            for candidate_id, span in material.evidence_by_id.items()
        }
        current_tokens: dict[str, ClaimVersionToken] = {}
        for claim_id in proposal.action.referenced_claim_ids():
            claim = self.reader.get_claim(proposal.bank_id, claim_id)
            if claim is not None:
                current_tokens[claim_id] = ClaimVersionToken.from_claim(claim)
        existing = None
        idempotency = proposal.preconditions.idempotency
        if idempotency is not None:
            event = self.memory.events.get_by_idempotency_key(proposal.bank_id, idempotency.key)
            if event is not None:
                existing = ExistingIdempotencyRecord(
                    bank_id=proposal.bank_id,
                    key=idempotency.key,
                    fingerprint=str(event.payload.get("fingerprint", "")),
                    state=IdempotencyRecordState.COMMITTED,
                )
        return ValidationContext(
            current_event_watermark=self.memory.events.current_watermark(proposal.bank_id),
            evidence_checks=evidence_checks,
            current_claim_tokens=current_tokens,
            existing_idempotency_record=existing,
        )

    def commit_batch(
        self,
        task: DreamTask,
        proposals: Sequence[ValidatedProposal],
    ) -> BatchCommitResult:
        validator = DreamProposalValidator()
        outcomes: list[ProposalCommitOutcome] = []
        with transaction(self.memory.connection):
            watermark_before = self.memory.events.current_watermark(task.bank_id)
            revalidated: list[ValidatedProposal] = []
            for item in proposals:
                validation = validator.validate(
                    item.proposal,
                    self.build_validation_context(task, item.proposal),
                )
                if validation.disposition is not ProposalDisposition.AUTO_ELIGIBLE:
                    outcomes.append(
                        ProposalCommitOutcome(
                            proposal_id=item.proposal.id,
                            status=ProposalCommitStatus.STALE,
                            message=validation.disposition.value,
                        )
                    )
                    continue
                revalidated.append(ValidatedProposal(item.proposal, validation))

            for item in revalidated:
                proposal = item.proposal
                idempotency = proposal.preconditions.idempotency
                if idempotency is None:
                    outcomes.append(
                        ProposalCommitOutcome(
                            proposal_id=proposal.id,
                            status=ProposalCommitStatus.SKIPPED,
                            message="missing idempotency contract",
                        )
                    )
                    continue
                existing = self.memory.events.get_by_idempotency_key(
                    proposal.bank_id,
                    idempotency.key,
                )
                if existing is not None:
                    outcomes.append(
                        ProposalCommitOutcome(
                            proposal_id=proposal.id,
                            status=ProposalCommitStatus.REPLAYED,
                        )
                    )
                    continue
                claim_id = self._commit_proposal(proposal, run_id=task.run_id)
                self.memory.events.append(
                    event_id=str(uuid4()),
                    bank_id=proposal.bank_id,
                    event_type="dream.proposal.committed",
                    aggregate_type="claim",
                    aggregate_id=claim_id,
                    actor_type="worker",
                    actor_id=task.task_id,
                    payload={
                        "run_id": task.run_id,
                        "proposal_id": proposal.id,
                        "claim_id": claim_id,
                        "fingerprint": idempotency.fingerprint,
                    },
                    idempotency_key=idempotency.key,
                    created_at=_now_string(),
                )
                outcomes.append(
                    ProposalCommitOutcome(
                        proposal_id=proposal.id,
                        status=ProposalCommitStatus.COMMITTED,
                    )
                )
        watermark_after = self.memory.events.current_watermark(task.bank_id)
        return BatchCommitResult(
            outcomes=tuple(outcomes),
            committed_event_range=(
                None
                if watermark_after == watermark_before
                else (watermark_before + 1, watermark_after)
            ),
        )

    def _build_proposal(
        self,
        *,
        task: DreamTask,
        source_bundle: SourceBundle,
        candidate: ExtractedClaimCandidate,
        subject: ExtractedEntityCandidate,
        object_entity: ExtractedEntityCandidate | None,
        object_value: Any,
        object_type: str,
        evidence_by_id: Mapping[str, EvidenceSpanCandidate],
        observation_by_evidence_id: Mapping[str, SourceObservation],
    ) -> DreamProposal:
        subject_record = self._find_entity(task.bank_id, subject.name, subject.entity_type)
        subject_id = (
            subject_record.id
            if subject_record is not None
            else f"pending:{_normalized(subject.name)}:{subject.entity_type}"
        )
        object_entity_id = None
        if object_entity is not None:
            object_record = self._find_entity(
                task.bank_id,
                object_entity.name,
                object_entity.entity_type,
            )
            object_entity_id = (
                object_record.id
                if object_record is not None
                else f"pending:{_normalized(object_entity.name)}:{object_entity.entity_type}"
            )
        predicate = self.memory.predicates.resolve(task.bank_id, candidate.predicate)
        predicate_definition = (
            PredicateDefinition.unknown(candidate.predicate, bank_id=task.bank_id)
            if predicate is None
            else self.reader.get_predicate_definition(task.bank_id, candidate.predicate)
        )
        assert predicate_definition is not None
        current = ()
        if subject_record is not None:
            current = tuple(
                claim
                for claim in self.reader.list_claims_for_slot(
                    task.bank_id,
                    subject_record.id,
                    candidate.predicate,
                )
                if claim.system_to is None
                and claim.lifecycle in {ClaimLifecycle.ACTIVE, ClaimLifecycle.CONTESTED}
            )
        commit_time = max(
            observation.observed_at for observation in observation_by_evidence_id.values()
        )
        evidence_ids = tuple(evidence_by_id)
        template = ClaimTemplate(
            bank_id=task.bank_id,
            subject_entity_id=subject_id,
            predicate=candidate.predicate,
            object_kind=candidate.object_candidate.kind,
            object_entity_id=object_entity_id,
            object_value_json=(
                None
                if candidate.object_candidate.kind is ClaimObjectKind.ENTITY
                else json.dumps(object_value, sort_keys=True, separators=(",", ":"))
            ),
            polarity=candidate.polarity,
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
            origin=ClaimOrigin.EXTRACTED,
            importance=0.5,
            created_by_run_id=None,
        )
        matching = next(
            (claim for claim in current if _claim_matches_template(claim, template)),
            None,
        )
        if matching is not None:
            action_type = DreamActionKind.CONFIRM
            action_rationale = "additional evidence confirms the current belief"
            plan = plan_confirm(
                matching,
                commit_time=commit_time,
                evidence_ids=evidence_ids,
            )
            target_ids = (matching.id,)
        elif current and predicate_definition.cardinality.value == "one":
            target = current[0]
            trust = min(
                _trust_ceiling(item.trust_class)
                for item in observation_by_evidence_id.values()
            )
            if trust >= 0.99:
                action_type = DreamActionKind.SUPERSEDE
                action_rationale = "new explicit owner evidence supersedes current value"
                plan = plan_supersede(
                    target,
                    template,
                    predicate_definition=predicate_definition,
                    commit_time=commit_time,
                    rationale=action_rationale,
                    evidence_ids=evidence_ids,
                )
            else:
                action_type = DreamActionKind.CONTRADICT
                action_rationale = (
                    "new evidence conflicts without sufficient authority to supersede"
                )
                plan = plan_contradict(
                    target,
                    template,
                    commit_time=commit_time,
                    rationale=action_rationale,
                    evidence_ids=evidence_ids,
                )
            target_ids = (target.id,)
        else:
            action_type = DreamActionKind.ASSERT
            action_rationale = "new evidence-backed belief"
            draft = DraftClaim(
                ref="asserted",
                bank_id=template.bank_id,
                subject_entity_id=template.subject_entity_id,
                predicate=template.predicate,
                object_kind=template.object_kind,
                object_entity_id=template.object_entity_id,
                object_value_json=template.object_value_json,
                polarity=template.polarity,
                valid_from=template.valid_from,
                valid_to=template.valid_to,
                system_from=commit_time,
                system_to=None,
                lifecycle=ClaimLifecycle.ACTIVE,
                origin=template.origin,
                importance=template.importance,
                created_at=commit_time,
                created_by_run_id=None,
            )
            plan = TransitionPlan(
                operation="assert",
                closures=(),
                draft_claims=(draft,),
                evidence_attachments=(
                    PlannedEvidenceAttachment(
                        target=ClaimHandle(draft_ref=draft.ref),
                        evidence_ids=evidence_ids,
                    ),
                ),
                relations=(),
            )
            target_ids = ()
        confidence = min(
            candidate.extraction_confidence,
            *(
                _trust_ceiling(observation.trust_class)
                for observation in observation_by_evidence_id.values()
            ),
        )
        ambiguous_current_slot = (
            predicate_definition.cardinality.value == "one" and len(current) > 1
        )
        if ambiguous_current_slot:
            target_ids = tuple(claim.id for claim in current)
            confidence = min(confidence, 0.89)
            action_rationale = (
                "multiple current claims already occupy this single-cardinality slot; "
                "human resolution required"
            )
        action = DreamAction(
            action_type=action_type,
            bank_id=task.bank_id,
            predicate_definition=predicate_definition,
            decision_confidence=confidence,
            transition_plan=plan,
            target_claim_ids=target_ids,
            evidence_ids=evidence_ids,
            rationale=action_rationale,
        )
        claim_preconditions = tuple(
            ClaimStatePrecondition(
                claim_id=claim.id,
                bank_id=task.bank_id,
                expected_token=ClaimVersionToken.from_claim(claim),
            )
            for claim in current
            if claim.id in action.referenced_claim_ids()
        )
        idempotency_key = f"dream:{task.bank_id}:{candidate.local_id}"
        proposal_id = str(uuid5(NAMESPACE_URL, f"{task.run_id}:{idempotency_key}"))
        fingerprint = fingerprint_for_value(action)
        return DreamProposal(
            id=proposal_id,
            bank_id=task.bank_id,
            action=action,
            preconditions=ProposalPreconditions(
                bank_id=task.bank_id,
                observed_event_watermark=task.input_watermark,
                claim_state_preconditions=claim_preconditions,
                idempotency=IdempotencyPrecondition(
                    key=idempotency_key,
                    fingerprint=fingerprint,
                ),
            ),
            created_at=commit_time,
        )

    def _route_ambiguous_slots_to_review(
        self,
        proposals: list[DreamProposal],
    ) -> list[DreamProposal]:
        slot_counts: dict[tuple[str, str, str, str], int] = {}
        for proposal in proposals:
            if proposal.action.predicate_definition.cardinality.value != "one":
                continue
            material = self.materials[proposal.id]
            key = (
                proposal.bank_id,
                _normalized(material.subject.name),
                material.subject.entity_type,
                material.claim_candidate.predicate,
            )
            slot_counts[key] = slot_counts.get(key, 0) + 1

        routed: list[DreamProposal] = []
        for proposal in proposals:
            material = self.materials[proposal.id]
            key = (
                proposal.bank_id,
                _normalized(material.subject.name),
                material.subject.entity_type,
                material.claim_candidate.predicate,
            )
            if slot_counts.get(key, 0) <= 1:
                routed.append(proposal)
                continue
            action = replace(
                proposal.action,
                decision_confidence=min(proposal.action.decision_confidence, 0.89),
                rationale=(
                    "multiple candidates target the same single-cardinality slot; "
                    "human resolution required"
                ),
            )
            idempotency = proposal.preconditions.idempotency
            preconditions = proposal.preconditions
            if idempotency is not None:
                preconditions = replace(
                    preconditions,
                    idempotency=replace(
                        idempotency,
                        fingerprint=fingerprint_for_value(action),
                    ),
                )
            routed.append(replace(proposal, action=action, preconditions=preconditions))
        return routed

    def _persist_proposal(self, task: DreamTask, proposal: DreamProposal) -> None:
        existing = self.memory.dream_proposals.get(proposal.bank_id, proposal.id)
        if existing is not None:
            return
        self.memory.dream_proposals.create(
            id=proposal.id,
            bank_id=proposal.bank_id,
            dream_run_id=task.run_id,
            proposal_type=proposal.action.action_type.value,
            preconditions=_jsonable(proposal.preconditions),
            action={
                "proposal": _jsonable(proposal.action),
                "material": _jsonable(self.materials.get(proposal.id)),
            },
            evidence_ids=list(proposal.action.referenced_evidence_ids()),
            model_trace=None,
            validation={},
            disposition="pending",
            created_at=_datetime_string(proposal.created_at or datetime.now(UTC)),
        )

    def _commit_proposal(self, proposal: DreamProposal, *, run_id: str) -> str:
        material = self.materials[proposal.id]
        evidence = tuple(material.evidence_by_id.values())
        first = evidence[0]
        first_observation = material.observation_by_evidence_id[first.candidate_id]
        excerpt = first_observation.content[first.start_offset:first.end_offset]
        known_at = _datetime_string(proposal.created_at or datetime.now(UTC))
        action_type = proposal.action.action_type
        target_id = (
            proposal.action.target_claim_ids[0]
            if proposal.action.target_claim_ids
            else None
        )
        if action_type is DreamActionKind.ASSERT:
            claim = self.memory.assert_claim(
                bank=proposal.bank_id,
                subject=material.subject.name,
                subject_type=material.subject.entity_type,
                predicate=material.claim_candidate.predicate,
                object=material.object_value,
                object_kind=material.claim_candidate.object_candidate.kind.value,
                object_type=material.object_type,
                observation_id=first_observation.observation_id,
                excerpt=excerpt,
                valid_from=_optional_datetime_string(material.claim_candidate.valid_from),
                valid_to=_optional_datetime_string(material.claim_candidate.valid_to),
                known_at=known_at,
                origin="extracted",
                importance=0.5,
                created_by_run_id=run_id,
            )
        elif action_type is DreamActionKind.CONFIRM:
            assert target_id is not None
            claim = self.memory.confirm_claim(
                target_id,
                bank=proposal.bank_id,
                observation_id=first_observation.observation_id,
                excerpt=excerpt,
                known_at=known_at,
            )
        elif action_type is DreamActionKind.SUPERSEDE:
            assert target_id is not None
            claim = self.memory.supersede_claim(
                target_id,
                bank=proposal.bank_id,
                object=material.object_value,
                object_kind=material.claim_candidate.object_candidate.kind.value,
                object_type=material.object_type,
                observation_id=first_observation.observation_id,
                excerpt=excerpt,
                valid_from=_optional_datetime_string(material.claim_candidate.valid_from),
                valid_to=_optional_datetime_string(material.claim_candidate.valid_to),
                known_at=known_at,
                rationale=proposal.action.rationale,
                created_by_run_id=run_id,
            )
        elif action_type is DreamActionKind.CONTRADICT:
            assert target_id is not None
            claim = self.memory.contradict_claim(
                target_id,
                bank=proposal.bank_id,
                object=material.object_value,
                object_kind=material.claim_candidate.object_candidate.kind.value,
                object_type=material.object_type,
                observation_id=first_observation.observation_id,
                excerpt=excerpt,
                valid_from=_optional_datetime_string(material.claim_candidate.valid_from),
                valid_to=_optional_datetime_string(material.claim_candidate.valid_to),
                known_at=known_at,
                rationale=proposal.action.rationale,
                created_by_run_id=run_id,
            )
        else:
            raise ValueError(f"Unsupported automatic dream action: {action_type.value}")
        for additional in evidence[1:]:
            observation = material.observation_by_evidence_id[additional.candidate_id]
            self.memory.confirm_claim(
                claim.id,
                bank=proposal.bank_id,
                observation_id=observation.observation_id,
                excerpt=observation.content[additional.start_offset:additional.end_offset],
                known_at=known_at,
            )
        return claim.id

    def _observations_for_task(self, task: DreamTask) -> tuple[ObservationRecord, ...]:
        if task.observation_ids:
            return tuple(
                record
                for record in (
                    self.memory.observations.get(task.bank_id, observation_id)
                    for observation_id in task.observation_ids
                )
                if record is not None and record.ingestion_state in {"pending", "processing"}
            )
        return self.memory.observations.list_for_ingestion(task.bank_id, limit=100)

    def _find_entity(self, bank_id: str, name: str, entity_type: str):
        records = self.memory.entities.list_by_name(
            bank_id,
            _normalized(name),
            entity_type=entity_type,
        )
        return records[0] if records else None


class EmbeddedDreamService:
    """Create durable run/task records and execute one bounded dream cycle."""

    def __init__(self, memory: MemoryGraph) -> None:
        self.memory = memory

    def enqueue(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        mode: DreamRunMode | str = DreamRunMode.APPLY,
        trigger: str = "manual",
        observation_ids: Sequence[str] = (),
    ) -> tuple[DreamRunRecord, DreamTaskRecord]:
        """Persist queued work without executing provider code in the caller process."""

        bank_record = self.memory.get_bank(bank)
        run_mode = DreamRunMode(mode)
        now = _now_string()
        run_id = str(uuid4())
        task_id = str(uuid4())
        provider_instance = provider or MetadataDreamProvider()
        provider_type = type(provider_instance)
        provider_identity = f"{provider_type.__module__}.{provider_type.__qualname__}"
        provider_hash = sha256(provider_identity.encode()).hexdigest()
        with transaction(self.memory.connection):
            if observation_ids:
                selected = tuple(
                    record
                    for record in (
                        self.memory.observations.get(bank_record.id, observation_id)
                        for observation_id in observation_ids
                    )
                    if record is not None and record.ingestion_state == "pending"
                )
            else:
                selected = self.memory.observations.list_for_ingestion(
                    bank_record.id,
                    limit=100,
                )
            selected_ids = tuple(record.id for record in selected)
            watermark = self.memory.events.current_watermark(bank_record.id)
            run = self.memory.dream_runs.create(
                id=run_id,
                bank_id=bank_record.id,
                trigger=trigger,
                mode=run_mode.value,
                state="queued",
                input_watermark=watermark,
                policy_version="dream-policy-v1",
                provider_config_hash=provider_hash,
                attempt_count=0,
                created_at=now,
            )
            task = self.memory.dream_tasks.create(
                id=task_id,
                bank_id=bank_record.id,
                dream_run_id=run_id,
                task_type="maintain_observations",
                resource_type="bank",
                resource_id=bank_record.id,
                idempotency_key=f"dream-task:{run_id}:{watermark}",
                state="queued",
                input={
                    "observation_ids": list(selected_ids),
                    "input_watermark": watermark,
                    "trigger": trigger,
                    "reason": trigger,
                },
                attempt_count=0,
                created_at=now,
            )
        return run, task

    def run(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        mode: DreamRunMode | str = DreamRunMode.APPLY,
        trigger: str = "manual",
        observation_ids: Sequence[str] = (),
    ) -> DreamRunReport:
        bank_record = self.memory.get_bank(bank)
        run_mode = DreamRunMode(mode)
        now = _now_string()
        run_id = str(uuid4())
        task_id = str(uuid4())
        provider_instance = provider or MetadataDreamProvider()
        provider_type = type(provider_instance)
        provider_identity = f"{provider_type.__module__}.{provider_type.__qualname__}"
        provider_hash = sha256(provider_identity.encode()).hexdigest()
        with transaction(self.memory.connection):
            if observation_ids:
                selected = tuple(
                    record
                    for record in (
                        self.memory.observations.get(bank_record.id, observation_id)
                        for observation_id in observation_ids
                    )
                    if record is not None and record.ingestion_state == "pending"
                )
            else:
                selected = self.memory.observations.list_for_ingestion(
                    bank_record.id,
                    limit=100,
                )
            selected_ids = tuple(record.id for record in selected)
            for observation_id in selected_ids:
                self.memory.observations.transition_ingestion_state(
                    bank_id=bank_record.id,
                    observation_id=observation_id,
                    from_states=("pending",),
                    to_state="processing",
                )
            watermark = self.memory.events.current_watermark(bank_record.id)
            self.memory.dream_runs.create(
                id=run_id,
                bank_id=bank_record.id,
                trigger=trigger,
                mode=run_mode.value,
                state="running",
                input_watermark=watermark,
                policy_version="dream-policy-v1",
                provider_config_hash=provider_hash,
                attempt_count=1,
                started_at=now,
                created_at=now,
            )
            self.memory.dream_tasks.create(
                id=task_id,
                bank_id=bank_record.id,
                dream_run_id=run_id,
                task_type="maintain_observations",
                resource_type="bank",
                resource_id=bank_record.id,
                idempotency_key=f"dream-task:{run_id}:{watermark}",
                state="running",
                input={"observation_ids": list(selected_ids), "input_watermark": watermark},
                attempt_count=1,
                created_at=now,
            )
        task = DreamTask(
            run_id=run_id,
            task_id=task_id,
            bank_id=bank_record.id,
            trigger=trigger,
            mode=run_mode,
            input_watermark=watermark,
            reason=trigger,
            observation_ids=selected_ids,
            created_at=_parse_datetime(now),
        )
        components = EmbeddedDreamComponents(self.memory)
        runtime = DreamRuntime(
            context_builder=components,
            pipeline=components,
            provider=provider_instance,
            validator=DreamProposalValidator(),
            committer=components,
        )
        report = runtime.run(task)
        self._finalize(report, components)
        return report

    def _finalize(
        self,
        report: DreamRunReport,
        components: EmbeddedDreamComponents,
    ) -> None:
        now = _now_string()
        if report.status.value == "failed":
            with transaction(self.memory.connection):
                self._transition_observations(report.task, to_state="pending")
                self.memory.dream_tasks.transition_state(
                    bank_id=report.task.bank_id,
                    task_id=report.task.task_id,
                    from_states=("running",),
                    to_state="failed",
                    error={
                        "stage": report.failure_stage,
                        "message": report.error_message,
                    },
                    completed_at=now,
                )
                self.memory.dream_runs.transition_state(
                    bank_id=report.task.bank_id,
                    run_id=report.task.run_id,
                    from_states=("running",),
                    to_state="failed",
                    usage=_jsonable(report.metrics),
                    error={
                        "stage": report.failure_stage,
                        "message": report.error_message,
                    },
                    completed_at=now,
                )
            return

        needs_review = False
        needs_correction = False
        stale = False
        with transaction(self.memory.connection):
            for item in report.proposal_results:
                disposition = item.validation.disposition.value
                if item.commit_outcome is not None:
                    disposition = (
                        "committed"
                        if item.commit_outcome.status is ProposalCommitStatus.REPLAYED
                        else item.commit_outcome.status.value
                    )
                    stale = stale or item.commit_outcome.status is ProposalCommitStatus.STALE
                self.memory.dream_proposals.update(
                    bank_id=report.task.bank_id,
                    proposal_id=item.proposal.id,
                    disposition=disposition,
                    validation=_jsonable(item.validation),
                )
                if item.validation.disposition is ProposalDisposition.REVIEW_REQUIRED:
                    needs_review = True
                    review = self.memory.review_items.get_by_proposal(
                        report.task.bank_id,
                        item.proposal.id,
                    )
                    if review is None:
                        self.memory.review_items.create(
                            id=str(uuid4()),
                            bank_id=report.task.bank_id,
                            proposal_id=item.proposal.id,
                            reason="; ".join(issue.message for issue in item.validation.issues),
                            state="pending",
                            created_at=now,
                        )
                elif item.validation.disposition is ProposalDisposition.REJECTED:
                    needs_correction = True
                elif item.validation.disposition is ProposalDisposition.STALE:
                    stale = True

            if report.task.mode is not DreamRunMode.APPLY or stale:
                self._transition_observations(report.task, to_state="pending")
            else:
                target_state = "partial" if needs_review or needs_correction else "processed"
                self._transition_observations(report.task, to_state=target_state)
            self.memory.dream_tasks.transition_state(
                bank_id=report.task.bank_id,
                task_id=report.task.task_id,
                from_states=("running",),
                to_state="completed",
                output=_jsonable(report.metrics),
                completed_at=now,
            )
            self.memory.dream_runs.transition_state(
                bank_id=report.task.bank_id,
                run_id=report.task.run_id,
                from_states=("running",),
                to_state="awaiting_review" if needs_review else "completed",
                usage=_jsonable(report.metrics),
                completed_at=now,
            )

    def _transition_observations(self, task: DreamTask, *, to_state: str) -> None:
        for observation_id in task.observation_ids:
            observation = self.memory.observations.get(task.bank_id, observation_id)
            if observation is None or observation.ingestion_state != "processing":
                continue
            self.memory.observations.transition_ingestion_state(
                bank_id=task.bank_id,
                observation_id=observation_id,
                from_states=("processing",),
                to_state=to_state,
            )


def _source_observation(record: ObservationRecord) -> SourceObservation:
    return SourceObservation(
        observation_id=record.id,
        source_key=record.source_key,
        content=record.content,
        actor_type=record.actor_type,
        actor_id=record.actor_id,
        observed_at=_parse_datetime(record.observed_at),
        effective_at=(
            None if record.effective_at is None else _parse_datetime(record.effective_at)
        ),
        trust_class=record.trust_class,
        sensitivity=record.sensitivity,
        metadata=record.metadata_json,
        chunks=tuple(
            SourceChunk(
                chunk_id=chunk.id,
                ordinal=chunk.ordinal,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                content=chunk.content,
            )
            for chunk in record.chunks
        ),
    )


def _validate_evidence_candidate(
    bank_id: str,
    span: EvidenceSpanCandidate,
    observation: SourceObservation,
) -> EvidenceSpanCheck:
    detail = None
    valid = span.observation_id == observation.observation_id
    if not valid:
        detail = "evidence observation does not match source bundle"
    elif span.start_offset < 0 or span.end_offset <= span.start_offset:
        valid = False
        detail = "evidence offsets must form a non-empty half-open interval"
    elif span.end_offset > len(observation.content):
        valid = False
        detail = "evidence span exceeds source content"
    elif span.excerpt is not None and (
        observation.content[span.start_offset:span.end_offset] != span.excerpt
    ):
        valid = False
        detail = "evidence excerpt does not match source content"
    return EvidenceSpanCheck(
        evidence_id=span.candidate_id,
        bank_id=bank_id,
        is_valid=valid,
        detail=detail,
    )


def _claim_matches_template(claim: Claim, template: ClaimTemplate) -> bool:
    object_signature = (
        (template.object_kind.value, template.object_entity_id)
        if template.object_kind is ClaimObjectKind.ENTITY
        else (template.object_kind.value, template.object_value_json)
    )
    return claim.object_signature == object_signature and claim.polarity is template.polarity


def _trust_ceiling(trust_class: str) -> float:
    return {
        "owner_explicit": 1.0,
        "authoritative_tool": 0.97,
        "authoritative_source": 0.95,
        "direct_observation": 0.9,
        "imported": 0.7,
        "model_generated": 0.4,
        "untrusted": 0.1,
    }.get(trust_class, 0.3)


def _mapping_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("candidate collection must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("candidate collection entries must be mappings")
    return tuple(value)


def _span_or_find(raw: Any, content: str, needle: str | None) -> tuple[int, int]:
    if isinstance(raw, Mapping) and "start" in raw and "end" in raw:
        return int(raw["start"]), int(raw["end"])
    if needle:
        start = content.find(needle)
        if start >= 0:
            return start, start + len(needle)
    return 0, len(content)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(str(value))


def _optional_datetime_string(value: datetime | None) -> str | None:
    return None if value is None else _datetime_string(value)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("dream timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _datetime_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now_string() -> str:
    return _datetime_string(datetime.now(UTC))


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _datetime_string(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"Value is not JSON serializable: {type(value).__name__}")
