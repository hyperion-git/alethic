"""Centralized client construction for the Alethic agent.

All modules that need an LLM client call get_client() instead of
directly instantiating anthropic.Anthropic. This enables swapping
to OpenRouter (or any other provider) via set_client_factory().
"""
from __future__ import annotations

import os

import anthropic

_factory = None  # None = use default (anthropic.Anthropic)


def get_client(api_key: str | None = None):
    """Return an LLM client. Uses the registered factory, or anthropic.Anthropic by default."""
    if _factory is not None:
        return _factory(api_key)
    return anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))


def set_client_factory(factory):
    """Register a custom client factory. Call at startup before creating agents."""
    global _factory
    _factory = factory


def reset_client_factory():
    """Reset to default (anthropic.Anthropic). Useful in tests."""
    global _factory
    _factory = None
