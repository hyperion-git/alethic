"""Tests for alethic exception hierarchy."""

from alethic.exceptions import (
    AlethicError,
    CheckpointError,
    ContextExhaustedError,
    TruncatedResponseError,
)


def test_hierarchy():
    """All custom exceptions inherit from AlethicError."""
    assert issubclass(TruncatedResponseError, AlethicError)
    assert issubclass(ContextExhaustedError, AlethicError)
    assert issubclass(CheckpointError, AlethicError)
    assert issubclass(AlethicError, Exception)


def test_messages():
    """Exceptions carry descriptive messages."""
    e = ContextExhaustedError("estimated 180000 tokens, limit 200000")
    assert "180000" in str(e)

    e = TruncatedResponseError("stop_reason=max_tokens")
    assert "max_tokens" in str(e)

    e = CheckpointError("disk full")
    assert "disk full" in str(e)
