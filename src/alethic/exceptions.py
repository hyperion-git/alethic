"""Custom exceptions for the Alethic agent."""


class AlethicError(Exception):
    """Base exception for all Alethic errors."""


class TruncatedResponseError(AlethicError):
    """Raised when an API response was truncated (stop_reason=max_tokens)."""


class ContextExhaustedError(AlethicError):
    """Raised when estimated input tokens approach the model's context limit."""


class CheckpointError(AlethicError):
    """Raised when checkpoint state cannot be written to disk."""


class ModelResponseError(AlethicError):
    """The provider returned no usable completion (e.g. refusal or empty choices)."""
