"""Small, SDK-independent contract shared by Alethic's model backends.

Clients expose ``messages.create(**kwargs)``. Requests contain a model, system
prompt, message history, token limit, optional tools and thinking budget.
Responses expose content blocks, a stop reason and token usage. The block
names retain compatibility with existing clients; no vendor SDK is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol


@dataclass(frozen=True)
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderMetadata:
    """Opaque continuation data, never exposed as solution text to a verifier."""

    type: str = "provider_metadata"
    message: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Message:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class ModelClient(Protocol):
    """Implement this interface to inject a backend without a global factory.

    Clients used with best-of-N or consensus must permit concurrent calls.
    ``max_tokens`` and ``tool_use`` stop reasons trigger truncation handling and
    tool continuation, respectively. Text extraction ignores all other blocks.
    """

    @property
    def messages(self) -> MessagesAPI: ...


def validate_request_options(options: dict[str, Any]) -> None:
    """Endpoint options must not replace the model or an isolated conversation."""
    if not isinstance(options, dict):
        raise ValueError("request_options must be a JSON object")
    extra = options.get("extra_body") or {}
    if not isinstance(extra, dict):
        raise ValueError("request_options.extra_body must be a JSON object")
    reserved = {"model", "messages", "system", "tools", "stream"}
    conflicts = reserved & (options.keys() | extra.keys())
    if conflicts:
        raise ValueError(
            f"request_options cannot override routing or conversation fields: {sorted(conflicts)}"
        )


def _sdk_errors(name: str) -> tuple[type[Exception], ...]:
    """Recognize native SDK errors without making either SDK a dependency."""
    errors: list[type[Exception]] = []
    for module in ("anthropic", "openai"):
        try:
            errors.append(getattr(import_module(module), name))
        except ImportError:
            continue
    return tuple(errors)


API_ERRORS = _sdk_errors("APIError")
RATE_LIMIT_ERRORS = _sdk_errors("RateLimitError")
