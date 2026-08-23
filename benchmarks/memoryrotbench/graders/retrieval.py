"""Grade retrieval results against scenario query contracts."""

from __future__ import annotations

from benchmarks.memoryrotbench.adapters.base import AdapterQueryResult
from benchmarks.memoryrotbench.results import QueryGrade
from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery


def grade_query_result(
    scenario: Scenario, query: ScenarioQuery, result: AdapterQueryResult
) -> QueryGrade:
    context_event_ids = tuple(event.event_id for event in result.context_events)
    context_banks = tuple(event.bank_id for event in result.context_events)
    context_blob = "\n".join(event.content for event in result.context_events)

    required_evidence_found = tuple(
        event_id for event_id in query.required_evidence_event_ids if event_id in context_event_ids
    )
    missing_required_evidence = tuple(
        event_id
        for event_id in query.required_evidence_event_ids
        if event_id not in context_event_ids
    )

    forbidden_evidence_event_ids = _forbidden_evidence_event_ids(scenario, query)
    present_forbidden_evidence = tuple(
        event_id for event_id in forbidden_evidence_event_ids if event_id in context_event_ids
    )

    leaked_banks = tuple(
        bank_id
        for bank_id in sorted(set(context_banks))
        if bank_id in set(query.forbidden_bank_ids)
    )

    present_forbidden_fragments = tuple(
        fragment for fragment in query.forbidden_answer_fragments if fragment in context_blob
    )

    required_evidence_recall = 1.0
    if query.required_evidence_event_ids:
        required_evidence_recall = len(required_evidence_found) / len(
            query.required_evidence_event_ids
        )

    passed = not (
        missing_required_evidence
        or present_forbidden_evidence
        or leaked_banks
        or present_forbidden_fragments
    )

    return QueryGrade(
        adapter_name=result.adapter_name,
        scenario_id=scenario.scenario_id,
        query_id=query.query_id,
        bank_id=result.bank_id,
        passed=passed,
        required_evidence_found=required_evidence_found,
        missing_required_evidence=missing_required_evidence,
        present_forbidden_evidence=present_forbidden_evidence,
        leaked_banks=leaked_banks,
        present_forbidden_fragments=present_forbidden_fragments,
        required_evidence_recall=required_evidence_recall,
    )


def _forbidden_evidence_event_ids(scenario: Scenario, query: ScenarioQuery) -> tuple[str, ...]:
    forbidden_events: list[str] = []
    forbidden_claims = set(query.forbidden_current_claim_ids)
    for claim in scenario.claims:
        if claim.claim_id in forbidden_claims:
            forbidden_events.extend(claim.evidence_event_ids)
    # Preserve order while deduplicating.
    return tuple(dict.fromkeys(forbidden_events))
