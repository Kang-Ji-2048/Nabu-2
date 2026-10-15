"""Exceptions raised by the supported library API."""


class NabuError(Exception):
    """Base class for library errors."""


class ValidationError(NabuError, ValueError):
    """Invalid input or configuration."""


class ScopeError(NabuError, PermissionError):
    """The supplied viewer context does not permit this operation."""


class NotFoundError(NabuError, LookupError):
    """No matching resource is available in the supplied context."""


class ClosedReviewerError(NabuError, RuntimeError):
    """An operation was attempted after closing a reviewer."""
