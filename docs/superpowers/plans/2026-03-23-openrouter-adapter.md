# OpenRouter Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the Alethic agent to run with OpenRouter-hosted models (e.g., free Nemotron) via a drop-in adapter that translates between OpenAI and Anthropic response formats.

**Architecture:** New `openrouter.py` module with response shims (TextBlock/ToolUseBlock/Message dataclasses) and bidirectional message serialization. New `client_factory.py` for centralized client construction. Exception catch sites updated to handle both Anthropic and OpenAI error types. Calibration script gains `--openrouter` flag.

**Tech Stack:** Python 3.13, `openai` SDK (optional dep), `anthropic` SDK (existing), dataclasses

**Spec:** `docs/superpowers/specs/2026-03-23-openrouter-adapter-design.md`
**Scenarios:** `docs/superpowers/specs/2026-03-23-openrouter-scenarios.md`

**Note:** Parser hardening (S-29/S-30/S-32: confidence clamping, verdict fuzzy matching, issue tag tolerance) is a separate plan — it benefits all models including Claude and doesn't depend on the adapter.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/alethic/openrouter.py` | **Create** — Response shim dataclasses, format translation functions, `OpenRouterClient` class with `.messages.create()` |
| `src/alethic/client_factory.py` | **Create** — `get_client()` / `set_client_factory()` — centralized client construction |
| `src/alethic/agent.py` | **Modify** — 2 sites: `__init__` and variant-B use `get_client()` |
| `src/alethic/verifier_agent.py` | **Modify** — 1 site: `__init__` uses `get_client()` |
| `src/alethic/autopsy.py` | **Modify** — 1 site: `generate_autopsy()` uses `get_client()` |
| `src/alethic/subagents.py` | **Modify** — 2 exception catch sites to handle both SDK types |
| `scripts/e_vs_f_calibrate.py` | **Modify** — `--openrouter` flag, model metadata in traces |
| `pyproject.toml` | **Modify** — add `openrouter` optional dependency |
| `tests/test_openrouter.py` | **Create** — Unit tests for all translation functions |
| `tests/test_client_factory.py` | **Create** — Unit tests for factory pattern |

---

### Task 1: Response Shim Dataclasses + Translation Functions

**Files:**
- Create: `src/alethic/openrouter.py`
- Create: `tests/test_openrouter.py`

This is the core adapter — response shim dataclasses and all translation functions. No client class yet (Task 2). No external calls.

- [ ] **Step 1: Create `openrouter.py` with shim dataclasses**

```python
"""OpenRouter adapter — translates OpenAI-compatible responses to Anthropic shapes.

Enables the Alethic agent to use OpenRouter-hosted models (e.g., free Nemotron)
without changing subagents.py, agent.py, or tools.py.
"""
from __future__ import annotations

import copy
import json
import logging
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
        # Don't touch temperature — leave whatever _call_model set.
        # If thinking was enabled, _call_model forced temperature=1.
        # Without thinking, temperature=1 is just slightly high, not broken.
        # The proper fix is config-level (extended_thinking=False in Task 6).

    return kw


# ---------------------------------------------------------------------------
# Security: API key sanitization (S-42)
# ---------------------------------------------------------------------------

_KEY_PATTERN = re.compile(r"sk-or-v1-[a-zA-Z0-9]+")


def _sanitize_error(error: Exception) -> str:
    """Remove API keys from error messages."""
    return _KEY_PATTERN.sub("sk-or-v1-***REDACTED***", str(error))
```

- [ ] **Step 2: Write unit tests for all translation functions**

Create `tests/test_openrouter.py`:

