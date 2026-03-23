# OpenRouter Adapter

**Date:** 2026-03-23
**Scope:** New `src/alethic/openrouter.py` module + client factory + calibration script integration
**Motivation:** Enable running the Alethic agent with free/cheap models via OpenRouter, primarily for E-vs-F calibration experiments without Anthropic API costs.

## Smoke Test Results

Both free Nemotron models pass structured output compliance:

| Test | Super-120B (12B active) | Nano-30B (3B active) |
|------|------------------------|---------------------|
| Verifier format (VERDICT/CONFIDENCE/CRITIQUE) | 3/4 (no issues on easy) | 4/4 |
| Flawed solution detection | FIXABLE + detected error | MAJOR_FLAW + [MAJOR] tag |
| Tool use (function calling) | tool_calls with valid JSON | tool_calls with valid JSON |
| Atom annotations (ATOM[N] deps= oracle=) | 6 atoms, contiguous, correct | 6 atoms, contiguous, correct |
| Response time | 16-49s | 2-5s |

**Recommended default model:** `nvidia/nemotron-3-nano-30b-a3b:free` (fast, free, good compliance).

## Architecture

### SDK Choice: openai (not litellm)

8-persona swarm analysis (7/8 consensus) recommended `openai` SDK over `litellm`:
- OpenRouter is natively OpenAI-compatible — just set `base_url`
- 3 transitive deps vs 30+ for litellm
- Stable API, single maintainer (OpenAI team)
- No registry/model compatibility issues

```python
from openai import OpenAI
raw_client = OpenAI(
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
)
```

The adapter translates between OpenAI response shapes and Anthropic response shapes (~150 lines).

### Client Factory (not per-constructor injection)

5 modules construct `anthropic.Anthropic` clients independently:

1. `agent.py:181` — `MathAgent.__init__`
2. `agent.py:414` — variant-B client
3. `verifier_agent.py:38` — `VerifierAgent.__init__`
4. `autopsy.py:155` — `generate_autopsy()`
5. `synthesizer.py:182` — `synthesize_critique()` (receives client as param)

Instead of adding `client=` to each constructor, create a lightweight factory:

```python
# src/alethic/client_factory.py

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
```

All 5 construction sites change from `anthropic.Anthropic(api_key=...)` to `get_client(api_key=...)`. One import change per file.

## Response Shape Translation

The codebase accesses these Anthropic-specific attributes (31 sites cataloged by reconnaissance):

### Response Object

```python
# Codebase expects:
response.content        # list of block objects
response.stop_reason    # "end_turn" | "max_tokens" | "tool_use"
response.usage.input_tokens
response.usage.output_tokens
```

Adapter provides frozen dataclasses that duck-type these:

```python
@dataclass(frozen=True)
class TextBlock:
    type: str = "text"
    text: str = ""

@dataclass(frozen=True)
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass(frozen=True)
class Message:
    content: list = field(default_factory=list)  # list[TextBlock | ToolUseBlock]
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
```

### Stop Reason Mapping

| OpenAI | Anthropic |
|--------|-----------|
| `"stop"` | `"end_turn"` |
| `"length"` | `"max_tokens"` |
| `"tool_calls"` | `"tool_use"` |

### Tool Schema Translation

Outbound (Anthropic → OpenAI):
```python
# Anthropic: {"name": "execute_python", "description": "...", "input_schema": {...}}
# OpenAI:    {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
```

Inbound (OpenAI response → Anthropic blocks):
```python
# OpenAI: response.choices[0].message.tool_calls[{id, function: {name, arguments: "json_str"}}]
# Anthropic: [ToolUseBlock(type="tool_use", id=id, name=name, input=json.loads(arguments))]
```

### Tool Result Translation

Outbound (Anthropic message format → OpenAI message format):
```python
# Anthropic: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}
# OpenAI:    {"role": "tool", "tool_call_id": "...", "content": "..."}
```

### Message Format Translation

Outbound (Anthropic kwargs → OpenAI kwargs):
- `system` param → prepend `{"role": "system", "content": system}` to messages
- `messages` with content block lists → flatten to string content where needed
- Strip `thinking` kwargs (not supported) with log warning

Inbound (round-trip): when `response.content` (block list) is appended to messages at `subagents.py:258`, the adapter must handle re-serialization on the next API call. The `.create()` method must translate assistant messages containing block objects back to OpenAI format.

## Exception Mapping

