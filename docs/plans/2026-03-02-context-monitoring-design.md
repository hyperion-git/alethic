# Context Window Monitoring & Checkpoint-Resume

**Date**: 2026-03-02
**Status**: Approved
**Scope**: Python library + skill orchestrator

## Problem

Neither the Python library nor the skill orchestrator monitors context window utilization:

- **Python library**: Each subagent call (`generate`, `verify`, `revise`) pastes full solution text and critique into the API call's user message with no length cap. `response.usage` is never read. `stop_reason == "max_tokens"` (truncated response) is never detected. If input exceeds the model's 200K context window, the API call fails with an unhandled error.
- **Skill orchestrator**: File-based state prevents most context growth, but Task call/response pairs accumulate in the main conversation. The `failed_approaches` list inlined into Generator prompts grows unboundedly. No mechanism to gracefully stop when context pressure builds.

## Approach

**Lightweight Token Ledger** (Approach A from brainstorming): Track token usage from `response.usage` after every API call. Use a chars/4 heuristic for pre-flight context estimation (no `count_tokens` API call — saves latency). When approaching the context limit, **checkpoint state to disk and stop** rather than truncating or degrading. Users resume from the checkpoint in a fresh session.

Key insight from user: the right response to context exhaustion is not degradation — it's serializing state and breaking cleanly so work can resume later.

## Design

### 1. Token Ledger (`models.py`)

New dataclass tracking cumulative token usage across all API calls in a session:

```python
@dataclass
class TokenLedger:
    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.api_calls += 1
```

New `context_threshold` field on `AgentConfig` (default `0.8`, configurable). Model context limits map:

```python
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
# Fallback: 200_000 for unknown models
```

`AgentResult` gains: `token_ledger: TokenLedger`, `session_dir: str | None`, `checkpoint_path: str | None`.

### 2. Context Safety in `subagents.py`

Changes to `_call_model()`:

**A. Token tracking**: Accept optional `ledger: TokenLedger` and `context_limit: int` parameters. After each `client.messages.create()`, call `ledger.record(response.usage)`.

**B. Truncated response detection**: Check `response.stop_reason == "max_tokens"`. When detected, raise `TruncatedResponseError`. Callers handle per-role:
- Generator truncation: skip candidate, try next
- Verifier truncation: treat as `unsolved` with confidence 0.0
- Reviser truncation: break revision loop

**C. Pre-flight estimate**: Before each call, estimate input tokens as `len(system + user_message) // 4`. If estimate exceeds `context_threshold * context_limit`, raise `ContextExhaustedError`. Re-estimate after each tool-use round (the message list grows with tool results).

**D. Tool-use truncation**: If `stop_reason == "max_tokens"` during a tool-use round, break the tool loop — don't parse partial tool results. Return whatever text exists or raise `TruncatedResponseError`.

Signature change (backward compatible — new params are optional):

```python
def _call_model(client, *, system, user_message, config, temperature,
                tools=None, ledger=None, context_limit=200_000) -> str:
```

### 3. Session Persistence & Checkpoint

**Session directory creation**: `MathAgent.solve()` auto-creates a session directory at the start of every call:
- Inside git repo: `.alethic/{slug}-{YYYYMMDD}-{4hex}/`
- Outside git repo: `/tmp/alethic-{slug}-{YYYYMMDD}-{4hex}/`

Writes `problem.md` and `session.json` immediately. Same layout as the skill.

**Incremental state persistence**: After every iteration, serialize full state to `session.json`:

```json
{
    "schema_version": 1,
    "session_id": "...",
    "problem": "...",
    "domain": "math|physics",
    "status": "running|solved|unsolved|checkpoint",
    "current_iteration": 3,
    "best_confidence": 0.87,
    "best_solution_path": "worklog/best_solution.md",
    "failed_approaches": ["..."],
    "stall_state": {
        "iterations_since_meaningful_improvement": 0,
        "iteration_final_verdicts": [],
        "resets_used": 0,
        "reset_cooldown_remaining": 0
    },
    "token_ledger": {"input_tokens": 45000, "output_tokens": 12000, "api_calls": 14},
    "config": {},
    "created_at": "...",
    "completed_at": null
}
```

Also writes `worklog/best_solution.md` whenever `best_solution` updates.

