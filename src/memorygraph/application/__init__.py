"""Application services that wire pure domain logic to embedded storage."""

from typing import TYPE_CHECKING, Any

from .dream_service import EmbeddedDreamService, MetadataDreamProvider

if TYPE_CHECKING:
    from .worker_service import DurableWorkerService

__all__ = ["DurableWorkerService", "EmbeddedDreamService", "MetadataDreamProvider"]


def __getattr__(name: str) -> Any:
    if name == "DurableWorkerService":
        from .worker_service import DurableWorkerService

        return DurableWorkerService
    raise AttributeError(name)