| Anthropic | OpenAI | Adapter strategy |
|-----------|--------|-----------------|
| `anthropic.RateLimitError` | `openai.RateLimitError` | Catch both in `_create_with_retry` |
| `anthropic.APIError` | `openai.APIError` | Catch both in `agent.py` error handler |
| `ValueError("Streaming is required")` | N/A | Adapter never raises this; no streaming fallback needed |

The adapter's `.messages.create()` method wraps exceptions from the openai SDK into anthropic-compatible types, OR the exception catch sites are updated to catch both.

Recommended: update catch sites (3 locations) since it's simpler than wrapping exceptions.

## Extended Thinking

Not supported by non-Claude models. The adapter strips `thinking` kwargs before sending, logs a warning:
```
WARNING: Extended thinking not supported by OpenRouter model — proceeding without.
```

The agent will function identically to `config.extended_thinking=False`.

## Variant-B

When `variant_b` config specifies a different model, the agent creates a second client at `agent.py:414`. With the client factory:
- If variant-B model is also an OpenRouter model: factory creates another OpenRouterClient
- If variant-B model is a Claude model: factory creates anthropic.Anthropic
- Decision is based on model string prefix (e.g., `nvidia/` → OpenRouter, `claude-` → Anthropic)

For initial implementation: **disable variant-B when using OpenRouter** (set `variant_b=None` in the adapter config). Add variant-B support in a follow-up.

## Files

| File | Action |
|------|--------|
| `src/alethic/openrouter.py` | **Create** — adapter module (~150 lines): response shims, format translation, `OpenRouterClient` class |
| `src/alethic/client_factory.py` | **Create** — `get_client()`, `set_client_factory()` (~20 lines) |
| `src/alethic/agent.py` | **Modify** — replace `anthropic.Anthropic(api_key=...)` with `get_client(api_key)` (2 sites) |
| `src/alethic/verifier_agent.py` | **Modify** — replace `anthropic.Anthropic(...)` with `get_client(...)` (1 site) |
| `src/alethic/autopsy.py` | **Modify** — replace `anthropic.Anthropic(...)` with `get_client(...)` (1 site) |
| `src/alethic/subagents.py` | **Modify** — update exception catches to handle both `anthropic.RateLimitError` and `openai.RateLimitError` (2 sites). Strip streaming fallback for non-Anthropic clients. |
| `scripts/e_vs_f_calibrate.py` | **Modify** — add `--openrouter` flag that calls `set_client_factory()` at startup |
| `pyproject.toml` | **Modify** — add `openai>=1.0` to `[project.optional-dependencies]` as `openrouter` extra |
| `tests/test_openrouter.py` | **Create** — unit tests: response translation, tool schema translation, tool result round-trip, exception mapping, stop_reason mapping. Mock openai.OpenAI, no real API calls. |

## What's NOT Changing

- Prompt templates — no changes (Nemotron follows them)
- `tools.py` — `PYTHON_TOOL` schema unchanged (adapter translates on the fly)
- `models.py` — no changes
- `error_taxonomy.py` — no changes
- Skill orchestrator — no changes

## Calibration Integration

```bash
# Install optional dependency
pip install alethic[openrouter]

# Run calibration with free Nemotron model
OPENROUTER_API_KEY=sk-or-... python scripts/e_vs_f_calibrate.py \
    --openrouter \
    --model nvidia/nemotron-3-nano-30b-a3b:free \
    --preset default
```

The `--openrouter` flag:
1. Imports `OpenRouterClient` from `alethic.openrouter`
2. Calls `set_client_factory(lambda api_key: OpenRouterClient(api_key=os.environ["OPENROUTER_API_KEY"], model=args.model))`
3. Forces `config.extended_thinking=False` and `config.variant_b=None`
4. Adds `model` field to trace entries for data lineage

## Testing Strategy

1. **Unit tests** (no API calls): Mock `openai.OpenAI().chat.completions.create()`, verify all translation functions produce correct shapes
2. **Integration smoke test** (real API): 1-problem end-to-end with Nemotron nano, verify solve() completes without crash
3. **Parser compliance** (real API): Run 5 problems, measure VERDICT/CONFIDENCE parse rate, gate on >80%

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Nemotron output quality degrades on hard problems | Default to nano model (better compliance); fall back to manual CLI |
| OpenRouter rate limits free models | Calibration script has `--workers` concurrency control; default to 1 for free models |
| openai SDK version churn | Pin `openai>=1.0,<3.0`; the chat completions API is stable |
| Tool use round-trip breaks on edge cases | Deep-copy PYTHON_TOOL before translation; test multi-round tool loops |
