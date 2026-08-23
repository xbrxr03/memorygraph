"""DreamRuntime adapter backed by the real embedded MemoryGraph engine."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from memorygraph import ConflictError, MemoryGraph

from .chaos_loader import ChaosCase
from .dream_contracts import (
    ArtifactRecord,
    CommitOutcome,
    DreamProposal,
    EvidenceSpan,
    RuntimeSnapshot,
)

BASE_SUPPORTED_ACCEPTANCE_CASES = {9, 10, 11, 12, 15}
if hasattr(MemoryGraph, "rollback"):
    BASE_SUPPORTED_ACCEPTANCE_CASES.add(13)
if hasattr(MemoryGraph, "delete_observation"):
    BASE_SUPPORTED_ACCEPTANCE_CASES.add(14)
SUPPORTED_ACCEPTANCE_CASES = frozenset(BASE_SUPPORTED_ACCEPTANCE_CASES)


class UnsupportedProductionHook(RuntimeError):
    """Raised when the current engine lacks a required runtime hook."""


class InjectedProviderFailure(RuntimeError):
    """Injected failure used to verify transaction rollback semantics."""


@dataclass(frozen=True)
class ProductionCaseSupport:
    case_id: str
    acceptance_case: int
    supported: bool
    reason: str


@dataclass(frozen=True)
class SyntheticDreamRecord:
    run_id: str
    task_id: str
    proposal_id: str
    slot: str
    external_claim_id: str
    replaced_external_claim_id: str | None
    subject: str
    predicate: str


class ProductionDreamRuntime:
    """Benchmark adapter over the current real MemoryGraph implementation."""

    def __init__(self, observations: dict[str, str]) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self._database_path = Path(self._tempdir.name) / "memoryrotbench-chaos.sqlite3"
        self.memory = MemoryGraph.open(self._database_path)
        self.bank = self.memory.create_bank("benchmark:chaos", name="MemoryRotBench Chaos")
        self._observation_content = dict(observations)
        self._observation_ids: dict[str, str] = {}
        self._claim_external_to_internal: dict[str, str] = {}
        self._active_claims: dict[str, bool] = {}
        self._current_by_slot: dict[str, str] = {}
        self._history_claim_ids: list[str] = []
        self._idempotency_keys: dict[str, str] = {}
        self._committed_runs: list[str] = []
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._version = 0
        self._run_counter = 0
        self._synthetic_records: dict[str, SyntheticDreamRecord] = {}
        self._seed_observations(observations)

    def close(self) -> None:
        self.memory.close()
        self._tempdir.cleanup()

    def __del__(self) -> None:  # pragma: no cover - cleanup fallback
        with suppress(Exception):
            self.close()

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            version=self._version,
            active_claim_ids=tuple(
                sorted(claim_id for claim_id, active in self._active_claims.items() if active)
            ),
            evidence_ids=tuple(sorted(self._observation_ids)),
            current_view=tuple(sorted(self._current_by_slot.items())),
            artifact_ids=tuple(sorted(self._artifacts)),
            committed_run_ids=tuple(self._committed_runs),
            history_claim_ids=tuple(self._history_claim_ids),
        )

    def process_proposal(
        self, proposal: DreamProposal, *, fail_after_validation: bool = False
    ) -> CommitOutcome:
        if proposal.idempotency_key in self._idempotency_keys:
            return CommitOutcome(
                status="duplicate",
                run_id=self._idempotency_keys[proposal.idempotency_key],
                reason="idempotency_key_replay",
            )
        if (
            proposal.precondition_version is not None
            and proposal.precondition_version != self._version
        ):
            return CommitOutcome(status="stale", run_id=None, reason="stale_precondition")

        self._validate_spans(proposal.evidence_spans)
        self._ensure_predicate(proposal.predicate)
        run_id = self._next_run_id()
        created_at = self._known_at(proposal.evidence_spans[0])
        synthetic = self._begin_synthetic_run(proposal, run_id=run_id, created_at=created_at)

        original_record_event = self.memory._record_event
        if fail_after_validation:

            def fail_commit(**_: object) -> None:
                raise InjectedProviderFailure("simulated provider failure after validation")

            self.memory._record_event = fail_commit  # type: ignore[method-assign]

        try:
            if proposal.replaces_claim_id is None:
                claim = self.memory.assert_claim(
                    bank=self.bank.id,
                    subject=proposal.subject,
                    predicate=proposal.predicate,
                    object=proposal.object_value,
                    object_kind="string",
                    observation_id=self._internal_observation_id(proposal.evidence_spans[0]),
                    excerpt=self._excerpt(proposal.evidence_spans[0]),
                    known_at=self._known_at(proposal.evidence_spans[0]),
                )
            else:
                prior_internal_id = self._claim_external_to_internal.get(proposal.replaces_claim_id)
                if prior_internal_id is None:
                    raise UnsupportedProductionHook(
                        f"replacement target {proposal.replaces_claim_id!r} is unknown"
                    )
                claim = self.memory.supersede_claim(
                    prior_internal_id,
                    bank=self.bank.id,
                    object=proposal.object_value,
                    object_kind="string",
                    observation_id=self._internal_observation_id(proposal.evidence_spans[0]),
                    excerpt=self._excerpt(proposal.evidence_spans[0]),
                    known_at=self._known_at(proposal.evidence_spans[0]),
                    rationale="MemoryRotBench chaos supersession",
                )
        except Exception as exc:
            self._fail_synthetic_run(synthetic, error=exc, completed_at=created_at)
            raise
        finally:
            self.memory._record_event = original_record_event  # type: ignore[method-assign]

        self._claim_external_to_internal[proposal.claim_id] = claim.id
        self._active_claims[proposal.claim_id] = True
        if proposal.claim_id not in self._history_claim_ids:
            self._history_claim_ids.append(proposal.claim_id)
        if proposal.replaces_claim_id is not None:
            self._active_claims[proposal.replaces_claim_id] = False
        self._current_by_slot[synthetic.slot] = proposal.claim_id
        self._idempotency_keys[proposal.idempotency_key] = run_id
        self._committed_runs.append(run_id)
        self._complete_synthetic_run(
            synthetic,
            proposal=proposal,
            internal_claim_id=claim.id,
            completed_at=created_at,
        )
        self._version += 1
        return CommitOutcome(status="committed", run_id=run_id)

    def rollback(self, run_id: str) -> CommitOutcome:
        rollback_method = getattr(self.memory, "rollback", None)
        if rollback_method is None:
            raise UnsupportedProductionHook("MemoryGraph has no public rollback(run_id) hook")
        synthetic = self._synthetic_records.get(run_id)
        if synthetic is None:
            return CommitOutcome(status="rejected", run_id=None, reason="unknown_run")
        rollback_result = rollback_method(run_id, bank=self.bank.id)
        self._active_claims[synthetic.external_claim_id] = False
        self._remove_from_current_view(synthetic.external_claim_id)
        if synthetic.replaced_external_claim_id is not None:
            if rollback_result.restored_claim_ids:
                self._claim_external_to_internal[synthetic.replaced_external_claim_id] = (
                    rollback_result.restored_claim_ids[0]
                )
            self._active_claims[synthetic.replaced_external_claim_id] = True
            self._current_by_slot[synthetic.slot] = synthetic.replaced_external_claim_id
        rollback_run_id = self._next_run_id()
        self._committed_runs.append(rollback_run_id)
        self._version += 1
        return CommitOutcome(status="committed", run_id=rollback_run_id)

    def delete_evidence(self, observation_id: str) -> None:
        delete_method = getattr(self.memory, "delete_observation", None)
        if delete_method is None:
            raise UnsupportedProductionHook(
                "MemoryGraph has no public source observation/evidence deletion hook"
            )
        internal_observation_id = self._observation_ids[observation_id]
        reverse_lookup = {
            internal: external for external, internal in self._claim_external_to_internal.items()
        }
        deletion = delete_method(internal_observation_id, bank=self.bank.id)
        self._observation_ids.pop(observation_id, None)
        self._observation_content.pop(observation_id, None)
        for affected_internal_id in deletion.affected_claim_ids:
            external_claim_id = reverse_lookup.get(affected_internal_id)
            if external_claim_id is None:
                continue
            affected_claim = self.memory.claims.get(self.bank.id, affected_internal_id)
            if (
                affected_claim is not None
                and affected_claim.system_to is None
                and affected_claim.lifecycle in {"active", "contested"}
            ):
                continue
            self._active_claims[external_claim_id] = False
            self._remove_from_current_view(external_claim_id)
        self._version += 1

    def refresh_artifact(
        self,
        artifact_id: str,
        *,
        body: str,
        source_claim_ids: Iterable[str],
        source_artifact_ids: Iterable[str] = (),
    ) -> ArtifactRecord:
        source_artifact_ids = tuple(source_artifact_ids)
        if source_artifact_ids:
            raise ValueError("artifacts may not be used as factual source evidence")
        source_claim_ids = tuple(source_claim_ids)
        for claim_id in source_claim_ids:
            if claim_id not in self._claim_external_to_internal:
                raise ValueError(f"unknown source claim: {claim_id}")
        watermark = self.memory.events.current_watermark(self.bank.id)
        created_at = _utc_now()
        record = self.memory.artifacts.create(
            id=artifact_id,
            bank_id=self.bank.id,
            kind="profile",
            artifact_key=artifact_id,
            content=body,
            source_claim_ids=list(source_claim_ids),
            source_watermark=watermark,
            generator_name="memoryrotbench.production_runtime",
            generator_version="1",
            created_at=created_at,
        )
        artifact = ArtifactRecord(
            artifact_id=record.id,
            body=record.content,
            source_claim_ids=tuple(record.source_claim_ids),
        )
        self._artifacts[artifact_id] = artifact
        return artifact

    def _seed_observations(self, observations: dict[str, str]) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for ordinal, (observation_id, content) in enumerate(observations.items()):
            observed_at = (base + timedelta(seconds=ordinal)).isoformat().replace("+00:00", "Z")
            observation = self.memory.observe(
                content,
                bank=self.bank.id,
                source_key=observation_id,
                observed_at=observed_at,
            )
            self._observation_ids[observation_id] = observation.id

    def _begin_synthetic_run(
        self,
        proposal: DreamProposal,
        *,
        run_id: str,
        created_at: str,
    ) -> SyntheticDreamRecord:
        task_id = f"{run_id}:task"
        proposal_id = f"{run_id}:proposal"
        slot = self._slot(proposal.subject, proposal.predicate)
        watermark = self.memory.events.current_watermark(self.bank.id)
        self.memory.dream_runs.create(
            id=run_id,
            bank_id=self.bank.id,
            trigger="memoryrotbench-chaos",
            mode="apply",
            state="running",
            input_watermark=watermark,
            policy_version="memoryrotbench-chaos-v1",
            provider_config_hash="memoryrotbench-production-runtime",
            started_at=created_at,
            created_at=created_at,
        )
        self.memory.dream_tasks.create(
            id=task_id,
            bank_id=self.bank.id,
            dream_run_id=run_id,
            task_type="chaos_process_proposal",
            resource_type="claim",
            resource_id=proposal.claim_id,
            idempotency_key=proposal.idempotency_key,
            state="running",
            input=self._proposal_payload(proposal),
            created_at=created_at,
        )
        self.memory.dream_proposals.create(
            id=proposal_id,
            bank_id=self.bank.id,
            dream_run_id=run_id,
            proposal_type=self._proposal_type(proposal),
            preconditions={
                "precondition_version": proposal.precondition_version,
                "idempotency_key": proposal.idempotency_key,
            },
            action=self._proposal_payload(proposal),
            evidence_ids=[span.observation_id for span in proposal.evidence_spans],
            disposition="pending",
            created_at=created_at,
        )
        record = SyntheticDreamRecord(
            run_id=run_id,
            task_id=task_id,
            proposal_id=proposal_id,
            slot=slot,
            external_claim_id=proposal.claim_id,
            replaced_external_claim_id=proposal.replaces_claim_id,
            subject=proposal.subject,
            predicate=proposal.predicate,
        )
        self._synthetic_records[run_id] = record
        return record

    def _complete_synthetic_run(
        self,
        record: SyntheticDreamRecord,
        *,
        proposal: DreamProposal,
        internal_claim_id: str,
        completed_at: str,
    ) -> None:
        self.memory.dream_proposals.update(
            bank_id=self.bank.id,
            proposal_id=record.proposal_id,
            disposition="committed",
            validation={"status": "committed"},
        )
        self.memory.dream_tasks.transition_state(
            bank_id=self.bank.id,
            task_id=record.task_id,
            from_states=("running",),
            to_state="completed",
            output={"proposal_id": record.proposal_id, "claim_id": internal_claim_id},
            completed_at=completed_at,
        )
        self.memory.dream_runs.transition_state(
            bank_id=self.bank.id,
            run_id=record.run_id,
            from_states=("running",),
            to_state="completed",
            usage={"committed_claim_id": internal_claim_id},
            completed_at=completed_at,
        )
        self.memory.events.append(
            event_id=str(uuid4()),
            bank_id=self.bank.id,
            event_type="dream.proposal.committed",
            aggregate_type="dream_proposal",
            aggregate_id=record.proposal_id,
            actor_type="worker",
            payload={
                "run_id": record.run_id,
                "task_id": record.task_id,
                "proposal_id": record.proposal_id,
                "claim_id": internal_claim_id,
                "external_claim_id": proposal.claim_id,
            },
            idempotency_key=f"dream-commit:{record.proposal_id}",
            created_at=completed_at,
        )

    def _fail_synthetic_run(
        self,
        record: SyntheticDreamRecord,
        *,
        error: Exception,
        completed_at: str,
    ) -> None:
        self.memory.dream_proposals.update(
            bank_id=self.bank.id,
            proposal_id=record.proposal_id,
            disposition="rejected",
            validation={"error": error.__class__.__name__},
        )
        self.memory.dream_tasks.transition_state(
            bank_id=self.bank.id,
            task_id=record.task_id,
            from_states=("running",),
            to_state="failed",
            error={"type": error.__class__.__name__, "message": str(error)},
            completed_at=completed_at,
        )
        self.memory.dream_runs.transition_state(
            bank_id=self.bank.id,
            run_id=record.run_id,
            from_states=("running",),
            to_state="failed",
            error={"type": error.__class__.__name__, "message": str(error)},
            completed_at=completed_at,
        )

    def _validate_spans(self, spans: tuple[EvidenceSpan, ...]) -> None:
        for span in spans:
            content = self._observation_content.get(span.observation_id)
            if content is None:
                raise ValueError(f"unknown observation: {span.observation_id}")
            if span.start < 0 or span.end <= span.start or span.end > len(content):
                raise ValueError("invalid evidence span")

    def _ensure_predicate(self, predicate: str) -> None:
        try:
            self.memory.define_predicate(
                predicate,
                bank=self.bank.id,
                cardinality="one",
                volatility="volatile",
            )
        except ConflictError:
            return

    def _internal_observation_id(self, span: EvidenceSpan) -> str:
        return self._observation_ids[span.observation_id]

    def _excerpt(self, span: EvidenceSpan) -> str:
        return self._observation_content[span.observation_id][span.start : span.end]

    def _known_at(self, span: EvidenceSpan) -> str:
        observation = self.memory.observations.get(
            self.bank.id,
            self._internal_observation_id(span),
        )
        if observation is None:
            raise RuntimeError(f"missing observation {span.observation_id!r}")
        return observation.observed_at

    def _next_run_id(self) -> str:
        self._run_counter += 1
        return f"run-{self._run_counter}"

    @staticmethod
    def _slot(subject: str, predicate: str) -> str:
        return f"{subject}|{predicate}"

    def _proposal_payload(self, proposal: DreamProposal) -> dict[str, object]:
        action_type = "supersede" if proposal.replaces_claim_id is not None else "assert"
        payload: dict[str, object] = {
            "action_type": action_type,
            "proposal_id": proposal.proposal_id,
            "claim_id": proposal.claim_id,
            "subject": proposal.subject,
            "predicate": proposal.predicate,
            "object_value": proposal.object_value,
            "precondition_version": proposal.precondition_version,
            "replaces_claim_id": proposal.replaces_claim_id,
            "evidence_spans": [
                {
                    "observation_id": span.observation_id,
                    "start": span.start,
                    "end": span.end,
                }
                for span in proposal.evidence_spans
            ],
        }
        if proposal.replaces_claim_id is not None:
            internal_target_id = self._claim_external_to_internal.get(proposal.replaces_claim_id)
            if internal_target_id is None:
                raise UnsupportedProductionHook(
                    f"replacement target {proposal.replaces_claim_id!r} is unknown"
                )
            payload["target_claim_ids"] = [internal_target_id]
        return payload

    @staticmethod
    def _proposal_type(proposal: DreamProposal) -> str:
        if proposal.replaces_claim_id is not None:
            return "supersede"
        return "assert"

    def _remove_from_current_view(self, external_claim_id: str) -> None:
        for slot, claim_id in tuple(self._current_by_slot.items()):
            if claim_id == external_claim_id:
                self._current_by_slot.pop(slot, None)

    def _recompute_slot_state(self, subject: str, predicate: str) -> None:
        history = self.memory.history(
            bank=self.bank.id,
            subject=subject,
            predicate=predicate,
            current_versions_only=True,
        )
        slot = self._slot(subject, predicate)
        self._current_by_slot.pop(slot, None)
        reverse_lookup = {
            internal: external for external, internal in self._claim_external_to_internal.items()
        }
        for external_claim_id in list(self._active_claims):
            if slot == self._slot_for_external(external_claim_id):
                self._active_claims[external_claim_id] = False
        for item in history:
            external_claim_id = reverse_lookup.get(item.claim.id)
            if external_claim_id is None:
                continue
            self._active_claims[external_claim_id] = item.claim.lifecycle in {"active", "contested"}
            if item.claim.lifecycle in {"active", "contested"}:
                self._current_by_slot[slot] = external_claim_id
                break

    def _slot_for_external(self, external_claim_id: str) -> str:
        for record in self._synthetic_records.values():
            if (
                record.external_claim_id == external_claim_id
                or record.replaced_external_claim_id == external_claim_id
            ):
                return record.slot
        return ""


def support_matrix(cases: Iterable[ChaosCase]) -> tuple[ProductionCaseSupport, ...]:
    rows: list[ProductionCaseSupport] = []
    for case in cases:
        if case.acceptance_case in SUPPORTED_ACCEPTANCE_CASES:
            rows.append(
                ProductionCaseSupport(
                    case_id=case.case_id,
                    acceptance_case=case.acceptance_case,
                    supported=True,
                    reason=(
                        "supported by current MemoryGraph API plus adapter-level "
                        "precondition/idempotency guards"
                    ),
                )
            )
            continue
        if case.acceptance_case == 13:
            reason = (
                "supported by current MemoryGraph API plus adapter-level "
                "precondition/idempotency guards"
                if hasattr(MemoryGraph, "rollback")
                else "missing public rollback(run_id) hook"
            )
            supported = hasattr(MemoryGraph, "rollback")
        elif case.acceptance_case == 14:
            reason = (
                "supported by current MemoryGraph API plus adapter-level "
                "precondition/idempotency guards"
                if hasattr(MemoryGraph, "delete_observation")
                else "missing public source observation/evidence deletion hook"
            )
            supported = hasattr(MemoryGraph, "delete_observation")
        else:
            reason = "unsupported by current production adapter"
            supported = False
        rows.append(
            ProductionCaseSupport(
                case_id=case.case_id,
                acceptance_case=case.acceptance_case,
                supported=supported,
                reason=reason,
            )
        )
    return tuple(rows)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