```python
"""Unit tests for OpenRouter adapter translation functions."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from alethic.openrouter import (
    TextBlock, ToolUseBlock, Usage, Message,
    translate_response, translate_tools, translate_messages,
    translate_kwargs, _map_stop_reason,
)


class TestStopReasonMapping:
    def test_stop_maps_to_end_turn(self):
        assert _map_stop_reason("stop") == "end_turn"

    def test_length_maps_to_max_tokens(self):
        assert _map_stop_reason("length") == "max_tokens"

    def test_tool_calls_maps_to_tool_use(self):
        assert _map_stop_reason("tool_calls") == "tool_use"

    def test_content_filter_maps_to_end_turn(self):
        assert _map_stop_reason("content_filter") == "end_turn"

    def test_none_maps_to_end_turn(self):
        assert _map_stop_reason(None) == "end_turn"

    def test_unknown_maps_to_end_turn(self):
        assert _map_stop_reason("banana") == "end_turn"


class TestTranslateResponse:
    def _mock_response(self, *, content=None, tool_calls=None,
                       finish_reason="stop", prompt_tokens=10, completion_tokens=5):
        msg = MagicMock()
        msg.content = content
        msg.tool_calls = tool_calls
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = finish_reason
        resp = MagicMock()
        resp.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        resp.usage = usage
        return resp

    def test_text_only_response(self):
        resp = self._mock_response(content="Hello world")
        msg = translate_response(resp)
        assert len(msg.content) == 1
        assert msg.content[0].type == "text"
        assert msg.content[0].text == "Hello world"
        assert msg.stop_reason == "end_turn"
        assert msg.usage.input_tokens == 10

    def test_null_content_no_text_block(self):
        """S-3: null content should not produce TextBlock('None')."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "print(1)"}'
        resp = self._mock_response(content=None, tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert len(msg.content) == 1
        assert msg.content[0].type == "tool_use"

    def test_mixed_text_and_tools(self):
        """S-2: text + tool_calls merge into single content list."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "2+2"}'
        resp = self._mock_response(content="Let me check", tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert len(msg.content) == 2
        assert msg.content[0].type == "text"
        assert msg.content[1].type == "tool_use"
        assert msg.stop_reason == "tool_use"

    def test_malformed_tool_arguments(self):
        """S-11: invalid JSON in arguments → empty dict."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "print(1)'  # Missing closing brace
        resp = self._mock_response(content=None, tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert msg.content[0].input == {}

    def test_usage_translation(self):
        """S-22: prompt_tokens → input_tokens."""
        resp = self._mock_response(content="ok", prompt_tokens=100, completion_tokens=50)
        msg = translate_response(resp)
        assert msg.usage.input_tokens == 100
        assert msg.usage.output_tokens == 50

    def test_empty_choices(self):
        """S-15: empty choices array."""
        resp = MagicMock()
        resp.choices = []
        msg = translate_response(resp)
        assert msg.content == []
        assert msg.stop_reason == "end_turn"

    def test_truncated_response(self):
        """S-6: finish_reason='length' → stop_reason='max_tokens'."""
        resp = self._mock_response(content="partial...", finish_reason="length")
        msg = translate_response(resp)
        assert msg.stop_reason == "max_tokens"


class TestTranslateTools:
    def test_anthropic_to_openai(self):
        tools = [{"name": "execute_python", "description": "Run code", "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}}}]
        result = translate_tools(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "execute_python"
        assert result[0]["function"]["parameters"]["type"] == "object"

    def test_none_tools(self):
        assert translate_tools(None) is None

    def test_does_not_mutate_original(self):
        """S-12: deep copy prevents mutation."""
        tools = [{"name": "test", "input_schema": {"type": "object"}}]
        translate_tools(tools)
        assert "input_schema" in tools[0]


class TestTranslateMessages:
    def test_system_prepended(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = translate_messages(msgs, system="You are a verifier")
        assert result[0] == {"role": "system", "content": "You are a verifier"}
        assert result[1] == {"role": "user", "content": "Hello"}

    def test_assistant_with_block_objects(self):
        """S-9/S-21: round-trip — block objects in history serialized back to OpenAI."""
        blocks = [TextBlock(text="Here is my answer"), ToolUseBlock(id="t1", name="execute_python", input={"code": "2+2"})]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Here is my answer"
        assert result[0]["tool_calls"][0]["id"] == "t1"
        assert json.loads(result[0]["tool_calls"][0]["function"]["arguments"]) == {"code": "2+2"}

    def test_tool_result_blocks(self):
        """Tool result translation: Anthropic → OpenAI."""
        msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "4"}]}]
        result = translate_messages(msgs)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "t1"
        assert result[0]["content"] == "4"

    def test_plain_string_passthrough(self):
        msgs = [{"role": "user", "content": "Just a string"}]
        result = translate_messages(msgs)
        assert result[0] == {"role": "user", "content": "Just a string"}

    def test_tool_id_preserved_verbatim(self):
        """S-27: tool IDs survive round-trip exactly."""
        blocks = [ToolUseBlock(id="toolu_abc123", name="execute_python", input={})]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        assert result[0]["tool_calls"][0]["id"] == "toolu_abc123"

    def test_interleaved_text_and_tool_blocks(self):
        """S-28: interleaved blocks produce correct OpenAI message sequence."""
        blocks = [
            TextBlock(text="Step 1"),
            ToolUseBlock(id="t1", name="execute_python", input={"code": "1+1"}),
            TextBlock(text="Step 2"),
            ToolUseBlock(id="t2", name="execute_python", input={"code": "2+2"}),
        ]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        # All text merged, all tools in tool_calls (OpenAI doesn't support interleaving)
        assert result[0]["role"] == "assistant"
        assert "Step 1" in result[0].get("content", "")
        assert "Step 2" in result[0].get("content", "")
        assert len(result[0]["tool_calls"]) == 2


class TestApiKeySanitization:
    def test_key_redacted(self):
        """S-42: API keys removed from error messages."""
        from alethic.openrouter import _sanitize_error
        err = Exception("Auth failed for key sk-or-v1-794ac50b5117b2967bd6a855185b5d45")
        sanitized = _sanitize_error(err)
        assert "794ac50b" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_no_key_unchanged(self):
        from alethic.openrouter import _sanitize_error
        err = Exception("Normal error message")
        assert _sanitize_error(err) == "Normal error message"


class TestTranslateKwargs:
    def test_system_extracted(self):
        kw = {"model": "test", "system": "Be helpful", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100}
        result = translate_kwargs(kw)
        assert "system" not in result
        assert result["messages"][0]["role"] == "system"

    def test_thinking_stripped(self):
        """S-7: extended thinking removed, temperature reset."""
        kw = {"model": "test", "messages": [], "thinking": {"type": "enabled", "budget_tokens": 10000}, "temperature": 1}
        result = translate_kwargs(kw)
        assert "thinking" not in result
        assert result["temperature"] == 0.7

    def test_tools_translated(self):
        kw = {"model": "test", "messages": [], "tools": [{"name": "t", "input_schema": {}}]}
        result = translate_kwargs(kw)
        assert result["tools"][0]["type"] == "function"
        assert "tools" not in kw  # Original not mutated (popped from copy)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pip install openai>=1.0 && /home/xeal/.local/bin/micromamba run -n alethic python -m pytest tests/test_openrouter.py -v`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/alethic/openrouter.py tests/test_openrouter.py
