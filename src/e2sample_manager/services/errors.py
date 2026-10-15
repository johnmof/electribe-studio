"""Service-layer exceptions."""


class ServiceError(RuntimeError):
    """An operation failed in a way suitable for display to a user."""
