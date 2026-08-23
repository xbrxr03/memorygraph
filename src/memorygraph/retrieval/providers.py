from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import blake2b

_TOKEN = re.compile(r"[\w./:+-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class FeatureHashEmbedder:
    """Deterministic dependency-free vector baseline.

    This is deliberately modest: it provides a local vector channel and stable tests, while a
    semantic provider can be injected through the same protocol for production comparisons.
    """

    dimensions: int = 256
    name: str = "feature-hash-v1"

    def __post_init__(self) -> None:
        if self.dimensions < 32:
            raise ValueError("feature-hash dimensions must be at least 32")

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        tokens = [token.casefold() for token in _TOKEN.findall(text)]
        for token in tokens:
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude:
            values = [value / magnitude for value in values]
        return tuple(values)