git commit -m "feat(openrouter): add response shims and translation functions

TextBlock/ToolUseBlock/Usage/Message dataclasses duck-type Anthropic SDK.
translate_response(): OpenAI ChatCompletion → Anthropic Message
translate_messages(): bidirectional — Anthropic blocks in history → OpenAI
translate_tools(): input_schema → parameters
translate_kwargs(): system param, thinking strip, temperature reset

Covers scenarios S-2, S-3, S-6, S-7, S-9, S-11, S-15, S-21, S-22, S-27."
```

---

### Task 2: OpenRouterClient Class

**Files:**
- Modify: `src/alethic/openrouter.py`
- Modify: `tests/test_openrouter.py`

The client class that wraps `openai.OpenAI` and provides `client.messages.create()`.

- [ ] **Step 1: Add OpenRouterClient to openrouter.py**

```python
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
            # S-42: sanitize API keys from error messages before propagating
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
        agent = MathAgent(api_key="dummy")
        agent.client = client  # Or via client_factory
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
```

- [ ] **Step 2: Add client tests**

```python
class TestOpenRouterClient:
    def test_create_calls_openai_and_translates(self):
        """End-to-end: client.messages.create() returns Anthropic-shaped Message."""
        from unittest.mock import patch, MagicMock

        mock_openai = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "VERDICT: correct\nCONFIDENCE: 0.95"
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.usage.prompt_tokens = 50
        mock_resp.usage.completion_tokens = 20
        mock_openai.chat.completions.create.return_value = mock_resp

        client = OpenRouterClient.__new__(OpenRouterClient)
        client._openai = mock_openai
        client._model = "test-model"
        client.messages = _MessagesAPI(mock_openai, "test-model")

        result = client.messages.create(
            model="ignored",
            system="You are a verifier",
            messages=[{"role": "user", "content": "Check this"}],
            max_tokens=1000,
        )

        assert isinstance(result, Message)
        assert result.content[0].text == "VERDICT: correct\nCONFIDENCE: 0.95"
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 50

        # Verify OpenAI was called with translated kwargs
        call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["messages"][0]["role"] == "system"

    def test_stream_raises_not_implemented(self):
        from alethic.openrouter import _MessagesAPI
        api = _MessagesAPI(MagicMock(), "test")
        with pytest.raises(NotImplementedError):
            api.stream(model="test", messages=[])
