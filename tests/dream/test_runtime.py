from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from memorygraph.domain import plan_confirm
from memorygraph.dream import (
    BatchCommitResult,
    ChallengeRequest,
    ChallengeResult,
    ClaimObjectCandidate,
    ClaimStatePrecondition,
    ClaimVersionToken,
    DeterministicDreamProvider,
    DreamAction,
    DreamActionKind,
    DreamProposal,
    DreamProposalValidator,
    DreamRunMode,
    DreamRunStatus,
    DreamRuntime,
    DreamTask,
    EvidenceSpanCandidate,
    EvidenceSpanCheck,
    ExtractedClaimCandidate,
    ExtractionCandidateBatch,
    ExtractionResult,
    IdempotencyPrecondition,
    ProposalCommitOutcome,
    ProposalCommitStatus,
    ProposalDisposition,
    ProposalPreconditions,
    ProviderCallTrace,
    ProviderOperation,
    SourceBundle,
    SourceObservation,
    ValidationContext,
)
from memorygraph.models import (
    Claim,
    ClaimLifecycle,
    ClaimObjectKind,
    ClaimOrigin,
    ClaimPolarity,
    EvidenceExplicitness,
    PredicateCardinality,
    PredicateDefinition,
    PredicateVolatility,
)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def make_claim(
    claim_id: str = "c1",
    *,
    system_from: datetime | None = None,
) -> Claim:
    created_at = system_from or ts("2026-01-01T00:00:00Z")
    return Claim(
        id=claim_id,
        bank_id="bank-1",
        subject_entity_id="project-1",
        predicate="uses_build_backend",
        object_kind=ClaimObjectKind.STRING,
        object_entity_id=None,
        object_value_json='"poetry"',
        polarity=ClaimPolarity.POSITIVE,
        valid_from=created_at,
        valid_to=None,
        system_from=created_at,
        system_to=None,
        lifecycle=ClaimLifecycle.ACTIVE,
        origin=ClaimOrigin.EXPLICIT,
        importance=0.7,
        created_at=created_at,
    )


def make_proposal(
    *,
    claim: Claim,
    proposal_id: str = "p1",
    evidence_candidate_id: str = "ev1",
) -> DreamProposal:
    action = DreamAction(
        action_type=DreamActionKind.CONFIRM,
        bank_id="bank-1",
        predicate_definition=PredicateDefinition(
            name="uses_build_backend",
            cardinality=PredicateCardinality.ONE,
            volatility=PredicateVolatility.VOLATILE,
            bank_id="bank-1",
        ),
        decision_confidence=0.97,
        transition_plan=plan_confirm(
            claim,
            commit_time=ts("2026-03-10T00:00:00Z"),
            evidence_ids=(evidence_candidate_id,),
        ),
        target_claim_ids=(claim.id,),
        evidence_ids=(evidence_candidate_id,),
    )
    preconditions = ProposalPreconditions(
        bank_id="bank-1",
        observed_event_watermark=10,
        claim_state_preconditions=(
            ClaimStatePrecondition(
                claim_id=claim.id,
                bank_id="bank-1",
                expected_token=ClaimVersionToken.from_claim(claim),
            ),
        ),
        idempotency=IdempotencyPrecondition(
            key=f"proposal:{proposal_id}",
            fingerprint="placeholder",
        ),
    )
    draft = DreamProposal(
        id=proposal_id,
        bank_id="bank-1",
        action=action,
        preconditions=preconditions,
    )
    return replace(
        draft,
        preconditions=ProposalPreconditions(
            bank_id="bank-1",
            observed_event_watermark=10,
            claim_state_preconditions=preconditions.claim_state_preconditions,
            idempotency=IdempotencyPrecondition(
                key=f"proposal:{proposal_id}",
                fingerprint=draft.action_fingerprint(),
            ),
        ),
    )


def make_bundle(bundle_id: str = "bundle-1") -> SourceBundle:
    return SourceBundle(
        bundle_id=bundle_id,
        bank_id="bank-1",
        reason="new_explicit_correction",
        priority=100,
        observations=(
            SourceObservation(
                observation_id="o1",
                source_key="thread:1",
                content="We switched to hatchling.",
                actor_type="user",
                actor_id="abrar",
                observed_at=ts("2026-03-10T00:00:00Z"),
            ),
        ),
    )


