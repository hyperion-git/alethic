"""OpenRouter adapter — translates OpenAI-compatible responses to Anthropic shapes.

Enables the Alethic agent to use OpenRouter-hosted models (e.g., free Nemotron)
without changing subagents.py, agent.py, or tools.py.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("alethic")


# ---------------------------------------------------------------------------
# Response shim dataclasses (duck-type Anthropic SDK response objects)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextBlock:
    """Duck-types anthropic.types.TextBlock."""
    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class ToolUseBlock:
    """Duck-types anthropic.types.ToolUseBlock."""
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    """Duck-types anthropic.types.Usage."""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Message:
    """Duck-types anthropic.types.Message."""
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


# ---------------------------------------------------------------------------
# Stop reason mapping
# ---------------------------------------------------------------------------

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


def _map_stop_reason(finish_reason: str | None) -> str:
    """Map OpenAI finish_reason to Anthropic stop_reason."""
    if finish_reason is None:
        return "end_turn"
    if finish_reason == "content_filter":
        logger.warning("Response was content-filtered by provider")
        return "end_turn"  # Not max_tokens — don't trigger TruncatedResponseError
    mapped = _STOP_REASON_MAP.get(finish_reason)
    if mapped is None:
        logger.warning("Unknown finish_reason %r — mapping to end_turn", finish_reason)
        return "end_turn"
    return mapped


# ---------------------------------------------------------------------------
# Inbound: OpenAI response → Anthropic Message
# ---------------------------------------------------------------------------

def translate_response(openai_response) -> Message:
    """Translate an OpenAI ChatCompletion response to an Anthropic-shaped Message.

    Handles: text content, tool_calls, null content, usage fields, stop_reason.
    """
    choice = openai_response.choices[0] if openai_response.choices else None
    if choice is None:
        return Message(content=[], stop_reason="end_turn", usage=Usage())

    msg = choice.message
    blocks: list[TextBlock | ToolUseBlock] = []

    # Text content (may be None for tool-only responses — S-3)
    if msg.content is not None and msg.content != "":
        blocks.append(TextBlock(type="text", text=msg.content))

    # Tool calls → ToolUseBlock objects
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed tool arguments for %s — using empty dict", tc.function.name)
                args = {}
            blocks.append(ToolUseBlock(
                type="tool_use",
                id=tc.id,
                name=tc.function.name,
                input=args,
            ))

    # Usage translation (prompt_tokens → input_tokens)
    usage = Usage(input_tokens=0, output_tokens=0)
    if openai_response.usage:
        usage = Usage(
            input_tokens=getattr(openai_response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(openai_response.usage, "completion_tokens", 0) or 0,
        )

    return Message(
        content=blocks,
        stop_reason=_map_stop_reason(choice.finish_reason),
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Outbound: Anthropic kwargs → OpenAI kwargs
# ---------------------------------------------------------------------------

def translate_tools(anthropic_tools: list[dict] | None) -> list[dict] | None:
    """Translate Anthropic tool schemas to OpenAI function schemas.

    Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    if not anthropic_tools:
        return None
    translated = []
    for tool in anthropic_tools:
        t = copy.deepcopy(tool)  # Don't mutate PYTHON_TOOL
        translated.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return translated