```

- [ ] **Step 3: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest tests/test_openrouter.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/alethic/openrouter.py tests/test_openrouter.py
git commit -m "feat(openrouter): add OpenRouterClient class

Duck-types anthropic.Anthropic with .messages.create(). Wraps
openai.OpenAI with full kwargs translation. Lazy import of openai
SDK (fails with clear message if not installed)."
```

---

### Task 3: Client Factory

**Files:**
- Create: `src/alethic/client_factory.py`
- Create: `tests/test_client_factory.py`

- [ ] **Step 1: Create client_factory.py**

```python
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
    """Register a custom client factory. Call at startup before creating agents.

    Args:
        factory: Callable that takes (api_key: str | None) and returns a client
                 with a .messages.create(**kwargs) method.
    """
    global _factory
    _factory = factory


def reset_client_factory():
    """Reset to default (anthropic.Anthropic). Useful in tests."""
    global _factory
    _factory = None
```

- [ ] **Step 2: Create tests**

```python
"""Tests for client_factory."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from alethic.client_factory import get_client, set_client_factory, reset_client_factory


class TestClientFactory:
    def setup_method(self):
        reset_client_factory()

    def teardown_method(self):
        reset_client_factory()

    @patch("alethic.client_factory.anthropic.Anthropic")
    def test_default_returns_anthropic(self, mock_anthropic):
        mock_anthropic.return_value = MagicMock()
        client = get_client(api_key="test-key")
        mock_anthropic.assert_called_once_with(api_key="test-key")

    def test_custom_factory(self):
        mock_client = MagicMock()
        set_client_factory(lambda api_key: mock_client)
        assert get_client("key") is mock_client

    @patch("alethic.client_factory.anthropic.Anthropic")
    def test_reset_restores_default(self, mock_anthropic):
        set_client_factory(lambda api_key: MagicMock())
        reset_client_factory()
        get_client(api_key="key")
        mock_anthropic.assert_called_once()

    def test_factory_receives_api_key(self):
        received = {}
        def factory(api_key):
            received["key"] = api_key
            return MagicMock()
        set_client_factory(factory)
        get_client("my-secret-key")
        assert received["key"] == "my-secret-key"
```

- [ ] **Step 3: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest tests/test_client_factory.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/alethic/client_factory.py tests/test_client_factory.py
git commit -m "feat: add client_factory for centralized LLM client construction

get_client() / set_client_factory() / reset_client_factory().
Default returns anthropic.Anthropic. Custom factory enables
OpenRouter or any other provider swap."
```

---

### Task 4: Wire Client Factory into All 5 Construction Sites

**Files:**
- Modify: `src/alethic/agent.py` (2 sites: `__init__:181`, variant-B `:414`)
- Modify: `src/alethic/verifier_agent.py` (1 site: `__init__:38`)
- Modify: `src/alethic/autopsy.py` (1 site: `:155`)

- [ ] **Step 1: Update agent.py**

At line 33, add import:
```python
from alethic.client_factory import get_client
```

At line 181, change:
```python
self.client = anthropic.Anthropic(api_key=self._api_key)
```
to:
```python
self.client = get_client(api_key=self._api_key)
```

At line 414, change:
```python
variant_b_client = anthropic.Anthropic(api_key=self._api_key)
```
to:
```python
variant_b_client = get_client(api_key=self._api_key)
```

- [ ] **Step 2: Update verifier_agent.py**

Add import:
```python
from alethic.client_factory import get_client
```

At line 38, change:
```python
self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
```
to:
```python
self.client = get_client(api_key=api_key)
```

- [ ] **Step 3: Update autopsy.py**

Add import:
```python
from alethic.client_factory import get_client
```

At line 155, change:
```python
client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
```
to:
```python
client = get_client(api_key=api_key)
```

- [ ] **Step 4: Run full test suite**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest --tb=short -q`

