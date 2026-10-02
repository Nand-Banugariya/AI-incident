"""Domain exceptions raised by the service layer and mapped to HTTP errors by the API layer."""


class ServiceError(Exception):
    """Base class for expected, client-facing service failures."""


class NotFoundError(ServiceError):
    """The requested resource does not exist (or is outside the caller's organization)."""


class ConflictError(ServiceError):
    """The request conflicts with the resource's current state."""


class ServiceUnavailableError(ServiceError):
    """A required dependency (e.g. the task queue) is unreachable."""


class NotSupportedError(ServiceError):
    """The operation is valid but not available yet (e.g. no connection probe for a provider type)."""


class ForbiddenError(ServiceError):
    """The caller is authenticated and has the permission, but a business rule forbids this action."""
