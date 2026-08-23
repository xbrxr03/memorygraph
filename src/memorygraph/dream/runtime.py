from __future__ import annotations

from dataclasses import dataclass

from memorygraph.dream.models import ProposalDisposition
from memorygraph.dream.protocols import (
    DreamContextBuilder,
    DreamProposalCommitter,
    DreamProposalPipeline,
    DreamProvider,
)
from memorygraph.dream.runtime_models import (
    DreamRunMetrics,
    DreamRunMode,
    DreamRunReport,
    DreamRunStatus,
    DreamTask,
    ProposalRunResult,
    ValidatedProposal,
)
from memorygraph.dream.validator import DreamProposalValidator


@dataclass(slots=True)
class DreamRuntime:
    context_builder: DreamContextBuilder
    pipeline: DreamProposalPipeline
    provider: DreamProvider
    validator: DreamProposalValidator
    committer: DreamProposalCommitter

    def run(self, task: DreamTask) -> DreamRunReport:
        provider_calls = []
        proposal_results: list[ProposalRunResult] = []
        source_bundle_id: str | None = None
        selected_observations = 0
        extracted_entities = 0
        extracted_claims = 0
        current_stage = "build_source_bundle"
        try:
            current_stage = "build_source_bundle"
            source_bundle = self.context_builder.build_source_bundle(task)
            source_bundle_id = source_bundle.bundle_id
            selected_observations = len(source_bundle.observations)

            current_stage = "extract"
            extraction = self.provider.extract(source_bundle)
            provider_calls.append(extraction.trace)
            extracted_entities = len(extraction.candidates.entities)
            extracted_claims = len(extraction.candidates.claims)

            proposals = list(
                self.pipeline.proposals_from_extraction(
                    task,
                    source_bundle,
                    extraction,
                )
            )

            challenged_proposals = []
            for proposal in proposals:
                enriched = proposal
                if self.pipeline.should_challenge(task, source_bundle, proposal):
                    current_stage = "challenge"
                    request = self.pipeline.build_challenge_request(task, source_bundle, proposal)
                    challenge = self.provider.challenge(request)
                    provider_calls.append(challenge.trace)
                    enriched = self.pipeline.apply_challenge_result(proposal, challenge)
                challenged_proposals.append(enriched)

            commit_candidates: list[ValidatedProposal] = []
            for proposal in challenged_proposals:
                current_stage = "validate"
                validation_context = self.context_builder.build_validation_context(task, proposal)
                validation = self.validator.validate(proposal, validation_context)
                proposal_results.append(ProposalRunResult(proposal=proposal, validation=validation))
                if (
                    task.mode is DreamRunMode.APPLY
                    and validation.disposition is ProposalDisposition.AUTO_ELIGIBLE
                ):
                    commit_candidates.append(
                        ValidatedProposal(
                            proposal=proposal,
                            validation=validation,
                        )
                    )

            commit_result = None
            if task.mode is DreamRunMode.APPLY and commit_candidates:
                current_stage = "commit"
                commit_result = self.committer.commit_batch(task, tuple(commit_candidates))
                outcomes = {item.proposal_id: item for item in commit_result.outcomes}
                proposal_results = [
                    ProposalRunResult(
                        proposal=item.proposal,
                        validation=item.validation,
                        commit_outcome=outcomes.get(item.proposal.id),
                    )
                    for item in proposal_results
                ]

            metrics = DreamRunMetrics.from_results(
                selected_observations=selected_observations,
                extracted_entities=extracted_entities,
                extracted_claims=extracted_claims,
                proposal_results=tuple(proposal_results),
                provider_calls=tuple(provider_calls),
            )
            return DreamRunReport(
                task=task,
                status=DreamRunStatus.COMPLETED,
                source_bundle_id=source_bundle_id,
                provider_calls=tuple(provider_calls),
                proposal_results=tuple(proposal_results),
                metrics=metrics,
                commit_result=commit_result,
            )
        except Exception as exc:
            metrics = DreamRunMetrics.from_results(
                selected_observations=selected_observations,
                extracted_entities=extracted_entities,
                extracted_claims=extracted_claims,
                proposal_results=tuple(proposal_results),
                provider_calls=tuple(provider_calls),
            )
            return DreamRunReport(
                task=task,
                status=DreamRunStatus.FAILED,
                source_bundle_id=source_bundle_id,
                provider_calls=tuple(provider_calls),
                proposal_results=tuple(proposal_results),
                metrics=metrics,
                error_message=str(exc),
                failure_stage=current_stage,
            )