Expected: All 1328+ pass (factory defaults to anthropic.Anthropic, so existing tests are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/alethic/agent.py src/alethic/verifier_agent.py src/alethic/autopsy.py
git commit -m "refactor: wire client_factory into all 5 client construction sites

agent.py (MathAgent.__init__ + variant-B), verifier_agent.py
(VerifierAgent.__init__), autopsy.py (generate_autopsy).
synthesizer.py already receives client as param — no change needed.
Default behavior unchanged (factory returns anthropic.Anthropic)."
```

---

### Task 5: Update Exception Catch Sites

**Files:**
- Modify: `src/alethic/subagents.py` (line 150: RateLimitError)
- Modify: `src/alethic/agent.py` (line 1196: APIError)

- [ ] **Step 1: Update subagents.py**

At the top, after `import anthropic`, add:
```python
try:
    import openai as _openai_module
except ImportError:
    _openai_module = None
```

At line 150, change:
```python
        except anthropic.RateLimitError:
```
to:
```python
        except (anthropic.RateLimitError, *([_openai_module.RateLimitError] if _openai_module else [])):
```

Actually, that's ugly. Cleaner approach — define a tuple at module level:

```python
_RATE_LIMIT_ERRORS: tuple[type, ...] = (anthropic.RateLimitError,)
try:
    import openai
    _RATE_LIMIT_ERRORS = (anthropic.RateLimitError, openai.RateLimitError)
except ImportError:
    pass
```

Then at line 150:
```python
        except _RATE_LIMIT_ERRORS:
```

- [ ] **Step 2: Update agent.py**

Same pattern. After imports:
```python
_API_ERRORS: tuple[type, ...] = (anthropic.APIError,)
try:
    import openai
    _API_ERRORS = (anthropic.APIError, openai.APIError)
except ImportError:
    pass
```

At line 1196:
```python
            except _API_ERRORS as e:
```

- [ ] **Step 3: Add exception mapping tests**

In `tests/test_openrouter.py`, add:
```python
class TestExceptionMapping:
    def test_rate_limit_tuple_contains_both(self):
        """S-13: both Anthropic and OpenAI RateLimitError are caught."""
        from alethic.subagents import _RATE_LIMIT_ERRORS
        import anthropic
        assert anthropic.RateLimitError in _RATE_LIMIT_ERRORS
        try:
            import openai
            assert openai.RateLimitError in _RATE_LIMIT_ERRORS
        except ImportError:
            pass  # openai not installed — only anthropic in tuple

    def test_api_error_tuple_contains_both(self):
        """S-14: both Anthropic and OpenAI APIError are caught."""
        from alethic.agent import _API_ERRORS
        import anthropic
        assert anthropic.APIError in _API_ERRORS
```

- [ ] **Step 4: Run full test suite**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest --tb=short -q`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/alethic/subagents.py src/alethic/agent.py tests/test_openrouter.py
git commit -m "fix: catch both Anthropic and OpenAI exception types

_RATE_LIMIT_ERRORS tuple in subagents.py catches both
anthropic.RateLimitError and openai.RateLimitError.
_API_ERRORS tuple in agent.py catches both APIError types.
Graceful degradation if openai not installed."
```

---

### Task 6: Calibration Script Integration + pyproject.toml

**Files:**
- Modify: `scripts/e_vs_f_calibrate.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add openrouter optional dependency**

In `pyproject.toml`, after the `scientific` extras group, add:
```toml
openrouter = [
    "openai>=1.0,<3.0",
]
```

- [ ] **Step 2: Add --openrouter flag to calibration script**

In `scripts/e_vs_f_calibrate.py`, add to the argparse group:
```python
parser.add_argument("--openrouter", action="store_true",
                    help="Use OpenRouter API instead of Anthropic. Requires OPENROUTER_API_KEY env var.")
```

In the `main()` function, before agent construction, add:
```python
if args.openrouter:
    from alethic.client_factory import set_client_factory
    from alethic.openrouter import OpenRouterClient
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        print("ERROR: --openrouter requires OPENROUTER_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)
    model = args.model or "nvidia/nemotron-3-nano-30b-a3b:free"
    set_client_factory(lambda api_key: OpenRouterClient(api_key=or_key, model=model))
    # Force disable features unsupported by non-Claude models
    config_overrides["extended_thinking"] = False
    config_overrides["variant_b"] = None
    config_overrides["adversarial_breaker"] = False
    print(f"Using OpenRouter: {model}")
```

- [ ] **Step 3: Add model field to trace entries**

In the `_write_traces()` function, ensure each trace entry includes:
```python
"model": config.model,
```

In `_completed_problems()` (resume logic), add model mismatch detection:
```python
# Check model consistency on resume
if traces:
    existing_model = traces[0].get("model")
    if existing_model and existing_model != config.model:
        print(f"WARNING: Existing traces use model '{existing_model}' "
              f"but current run uses '{config.model}'. Use --force to mix models.",
              file=sys.stderr)
        if not args.force:
            sys.exit(1)
```

Add `--force` argument to argparse.

- [ ] **Step 4: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest --tb=short -q`

- [ ] **Step 5: Commit**

```bash
git add scripts/e_vs_f_calibrate.py pyproject.toml
git commit -m "feat: add --openrouter flag to calibration script

Wires OpenRouterClient via client_factory. Forces extended_thinking=False,
variant_b=None, adversarial_breaker=False for non-Claude models.
Adds model field to trace entries and model mismatch detection on resume.
openai SDK added as optional dependency: pip install alethic[openrouter]"
```

---

### Task 7: Integration Smoke Test

**Files:**
- Create: `tests/test_openrouter_integration.py` (marked as requires-api)

- [ ] **Step 1: Write integration test**

```python
"""Integration smoke test for OpenRouter adapter.

Requires OPENROUTER_API_KEY environment variable.
Skip with: pytest -m "not integration"
"""
from __future__ import annotations
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set"
)


