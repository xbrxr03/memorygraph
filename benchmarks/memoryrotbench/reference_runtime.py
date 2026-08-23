"""Reference DreamRuntime implementation for deterministic contract testing."""

from __future__ import annotations

from dataclasses import dataclass

from .dream_contracts import (
    ArtifactRecord,
    CommitOutcome,
    DreamProposal,
    EvidenceSpan,
    RuntimeSnapshot,
)


class InjectedProviderFailure(RuntimeError):
    """Raised by the reference runtime to simulate provider failure after validation."""


@dataclass
class ClaimState:
    claim_id: str
    subject: str
    predicate: str
    object_value: str
    evidence_ids: tuple[str, ...]
    active: bool = True


class FakeDreamRuntime:
    """Small in-memory runtime implementing the DreamRuntime protocol."""

    def __init__(self, observations: dict[str, str]) -> None:
        self._observations = dict(observations)
        self._deleted_observations: set[str] = set()
        self._claims: dict[str, ClaimState] = {}
        self._idempotency_keys: dict[str, str] = {}
        self._current_by_slot: dict[str, str] = {}
        self._run_counter = 0
        self._version = 0
        self._committed_runs: list[str] = []
        self._history_claim_ids: list[str] = []
        self._run_log: dict[str, dict[str, str | None]] = {}
        self._artifacts: dict[str, ArtifactRecord] = {}

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            version=self._version,
            active_claim_ids=tuple(
                sorted(claim.claim_id for claim in self._claims.values() if claim.active)
            ),
            evidence_ids=tuple(
                sorted(
                    observation_id
                    for observation_id in self._observations
                    if observation_id not in self._deleted_observations
                )
            ),
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
        self._validate_evidence(proposal.evidence_spans)
        if fail_after_validation:
            raise InjectedProviderFailure("simulated provider failure after validation")

        run_id = self._next_run_id()
        slot = self._slot(proposal.subject, proposal.predicate)
        if proposal.replaces_claim_id and proposal.replaces_claim_id in self._claims:
            self._claims[proposal.replaces_claim_id].active = False
        claim = ClaimState(
            claim_id=proposal.claim_id,
            subject=proposal.subject,
            predicate=proposal.predicate,
            object_value=proposal.object_value,
            evidence_ids=tuple(span.observation_id for span in proposal.evidence_spans),
        )
        self._claims[proposal.claim_id] = claim
        if proposal.claim_id not in self._history_claim_ids:
            self._history_claim_ids.append(proposal.claim_id)
        self._current_by_slot[slot] = proposal.claim_id
        self._idempotency_keys[proposal.idempotency_key] = run_id
        self._committed_runs.append(run_id)
        self._run_log[run_id] = {
            "claim_id": proposal.claim_id,
            "slot": slot,
            "replaced": proposal.replaces_claim_id,
        }
        self._version += 1
        return CommitOutcome(status="committed", run_id=run_id)

    def rollback(self, run_id: str) -> CommitOutcome:
        if run_id not in self._run_log:
            return CommitOutcome(status="rejected", run_id=None, reason="unknown_run")
        log = self._run_log[run_id]
        claim_id = str(log["claim_id"])
        slot = str(log["slot"])
        replaced = log["replaced"]
        if claim_id in self._claims:
            self._claims[claim_id].active = False
        if replaced is not None and str(replaced) in self._claims:
            self._claims[str(replaced)].active = True
            self._current_by_slot[slot] = str(replaced)
        else:
            self._current_by_slot.pop(slot, None)
        rollback_run_id = self._next_run_id()
        self._committed_runs.append(rollback_run_id)
        self._version += 1
        return CommitOutcome(status="committed", run_id=rollback_run_id)

    def delete_evidence(self, observation_id: str) -> None:
        self._deleted_observations.add(observation_id)
        slots_to_remove: list[str] = []
        for claim in self._claims.values():
            if observation_id in claim.evidence_ids:
                claim.active = False
                slots_to_remove.append(self._slot(claim.subject, claim.predicate))
        for slot in slots_to_remove:
            current_claim_id = self._current_by_slot.get(slot)
            if current_claim_id and not self._claims[current_claim_id].active:
                self._current_by_slot.pop(slot, None)
        self._version += 1

    def refresh_artifact(
        self,
        artifact_id: str,
        *,
        body: str,
        source_claim_ids: tuple[str, ...],
        source_artifact_ids: tuple[str, ...] = (),
    ) -> ArtifactRecord:
        if source_artifact_ids:
            raise ValueError("artifacts may not be used as factual source evidence")
        for claim_id in source_claim_ids:
            if claim_id not in self._claims:
                raise ValueError(f"unknown source claim: {claim_id}")
        artifact = ArtifactRecord(
            artifact_id=artifact_id,
            body=body,
            source_claim_ids=tuple(source_claim_ids),
        )
        self._artifacts[artifact_id] = artifact
        return artifact

    def _validate_evidence(self, evidence_spans: tuple[EvidenceSpan, ...]) -> None:
        for span in evidence_spans:
            if span.observation_id not in self._observations:
                raise ValueError(f"unknown observation: {span.observation_id}")
            if span.observation_id in self._deleted_observations:
                raise ValueError(f"deleted observation: {span.observation_id}")
            content = self._observations[span.observation_id]
            if span.start < 0 or span.end <= span.start or span.end > len(content):
                raise ValueError("invalid evidence span")

    def _next_run_id(self) -> str:
        self._run_counter += 1
        return f"run-{self._run_counter}"

    @staticmethod
    def _slot(subject: str, predicate: str) -> str:
        return f"{subject}|{predicate}"