def make_extraction_result(bundle_id: str = "bundle-1") -> ExtractionResult:
    candidate = ExtractedClaimCandidate(
        local_id="claim-1",
        subject_local_id="entity-1",
        predicate="uses_build_backend",
        object_candidate=ClaimObjectCandidate(kind=ClaimObjectKind.STRING, value="hatchling"),
        polarity=ClaimPolarity.POSITIVE,
        explicitness=EvidenceExplicitness.EXPLICIT,
        evidence_spans=(
            EvidenceSpanCandidate(
                candidate_id="ev1",
                observation_id="o1",
                start_offset=0,
                end_offset=24,
                excerpt="We switched to hatchling.",
            ),
        ),
        extraction_confidence=0.98,
    )
    return ExtractionResult(
        candidates=ExtractionCandidateBatch(claims=(candidate,)),
        trace=ProviderCallTrace(
            operation=ProviderOperation.EXTRACT,
            provider_name="deterministic",
            model_name="test-extractor",
            latency_ms=5,
        ),
    )


def make_challenge_trace() -> ProviderCallTrace:
    return ProviderCallTrace(
        operation=ProviderOperation.CHALLENGE,
        provider_name="deterministic",
        model_name="test-challenger",
        latency_ms=3,
    )


class StubContextBuilder:
    def __init__(self, bundle: SourceBundle, contexts: dict[str, ValidationContext]) -> None:
        self.bundle = bundle
        self.contexts = contexts

    def build_source_bundle(self, task: DreamTask) -> SourceBundle:
        return self.bundle

    def build_validation_context(
        self,
        task: DreamTask,
        proposal: DreamProposal,
    ) -> ValidationContext:
        return self.contexts[proposal.id]