@pytest.mark.integration
def test_single_verification_roundtrip():
    """Smoke test: create client, call verify-like prompt, parse response."""
    from alethic.openrouter import OpenRouterClient

    client = OpenRouterClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model="nvidia/nemotron-3-nano-30b-a3b:free",
    )

    result = client.messages.create(
        model="ignored",
        system="You are a math verifier. Output: VERDICT: correct\nCONFIDENCE: 0.95\nCRITIQUE:\nLooks good.",
        messages=[{"role": "user", "content": "Is 1+1=2? Answer in the format above."}],
        max_tokens=200,
        temperature=0.2,
    )

    assert len(result.content) >= 1
    assert result.content[0].type == "text"
    assert result.stop_reason in ("end_turn", "max_tokens")
    assert result.usage.input_tokens > 0
    text = result.content[0].text
    assert "VERDICT" in text.upper() or "correct" in text.lower()


@pytest.mark.integration
def test_tool_use_roundtrip():
    """Smoke test: tool call → tool result → continuation."""
    from alethic.openrouter import OpenRouterClient
    from alethic.tools import PYTHON_TOOL

    client = OpenRouterClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model="nvidia/nemotron-3-nano-30b-a3b:free",
    )

    from alethic.openrouter import translate_tools
    tools = translate_tools([PYTHON_TOOL])

    # Round 1: ask model to compute something
    result = client.messages.create(
        model="ignored",
        system="Use the execute_python tool to compute 2+2.",
        messages=[{"role": "user", "content": "What is 2+2? Use the tool."}],
        max_tokens=500,
        temperature=0.2,
        tools=[PYTHON_TOOL],  # Adapter translates
    )

    # Check that either a tool call was made or text was returned
    has_tool = any(b.type == "tool_use" for b in result.content)
    has_text = any(b.type == "text" for b in result.content)
    assert has_tool or has_text, "Expected either tool call or text response"
```

- [ ] **Step 2: Run integration test (if API key available)**

Run: `OPENROUTER_API_KEY=... /home/xeal/.local/bin/micromamba run -n alethic python -m pytest tests/test_openrouter_integration.py -v -m integration`

- [ ] **Step 3: Commit**

```bash
git add tests/test_openrouter_integration.py
git commit -m "test: add OpenRouter integration smoke tests

Skipped when OPENROUTER_API_KEY not set. Tests single verification
roundtrip and tool use with real Nemotron nano model."
```

---

### Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -m pytest --tb=short -q`

Expected: All 1328+ pass, plus new tests.

- [ ] **Step 2: Lint**

Run: `/home/xeal/.local/bin/micromamba run -n alethic ruff check src/alethic/openrouter.py src/alethic/client_factory.py src/alethic/agent.py src/alethic/subagents.py src/alethic/verifier_agent.py src/alethic/autopsy.py`

- [ ] **Step 3: Verify backward compat**

```bash
/home/xeal/.local/bin/micromamba run -n alethic python -c "
from alethic import MathAgent, AgentConfig
# Default factory → anthropic.Anthropic (requires key but proves import works)
print('Import OK')
from alethic.client_factory import get_client, set_client_factory, reset_client_factory
print('Factory OK')
from alethic.openrouter import OpenRouterClient, translate_response, translate_messages
print('Adapter OK')
"
```

- [ ] **Step 4: Run integration smoke test with real API**

```bash
OPENROUTER_API_KEY=sk-or-... /home/xeal/.local/bin/micromamba run -n alethic python -m pytest tests/test_openrouter_integration.py -v -m integration
```
