"""Construct instance-scoped clients; retain the legacy startup factory hook."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from alethic.llm import ModelClient
    from alethic.models import ModelConfig

_factory: Callable[[str | None], ModelClient] | None = None


def get_client(api_key: str | None = None, *, config: ModelConfig | None = None) -> ModelClient:
    """Build the configured backend, resolving only that provider's API key.

    New applications should pass a config or inject a client into an agent.
    A registered legacy factory retains precedence for existing scripts.
    """
    if _factory is not None:
        return _factory(api_key)

    from alethic.models import ModelConfig
    from alethic.providers import AnthropicClient, OpenAICompatibleClient

    cfg = config or ModelConfig()
    key = api_key or os.environ.get(f"{cfg.provider.upper()}_API_KEY")
    if cfg.provider == "anthropic":
        return AnthropicClient(
            api_key=key, base_url=cfg.base_url, request_options=cfg.request_options
        )
    if cfg.provider == "openrouter":
        from alethic.openrouter import OpenRouterClient

        return OpenRouterClient(
            api_key=key,
            model=cfg.model,
            base_url=cfg.base_url or "https://openrouter.ai/api/v1",
            request_options=cfg.request_options,
            token_parameter=cfg.token_parameter or "max_tokens",
        )
    return OpenAICompatibleClient(
        api_key=key,
        model=cfg.model,
        base_url=cfg.base_url,
        request_options=cfg.request_options,
        token_parameter=cfg.token_parameter or "max_completion_tokens",
    )


def set_client_factory(factory: Callable[[str | None], ModelClient]) -> None:
    """Legacy process-wide hook. Prefer ``MathAgent(client=...)`` for isolation."""
    global _factory
    _factory = factory


def reset_client_factory() -> None:
    """Reset the legacy hook; subsequent agents use their configured provider."""
    global _factory
    _factory = None
