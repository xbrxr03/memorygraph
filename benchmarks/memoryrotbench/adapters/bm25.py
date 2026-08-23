"""Small dependency-free BM25 retrieval baseline."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from benchmarks.memoryrotbench.scenario_loader import Scenario, ScenarioQuery

from .base import ContextEvent, RetrievalAdapter

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._/-]*")


class BM25Adapter(RetrievalAdapter):
    adapter_name = "bm25"

    def __init__(
        self,
        *,
        max_items: int = 10,
        max_tokens: int = 512,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if max_items < 1 or max_tokens < 1:
            raise ValueError("max_items and max_tokens must be positive")
        self.max_items = max_items
        self.max_tokens = max_tokens
        self.k1 = k1
        self.b = b

    def select_events(
        self, corpus: Sequence[Scenario], target_scenario: Scenario, query: ScenarioQuery
    ) -> Sequence[ContextEvent]:
        documents = self.visible_events(corpus, target_scenario, query)
        if not documents:
            return ()
        query_terms = tuple(dict.fromkeys(_tokens(query.question)))
        tokenized = [_tokens(document.content) for document in documents]
        average_length = sum(map(len, tokenized)) / len(tokenized)
        document_frequency = Counter(
            term for terms in tokenized for term in set(terms) if term in query_terms
        )
        ranked: list[tuple[float, ContextEvent]] = []
        for document, terms in zip(documents, tokenized, strict=True):
            frequencies = Counter(terms)
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_document_frequency = math.log(
                    1
                    + (len(documents) - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                normalization = frequency + self.k1 * (
                    1 - self.b + self.b * len(terms) / max(average_length, 1)
                )
                score += inverse_document_frequency * frequency * (self.k1 + 1) / normalization
            if score > 0:
                ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].at, item[1].event_id))

        selected: list[ContextEvent] = []
        tokens = 0
        for score, document in ranked:
            document_tokens = max(1, len(document.content.split()))
            if selected and tokens + document_tokens > self.max_tokens:
                continue
            selected.append(
                replace(
                    document,
                    score=score,
                    metadata={"retrieval": "bm25"},
                )
            )
            tokens += document_tokens
            if len(selected) >= self.max_items:
                break
        return selected


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.casefold())
