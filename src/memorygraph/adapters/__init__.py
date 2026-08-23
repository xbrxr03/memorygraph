"""Adapters between persistence records and the pure MemoryGraph domain."""

from .storage_reader import StorageDomainReader

__all__ = ["StorageDomainReader"]
