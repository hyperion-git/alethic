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

| OpenAI | Anthropic | Notes |
|--------|-----------|-------|
| `"stop"` | `"end_turn"` | Normal completion |
| `"length"` | `"max_tokens"` | Critical: triggers TruncatedResponseError (S-6) |
| `"tool_calls"` | `"tool_use"` | Model wants to call a tool |
| `"content_filter"` | `"end_turn"` + `content_filtered=True` flag | S-37: don't map to max_tokens; log warning, retry once |
| unknown | `"end_turn"` | Log warning with raw value |

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

### Bidirectional Message Serialization (Critical — S-9/S-21/S-28)

When `response.content` (block list) is appended to messages at `subagents.py:258`, the adapter must serialize them BACK to OpenAI format on the next `.create()` call. This is the hardest translation:

**Anthropic assistant message in history:**
```python
{"role": "assistant", "content": [TextBlock("Let me verify..."), ToolUseBlock(id="t1", name="execute_python", input={"code": "..."})]}
```

**Must become OpenAI format:**
```python
{"role": "assistant", "content": "Let me verify...", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "execute_python", "arguments": "{\"code\": \"...\"}"}}]}
```

**Interleaved blocks** (S-28): If text and tool_use blocks alternate within one assistant message, OpenAI format cannot represent this in a single message. The adapter splits into sequential messages preserving causal order.

**Tool result messages:**
```python
# Anthropic in history: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "4"}]}
# Must become: {"role": "tool", "tool_call_id": "t1", "content": "4"}
```

The adapter's `.create()` method pre-processes all messages before sending, translating any Anthropic-shaped content to OpenAI format.

### Null Content Handling (S-3)

When OpenAI returns `content=null` (tool-only response), the adapter must NOT create `TextBlock(text="None")`. Produce `response.content = [ToolUseBlock(...)]` with zero TextBlocks. The tool-use loop handles rounds with no text blocks correctly.

### Malformed Tool Arguments (S-11)

When `tool_calls[].function.arguments` is invalid JSON, catch `json.JSONDecodeError` and construct `ToolUseBlock(input={"code": ""})`. The sandbox returns an error message, and the tool-use loop continues.

## Exception Mapping

| Anthropic | OpenAI | Adapter strategy |
|-----------|--------|-----------------|
| `anthropic.RateLimitError` | `openai.RateLimitError` | Catch both in `_create_with_retry` |
| `anthropic.APIError` | `openai.APIError` | Catch both in `agent.py` error handler |
| `ValueError("Streaming is required")` | N/A | Adapter never raises this; no streaming fallback needed |

The adapter's `.messages.create()` method wraps exceptions from the openai SDK into anthropic-compatible types, OR the exception catch sites are updated to catch both.

Recommended: update catch sites (3 locations) since it's simpler than wrapping exceptions.

## Extended Thinking

Not supported by non-Claude models. The adapter:
1. Strips `thinking` kwargs before sending
2. **Resets temperature to the configured value** (S-7: Anthropic forces `temperature=1` when thinking is enabled; the adapter must undo this, restoring the original temperature from `_call_model`'s `temperature` parameter)
3. Logs a warning: `WARNING: Extended thinking not supported by OpenRouter model — proceeding without.`

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

## Security (S-42)

API keys must never leak into logs, session files, or error messages.

1. The adapter sanitizes all exceptions before re-raising: regex-replace `sk-or-v1-[a-zA-Z0-9]+` with `sk-or-v1-***REDACTED***`
2. `_create_with_retry` error logging must not include raw request headers
3. Session `events.jsonl` must not contain API keys (verify in tests)

## Model Context Limits (S-34)

`MODEL_CONTEXT_LIMITS` in `models.py` only has Claude entries. The adapter should:
1. Add OpenRouter model entries to the dict (or query OpenRouter's model metadata API at init)
2. Apply a 0.8x safety factor for non-Claude tokenizers in the chars/4 heuristic
3. Map OpenRouter 400 errors containing "context length" to `ContextExhaustedError`

## Parser Hardening for Non-Claude Models (S-29/S-30/S-32)

These are NOT adapter changes — they're `subagents.py` improvements that make the pipeline robust to non-Claude output:

1. **Confidence clamping** (S-29): After parsing, clamp to [0.0, 1.0]. If raw value is in (1.0, 100.0], divide by 100 (percentage heuristic). Log warning for any clamped value.
2. **Verdict fuzzy matching** (S-30): Case-insensitive match with punctuation stripping. If no exact match, substring containment (`"MOSTLY_CORRECT"` contains `"CORRECT"` → map to `MINOR_ISSUES`). Conservative fallback: `MAJOR_FLAW`.
3. **Issue tag tolerance** (S-32): Accept `[major]` (lowercase), `[MAJOR]` anywhere in line (not just prefix). For untagged issues, keyword heuristic → MAJOR if contains "error"/"wrong"/"missing", else MINOR.

These improvements benefit ALL models (including Claude edge cases) and should be implemented regardless of the adapter.

## Calibration Resume Safety (P7-4)

The calibration script must:
1. Store `model` field in each trace entry
2. On `--resume`, validate that existing traces use the same model as the current `--model` flag
3. Reject mixed-model resume unless `--force-resume` is specified

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Nemotron output quality degrades on hard problems | Default to nano model (better compliance); fall back to manual CLI |
| OpenRouter rate limits free models | Default to `--workers 1` for free models; add jitter to retry delays (S-24) |
| openai SDK version churn | Pin `openai>=1.0,<3.0`; the chat completions API is stable |
| Tool use round-trip breaks on edge cases | Deep-copy PYTHON_TOOL before translation; test multi-round tool loops |
| Bidirectional message serialization (S-9/S-21) | Dedicated `_serialize_messages()` method with round-trip tests |
| API key leakage (S-42) | Sanitize exceptions; test that events.jsonl contains no key substrings |
| Daily token limit on free models (S-44) | Detect "daily" in rate limit message; checkpoint immediately instead of retrying |
| Content filtering on physics problems (S-37) | Map content_filter finish_reason; retry once; fall back to different model |
| Mixed-model calibration resume (P7-4) | Store model in traces; validate on resume |
