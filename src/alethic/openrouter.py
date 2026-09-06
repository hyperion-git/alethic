"""OpenRouter convenience wrapper around the shared Chat Completions adapter.

Existing translation/type imports remain available for backwards compatibility.
The constructor's model is a default; per-call models always take precedence.
"""

from __future__ import annotations

import os
import re

from alethic.llm import Message, TextBlock, ToolUseBlock, Usage
from alethic.providers import (
    OpenAICompatibleClient,
    _OpenAIMessages,
    translate_kwargs,
    translate_messages,
    translate_response,
    translate_tools,
)
from alethic.providers import (
    _map_stop_reason as _map_stop_reason,
)

__all__ = [
    "Message",
    "OpenRouterClient",
    "TextBlock",
    "ToolUseBlock",
    "Usage",
    "translate_kwargs",
    "translate_messages",
    "translate_response",
    "translate_tools",
]


def _sanitize_error(error: Exception) -> str:
    return re.sub(r"sk-or-v1-[a-zA-Z0-9]+", "sk-or-v1-***REDACTED***", str(error))


class _MessagesAPI(_OpenAIMessages):
    """Compatibility name for the former OpenRouter message adapter."""

    def __init__(self, openai_client, model: str, request_interval: float = 0.0):
        super().__init__(
            openai_client,
            model,
            request_interval,
            provider="openrouter",
            token_parameter="max_tokens",
        )


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        request_interval: float | None = None,
        *,
        request_options: dict | None = None,
        token_parameter: str = "max_tokens",
    ):
        interval = request_interval
        if interval is None:
            interval = 4.0 if model and model.endswith(":free") else 0.0
        super().__init__(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
            model=model,
            base_url=base_url,
            request_interval=interval,
            provider="openrouter",
            request_options=request_options,
            token_parameter=token_parameter,
        )
