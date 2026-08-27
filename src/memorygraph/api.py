"""High-level embedded API for the MemoryGraph MVP."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from memorygraph.adapters.storage_reader import claim_from_record, predicate_from_record
from memorygraph.config import MemoryGraphConfig
from memorygraph.domain import ClaimTemplate, plan_contradict, plan_retract, plan_supersede
from memorygraph.dream import DreamProvider, DreamRunMode, DreamRunReport
from memorygraph.errors import BankNotFoundError, ConflictError, NotFoundError, ValidationError
from memorygraph.models import (
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    DecisionMethod,
    HalfOpenInterval,
)
from memorygraph.retrieval import Embedder, FeatureHashEmbedder, HybridRetriever
from memorygraph.security import assess_retrieved_content
from memorygraph.storage import (
    ArtifactRepository,
    BankRecord,
    BankRepository,
    ClaimEvidenceRecord,
    ClaimEvidenceRepository,
    ClaimRecord,
    ClaimRelationRecord,
    ClaimRelationRepository,
    ClaimRepository,
    DatabaseConfig,
    DreamProposalRepository,
    DreamRunRecord,
    DreamRunRepository,
    DreamTaskRecord,
    DreamTaskRepository,
    EmbeddingRepository,
    EntityRecord,
    EntityRepository,
    MemoryEventRecord,
    MemoryEventRepository,
    MigrationRunner,
    ObservationChunkInput,
    ObservationRecord,
    ObservationRepository,
    PredicateDefinitionRecord,
    PredicateDefinitionRepository,
    ProceduralEpisodeRecord,
    ProceduralEpisodeRepository,
    ReviewItemRecord,
    ReviewItemRepository,
    SearchDocumentRepository,
    connect,
    transaction,
)


def utc_now() -> str:
    """Return the canonical UTC timestamp used by the persistence layer."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RecallHit:
    """One source observation selected through a current temporal claim."""

    event_id: str
    at: str
    content: str
    bank_id: str
    scenario_id: str = "memorygraph"
    score: float | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ClaimHistoryItem:
    """A human-readable claim version in a subject/predicate history."""

    claim: ClaimRecord
    subject: str
    object: Any
    evidence: tuple[ClaimEvidenceRecord, ...]