def translate_messages(messages: list[dict], system: str | None = None) -> list[dict]:
    """Translate Anthropic-shaped message history to OpenAI format.

    Handles:
    - system param → system message (prepended)
    - Assistant messages with block objects → text + tool_calls
    - User messages with tool_result blocks → separate tool role messages
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Plain string content — pass through
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # List of blocks (Anthropic format) — translate
        if isinstance(content, list) and content:
            first = content[0]

            # Assistant message with block objects (from response.content round-trip)
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in content:
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "text":
                        text_parts.append(getattr(block, "text", "") or (block.get("text", "") if isinstance(block, dict) else ""))
                    elif btype == "tool_use":
                        bid = getattr(block, "id", "") or (block.get("id", "") if isinstance(block, dict) else "")
                        bname = getattr(block, "name", "") or (block.get("name", "") if isinstance(block, dict) else "")
                        binput = getattr(block, "input", {}) or (block.get("input", {}) if isinstance(block, dict) else {})
                        tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": json.dumps(binput),
                            },
                        })

                assistant_msg: dict = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                else:
                    assistant_msg["content"] = None
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
                continue

            # User message with tool_result blocks
            if role == "user" and isinstance(first, dict) and first.get("type") == "tool_result":
                for block in content:
                    out.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(block.get("content", "")),
                    })
                continue

        # Fallback: convert to string
        out.append({"role": role, "content": str(content)})

    return out


def translate_kwargs(anthropic_kwargs: dict) -> dict:
    """Translate Anthropic client.messages.create() kwargs to OpenAI format.

    Handles: system param, messages, tools, thinking strip, temperature reset.
    """
    kw = dict(anthropic_kwargs)

    # System param → handled by translate_messages
    system = kw.pop("system", None)

    # Messages translation (bidirectional round-trip)
    messages = kw.get("messages", [])
    kw["messages"] = translate_messages(messages, system=system)

    # Tools translation
    tools = kw.pop("tools", None)
    if tools:
        kw["tools"] = translate_tools(tools)

    # Strip extended thinking (not supported by non-Claude models — S-7)
    # Primary fix: calibration script sets config.extended_thinking=False,
    # so _call_model never adds thinking kwargs. This is a safety net.
    if "thinking" in kw:
        logger.warning("Extended thinking not supported by OpenRouter model — proceeding without.")
        kw.pop("thinking")
        # Reset temperature to a sensible default if it was forced to 1 for thinking.
        # With thinking enabled, _call_model forces temperature=1 (required by Claude).
        # Without thinking, that's unnecessarily high — reset to 0.7.
        if kw.get("temperature") == 1:
            kw["temperature"] = 0.7

    return kw


# ---------------------------------------------------------------------------
# Security: API key sanitization (S-42)
# ---------------------------------------------------------------------------

_KEY_PATTERN = re.compile(r"sk-or-v1-[a-zA-Z0-9]+")


def _sanitize_error(error: Exception) -> str:
    """Remove API keys from error messages."""
    return _KEY_PATTERN.sub("sk-or-v1-***REDACTED***", str(error))


# ---------------------------------------------------------------------------
# Client class (duck-types anthropic.Anthropic)
# ---------------------------------------------------------------------------

class _MessagesAPI:
    """Duck-types anthropic.Anthropic().messages with .create() and .stream()."""

    def __init__(self, openai_client, model: str):
        self._client = openai_client
        self._model = model

    def create(self, **kwargs) -> Message:
        """Translate Anthropic kwargs → OpenAI call → translate response back."""
        kwargs["model"] = self._model
        openai_kwargs = translate_kwargs(kwargs)
        try:
            openai_response = self._client.chat.completions.create(**openai_kwargs)
        except Exception as e:
            sanitized = _sanitize_error(e)
            logger.error("OpenRouter API error (sanitized): %s", sanitized)
            raise
        return translate_response(openai_response)

    def stream(self, **kwargs):
        """Streaming not implemented — raise descriptive error."""
        raise NotImplementedError(
            "OpenRouterClient does not support streaming. "
            "The Anthropic SDK streaming fallback will not trigger."
        )


class OpenRouterClient:
    """Drop-in replacement for anthropic.Anthropic that routes to OpenRouter.

    Usage:
        client = OpenRouterClient(api_key="sk-or-...", model="nvidia/nemotron-3-nano-30b-a3b:free")
    """

    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1"):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "OpenRouter support requires the 'openai' package. "
                "Install with: pip install alethic[openrouter]"
            ) from e

        self._openai = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self.messages = _MessagesAPI(self._openai, model)
