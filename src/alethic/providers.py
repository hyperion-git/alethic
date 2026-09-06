"""Backend adapters. Provider-specific request translation lives here only."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Any

from alethic.exceptions import ModelResponseError
from alethic.llm import (
    Message,
    ProviderMetadata,
    TextBlock,
    ToolUseBlock,
    Usage,
    validate_request_options,
)

logger = logging.getLogger("alethic")


def _options(kwargs: dict, options: dict) -> dict:
    """Apply explicit endpoint options; None omits an unsupported parameter."""
    validate_request_options(options)
    result = copy.deepcopy(kwargs)
    for key, value in options.items():
        if value is None:
            result.pop(key, None)
        elif key == "extra_body":
            result[key] = {**result.get(key, {}), **copy.deepcopy(value)}
        else:
            result[key] = copy.deepcopy(value)
    return result


def _get(block: Any, name: str, default: Any = None) -> Any:
    return block.get(name, default) if isinstance(block, dict) else getattr(block, name, default)


def translate_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": copy.deepcopy(tool.get("input_schema", {})),
            },
        }
        for tool in tools
    ]


def translate_messages(messages: list[dict], system: str | None = None) -> list[dict]:
    """Convert block history to Chat Completions, preserving tool-call IDs.

    Native assistant continuations retain opaque reasoning/signature fields
    required by some endpoints. They stay in this call's private history.
    """
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for message in messages:
        role, content = message["role"], message.get("content", "")
        if isinstance(content, str) or content is None:
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            native = next((b for b in content if _get(b, "type") == "provider_metadata"), None)
            if native is not None:
                out.append(copy.deepcopy(_get(native, "message")))
                continue
            texts, calls = [], []
            for block in content:
                if _get(block, "type") == "text":
                    texts.append(_get(block, "text", ""))
                elif _get(block, "type") == "tool_use":
                    calls.append(
                        {
                            "id": _get(block, "id"),
                            "type": "function",
                            "function": {
                                "name": _get(block, "name"),
                                "arguments": json.dumps(_get(block, "input", {})),
                            },
                        }
                    )
            entry: dict = {"role": role, "content": "\n".join(texts) or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        else:
            # Tool results and ordinary user text may coexist in one turn.
            for block in content:
                if _get(block, "type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": _get(block, "tool_use_id"),
                            "content": str(_get(block, "content", "")),
                        }
                    )
                elif _get(block, "type") == "text":
                    out.append({"role": role, "content": _get(block, "text", "")})
                else:
                    raise ValueError(f"Unsupported {role} content block: {_get(block, 'type')}")
    return out


def _map_stop_reason(reason: str | None) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "refusal",
    }.get(reason or "stop", "end_turn")


def translate_response(response: Any) -> Message:
    if not response.choices:
        raise ModelResponseError("Provider returned no completion choices")
    choice = response.choices[0]
    msg = choice.message
    refusal = getattr(msg, "refusal", None)
    if choice.finish_reason == "content_filter" or (isinstance(refusal, str) and refusal):
        raise ModelResponseError("Provider refused or filtered the response")
    blocks: list[Any] = []
    if msg.content:
        blocks.append(TextBlock(text=msg.content))
    for call in msg.tool_calls or []:
        try:
            args = json.loads(call.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = None
        if not isinstance(args, dict):
            # The tool dispatcher returns a missing-code error to the model,
            # allowing it to repair malformed arguments on the next round.
            logger.warning(
                "Invalid tool arguments for %s; expected a JSON object", call.function.name
            )
            args = {}
        blocks.append(ToolUseBlock(id=call.id, name=call.function.name, input=args))
    if msg.tool_calls:
        dump = getattr(msg, "model_dump", None)
        native = dump(exclude_none=True) if callable(dump) else None
        if isinstance(native, dict):
            blocks.append(ProviderMetadata(message=native))
    usage = response.usage
    return Message(
        content=blocks,
        stop_reason=_map_stop_reason(choice.finish_reason),
        usage=Usage(
            input_tokens=(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=(getattr(usage, "completion_tokens", 0) or 0),
        ),
    )


def translate_kwargs(kwargs: dict, *, provider: str = "openrouter") -> dict:
    """Translate requests by API dialect, never by model-name substring."""
    kw = copy.deepcopy(kwargs)
    kw["messages"] = translate_messages(kw.get("messages", []), kw.pop("system", None))
    if kw.get("tools"):
        kw["tools"] = translate_tools(kw["tools"])
    thinking = kw.pop("thinking", None)
    if thinking:
        if provider == "openrouter":
            extra = kw.setdefault("extra_body", {})
            extra.setdefault("reasoning", {"max_tokens": thinking["budget_tokens"]})
        else:
            # Chat Completions has no portable numerical reasoning budget.
            # An explicit reasoning_effort may be supplied in request_options.
            logger.warning(
                "This endpoint has no portable thinking-token budget; "
                "configure reasoning_effort/request_options for the chosen model."
            )
    return kw


class _OpenAIMessages:
    def __init__(
        self,
        client: Any,
        model: str | None = None,
        request_interval: float = 0.0,
        *,
        provider: str = "openai",
        request_options: dict | None = None,
        token_parameter: str = "max_completion_tokens",
    ):
        if request_interval < 0:
            raise ValueError("request_interval must be >= 0")
        self._client = client
        self._model = model
        self._provider = provider
        self._options = copy.deepcopy(request_options or {})
        self._token_parameter = token_parameter
        self._request_interval = request_interval
        self._last_request: float | None = None
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        if self._request_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_request is not None:
                delay = self._request_interval - (now - self._last_request)
                if delay > 0:
                    time.sleep(delay)
            self._last_request = time.monotonic()

    def create(self, **kwargs: Any) -> Message:
        self._throttle()
        if not kwargs.get("model"):
            kwargs["model"] = self._model
        if not kwargs["model"]:
            raise ValueError("A model ID is required")
        kw = translate_kwargs(kwargs, provider=self._provider)
        if "max_tokens" in kw and self._token_parameter != "max_tokens":
            kw[self._token_parameter] = kw.pop("max_tokens")
        kw = _options(kw, self._options)
        # Preserve SDK exceptions for callers; a library must never sys.exit().
        return translate_response(self._client.chat.completions.create(**kw))

    def stream(self, **kwargs: Any) -> Any:
        raise NotImplementedError("Use messages.create(); this adapter collects complete responses")


class OpenAICompatibleClient:
    """OpenAI Chat Completions or an endpoint implementing the same protocol.

    No model allowlist. ``token_parameter='max_tokens'`` supports older servers;
    ``request_options={'temperature': None}`` omits unsupported sampling knobs.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        request_options: dict | None = None,
        token_parameter: str = "max_completion_tokens",
        request_interval: float = 0.0,
        provider: str = "openai",
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Install this backend with: pip install 'alethic[openai]'") from exc
        options: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            options["base_url"] = base_url
        self._openai = OpenAI(**options)
        self.messages = _OpenAIMessages(
            self._openai,
            model,
            request_interval,
            provider=provider,
            request_options=request_options,
            token_parameter=token_parameter,
        )

    def close(self) -> None:
        self._openai.close()


class _AnthropicMessages:
    def __init__(self, messages: Any, request_options: dict | None):
        self._messages = messages
        self._options = copy.deepcopy(request_options or {})

    def _kwargs(self, kwargs: dict) -> dict:
        kw = _options(kwargs, self._options)
        if kw.get("thinking", {}).get("type") == "enabled":
            kw["temperature"] = 1
        return kw

    def create(self, **kwargs: Any) -> Any:
        return self._messages.create(**self._kwargs(kwargs))

    def stream(self, **kwargs: Any) -> Any:
        return self._messages.stream(**self._kwargs(kwargs))


class AnthropicClient:
    """Native Messages API; signed thinking blocks survive tool continuations."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        request_options: dict | None = None,
    ):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ImportError(
                "Install this backend with: pip install 'alethic[anthropic]'"
            ) from exc
        options: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            options["base_url"] = base_url
        self._anthropic = Anthropic(**options)
        self.messages = _AnthropicMessages(self._anthropic.messages, request_options)

    def close(self) -> None:
        self._anthropic.close()