class StubPipeline:
    def __init__(
        self,
        proposals_by_candidate: dict[str, DreamProposal],
        *,
        challenge_proposal_ids: tuple[str, ...] = (),
    ) -> None:
        self.proposals_by_candidate = proposals_by_candidate
        self.challenge_proposal_ids = set(challenge_proposal_ids)

    def proposals_from_extraction(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        extraction: ExtractionResult,
    ) -> tuple[DreamProposal, ...]:
        return tuple(
            self.proposals_by_candidate[item.local_id] for item in extraction.candidates.claims
        )

    def should_challenge(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> bool:
        return proposal.id in self.challenge_proposal_ids

    def build_challenge_request(
        self,
        task: DreamTask,
        source_bundle: SourceBundle,
        proposal: DreamProposal,
    ) -> ChallengeRequest:
        return ChallengeRequest(
            proposal_id=proposal.id,
            bank_id=task.bank_id,
            source_bundle_id=source_bundle.bundle_id,
            proposal=proposal,
            evidence_candidate_ids=proposal.action.referenced_evidence_ids(),
        )

    def apply_challenge_result(self, proposal: DreamProposal, challenge_result) -> DreamProposal:
        return replace(
            proposal,
            challenger_objections=proposal.challenger_objections + challenge_result.objections,
        )


class RecordingCommitter:
    def __init__(
        self,
        result: BatchCommitResult,
        provider: DeterministicDreamProvider | None = None,
    ) -> None:
        self.result = result
        self.provider = provider
        self.calls = 0
        self.provider_calls_seen_at_commit: list[tuple[str, str]] | None = None

    def commit_batch(self, task: DreamTask, proposals) -> BatchCommitResult:
        self.calls += 1
        if self.provider is not None:
            self.provider_calls_seen_at_commit = list(self.provider.call_log)
        return self.result


def make_context(claim: Claim, *, current_event_watermark: int = 10) -> ValidationContext:
    return ValidationContext(
        current_event_watermark=current_event_watermark,
        evidence_checks={
            "ev1": EvidenceSpanCheck(
                evidence_id="ev1",
                bank_id="bank-1",
                is_valid=True,
            )
        },
        current_claim_tokens={claim.id: ClaimVersionToken.from_claim(claim)},
    )


def make_task(mode: DreamRunMode = DreamRunMode.APPLY) -> DreamTask:
    return DreamTask(
        run_id="run-1",
        task_id="task-1",
        bank_id="bank-1",
        trigger="manual",
        mode=mode,
        input_watermark=10,
        reason="test",
        observation_ids=("o1",),
    )


def test_runtime_batches_provider_calls_before_commit() -> None:
    claim = make_claim()
    proposal = make_proposal(claim=claim)
    bundle = make_bundle()
    extraction = make_extraction_result()
    provider = DeterministicDreamProvider(
        extraction_results_by_bundle={"bundle-1": extraction},
        challenge_results_by_proposal={
            "p1": ChallengeResult(objections=(), trace=make_challenge_trace())
        },
    )
    committer = RecordingCommitter(
        result=BatchCommitResult(
            outcomes=(
                ProposalCommitOutcome(
                    proposal_id="p1",
                    status=ProposalCommitStatus.COMMITTED,
                ),
            ),
            committed_event_range=(101, 103),
        ),
        provider=provider,
    )
    runtime = DreamRuntime(
        context_builder=StubContextBuilder(bundle, {"p1": make_context(claim)}),
        pipeline=StubPipeline({"claim-1": proposal}, challenge_proposal_ids=("p1",)),
        provider=provider,
        validator=DreamProposalValidator(),
        committer=committer,
    )

    report = runtime.run(make_task())

    assert report.status is DreamRunStatus.COMPLETED
    assert provider.call_log == [("extract", "bundle-1"), ("challenge", "p1")]
    assert committer.calls == 1
    assert committer.provider_calls_seen_at_commit == [("extract", "bundle-1"), ("challenge", "p1")]
    assert report.proposal_results[0].commit_outcome is not None
    assert report.proposal_results[0].commit_outcome.status is ProposalCommitStatus.COMMITTED
    assert report.metrics.committed == 1


def test_runtime_provider_failure_returns_failed_report_and_skips_commit() -> None:
    claim = make_claim()
    proposal = make_proposal(claim=claim)
    bundle = make_bundle()
    provider = DeterministicDreamProvider(
        extraction_results_by_bundle={"bundle-1": make_extraction_result()},
        challenge_results_by_proposal={
            "p1": ChallengeResult(objections=(), trace=make_challenge_trace())
        },
        fail_challenge_proposal_ids=("p1",),
    )
    committer = RecordingCommitter(result=BatchCommitResult(outcomes=()))
    runtime = DreamRuntime(
        context_builder=StubContextBuilder(bundle, {"p1": make_context(claim)}),
        pipeline=StubPipeline({"claim-1": proposal}, challenge_proposal_ids=("p1",)),
        provider=provider,
        validator=DreamProposalValidator(),
        committer=committer,
    )

    report = runtime.run(make_task())

    assert report.status is DreamRunStatus.FAILED
    assert report.failure_stage == "challenge"
    assert committer.calls == 0
    assert report.proposal_results == ()


def test_runtime_stale_validation_skips_commit() -> None:
    claim = make_claim()
    proposal = make_proposal(claim=claim)
    bundle = make_bundle()
    provider = DeterministicDreamProvider(
        extraction_results_by_bundle={"bundle-1": make_extraction_result()}
    )
    committer = RecordingCommitter(result=BatchCommitResult(outcomes=()))
    runtime = DreamRuntime(
        context_builder=StubContextBuilder(
            bundle,
            {"p1": make_context(claim, current_event_watermark=11)},
        ),
        pipeline=StubPipeline({"claim-1": proposal}),
        provider=provider,
        validator=DreamProposalValidator(),
        committer=committer,
    )

    report = runtime.run(make_task())

    assert report.status is DreamRunStatus.COMPLETED
    assert committer.calls == 0
    assert report.commit_result is None
    assert report.proposal_results[0].validation.disposition is ProposalDisposition.STALE
    assert report.metrics.stale == 1


def test_runtime_records_replay_commit_outcome() -> None:
    claim = make_claim()
    proposal = make_proposal(claim=claim)
    bundle = make_bundle()
    provider = DeterministicDreamProvider(
        extraction_results_by_bundle={"bundle-1": make_extraction_result()}
    )
    committer = RecordingCommitter(
        result=BatchCommitResult(
            outcomes=(
                ProposalCommitOutcome(
                    proposal_id="p1",
                    status=ProposalCommitStatus.REPLAYED,
                ),
            )
        )
    )
    runtime = DreamRuntime(
        context_builder=StubContextBuilder(bundle, {"p1": make_context(claim)}),
        pipeline=StubPipeline({"claim-1": proposal}),
        provider=provider,
        validator=DreamProposalValidator(),
        committer=committer,
    )

    report = runtime.run(make_task())

    assert report.status is DreamRunStatus.COMPLETED
    assert report.proposal_results[0].commit_outcome is not None
    assert report.proposal_results[0].commit_outcome.status is ProposalCommitStatus.REPLAYED
    assert report.metrics.replayed == 1
