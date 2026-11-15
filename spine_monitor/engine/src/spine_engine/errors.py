"""Domain errors of the engine; the API translates them to problem details."""


class EngineError(Exception):
    """Base of the errors the engine knows how to explain to the client."""


class InsufficientDataError(EngineError):
    """There is not enough data to produce the requested result."""


class UnknownFeatureError(EngineError):
    """An override points to a feature outside the catalog or without a value in the base."""


class InvalidOverrideError(EngineError):
    """An override produces a value that is not a finite number."""
