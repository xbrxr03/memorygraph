"""Manifest-driven dogfood evaluation harness for real MemoryGraph tasks."""

from .ledger import DogfoodExperimentLog, DuplicateRunError
from .manifest import (
    DogfoodManifest,
    ManifestValidationError,
    PredicateDefinition,
    TaskManifest,
    load_manifest,
)
from .results import DogfoodRunResult, PairwiseComparison, PairwiseSummary, RunSummary
from .runner import DogfoodRunner, RunConfig

__all__ = [
    "DogfoodExperimentLog",
    "DogfoodManifest",
    "DogfoodRunResult",
    "DogfoodRunner",
    "DuplicateRunError",
    "ManifestValidationError",
    "PairwiseComparison",
    "PairwiseSummary",
    "PredicateDefinition",
    "RunConfig",
    "RunSummary",
    "TaskManifest",
    "load_manifest",
]