@dataclass(frozen=True, slots=True)
class ClaimExplanation:
    """An inspectable claim with its evidence and graph relations."""

    claim: ClaimRecord
    subject: str
    object: Any
    evidence: tuple[ClaimEvidenceRecord, ...]
    relations: tuple[ClaimRelationRecord, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RollbackResult:
    """Audit summary for one compensating dream-run rollback."""

    original_run_id: str
    rollback_run_id: str
    retracted_claim_ids: tuple[str, ...]
    restored_claim_ids: tuple[str, ...]
    removed_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationDeletionResult:
    """Privacy-deletion result without retaining source content."""

    observation_id: str
    affected_claim_ids: tuple[str, ...]
    retracted_claim_ids: tuple[str, ...]
    stale_artifact_ids: tuple[str, ...]
    residue_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AttemptRecallHit:
    """One prior attempt selected for procedural reuse or failure avoidance."""

    episode: ProceduralEpisodeRecord
    source_key: str
    content: str
    score: float


class MemoryGraph:
    """Local embedded MemoryGraph database.

    This is the embedded MVP surface: immutable observations, typed graph claims,
    deterministic temporal transitions, lexical recall, history, and explanation.
    """

    def __init__(
        self,
        config: MemoryGraphConfig,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config
        self.connection = connect(
            DatabaseConfig(
                path=config.database_path,
                busy_timeout_ms=config.busy_timeout_ms,
            )
        )
        MigrationRunner(self.connection).migrate()
        self.banks = BankRepository(self.connection)
        self.observations = ObservationRepository(self.connection)
        self.entities = EntityRepository(self.connection)
        self.predicates = PredicateDefinitionRepository(self.connection)
        self.procedural_episodes = ProceduralEpisodeRepository(self.connection)
        self.claims = ClaimRepository(self.connection)
        self.evidence = ClaimEvidenceRepository(self.connection)
        self.relations = ClaimRelationRepository(self.connection)
        self.search = SearchDocumentRepository(self.connection)
        self.embeddings = EmbeddingRepository(self.connection)
        self.embedder = embedder
        if self.embedder is None and config.enable_local_embeddings:
            self.embedder = FeatureHashEmbedder(
                dimensions=config.local_embedding_dimensions,
            )
        self.retriever = HybridRetriever(
            self.search,
            self.embeddings,
            embedder=self.embedder,
        )
        self.events = MemoryEventRepository(self.connection)
        self.dream_runs = DreamRunRepository(self.connection)
        self.dream_tasks = DreamTaskRepository(self.connection)
        self.dream_proposals = DreamProposalRepository(self.connection)
        self.review_items = ReviewItemRepository(self.connection)
        self.artifacts = ArtifactRepository(self.connection)

    @classmethod
    def open(
        cls,
        database_path: str | Path,
        *,
        embedder: Embedder | None = None,
    ) -> Self:
        """Open or initialize an embedded database."""

        return cls(MemoryGraphConfig.local(database_path), embedder=embedder)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def create_bank(
        self,
        slug: str,
        *,
        name: str | None = None,
        mission: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> BankRecord:
        """Create a hard memory-isolation boundary.

        Repeating the same slug returns the existing bank so installers can be safely rerun.
        """

        normalized_slug = slug.strip()
        if not normalized_slug:
            raise ValueError("Bank slug cannot be empty.")
        existing = self.banks.get_by_slug(normalized_slug)
        if existing is not None:
            return existing
        now = utc_now()
        try:
            return self.banks.create(
                id=str(uuid4()),
                slug=normalized_slug,
                name=(name or normalized_slug).strip(),
                mission=mission,
                policy_json=policy,
                created_at=now,
            )
        except sqlite3.IntegrityError as error:
            raise ConflictError(f"Could not create bank {normalized_slug!r}.") from error

    def get_bank(self, bank: str) -> BankRecord:
        """Resolve a bank by stable slug or UUID."""

        record = self.banks.get_by_slug(bank) or self.banks.get(bank)
        if record is None:
            raise BankNotFoundError(f"Memory bank {bank!r} does not exist.")
        return record

    def observe(
        self,
        content: str,
        *,
        bank: str,
        source_key: str,
        kind: str = "explicit_assertion",
        actor_type: str = "user",
        actor_id: str | None = None,
        effective_at: str | None = None,
        trust_class: str = "owner_explicit",
        sensitivity: str = "normal",
        metadata: dict[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> ObservationRecord:
        """Store an immutable, idempotent source observation.

        The MVP creates one exact whole-content chunk. Later chunkers may add bounded chunks
        without changing the authoritative observation.
        """

        bank_record = self.get_bank(bank)
        normalized_source_key = source_key.strip()
        if not normalized_source_key:
            raise ValueError("source_key cannot be empty.")
        if not content:
            raise ValueError("Observation content cannot be empty.")

        content_hash = sha256(content.encode("utf-8")).hexdigest()
        existing = self.observations.list_by_source_key(bank_record.id, normalized_source_key)
        for observation in existing:
            if observation.content_sha256 == content_hash:
                return observation

        now = utc_now()
        observation_id = str(uuid4())
        chunk = ObservationChunkInput(
            id=str(uuid4()),
            ordinal=0,
            start_offset=0,
            end_offset=len(content),
            content=content,
            content_sha256=content_hash,
            created_at=now,
        )
        try:
            with transaction(self.connection):
                observation = self.observations.create(
                    id=observation_id,
                    bank_id=bank_record.id,
                    kind=kind,
                    source_key=normalized_source_key,
                    content_sha256=content_hash,
                    content=content,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    observed_at=_canonical_timestamp(observed_at) or now,
                    effective_at=_canonical_timestamp(effective_at),
                    trust_class=trust_class,
                    sensitivity=sensitivity,
                    metadata_json=metadata,
                    ingestion_state="pending",
                    created_at=now,
                    chunks=(chunk,),
                )
                self.events.append(
                    event_id=str(uuid4()),
                    bank_id=bank_record.id,
                    event_type="observation.recorded",
                    aggregate_type="observation",
                    aggregate_id=observation.id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload={
                        "observation_id": observation.id,
                        "source_key": observation.source_key,
                        "content_sha256": observation.content_sha256,
                    },
                    idempotency_key=f"observation:{observation.id}",
                    created_at=now,
                )
                return observation
        except sqlite3.IntegrityError as error:
            raise ConflictError(
                f"Observation conflicts with source key {normalized_source_key!r}."
            ) from error

    def define_predicate(
        self,
        name: str,
        *,
        bank: str,
        cardinality: str = "many",
        volatility: str = "durable",
        conflict_policy: str = "conservative",
        subject_type: str | None = None,
        object_type: str | None = None,
        default_validity_seconds: int | None = None,
        sensitivity: str = "normal",
    ) -> PredicateDefinitionRecord:
        """Define how one class of graph edge behaves during belief revision."""

        bank_record = self.get_bank(bank)
        predicate = _normalize_predicate(name)
        existing = self.predicates.resolve(bank_record.id, predicate)
        if existing is not None:
            requested = (
                cardinality,
                volatility,
                conflict_policy,
                subject_type,
                object_type,
                default_validity_seconds,
                sensitivity,
            )
            actual = (
                existing.cardinality,
                existing.volatility,
                existing.conflict_policy,
                existing.subject_type,
                existing.object_type,
                existing.default_validity_seconds,
                existing.sensitivity,
            )
            if requested != actual:
                raise ConflictError(
                    f"Predicate {predicate!r} already exists with a different policy."
                )
            return existing
        try:
            return self.predicates.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                name=predicate,
                cardinality=cardinality,
                volatility=volatility,
                conflict_policy=conflict_policy,
                subject_type=subject_type,
                object_type=object_type,
                default_validity_seconds=default_validity_seconds,
                sensitivity=sensitivity,
                created_at=utc_now(),
            )
        except sqlite3.IntegrityError as error:
            raise ValidationError(f"Invalid predicate policy for {predicate!r}.") from error

    def record_attempt(
        self,
        *,
        bank: str,
        source_key: str,
        task_key: str,
        strategy: str,
        outcome: str,
        failure: str | None = None,
        applicability: Any = None,
        environment: Any = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        actor_id: str | None = None,
        workspace: str | None = None,
    ) -> ProceduralEpisodeRecord:
        """Record one sourced coding attempt, including failures and applicability bounds."""

        normalized_task = " ".join(task_key.split())
        normalized_strategy = strategy.strip()
        if not normalized_task or not normalized_strategy:
            raise ValueError("task_key and strategy cannot be empty")
        if outcome not in {"success", "failure", "partial", "unknown"}:
            raise ValidationError("attempt outcome must be success, failure, partial, or unknown")
        content = "\n".join(
            line
            for line in (
                f"Task: {normalized_task}",
                f"Strategy: {normalized_strategy}",
                f"Outcome: {outcome}",
                None if failure is None else f"Failure: {failure}",
                f"Applicability: {json.dumps(applicability or {}, sort_keys=True)}",
                f"Environment: {json.dumps(environment or {}, sort_keys=True)}",
            )
            if line is not None
        )
        observation = self.observe(
            content,
            bank=bank,
            source_key=source_key,
            kind="event",
            actor_type="agent",
            actor_id=actor_id,
            effective_at=completed_at or started_at,
            trust_class="direct_observation",
            metadata={
                "freshness_form": "snapshot",
                "procedural_memory": True,
                "task_key": normalized_task,
                **({"workspace": workspace} if workspace else {}),
            },
        )
        existing = self.procedural_episodes.get_by_source(
            observation.bank_id,
            observation.id,
            normalized_task,
            normalized_strategy,
        )
        if existing is not None:
            return existing
        created_at = utc_now()
        episode = self.procedural_episodes.create(
            id=str(uuid4()),
            bank_id=observation.bank_id,
            source_observation_id=observation.id,
            task_key=normalized_task,
            strategy=normalized_strategy,
            outcome=outcome,
            failure=failure,
            applicability=applicability or {},
            environment=environment or {},
            started_at=_canonical_timestamp(started_at),
            completed_at=_canonical_timestamp(completed_at),
            created_at=created_at,
        )
        self.events.append(
            event_id=str(uuid4()),
            bank_id=observation.bank_id,
            event_type="attempt.recorded",
            aggregate_type="procedural_episode",
            aggregate_id=episode.id,
            actor_type="agent",
            actor_id=actor_id,
            payload={
                "episode_id": episode.id,
                "observation_id": observation.id,
                "outcome": outcome,
            },
            idempotency_key=f"procedural:{episode.id}",
            created_at=created_at,
        )
        return episode

    def recall_attempts(
        self,
        *,
        bank: str,
        query_text: str,
        limit: int = 5,
        as_of: str | None = None,
    ) -> tuple[AttemptRecallHit, ...]:
        """Retrieve bounded prior attempts without generalizing their applicability."""

        bank_record = self.get_bank(bank)
        query = _procedural_fts_query(query_text)
        if not query or limit <= 0:
            return ()
        cutoff = _canonical_timestamp(as_of)
        rows = self.procedural_episodes.search(
            bank_id=bank_record.id,
            query=query,
            limit=max(limit * 5, 20),
        )
        hits: list[AttemptRecallHit] = []
        for row in rows:
            episode_time = (
                row.episode.completed_at
                or row.episode.started_at
                or row.episode.created_at
            )
            if cutoff is not None and episode_time > cutoff:
                continue
            observation = self.observations.get(
                bank_record.id,
                row.episode.source_observation_id,
            )
            if observation is None or observation.ingestion_state == "deleted":
                continue
            assessment = assess_retrieved_content(
                observation.content,
                trust_class=observation.trust_class,
            )
            if not assessment.safe_for_agent_context:
                continue
            hits.append(
                AttemptRecallHit(
                    episode=row.episode,
                    source_key=observation.source_key,
                    content=observation.content,
                    score=row.score,
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)

    def entity(
        self,
        name: str,
        *,
        bank: str,
        entity_type: str = "entity",
    ) -> EntityRecord:
        """Resolve or create a canonical node inside one memory bank."""

        bank_record = self.get_bank(bank)
        canonical_name = " ".join(name.split())
        if not canonical_name:
            raise ValueError("Entity name cannot be empty.")
        normalized_name = _normalize_entity_name(canonical_name)
        existing = self.entities.list_by_name(
            bank_record.id,
            normalized_name,
            entity_type=entity_type,
        )
        if existing:
            return existing[0]
        try:
            return self.entities.create_entity(
                id=str(uuid4()),
                bank_id=bank_record.id,
                canonical_name=canonical_name,
                normalized_name=normalized_name,
                entity_type=entity_type,
                created_at=utc_now(),
            )
        except sqlite3.IntegrityError as error:
            raise ConflictError(f"Could not create entity {canonical_name!r}.") from error

    def assert_claim(
        self,
        *,
        bank: str,
        subject: str,
        predicate: str,
        object: Any,
        observation_id: str,
        object_kind: str = "entity",
        subject_type: str = "entity",
        object_type: str = "entity",
        excerpt: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        known_at: str | None = None,
        polarity: str = "positive",
        origin: str = "explicit",
        importance: float = 0.5,
        created_by_run_id: str | None = None,
    ) -> ClaimRecord:
        """Commit one atomic graph claim grounded in an exact source span.

        A single-valued predicate refuses a second active value; callers must make the
        transition explicit with :meth:`supersede_claim` or :meth:`contradict_claim`.
        """

        bank_record = self.get_bank(bank)
        predicate_name = _normalize_predicate(predicate)
        observation = self._observation(bank_record.id, observation_id)
        predicate_definition = self.predicates.resolve(bank_record.id, predicate_name)
        if predicate_definition is None:
            predicate_definition = self.define_predicate(predicate_name, bank=bank_record.id)

        with transaction(self.connection):
            subject_entity = self.entity(subject, bank=bank_record.id, entity_type=subject_type)
            object_entity, object_value = self._resolve_object(
                bank_id=bank_record.id,
                value=object,
                object_kind=object_kind,
                object_type=object_type,
            )
            current = self._current_claims(bank_record.id, subject_entity.id, predicate_name)
            if predicate_definition.cardinality == "one" and current:
                matching = next(
                    (
                        item
                        for item in current
                        if _record_object_signature(item)
                        == _object_signature(object_kind, object_entity, object_value)
                        and item.polarity == polarity
                    ),
                    None,
                )
                if matching is not None:
                    return matching
                raise ConflictError(
                    f"{subject_entity.canonical_name}.{predicate_name} already has a current "
                    "value; use supersede_claim() or contradict_claim()."
                )

            now = _canonical_timestamp(known_at) or utc_now()
            claim = self.claims.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                subject_entity_id=subject_entity.id,
                predicate=predicate_name,
                object_kind=object_kind,
                object_entity_id=object_entity.id if object_entity else None,
                object_value=object_value,
                polarity=polarity,
                valid_from=_canonical_timestamp(valid_from),
                valid_to=_canonical_timestamp(valid_to),
                system_from=now,
                lifecycle="active",
                origin=origin,
                importance=importance,
                created_by_run_id=created_by_run_id,
                created_at=now,
            )
            evidence = self._attach_evidence(
                claim=claim,
                observation=observation,
                excerpt=excerpt,
                stance="supports",
                created_at=now,
            )
            self._index_claim(claim, subject_entity, object_entity, object_value, evidence)
            self._record_event(
                bank_id=bank_record.id,
                event_type="claim.asserted",
                aggregate_id=claim.id,
                payload={"claim_id": claim.id, "evidence_id": evidence.id},
                created_at=now,
            )
            return claim

    def confirm_claim(
        self,
        claim_id: str,
        *,
        bank: str,
        observation_id: str,
        excerpt: str | None = None,
        known_at: str | None = None,
    ) -> ClaimRecord:
        """Attach independent supporting evidence without duplicating the claim."""

        bank_record = self.get_bank(bank)
        claim = self.claims.get(bank_record.id, claim_id)
        if claim is None:
            raise NotFoundError(f"Claim {claim_id!r} does not exist in bank {bank!r}.")
        if claim.system_to is not None or claim.lifecycle not in {"active", "contested"}:
            raise ConflictError(f"Claim {claim_id!r} is not current.")
        observation = self._observation(bank_record.id, observation_id)
        now = _canonical_timestamp(known_at) or utc_now()
        exact_excerpt = observation.content if excerpt is None else excerpt
        if any(
            item.observation_id == observation.id and item.excerpt == exact_excerpt
            for item in self.evidence.list_for_claim(bank_record.id, claim.id)
        ):
            return claim
        with transaction(self.connection):
            evidence = self._attach_evidence(
                claim=claim,
                observation=observation,
                excerpt=excerpt,
                stance="supports",
                created_at=now,
            )
            self._record_event(
                bank_id=bank_record.id,
                event_type="claim.confirmed",
                aggregate_id=claim.id,
                payload={"claim_id": claim.id, "evidence_id": evidence.id},
                created_at=now,
            )
        return claim

    def contradict_claim(
        self,
        claim_id: str,
        *,
        bank: str,
        object: Any,
        observation_id: str,
        object_kind: str | None = None,
        object_type: str = "entity",
        excerpt: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        known_at: str | None = None,
        rationale: str = "equal-authority evidence conflicts",
        created_by_run_id: str | None = None,
    ) -> ClaimRecord:
        """Preserve both sides of an unresolved conflict as contested graph claims."""

        bank_record = self.get_bank(bank)
        current_record = self.claims.get(bank_record.id, claim_id)
        if current_record is None:
            raise NotFoundError(f"Claim {claim_id!r} does not exist in bank {bank!r}.")
        if current_record.system_to is not None or current_record.lifecycle not in {
            "active",
            "contested",
        }:
            raise ConflictError(f"Claim {claim_id!r} is not transitionable.")
        observation = self._observation(bank_record.id, observation_id)
        predicate_definition = self.predicates.resolve(bank_record.id, current_record.predicate)
        if predicate_definition is None:
            predicate_definition = self.define_predicate(
                current_record.predicate,
                bank=bank_record.id,
            )
        replacement_kind = object_kind or current_record.object_kind
        subject_entity = self._entity_by_id(bank_record.id, current_record.subject_entity_id)

        with transaction(self.connection):
            object_entity, object_value = self._resolve_object(
                bank_id=bank_record.id,
                value=object,
                object_kind=replacement_kind,
                object_type=object_type,
            )
            commit_time = _transition_commit_time(current_record.system_from, known_at)
            plan = plan_contradict(
                claim_from_record(current_record),
                ClaimTemplate(
                    bank_id=bank_record.id,
                    subject_entity_id=current_record.subject_entity_id,
                    predicate=current_record.predicate,
                    object_kind=ClaimObjectKind(replacement_kind),
                    object_entity_id=object_entity.id if object_entity else None,
                    object_value_json=(
                        None
                        if replacement_kind == "entity"
                        else json.dumps(object_value, sort_keys=True, separators=(",", ":"))
                    ),
                    polarity=ClaimPolarity(current_record.polarity),
                    valid_from=_parse_timestamp(valid_from),
                    valid_to=_parse_timestamp(valid_to),
                    origin=ClaimOrigin.EXPLICIT,
                    importance=current_record.importance,
                ),
                commit_time=commit_time,
                rationale=rationale,
                evidence_ids=(),
                decision_method=DecisionMethod.EXPLICIT,
            )
            commit_at = _format_timestamp(commit_time)
            if plan.closures:
                existing_draft = plan.draft_claims[0]
                contested_existing = self.claims.create_successor(
                    bank_id=bank_record.id,
                    prior_claim_id=current_record.id,
                    successor_id=str(uuid4()),
                    successor_system_from=commit_at,
                    successor_created_at=commit_at,
                    successor_lifecycle=existing_draft.lifecycle.value,
                    created_by_run_id=created_by_run_id,
                ).successor
                copied_evidence = self._copy_evidence(
                    current_record.id,
                    contested_existing,
                    created_at=commit_at,
                )
                if copied_evidence:
                    current_object_entity = (
                        self._entity_by_id(bank_record.id, contested_existing.object_entity_id)
                        if contested_existing.object_entity_id
                        else None
                    )
                    self._index_claim(
                        contested_existing,
                        subject_entity,
                        current_object_entity,
                        contested_existing.object_value,
                        copied_evidence[0],
                    )
                contradiction_draft = plan.draft_claims[1]
            else:
                contested_existing = current_record
                contradiction_draft = plan.draft_claims[0]

            contradiction = self.claims.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                subject_entity_id=contradiction_draft.subject_entity_id,
                predicate=contradiction_draft.predicate,
                object_kind=contradiction_draft.object_kind.value,
                object_entity_id=contradiction_draft.object_entity_id,
                object_value=object_value,
                polarity=contradiction_draft.polarity.value,
                valid_from=_format_optional_timestamp(contradiction_draft.valid_from),
                valid_to=_format_optional_timestamp(contradiction_draft.valid_to),
                system_from=commit_at,
                lifecycle=contradiction_draft.lifecycle.value,
                origin=contradiction_draft.origin.value,
                importance=contradiction_draft.importance,
                created_by_run_id=created_by_run_id,
                created_at=commit_at,
            )
            contradiction_evidence = self._attach_evidence(
                claim=contradiction,
                observation=observation,
                excerpt=excerpt,
                stance="supports",
                created_at=commit_at,
            )
            self.relations.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                from_claim_id=contradiction.id,
                to_claim_id=contested_existing.id,
                relation="contradicts",
                rationale=rationale,
                decision_method="explicit",
                decision_confidence=1.0,
                dream_run_id=created_by_run_id,
                created_at=commit_at,
            )
            self._index_claim(
                contradiction,
                subject_entity,
                object_entity,
                object_value,
                contradiction_evidence,
            )
            self._record_event(
                bank_id=bank_record.id,
                event_type="claim.contested",
                aggregate_id=contradiction.id,
                payload={
                    "existing_claim_id": contested_existing.id,
                    "contradictory_claim_id": contradiction.id,
                },
                created_at=commit_at,
            )
            return contradiction

    def supersede_claim(
        self,
        claim_id: str,
        *,
        bank: str,
        object: Any,
        observation_id: str,
        object_kind: str | None = None,
        object_type: str = "entity",
        excerpt: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        known_at: str | None = None,
        rationale: str = "explicit correction",
        created_by_run_id: str | None = None,
    ) -> ClaimRecord:
        """Atomically retire one belief and install its evidence-backed replacement."""

        bank_record = self.get_bank(bank)
        current_record = self.claims.get(bank_record.id, claim_id)
        if current_record is None:
            raise NotFoundError(f"Claim {claim_id!r} does not exist in bank {bank!r}.")
        if current_record.system_to is not None or current_record.lifecycle not in {
            "active",
            "contested",
        }:
            raise ConflictError(f"Claim {claim_id!r} is not transitionable.")

        observation = self._observation(bank_record.id, observation_id)
        replacement_kind = object_kind or current_record.object_kind
        subject_entity = self._entity_by_id(bank_record.id, current_record.subject_entity_id)
        predicate_definition = self.predicates.resolve(bank_record.id, current_record.predicate)
        if predicate_definition is None:
            predicate_definition = self.define_predicate(
                current_record.predicate,
                bank=bank_record.id,
            )

        with transaction(self.connection):
            object_entity, object_value = self._resolve_object(
                bank_id=bank_record.id,
                value=object,
                object_kind=replacement_kind,
                object_type=object_type,
            )
            commit_time = _transition_commit_time(current_record.system_from, known_at)
            replacement_valid_from = _parse_timestamp(valid_from)
            replacement_valid_to = _parse_timestamp(valid_to)
            plan = plan_supersede(
                claim_from_record(current_record),
                ClaimTemplate(
                    bank_id=bank_record.id,
                    subject_entity_id=current_record.subject_entity_id,
                    predicate=current_record.predicate,
                    object_kind=ClaimObjectKind(replacement_kind),
                    object_entity_id=object_entity.id if object_entity else None,
                    object_value_json=(
                        None
                        if replacement_kind == "entity"
                        else json.dumps(object_value, sort_keys=True, separators=(",", ":"))
                    ),
                    polarity=ClaimPolarity(current_record.polarity),
                    valid_from=replacement_valid_from,
                    valid_to=replacement_valid_to,
                    origin=ClaimOrigin.EXPLICIT,
                    importance=current_record.importance,
                ),
                predicate_definition=predicate_from_record(predicate_definition),
                commit_time=commit_time,
                rationale=rationale,
                evidence_ids=(),
                decision_method=DecisionMethod.EXPLICIT,
            )
            commit_at = _format_timestamp(commit_time)
            retired_draft, replacement_draft = plan.draft_claims
            successor = self.claims.create_successor(
                bank_id=bank_record.id,
                prior_claim_id=current_record.id,
                successor_id=str(uuid4()),
                successor_system_from=commit_at,
                successor_created_at=commit_at,
                successor_lifecycle=retired_draft.lifecycle.value,
                valid_to=_format_optional_timestamp(retired_draft.valid_to),
                created_by_run_id=created_by_run_id,
            ).successor
            retired_evidence = self._copy_evidence(
                current_record.id,
                successor,
                created_at=commit_at,
            )
            if retired_evidence:
                retired_object_entity = (
                    self._entity_by_id(bank_record.id, successor.object_entity_id)
                    if successor.object_entity_id
                    else None
                )
                self._index_claim(
                    successor,
                    subject_entity,
                    retired_object_entity,
                    successor.object_value,
                    retired_evidence[0],
                )

            replacement = self.claims.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                subject_entity_id=replacement_draft.subject_entity_id,
                predicate=replacement_draft.predicate,
                object_kind=replacement_draft.object_kind.value,
                object_entity_id=replacement_draft.object_entity_id,
                object_value=object_value,
                polarity=replacement_draft.polarity.value,
                valid_from=_format_optional_timestamp(replacement_draft.valid_from),
                valid_to=_format_optional_timestamp(replacement_draft.valid_to),
                system_from=commit_at,
                lifecycle=replacement_draft.lifecycle.value,
                origin=replacement_draft.origin.value,
                importance=replacement_draft.importance,
                created_by_run_id=created_by_run_id,
                created_at=commit_at,
            )
            replacement_evidence = self._attach_evidence(
                claim=replacement,
                observation=observation,
                excerpt=excerpt,
                stance="supports",
                created_at=commit_at,
            )
            self.relations.create(
                id=str(uuid4()),
                bank_id=bank_record.id,
                from_claim_id=replacement.id,
                to_claim_id=successor.id,
                relation="supersedes",
                rationale=rationale,
                decision_method="explicit",
                decision_confidence=1.0,
                dream_run_id=created_by_run_id,
                created_at=commit_at,
            )
            self._index_claim(
                replacement,
                subject_entity,
                object_entity,
                object_value,
                replacement_evidence,
            )
            self._record_event(
                bank_id=bank_record.id,
                event_type="claim.superseded",
                aggregate_id=replacement.id,
                payload={
                    "prior_claim_id": current_record.id,
                    "retired_claim_id": successor.id,
                    "replacement_claim_id": replacement.id,
                    "warnings": list(plan.warnings),
                },
                created_at=commit_at,
            )
            return replacement

    def retract_claim(
        self,
        claim_id: str,
        *,
        bank: str,
        observation_id: str | None = None,
        excerpt: str | None = None,
        effective_at: str | None = None,
        known_at: str | None = None,
        reason: str = "explicit retraction",
        created_by_run_id: str | None = None,
    ) -> ClaimRecord:
        """Retract a current claim without erasing its evidence or audit history."""

        bank_record = self.get_bank(bank)
        current = self.claims.get(bank_record.id, claim_id)
        if current is None:
            raise NotFoundError(f"Claim {claim_id!r} does not exist in bank {bank!r}.")
        if current.system_to is not None or current.lifecycle not in {"active", "contested"}:
            raise ConflictError(f"Claim {claim_id!r} is not transitionable.")
        observation = (
            None
            if observation_id is None
            else self._observation(bank_record.id, observation_id)
        )
        commit_time = _transition_commit_time(current.system_from, known_at)
        replacement_interval = None
        if effective_at is not None:
            replacement_interval = HalfOpenInterval(
                _parse_timestamp(current.valid_from),
                _parse_timestamp(effective_at),
            )
        plan = plan_retract(
            claim_from_record(current),
            commit_time=commit_time,
            evidence_ids=(),
            replacement_valid_interval=replacement_interval,
        )
        draft = plan.draft_claims[0]
        commit_at = _format_timestamp(commit_time)
        subject = self._entity_by_id(bank_record.id, current.subject_entity_id)

        with transaction(self.connection):
            retracted = self.claims.create_successor(
                bank_id=bank_record.id,
                prior_claim_id=current.id,
                successor_id=str(uuid4()),
                successor_system_from=commit_at,
                successor_created_at=commit_at,
                successor_lifecycle=draft.lifecycle.value,
                valid_from=_format_optional_timestamp(draft.valid_from),
                valid_to=_format_optional_timestamp(draft.valid_to),
                created_by_run_id=created_by_run_id,
            ).successor
            copied = self._copy_evidence(current.id, retracted, created_at=commit_at)
            if observation is not None:
                self._attach_evidence(
                    claim=retracted,
                    observation=observation,
                    excerpt=excerpt,
                    stance="contradicts",
                    created_at=commit_at,
                )
            if copied:
                object_entity = (
                    self._entity_by_id(bank_record.id, retracted.object_entity_id)
                    if retracted.object_entity_id
                    else None
                )
                self._index_claim(
                    retracted,
                    subject,
                    object_entity,
                    retracted.object_value,
                    copied[0],
                )
            self._record_event(
                bank_id=bank_record.id,
                event_type="claim.retracted",
                aggregate_id=retracted.id,
                payload={
                    "prior_claim_id": current.id,
                    "retracted_claim_id": retracted.id,
                    "reason": reason,
                },
                created_at=commit_at,
            )
            return retracted

    def history(
        self,
        *,
        bank: str,
        subject: str,
        predicate: str,
        current_versions_only: bool = False,
    ) -> tuple[ClaimHistoryItem, ...]:
        """Return the complete bitemporal audit trail for one graph slot."""

        bank_record = self.get_bank(bank)
        entity = self._find_entity(bank_record.id, subject)
        if entity is None:
            return ()
        records = self.claims.list_versions(
            bank_record.id,
            entity.id,
            _normalize_predicate(predicate),
        )
        if current_versions_only:
            records = tuple(record for record in records if record.system_to is None)
        records = tuple(
            sorted(
                records,
                key=lambda record: (
                    record.system_from,
                    {"retracted": 0, "superseded": 1, "contested": 2, "active": 3}[
                        record.lifecycle
                    ],
                    record.created_at,
                    record.id,
                ),
            )
        )
        return tuple(
            ClaimHistoryItem(
                claim=record,
                subject=entity.canonical_name,
                object=self._display_object(record),
                evidence=self.evidence.list_for_claim(bank_record.id, record.id),
            )
            for record in records
        )

    def explain(self, claim_id: str, *, bank: str) -> ClaimExplanation:
        """Explain why a claim exists, including incoming and outgoing graph edges."""

        bank_record = self.get_bank(bank)
        claim = self.claims.get(bank_record.id, claim_id)
        if claim is None:
            raise NotFoundError(f"Claim {claim_id!r} does not exist in bank {bank!r}.")
        subject = self._entity_by_id(bank_record.id, claim.subject_entity_id)
        rows = self.connection.execute(
            """
            SELECT id
            FROM claim_relations
            WHERE bank_id = ? AND (from_claim_id = ? OR to_claim_id = ?)
            ORDER BY created_at, id
            """,
            (bank_record.id, claim.id, claim.id),
        ).fetchall()
        relations = tuple(
            relation
            for relation in (self.relations.get(bank_record.id, row["id"]) for row in rows)
            if relation is not None
        )
        evidence = self.evidence.list_for_claim(bank_record.id, claim.id)
        warnings: list[str] = []
        if claim.lifecycle != "active":
            warnings.append(f"claim is {claim.lifecycle}")
        if not any(item.stance == "supports" for item in evidence):
            warnings.append("claim has no supporting evidence")
        if claim.valid_from is None or claim.valid_to is None:
            warnings.append("claim has an unknown valid-time bound")
        return ClaimExplanation(
            claim=claim,
            subject=subject.canonical_name,
            object=self._display_object(claim),
            evidence=evidence,
            relations=relations,
            warnings=tuple(warnings),
        )

    def recall(
        self,
        *,
        bank_id: str,
        query_text: str,
        as_of: str | None = None,
        max_items: int = 10,
        max_tokens: int = 512,
    ) -> tuple[RecallHit, ...]:
        """Retrieve source observations through valid, known, non-retired claims.

        The returned ``event_id`` is the stable observation ``source_key`` so benchmark
        fixtures and external systems can retain their own event identity.
        """

        bank = self.get_bank(bank_id)
        known_at = _canonical_timestamp(as_of) or utc_now()
        valid_at = _infer_valid_at(query_text, known_at)
        fts_query = _fts_query(query_text)
        if not fts_query or max_items <= 0 or max_tokens <= 0:
            return ()
        try:
            search_hits = self.retriever.search(
                bank_id=bank.id,
                lexical_query=fts_query,
                semantic_query=query_text,
                limit=max(max_items * 5, 20),
            )
        except sqlite3.OperationalError as error:
            raise ValidationError(f"Invalid recall query {query_text!r}.") from error

        attempt_candidates = self.recall_attempts(
            bank=bank.id,
            query_text=query_text,
            limit=1,
            as_of=known_at,
        )
        reserved_attempts = tuple(
            attempt
            for attempt in attempt_candidates
            if max(1, len(attempt.content.split())) <= max_tokens
        )
        reserved_tokens = sum(
            max(1, len(attempt.content.split())) for attempt in reserved_attempts
        )
        claim_item_limit = max_items - len(reserved_attempts)
        claim_token_limit = max_tokens - reserved_tokens

        recalled: list[RecallHit] = []
        seen_observations: set[str] = set()
        used_tokens = 0
        for search_hit in search_hits:
            if len(recalled) >= claim_item_limit:
                break
            if search_hit.resource_type != "claim":
                continue
            claim = self.claims.get(bank.id, search_hit.resource_id)
            if claim is None or not _claim_visible_at(claim, known_at, valid_at):
                continue
            for evidence in self.evidence.list_for_claim(bank.id, claim.id):
                if evidence.stance != "supports" or evidence.observation_id in seen_observations:
                    continue
                observation = self.observations.get(bank.id, evidence.observation_id)
                if observation is None or observation.ingestion_state == "deleted":
                    continue
                assessment = assess_retrieved_content(
                    observation.content,
                    trust_class=observation.trust_class,
                )
                if not assessment.safe_for_agent_context:
                    continue
                token_count = max(1, len(observation.content.split()))
                if token_count > claim_token_limit - used_tokens:
                    continue
                recalled.append(
                    RecallHit(
                        event_id=observation.source_key,
                        at=observation.observed_at,
                        content=observation.content,
                        bank_id=bank.slug,
                        scenario_id=str(
                            observation.metadata_json.get("scenario_id", "memorygraph")
                        ),
                        score=search_hit.score,
                        metadata={
                            "claim_id": claim.id,
                            "predicate": claim.predicate,
                            "lifecycle": claim.lifecycle,
                            "derivation_method": claim.origin,
                            "source_authority": observation.trust_class,
                            "evidence_explicitness": evidence.explicitness,
                            "evidence_strength": f"{evidence.source_reliability:.3f}",
                            "extraction_confidence": f"{evidence.extraction_confidence:.3f}",
                            "freshness_form": _freshness_form(claim, observation.metadata_json),
                            "currentness": _currentness_label(claim, known_at, valid_at),
                            "retrieval_channels": ",".join(search_hit.channels),
                            "security_disposition": assessment.disposition,
                            "security_reasons": ",".join(assessment.reasons),
                            "known_at": known_at,
                            "valid_at": valid_at,
                            "evidence_id": evidence.id,
                        },
                    )
                )
                seen_observations.add(observation.id)
                used_tokens += token_count
                if len(recalled) >= claim_item_limit:
                    break
        for attempt in reserved_attempts:
            token_count = max(1, len(attempt.content.split()))
            if token_count > max_tokens - used_tokens:
                continue
            recalled.append(
                RecallHit(
                    event_id=attempt.source_key,
                    at=attempt.episode.completed_at or attempt.episode.created_at,
                    content=attempt.content,
                    bank_id=bank.slug,
                    score=attempt.score,
                    metadata={
                        "memory_kind": "attempt",
                        "episode_id": attempt.episode.id,
                        "task_key": attempt.episode.task_key,
                        "outcome": attempt.episode.outcome,
                        "failure": attempt.episode.failure or "",
                        "applicability": json.dumps(
                            attempt.episode.applicability,
                            sort_keys=True,
                        ),
                        "environment": json.dumps(
                            attempt.episode.environment,
                            sort_keys=True,
                        ),
                        "freshness_form": "snapshot",
                    },
                )
            )
            used_tokens += token_count
            if len(recalled) >= max_items:
                break
        return tuple(recalled)

    def run_dream(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        mode: DreamRunMode | str = DreamRunMode.APPLY,
        trigger: str = "manual",
        observation_ids: tuple[str, ...] = (),
    ) -> DreamRunReport:
        """Run one bounded, durable proposal/validation/commit cycle.

        Providers can only return candidates. Deterministic validation and an atomic
        storage committer remain the sole path to mutating memory.
        """

        from memorygraph.application import EmbeddedDreamService

        return EmbeddedDreamService(self).run(
            bank=bank,
            provider=provider,
            mode=mode,
            trigger=trigger,
            observation_ids=observation_ids,
        )

    def queue_dream(
        self,
        *,
        bank: str,
        provider: DreamProvider | None = None,
        mode: DreamRunMode | str = DreamRunMode.APPLY,
        trigger: str = "manual",
        observation_ids: tuple[str, ...] = (),
    ) -> tuple[DreamRunRecord, DreamTaskRecord]:
        """Persist a Dream run and task for durable worker execution."""

        from memorygraph.application import EmbeddedDreamService

        return EmbeddedDreamService(self).enqueue(
            bank=bank,
            provider=provider,
            mode=mode,
            trigger=trigger,
            observation_ids=observation_ids,
        )

    def pending_reviews(self, *, bank: str, limit: int = 100) -> tuple[ReviewItemRecord, ...]:
        """List pending dream proposals that require human judgment."""

        bank_record = self.get_bank(bank)
        return self.review_items.list_pending(bank_record.id, limit=limit)

    def project_obsidian(
        self,
        *,
        bank: str,
        output_directory: str | Path,
    ) -> Any:
        """Regenerate the optional Obsidian-compatible human review projection."""

        from memorygraph.exporters import ObsidianProjector

        return ObsidianProjector(self).project(
            bank=bank,
            output_directory=output_directory,
        )

    def rollback(self, run_id: str, *, bank: str) -> RollbackResult:
        """Compensate a committed dream run without deleting its historical trace."""

        bank_record = self.get_bank(bank)
        original_run = self.dream_runs.get(bank_record.id, run_id)
        if original_run is None:
            raise NotFoundError(f"Dream run {run_id!r} does not exist in bank {bank!r}.")
        idempotency_key = f"dream-rollback:{bank_record.id}:{run_id}"
        existing = self.events.get_by_idempotency_key(bank_record.id, idempotency_key)
        if existing is not None:
            return _rollback_result_from_payload(existing.payload)

        proposals = tuple(
            proposal
            for proposal in self.dream_proposals.list_for_run(bank_record.id, run_id)
            if proposal.disposition == "committed"
        )
        if not proposals:
            raise ConflictError(f"Dream run {run_id!r} has no committed proposals to roll back.")
        all_events = self._all_events(bank_record.id)
        commit_events = {
            str(event.payload.get("proposal_id")): event
            for event in all_events
            if event.event_type == "dream.proposal.committed"
            and str(event.payload.get("run_id", run_id)) == run_id
        }
        missing = [proposal.id for proposal in proposals if proposal.id not in commit_events]
        if missing:
            raise ConflictError(
                "Rollback cannot prove the committed event range for proposals: "
                + ", ".join(missing)
            )

        inverse_steps: list[tuple[Any, MemoryEventRecord, ClaimRecord]] = []
        latest_system_time = datetime.now(UTC)
        for proposal in reversed(proposals):
            commit_event = commit_events[proposal.id]
            claim_id = str(commit_event.payload.get("claim_id", ""))
            claim = self.claims.get(bank_record.id, claim_id)
            if (
                claim is None
                or claim.system_to is not None
                or claim.lifecycle not in {"active", "contested"}
            ):
                raise ConflictError(
                    f"Rollback is unsafe because committed claim {claim_id!r} is no longer current."
                )
            self._assert_no_later_claim_event(
                all_events,
                after_sequence=commit_event.sequence,
                claim_id=claim.id,
            )
            action = _proposal_action(proposal.action)
            action_type = str(action.get("action_type", proposal.proposal_type))
            if action_type not in {"assert", "supersede"}:
                raise ConflictError(
                    f"Automatic rollback for dream action {action_type!r} requires review."
                )
            if action_type == "supersede":
                target_ids = tuple(str(item) for item in action.get("target_claim_ids", ()))
                if len(target_ids) != 1:
                    raise ConflictError("Rollback requires exactly one prior target claim.")
                source = self.claims.get(bank_record.id, target_ids[0])
                if source is None or not self.evidence.list_for_claim(bank_record.id, source.id):
                    raise ConflictError("Rollback cannot restore a prior claim without evidence.")
            claim_system_time = _parse_timestamp(claim.system_from)
            assert claim_system_time is not None
            latest_system_time = max(
                latest_system_time,
                claim_system_time + timedelta(microseconds=1),
            )
            inverse_steps.append((proposal, commit_event, claim))

        rollback_at = _format_timestamp(latest_system_time)
        rollback_run_id = str(uuid4())
        rollback_task_id = str(uuid4())
        retracted_ids: list[str] = []
        restored_ids: list[str] = []
        removed_evidence_ids: list[str] = []
        with transaction(self.connection):
            self.dream_runs.create(
                id=rollback_run_id,
                bank_id=bank_record.id,
                trigger="rollback",
                mode="apply",
                state="running",
                input_watermark=self.events.current_watermark(bank_record.id),
                policy_version="rollback-policy-v1",
                provider_config_hash=sha256(b"deterministic-compensation-v1").hexdigest(),
                attempt_count=1,
                started_at=rollback_at,
                created_at=rollback_at,
            )
            self.dream_tasks.create(
                id=rollback_task_id,
                bank_id=bank_record.id,
                dream_run_id=rollback_run_id,
                task_type="rollback",
                resource_type="dream_run",
                resource_id=run_id,
                idempotency_key=idempotency_key,
                state="running",
                input={"original_run_id": run_id},
                attempt_count=1,
                created_at=rollback_at,
            )
            for proposal, _, claim in inverse_steps:
                action = _proposal_action(proposal.action)
                action_type = str(action.get("action_type", proposal.proposal_type))
                retracted = self.retract_claim(
                    claim.id,
                    bank=bank_record.id,
                    known_at=rollback_at,
                    reason=f"compensating rollback of dream run {run_id}",
                    created_by_run_id=rollback_run_id,
                )
                retracted_ids.append(retracted.id)
                if action_type == "supersede":
                    target_id = str(tuple(action.get("target_claim_ids", ()))[0])
                    restored = self._restore_claim_version(
                        bank_id=bank_record.id,
                        source_claim_id=target_id,
                        replaced_claim_id=retracted.id,
                        rollback_run_id=rollback_run_id,
                        created_at=rollback_at,
                    )
                    restored_ids.append(restored.id)

            result = RollbackResult(
                original_run_id=run_id,
                rollback_run_id=rollback_run_id,
                retracted_claim_ids=tuple(retracted_ids),
                restored_claim_ids=tuple(restored_ids),
                removed_evidence_ids=tuple(removed_evidence_ids),
            )
            payload = {
                "original_run_id": result.original_run_id,
                "rollback_run_id": result.rollback_run_id,
                "retracted_claim_ids": list(result.retracted_claim_ids),
                "restored_claim_ids": list(result.restored_claim_ids),
                "removed_evidence_ids": list(result.removed_evidence_ids),
            }
            self.events.append(
                event_id=str(uuid4()),
                bank_id=bank_record.id,
                event_type="dream.run.rolled_back",
                aggregate_type="dream_run",
                aggregate_id=run_id,
                actor_type="system",
                actor_id=rollback_task_id,
                payload=payload,
                idempotency_key=idempotency_key,
                created_at=rollback_at,
            )
            self.dream_tasks.transition_state(
                bank_id=bank_record.id,
                task_id=rollback_task_id,
                from_states=("running",),
                to_state="completed",
                output=payload,
                completed_at=rollback_at,
            )
            self.dream_runs.transition_state(
                bank_id=bank_record.id,
                run_id=rollback_run_id,
                from_states=("running",),
                to_state="completed",
                usage={"compensated_proposals": len(inverse_steps)},
                completed_at=rollback_at,
            )
        return result

    def delete_observation(
        self,
        observation_id: str,
        *,
        bank: str,
    ) -> ObservationDeletionResult:
        """Privacy-delete source content and recompute directly dependent current claims."""

        bank_record = self.get_bank(bank)
        idempotency_key = f"observation-delete:{bank_record.id}:{observation_id}"
        existing = self.events.get_by_idempotency_key(bank_record.id, idempotency_key)
        if existing is not None:
            return _deletion_result_from_payload(existing.payload)
        observation = self.observations.get(bank_record.id, observation_id)
        if observation is None:
            raise NotFoundError(
                f"Observation {observation_id!r} does not exist in bank {bank!r}."
            )
        evidence = self.evidence.list_for_observation(bank_record.id, observation_id)
        deleted_content = observation.content
        affected_claim_ids = tuple(dict.fromkeys(item.claim_id for item in evidence))
        deleted_at = utc_now()
        retracted_ids: list[str] = []
        stale_artifact_ids: list[str] = []
        with transaction(self.connection):
            self.evidence.delete_for_observation(bank_record.id, observation_id)
            for chunk in observation.chunks:
                self.search.delete(bank_record.id, "observation_chunk", chunk.id)
                self.embeddings.delete_resource(
                    bank_id=bank_record.id,
                    resource_type="observation_chunk",
                    resource_id=chunk.id,
                )
            self.observations.tombstone(
                bank_id=bank_record.id,
                observation_id=observation_id,
                deleted_at=deleted_at,
            )
            for claim_id in affected_claim_ids:
                claim = self.claims.get(bank_record.id, claim_id)
                self.search.delete(bank_record.id, "claim", claim_id)
                self.embeddings.delete_resource(
                    bank_id=bank_record.id,
                    resource_type="claim",
                    resource_id=claim_id,
                )
                if claim is None:
                    continue
                remaining = self.evidence.list_for_claim(bank_record.id, claim.id)
                has_support = any(item.stance == "supports" for item in remaining)
                if claim.system_to is not None or claim.lifecycle not in {"active", "contested"}:
                    continue
                if has_support:
                    supporting = next(item for item in remaining if item.stance == "supports")
                    subject = self._entity_by_id(bank_record.id, claim.subject_entity_id)
                    object_entity = (
                        self._entity_by_id(bank_record.id, claim.object_entity_id)
                        if claim.object_entity_id is not None
                        else None
                    )
                    self._index_claim(
                        claim,
                        subject,
                        object_entity,
                        claim.object_value,
                        supporting,
                    )
                    continue
                retracted = self.retract_claim(
                    claim.id,
                    bank=bank_record.id,
                    known_at=_strict_successor_timestamp(claim.system_from, deleted_at),
                    reason="all supporting source evidence was deleted",
                )
                retracted_ids.append(retracted.id)
            for artifact in self.artifacts.list_current(
                bank_record.id,
                as_of=deleted_at,
            ):
                if not set(artifact.source_claim_ids).intersection(affected_claim_ids):
                    continue
                replacement_hash = sha256(
                    f"{bank_record.id}:{artifact.id}:{deleted_at}".encode()
                ).hexdigest()
                self.artifacts.redact(
                    bank_id=bank_record.id,
                    artifact_id=artifact.id,
                    replacement_content=f"[redacted derived artifact:{replacement_hash}]",
                    stale_at=deleted_at,
                )
                self.search.delete(bank_record.id, "artifact", artifact.id)
                self.embeddings.delete_resource(
                    bank_id=bank_record.id,
                    resource_type="artifact",
                    resource_id=artifact.id,
                )
                stale_artifact_ids.append(artifact.id)
            self.connection.execute(
                """
                UPDATE directives
                SET text = '[redacted deleted source]', enabled = 0, valid_to = ?
                WHERE bank_id = ? AND source_observation_id = ?
                """,
                (deleted_at, bank_record.id, observation_id),
            )
            procedural_hash = sha256(
                f"{bank_record.id}:{observation_id}:{deleted_at}".encode()
            ).hexdigest()
            self.procedural_episodes.redact_for_source(
                bank_id=bank_record.id,
                source_observation_id=observation_id,
                replacement=f"[redacted procedural source:{procedural_hash}]",
            )
            residue_issues = self._scan_exact_residue(
                bank_id=bank_record.id,
                deleted_content=deleted_content,
            )
            result = ObservationDeletionResult(
                observation_id=observation_id,
                affected_claim_ids=affected_claim_ids,
                retracted_claim_ids=tuple(retracted_ids),
                stale_artifact_ids=tuple(stale_artifact_ids),
                residue_issues=residue_issues,
            )
            self.events.append(
                event_id=str(uuid4()),
                bank_id=bank_record.id,
                event_type="observation.deleted",
                aggregate_type="observation",
                aggregate_id=observation_id,
                actor_type="administrator",
                payload={
                    "observation_id": observation_id,
                    "affected_claim_ids": list(result.affected_claim_ids),
                    "retracted_claim_ids": list(result.retracted_claim_ids),
                    "stale_artifact_ids": list(result.stale_artifact_ids),
                    "residue_issues": list(result.residue_issues),
                },
                idempotency_key=idempotency_key,
                created_at=deleted_at,
            )
        return result

    def _scan_exact_residue(
        self,
        *,
        bank_id: str,
        deleted_content: str,
    ) -> tuple[str, ...]:
        """Audit authoritative and derived SQLite text fields for exact source residue."""

        if not deleted_content:
            return ()
        checks = (
            ("observations", "id", ("content",)),
            ("observation_chunks", "id", ("content",)),
            ("claim_evidence", "id", ("excerpt",)),
            ("claims", "id", ("object_value_json",)),
            ("entities", "id", ("canonical_name", "description")),
            ("entity_aliases", "id", ("alias", "normalized_alias")),
            ("directives", "id", ("text",)),
            ("artifacts", "id", ("content",)),
            ("search_documents", "resource_id", ("title", "body", "metadata_text")),
            (
                "dream_proposals",
                "id",
                ("preconditions_json", "action_json", "model_trace_json", "validation_json"),
            ),
            ("review_items", "id", ("reason", "decision_json")),
            (
                "procedural_episodes",
                "id",
                ("task_key", "strategy", "failure", "applicability_json", "environment_json"),
            ),
        )
        issues: list[str] = []
        for table, id_column, text_columns in checks:
            selected = ", ".join((id_column, *text_columns))
            rows = self.connection.execute(
                f"SELECT {selected} FROM {table} WHERE bank_id = ?",
                (bank_id,),
            ).fetchall()
            for row in rows:
                if any(
                    deleted_content in str(row[column])
                    for column in text_columns
                    if row[column] is not None
                ):
                    issues.append(f"{table}:{row[id_column]}")
        return tuple(sorted(set(issues)))

    def _all_events(self, bank_id: str) -> tuple[MemoryEventRecord, ...]:
        events: list[MemoryEventRecord] = []
        cursor = 0
        while True:
            page = self.events.list_after(bank_id, sequence_exclusive=cursor, limit=500)
            if not page:
                break
            events.extend(page)
            cursor = page[-1].sequence
        return tuple(events)

    @staticmethod
    def _assert_no_later_claim_event(
        events: tuple[MemoryEventRecord, ...],
        *,
        after_sequence: int,
        claim_id: str,
    ) -> None:
        if any(
            event.sequence > after_sequence
            and event.aggregate_type == "claim"
            and event.aggregate_id == claim_id
            and event.event_type.startswith("claim.")
            for event in events
        ):
            raise ConflictError(
                f"Rollback is unsafe because claim {claim_id!r} has later dependent events."
            )

    def _restore_claim_version(
        self,
        *,
        bank_id: str,
        source_claim_id: str,
        replaced_claim_id: str,
        rollback_run_id: str,
        created_at: str,
    ) -> ClaimRecord:
        source = self.claims.get(bank_id, source_claim_id)
        if source is None:
            raise ConflictError(f"Rollback source claim {source_claim_id!r} disappeared.")
        restored = self.claims.create(
            id=str(uuid4()),
            bank_id=bank_id,
            subject_entity_id=source.subject_entity_id,
            predicate=source.predicate,
            object_kind=source.object_kind,
            object_entity_id=source.object_entity_id,
            object_value=source.object_value,
            polarity=source.polarity,
            valid_from=source.valid_from,
            valid_to=source.valid_to,
            system_from=created_at,
            lifecycle="active",
            origin=source.origin,
            importance=source.importance,
            created_by_run_id=rollback_run_id,
            created_at=created_at,
        )
        evidence = self._copy_evidence(source.id, restored, created_at=created_at)
        if not evidence:
            raise ConflictError("Rollback cannot restore a claim without retained evidence.")
        subject = self._entity_by_id(bank_id, restored.subject_entity_id)
        object_entity = (
            self._entity_by_id(bank_id, restored.object_entity_id)
            if restored.object_entity_id is not None
            else None
        )
        self._index_claim(restored, subject, object_entity, restored.object_value, evidence[0])
        self.relations.create(
            id=str(uuid4()),
            bank_id=bank_id,
            from_claim_id=restored.id,
            to_claim_id=replaced_claim_id,
            relation="supersedes",
            rationale="compensating dream-run rollback",
            decision_method="rule",
            decision_confidence=1.0,
            dream_run_id=rollback_run_id,
            created_at=created_at,
        )
        self.events.append(
            event_id=str(uuid4()),
            bank_id=bank_id,
            event_type="claim.restored",
            aggregate_type="claim",
            aggregate_id=restored.id,
            actor_type="system",
            actor_id=rollback_run_id,
            payload={
                "source_claim_id": source.id,
                "replaced_claim_id": replaced_claim_id,
                "restored_claim_id": restored.id,
            },
            created_at=created_at,
        )
        return restored

    def _resolve_object(
        self,
        *,
        bank_id: str,
        value: Any,
        object_kind: str,
        object_type: str,
    ) -> tuple[EntityRecord | None, Any]:
        try:
            ClaimObjectKind(object_kind)
        except ValueError as error:
            raise ValidationError(f"Unsupported claim object kind {object_kind!r}.") from error
        if object_kind == "entity":
            if not isinstance(value, str):
                raise ValidationError("Entity-valued claims require a string entity name.")
            return self.entity(value, bank=bank_id, entity_type=object_type), None
        if object_kind == "string" and not isinstance(value, str):
            raise ValidationError("String-valued claims require a string value.")
        if object_kind == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValidationError("Number-valued claims require an integer or float.")
        if object_kind == "boolean" and not isinstance(value, bool):
            raise ValidationError("Boolean-valued claims require true or false.")
        if object_kind == "datetime":
            if not isinstance(value, str):
                raise ValidationError("Datetime-valued claims require an ISO-8601 string.")
            value = _canonical_timestamp(value)
        if object_kind == "json" and not isinstance(value, (dict, list)):
            raise ValidationError("JSON-valued claims require an object or array.")
        try:
            json.dumps(value, sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ValidationError("Claim values must be JSON serializable.") from error
        return None, value

    def _observation(self, bank_id: str, observation_id: str) -> ObservationRecord:
        observation = self.observations.get(bank_id, observation_id)
        if observation is None:
            raise NotFoundError(
                f"Observation {observation_id!r} does not exist in the selected bank."
            )
        if observation.ingestion_state == "deleted":
            raise ValidationError(f"Observation {observation_id!r} has been deleted.")
        return observation

    def _entity_by_id(self, bank_id: str, entity_id: str) -> EntityRecord:
        entity = self.entities.get_entity(bank_id, entity_id)
        if entity is None:
            raise RuntimeError(f"Claim references missing entity {entity_id!r}.")
        return entity

    def _find_entity(self, bank_id: str, name: str) -> EntityRecord | None:
        entities = self.entities.list_by_name(bank_id, _normalize_entity_name(name))
        return entities[0] if entities else None

    def _current_claims(
        self,
        bank_id: str,
        subject_entity_id: str,
        predicate: str,
    ) -> tuple[ClaimRecord, ...]:
        return tuple(
            claim
            for claim in self.claims.list_versions(bank_id, subject_entity_id, predicate)
            if claim.system_to is None and claim.lifecycle in {"active", "contested"}
        )

    def _attach_evidence(
        self,
        *,
        claim: ClaimRecord,
        observation: ObservationRecord,
        excerpt: str | None,
        stance: str,
        created_at: str,
    ) -> ClaimEvidenceRecord:
        exact_excerpt = observation.content if excerpt is None else excerpt
        if not exact_excerpt:
            raise ValidationError("Evidence excerpts cannot be empty.")
        start_offset = observation.content.find(exact_excerpt)
        if start_offset < 0:
            raise ValidationError("Evidence excerpt must be an exact span of the observation.")
        end_offset = start_offset + len(exact_excerpt)
        reliability = _trust_reliability(observation.trust_class)
        try:
            return self.evidence.create(
                id=str(uuid4()),
                bank_id=claim.bank_id,
                claim_id=claim.id,
                observation_id=observation.id,
                chunk_id=observation.chunks[0].id if observation.chunks else None,
                start_offset=start_offset,
                end_offset=end_offset,
                excerpt=exact_excerpt,
                stance=stance,
                explicitness="explicit",
                source_reliability=reliability,
                extraction_confidence=1.0,
                extractor_name="memorygraph.explicit",
                extractor_version="1",
                created_at=created_at,
            )
        except ValueError as error:
            raise ValidationError(str(error)) from error

    def _copy_evidence(
        self,
        source_claim_id: str,
        target_claim: ClaimRecord,
        *,
        created_at: str,
    ) -> tuple[ClaimEvidenceRecord, ...]:
        copies: list[ClaimEvidenceRecord] = []
        for item in self.evidence.list_for_claim(target_claim.bank_id, source_claim_id):
            copies.append(
                self.evidence.create(
                    id=str(uuid4()),
                    bank_id=target_claim.bank_id,
                    claim_id=target_claim.id,
                    observation_id=item.observation_id,
                    chunk_id=item.chunk_id,
                    start_offset=item.start_offset,
                    end_offset=item.end_offset,
                    excerpt=item.excerpt,
                    stance=item.stance,
                    explicitness=item.explicitness,
                    source_reliability=item.source_reliability,
                    extraction_confidence=item.extraction_confidence,
                    extractor_name=item.extractor_name,
                    extractor_version=item.extractor_version,
                    created_at=created_at,
                )
            )
        return tuple(copies)

    def _index_claim(
        self,
        claim: ClaimRecord,
        subject: EntityRecord,
        object_entity: EntityRecord | None,
        object_value: Any,
        evidence: ClaimEvidenceRecord,
    ) -> None:
        display_object = (
            object_entity.canonical_name if object_entity else _display_value(object_value)
        )
        title = f"{subject.canonical_name} {claim.predicate.replace('_', ' ')} {display_object}"
        body = f"{title}\n{evidence.excerpt}"
        self.search.upsert(
            bank_id=claim.bank_id,
            resource_type="claim",
            resource_id=claim.id,
            title=title,
            body=body,
            metadata_text=f"{claim.predicate} {claim.lifecycle} {claim.origin}",
            content_sha256=sha256(body.encode("utf-8")).hexdigest(),
            created_at=claim.created_at,
        )
        if self.embedder is not None:
            vector = self.embedder.embed((body,))[0]
            self.embeddings.replace(
                bank_id=claim.bank_id,
                resource_type="claim",
                resource_id=claim.id,
                model=self.embedder.name,
                content_sha256=sha256(body.encode("utf-8")).hexdigest(),
                vector=vector,
                created_at=claim.created_at,
            )

    def _display_object(self, claim: ClaimRecord) -> Any:
        if claim.object_kind == "entity":
            assert claim.object_entity_id is not None
            return self._entity_by_id(claim.bank_id, claim.object_entity_id).canonical_name
        return claim.object_value

    def _record_event(
        self,
        *,
        bank_id: str,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        self.events.append(
            event_id=str(uuid4()),
            bank_id=bank_id,
            event_type=event_type,
            aggregate_type="claim",
            aggregate_id=aggregate_id,
            actor_type="user",
            payload=payload,
            created_at=created_at,
        )


_FTS_TOKEN = re.compile(r"[^\W_]+", flags=re.UNICODE)


def _normalize_entity_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalize_predicate(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip().casefold())
    if not normalized:
        raise ValueError("Predicate name cannot be empty.")
    if not re.fullmatch(r"[a-z][a-z0-9_.:-]*", normalized):
        raise ValidationError(
            "Predicate names must start with a letter and contain only letters, digits, "
            "underscore, dot, colon, or hyphen."
        )
    return normalized


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"Invalid ISO-8601 timestamp {value!r}.") from error
    if parsed.tzinfo is None:
        raise ValidationError("Timestamps must include a timezone.")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("Timestamps must include a timezone.")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _format_optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else _format_timestamp(value)


def _canonical_timestamp(value: str | None) -> str | None:
    parsed = _parse_timestamp(value)
    return _format_optional_timestamp(parsed)


def _strict_successor_timestamp(prior: str, candidate: str) -> str:
    prior_time = _parse_timestamp(prior)
    candidate_time = _parse_timestamp(candidate)
    assert prior_time is not None and candidate_time is not None
    return _format_timestamp(max(candidate_time, prior_time + timedelta(microseconds=1)))


def _transition_commit_time(prior: str, known_at: str | None) -> datetime:
    explicit = _parse_timestamp(known_at)
    if explicit is not None:
        return explicit
    candidate = _format_timestamp(datetime.now(UTC))
    adjusted = _parse_timestamp(_strict_successor_timestamp(prior, candidate))
    assert adjusted is not None
    return adjusted


def _proposal_action(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConflictError("Persisted dream proposal action is not an object.")
    nested = value.get("proposal", value)
    if not isinstance(nested, dict):
        raise ConflictError("Persisted dream proposal action envelope is invalid.")
    return nested


def _rollback_result_from_payload(value: Any) -> RollbackResult:
    if not isinstance(value, dict):
        raise ConflictError("Persisted rollback event payload is invalid.")
    return RollbackResult(
        original_run_id=str(value["original_run_id"]),
        rollback_run_id=str(value["rollback_run_id"]),
        retracted_claim_ids=tuple(str(item) for item in value.get("retracted_claim_ids", ())),
        restored_claim_ids=tuple(str(item) for item in value.get("restored_claim_ids", ())),
        removed_evidence_ids=tuple(str(item) for item in value.get("removed_evidence_ids", ())),
    )


def _deletion_result_from_payload(value: Any) -> ObservationDeletionResult:
    if not isinstance(value, dict):
        raise ConflictError("Persisted observation deletion event payload is invalid.")
    return ObservationDeletionResult(
        observation_id=str(value["observation_id"]),
        affected_claim_ids=tuple(str(item) for item in value.get("affected_claim_ids", ())),
        retracted_claim_ids=tuple(str(item) for item in value.get("retracted_claim_ids", ())),
        stale_artifact_ids=tuple(str(item) for item in value.get("stale_artifact_ids", ())),
        residue_issues=tuple(str(item) for item in value.get("residue_issues", ())),
    )


def _record_object_signature(claim: ClaimRecord) -> tuple[str, Any]:
    if claim.object_kind == "entity":
        return (claim.object_kind, claim.object_entity_id)
    return (claim.object_kind, claim.object_value)


def _object_signature(
    object_kind: str,
    object_entity: EntityRecord | None,
    object_value: Any,
) -> tuple[str, Any]:
    if object_kind == "entity":
        return (object_kind, object_entity.id if object_entity else None)
    return (object_kind, object_value)


def _trust_reliability(trust_class: str) -> float:
    return {
        "owner_explicit": 1.0,
        "authoritative_tool": 0.95,
        "authoritative_source": 0.9,
        "direct_observation": 0.85,
        "imported": 0.7,
        "model_generated": 0.4,
        "untrusted": 0.1,
    }.get(trust_class, 0.5)


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _freshness_form(claim: ClaimRecord, metadata: dict[str, Any]) -> str:
    declared = str(metadata.get("freshness_form", "")).casefold()
    if declared in {"timeless", "snapshot", "pointer"}:
        return declared
    if claim.valid_to is not None:
        return "snapshot"
    return "timeless"


def _currentness_label(claim: ClaimRecord, known_at: str, valid_at: str) -> str:
    if claim.lifecycle != "active" or not _claim_visible_at(claim, known_at, valid_at):
        return "not_current"
    if claim.valid_from is None or claim.valid_to is None:
        return "current_with_unknown_bound"
    return "current"


def _fts_query(query_text: str) -> str:
    tokens = []
    seen: set[str] = set()
    for token in _FTS_TOKEN.findall(query_text.casefold()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token.replace('"', '""'))
    return " OR ".join(f'"{token}"' for token in tokens)


_PROCEDURAL_STOPWORDS = {
    "and",
    "are",
    "did",
    "does",
    "for",
    "from",
    "how",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def _procedural_fts_query(query_text: str) -> str:
    tokens = []
    seen: set[str] = set()
    for token in _FTS_TOKEN.findall(query_text.casefold()):
        if len(token) < 3 or token in seen or token in _PROCEDURAL_STOPWORDS:
            continue
        seen.add(token)
        tokens.append(token.replace('"', '""'))
    return " OR ".join(f'"{token}"' for token in tokens)


def _infer_valid_at(query_text: str, known_at: str) -> str:
    """Infer a coarse valid-time target while keeping known-time explicit.

    The MVP intentionally handles only explicit English month names. Anything more
    sophisticated belongs in a provider proposal whose parsed time is then validated.
    """

    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    lowered = query_text.casefold()
    known = _parse_timestamp(known_at)
    assert known is not None
    for name, month in month_names.items():
        if re.search(rf"\b{name}\b", lowered):
            year = known.year if month <= known.month else known.year - 1
            return _format_timestamp(datetime(year, month, 15, 12, tzinfo=UTC))
    return known_at


def _claim_visible_at(claim: ClaimRecord, known_at: str, valid_at: str) -> bool:
    allowed_lifecycles = {"active", "contested"}
    if valid_at != known_at:
        allowed_lifecycles.update({"superseded", "retracted"})
    if claim.lifecycle not in allowed_lifecycles:
        return False
    if claim.system_from > known_at or (
        claim.system_to is not None and known_at >= claim.system_to
    ):
        return False
    if claim.valid_from is not None and valid_at < claim.valid_from:
        return False
    return claim.valid_to is None or valid_at < claim.valid_to
