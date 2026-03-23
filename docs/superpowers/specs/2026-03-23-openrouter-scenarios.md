# OpenRouter Adapter — Scenario Analysis (50 scenarios)

**Date:** 2026-03-23
**Depth:** Deep (50 iterations across 16 dimensions)

## Implementation-Critical Scenarios (must-fix — will crash or silently corrupt)

| ID | Scenario | Why critical |
|----|----------|-------------|
| S-2 | Mixed text+tool_calls must merge into single `response.content` list | `process_tool_calls()` iterates `response.content`; if tool blocks are in a separate attribute, tools never execute |
| S-9/S-21/S-28 | Block round-trip: Anthropic blocks in message history must serialize back to OpenAI format | Tool-use loop appends raw blocks at `subagents.py:258`; next API call sends them back; OpenAI rejects Anthropic-shaped objects |
| S-6 | stop_reason "length" → "max_tokens" | `TruncatedResponseError` check at `subagents.py:248` silently misses truncations |
| S-8 | Tool arguments: JSON string → dict | `block.input.get("code")` crashes with `AttributeError` if input is a raw JSON string |
| S-13 | Exception mapping: `openai.RateLimitError` → `anthropic.RateLimitError` | Retry logic at `subagents.py:150` catches only the Anthropic type |
| S-14/S-16 | Auth/timeout errors must map to `anthropic.APIError` | Agent error handler at `agent.py:1196` catches only Anthropic types |
| S-22 | Usage: `prompt_tokens` → `input_tokens` | `TokenLedger.record()` crashes with `AttributeError` on wrong field names |
| S-25/S-26/S-27 | All 5 client construction sites + variant-B must use factory | Missing any site causes mixed backends and auth failures |

## Quality-Critical Scenarios (should-fix — degrade quality silently)

| ID | Scenario | Impact |
|----|----------|--------|
| S-3 | Null content (tool-only response) → don't create TextBlock("None") | Poisons solution text with "None" string |
| S-7 | Strip thinking kwargs AND reset temperature to configured value | Temperature stuck at 1.0 makes verifier noisy |
| S-29 | Clamp CONFIDENCE to [0.0, 1.0]; heuristic for percentages | Out-of-range values corrupt stall detection and ranking |
| S-30 | Fuzzy match unknown VERDICTs (case-insensitive, strip punctuation) | Non-standard verdicts default to UNSOLVED, wasting iterations |
| S-31 | Detect finish_reason="content_filter" separately from "length" | Content-filtered responses waste revision budget on unfixable solutions |
| S-42 | Sanitize API keys from error messages before logging | Key leaks into session files, potentially committed to git |
| S-47 | Handle rate limit mid-tool-loop (round K of 15) | Lose K-1 rounds of accumulated work |

## Robustness Scenarios (nice-to-have)

| ID | Scenario | Impact |
|----|----------|--------|
| S-15 | Empty choices array → synthetic APIError | Cryptic IndexError instead of clean error |
| S-24 | Jitter on concurrent retry delays | Thundering herd on free model rate limits |
| S-34 | Query OpenRouter for model context limits | chars/4 heuristic underestimates for non-Claude tokenizers |
| S-37 | Content filter detection and retry | Physics problems with nuclear/weapons keywords get filtered |
| S-39 | Adaptive feature disabling based on compliance profile | Atom annotations waste tokens when model doesn't produce them |
| S-44 | Distinguish daily limits from burst limits | Retry loop wastes time on non-recoverable limit |
| S-49 | JSON-serialize all event data (no adapter-internal objects) | Checkpoint write fails, losing iteration work |

## Dimension Coverage

| Dimension | Scenarios | Key risk |
|-----------|-----------|----------|
| D1: Response translation | S-1 to S-7 | Block attribute access patterns |
| D2: Tool use round-trip | S-8 to S-12 | JSON argument parsing, multi-round serialization |
| D3: Error conditions | S-13 to S-16 | Exception type mismatch |
| D4: Model behavioral differences | S-17 to S-19 | Parser compliance on smaller models |
| D5: Multi-iteration loop | S-20 to S-22 | State isolation, usage field translation |
| D6: Concurrency | S-23 to S-24 | Thread safety, thundering herd |
| D7: Client factory | S-25 to S-28 | 5-site coverage, variant-B |
| D8: Calibration pipeline | S-28 to S-30 | Model metadata, mixed-model resume |
| D9: Block round-trip corruption | S-26 to S-28 | Interleaved text+tool serialization |
| D10: Parser failure cascades | S-29 to S-32 | Invalid confidence, unknown verdict, truncation |
| D11: State pollution | S-33 to S-35 | Multi-model history, token counting divergence |
| D12: Provider quirks | S-36 to S-38 | Rate limit headers, content filtering, injected system messages |
| D13: Graceful degradation | S-39 to S-41 | Feature disabling based on compliance |
| D14: Security | S-42 to S-43 | API key leakage, prompt injection |
| D15: Cost management | S-44 to S-46 | Daily limits, token count divergence, cost runaway |
| D16: Compound failures | S-47 to S-50 | Mid-loop rate limit, variant-B timeout, checkpoint failure, tool→text switch |
