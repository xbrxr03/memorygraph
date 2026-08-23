"""Integration-ready dream runtime contracts for phase-2 acceptance tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class EvidenceSpan:
    observation_id: str
    start: int
    end: int


@dataclass(frozen=True)
class DreamProposal:
    proposal_id: str
    idempotency_key: str
    claim_id: str
    subject: str
    predicate: str
    object_value: str
    evidence_spans: tuple[EvidenceSpan, ...]
    precondition_version: int | None = None
    replaces_claim_id: str | None = None


@dataclass(frozen=True)
class CommitOutcome:
    status: Literal["committed", "rejected", "duplicate", "stale"]
    run_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    body: str
    source_claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeSnapshot:
    version: int
    active_claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    current_view: tuple[tuple[str, str], ...]
    artifact_ids: tuple[str, ...]
    committed_run_ids: tuple[str, ...]
    history_claim_ids: tuple[str, ...]


class DreamRuntime(Protocol):
    """Minimal contract the root runtime can satisfy for acceptance testing."""

    def snapshot(self) -> RuntimeSnapshot: ...

    def process_proposal(
        self, proposal: DreamProposal, *, fail_after_validation: bool = False
    ) -> CommitOutcome: ...

    def rollback(self, run_id: str) -> CommitOutcome: ...

    def delete_evidence(self, observation_id: str) -> None: ...

    def refresh_artifact(
        self,
        artifact_id: str,
        *,
        body: str,
        source_claim_ids: Sequence[str],
        source_artifact_ids: Sequence[str] = (),
    ) -> ArtifactRecord: ...