**Checkpoint trigger**: When `ContextExhaustedError` is caught in the main loop:
1. Write `session.json` with `"status": "checkpoint"`
2. Write `worklog/best_solution.md`
3. Return `AgentResult` with `verdict=UNSOLVED`, `admitted_failure=False`, `checkpoint_path=session_dir`
4. Log: `[CHECKPOINT] Context window approaching limit — session saved to {path}`

**Resume**: `solve()` gains `resume_from: str | None = None`. When provided:
1. Read `session.json`, validate status is `running` or `checkpoint`
2. Reconstruct `RunState` from saved fields
3. Read `worklog/best_solution.md` as `best_solution`
4. Start iteration loop from `current_iteration + 1`
5. Fresh `TokenLedger` (context resets on resume)

CLI gains `--resume PATH` flag.

### 4. Skill Orchestrator Improvements

**A. Cap `failed_approaches`**: In Step 2a, include only the last 5 entries in Generator prompts. Full list stays in `session.json`.

**B. `--resume` flag**: Parse `--resume PATH` in argument table. On startup:
- If provided: read session dir, validate status, restore state, start from `current_iteration + 1`
- Auto-detect: scan `.alethic/` for `status == "running"|"checkpoint"` sessions with matching problem text. Print hint and ask to resume.

**C. Context-pressure heuristic**: New rule in orchestrator main loop preamble: "Past iteration 6, if conversation feels sluggish or auto-compression has activated, checkpoint immediately with `status: checkpoint` and present results with a resume note."

### 5. Exception Hierarchy

New `src/alethic/exceptions.py`:

```python
class AlethicError(Exception): ...
class TruncatedResponseError(AlethicError): ...
class ContextExhaustedError(AlethicError): ...
class CheckpointError(AlethicError): ...
```

### 6. Edge Cases

1. **Resume with different config**: Saved config is used. Explicit CLI flags override saved values (same precedence as presets).
2. **Resume solved/unsolved session**: Rejected with clear error.
3. **Concurrent access**: No locking — acceptable for user-local sessions.
4. **Disk write failures**: `OSError` caught and logged as warning. Solve loop continues without persistence. Only checkpoint writes raise `CheckpointError`.
5. **Tool-use context growth**: Re-estimate after each tool round.
6. **`stop_reason` during tool rounds**: Break tool loop, don't parse partial results.

## Files Changed

| File | Change |
|------|--------|
| **New: `src/alethic/exceptions.py`** | Exception hierarchy |
| **New: `src/alethic/session.py`** | Session dir creation, checkpoint write/load, incomplete session scan |
| `src/alethic/models.py` | `TokenLedger`, `MODEL_CONTEXT_LIMITS`, `context_threshold` on `AgentConfig`, new fields on `AgentResult` |
| `src/alethic/subagents.py` | `_call_model()` gains ledger/context_limit, token tracking, stop_reason check, pre-flight estimate |
| `src/alethic/agent.py` | Session dir at solve() start, ledger through all calls, ContextExhaustedError → checkpoint, incremental writes, resume_from param |
| `src/alethic/physics_agent.py` | No changes (inherits) |
| `src/alethic/cli.py` | `--resume`, `--context-threshold`, token usage in JSON output |
| `src/alethic/__init__.py` | Export new public types |
| `skills/alethic-common/orchestrator.md` | Cap failed_approaches to 5, --resume flag + auto-detect, context-pressure heuristic |
| `skills/alethic-solve/SKILL.md` | --resume in flag table |
| `skills/alethic-derive/SKILL.md` | --resume in flag table |
| `CLAUDE.md` | Document new features |

**Unchanged**: `prompts.py`, `physics_prompts.py`, `tools.py`, `verifier_agent.py`, `synthesizer.py`, `domain.py`, `output.py`, `check_prompts.py`, `session_reader.py`.

## Test Plan

- Unit: `TokenLedger.record()` with mock `Usage` objects
- Unit: Pre-flight estimate triggering `ContextExhaustedError`
- Unit: `stop_reason == "max_tokens"` → `TruncatedResponseError`
- Unit: Checkpoint write/load round-trip
- Unit: Resume starts from `current_iteration + 1`
- Unit: Resume rejects solved/unsolved sessions
- Integration: Mock API returns large responses → checkpoint triggers
- Regression: All 585 existing tests pass (no behavioral change when `ledger=None`)
