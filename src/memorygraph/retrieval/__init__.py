"""Provider-neutral hybrid retrieval primitives."""

from .fusion import HybridCandidate, HybridRetriever, reciprocal_rank_fusion
from .protocols import Embedder
from .providers import FeatureHashEmbedder

__all__ = [
    "Embedder",
    "FeatureHashEmbedder",
    "HybridCandidate",
    "HybridRetriever",
    "reciprocal_rank_fusion",
]
