from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from memorygraph.models import Claim, ClaimEvidence, ClaimRelation, PredicateDefinition


class PredicateDefinitionLookup(Protocol):
    def get_predicate_definition(
        self, bank_id: str, predicate: str
    ) -> PredicateDefinition | None: ...


class ClaimReader(PredicateDefinitionLookup, Protocol):
    def get_claim(self, bank_id: str, claim_id: str) -> Claim | None: ...

    def get_claims(self, bank_id: str, claim_ids: Sequence[str]) -> Mapping[str, Claim]: ...

    def list_claims_for_slot(
        self, bank_id: str, subject_entity_id: str, predicate: str
    ) -> Sequence[Claim]: ...

    def list_evidence_for_claim(self, bank_id: str, claim_id: str) -> Sequence[ClaimEvidence]: ...

    def list_relations_for_claim(self, bank_id: str, claim_id: str) -> Sequence[ClaimRelation]: ...
