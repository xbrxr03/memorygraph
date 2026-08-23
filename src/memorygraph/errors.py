"""Public exception hierarchy."""


class MemoryGraphError(Exception):
    """Base class for expected MemoryGraph failures."""


class ValidationError(MemoryGraphError):
    """Input or domain validation failed."""


class NotFoundError(MemoryGraphError):
    """A requested resource does not exist in the scoped bank."""


class BankNotFoundError(NotFoundError):
    """The requested bank does not exist."""


class ConflictError(MemoryGraphError):
    """A write conflicts with current state or an idempotency invariant."""


class ProviderError(MemoryGraphError):
    """A semantic provider was unavailable or returned an invalid response."""
