from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from memorygraph.storage.repositories.embeddings import EmbeddingRepository
from memorygraph.storage.repositories.search import SearchDocumentRepository

from .protocols import Embedder


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    resource_type: str
    resource_id: str
    score: float
    channels: tuple[str, ...]


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[tuple[str, str]]],
    *,
    channel_weights: Mapping[str, float] | None = None,
    rank_constant: int = 60,
) -> tuple[HybridCandidate, ...]:
    if rank_constant <= 0:
        raise ValueError("rank_constant must be positive")
    weights = channel_weights or {}
    scores: dict[tuple[str, str], float] = {}
    channels: dict[tuple[str, str], set[str]] = {}
    for channel, ranking in rankings.items():
        weight = float(weights.get(channel, 1.0))
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (rank_constant + rank)
            channels.setdefault(key, set()).add(channel)
    ordered = sorted(scores, key=lambda key: (-scores[key], key[0], key[1]))
    return tuple(
        HybridCandidate(
            resource_type=resource_type,
            resource_id=resource_id,
            score=scores[(resource_type, resource_id)],
            channels=tuple(sorted(channels[(resource_type, resource_id)])),
        )
        for resource_type, resource_id in ordered
    )


class HybridRetriever:
    """Fuse SQLite FTS with an optional embedding index using deterministic RRF."""

    def __init__(
        self,
        search: SearchDocumentRepository,
        embeddings: EmbeddingRepository,
        *,
        embedder: Embedder | None,
    ) -> None:
        self._search = search
        self._embeddings = embeddings
        self._embedder = embedder

    def search(
        self,
        *,
        bank_id: str,
        lexical_query: str,
        semantic_query: str,
        limit: int,
    ) -> tuple[HybridCandidate, ...]:
        if limit <= 0:
            return ()
        lexical = self._search.search(bank_id=bank_id, query=lexical_query, limit=limit * 3)
        rankings: dict[str, Sequence[tuple[str, str]]] = {
            "fts": tuple(
                (hit.document.resource_type, hit.document.resource_id) for hit in lexical
            )
        }
        if self._embedder is not None:
            query_vector = self._embedder.embed((semantic_query,))[0]
            vector_rows = self._embeddings.list_for_bank(
                bank_id=bank_id,
                model=self._embedder.name,
            )
            ranked_vectors = sorted(
                vector_rows,
                key=lambda row: (
                    -_cosine_similarity(query_vector, row.vector),
                    row.resource_type,
                    row.resource_id,
                ),
            )
            rankings["vector"] = tuple(
                (row.resource_type, row.resource_id) for row in ranked_vectors[: limit * 3]
            )
        return reciprocal_rank_fusion(
            rankings,
            channel_weights={"fts": 1.0, "vector": 0.7},
        )[:limit]


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return -math.inf
    return sum(a * b for a, b in zip(left, right, strict=True))
