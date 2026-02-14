# Plan: Configurable Agent Settings + Adaptive Loop Improvements

Based on our discussion and insights from arxiv:2602.11865 (Intelligent AI Delegation).

## Changes overview

Five changes, ordered by dependency. Each is self-contained and testable independently.

---

## 1. Configurable confidence threshold (`models.py`, `agent.py`)

**What:** Add `confidence_threshold: float = 0.90` to `AgentConfig`. Replace the two hardcoded `0.90` literals in `VerificationResult.is_acceptable` and `needs_revision` with a threshold parameter.

**Problem:** `VerificationResult` doesn't currently have access to `AgentConfig` — the threshold is baked into the dataclass property. Two options:

- **(a)** Pass threshold as a parameter to `is_acceptable()` / `needs_revision()` methods → breaks the `@property` interface, requires updating all call sites in `agent.py`.
- **(b)** Add `confidence_threshold` field to `VerificationResult` itself, set during construction in `verify()` → preserves `@property`, minimal call-site changes.

**Choice:** (a) — convert properties to methods. There are only 4 call sites in `agent.py` (lines 149, 185, 226, and the implicit `needs_revision`). This is cleaner than duplicating config state into every `VerificationResult`.

**Files changed:**
- `src/alethic/models.py`: Add field to `AgentConfig`, change `is_acceptable` / `needs_revision` from `@property` to methods accepting `threshold` param.
- `src/alethic/agent.py`: Pass `self.config.confidence_threshold` at each call site.
- `tests/test_alethic.py`: Update tests that call `.is_acceptable` / `.needs_revision` (now methods with args). Add tests for custom thresholds.

---

## 2. Diminishing-returns detection (`agent.py`)

**What:** During the revision sub-loop (agent.py lines 186-268), track confidence across consecutive revisions. If confidence changes by less than `stall_threshold` (default 0.03) for 2 consecutive revisions, break out of the revision loop early and restart from the generator.

**Mechanism:** Before the revision for-loop, initialize `prev_confidence = verification.confidence`. After each re-verification, compute `delta = abs(verification.confidence - prev_confidence)`. If `delta < stall_threshold` for `stall_window` consecutive revisions, log a stall detection message and `break`.

**Config additions to `AgentConfig`:**
- `stall_threshold: float = 0.03` — minimum confidence change to consider progress
- `stall_window: int = 2` — consecutive stalled revisions before restart

**Files changed:**
- `src/alethic/models.py`: Add two fields.
- `src/alethic/agent.py`: Add stall detection logic inside the revision loop.
- `tests/test_alethic.py`: Add test where confidence plateaus and agent breaks out early.

---

## 3. Escalation ladder (`agent.py`)

**What:** If the agent exhausts `max_revisions_per_cycle` without success AND extended thinking is not yet enabled, automatically escalate by enabling extended thinking for subsequent iterations. This is the "adaptive response based on reversibility" insight from the delegation paper — try the cheap approach first, escalate only when stuck.

**Mechanism:** Track an `escalated` boolean. After any iteration where all revisions are exhausted without acceptance:
- If `not config.extended_thinking and not escalated`, set `escalated = True`, temporarily enable thinking on the config, and log `[ESCALATE] Enabling extended thinking for remaining iterations`.
- On the final `AgentResult`, include whether escalation occurred (add `escalated: bool` field to `AgentResult`).

**Guard:** Only escalate once. Don't escalate if the user already enabled `--thinking`. Don't mutate the original config — use a local copy or a flag that `_call_model` checks.

**Files changed:**
- `src/alethic/models.py`: Add `escalated: bool = False` to `AgentResult`.
- `src/alethic/agent.py`: Add escalation logic between iterations.
- `tests/test_alethic.py`: Add test verifying escalation triggers after exhausted revisions.

---

## 4. CLI flags for new settings + presets (`cli.py`)

**What:** Expose the new config fields and add a `--preset` flag.

**New CLI flags:**
- `--confidence-threshold` (float, default 0.90)
- `--temperature-generator` (float, default 1.0)
- `--temperature-verifier` (float, default 0.2)
- `--temperature-reviser` (float, default 0.7)
- `--preset {quick,default,thorough}` — sets a bundle of defaults that individual flags can override

**Preset definitions:**
- `quick`: iterations=2, revisions=1, confidence_threshold=0.85, max_tokens=8192
- `default`: current defaults (no-op)
- `thorough`: iterations=8, revisions=5, confidence_threshold=0.95, extended_thinking=True, thinking_budget=15000, max_tokens=32768

**Precedence:** Preset sets the base, then explicit flags override. Implementation: apply preset values first, then overwrite with any explicitly-provided CLI args (using argparse defaults detection).

**Files changed:**
- `src/alethic/cli.py`: Add flags, add preset logic, wire into AgentConfig construction.
- `tests/test_alethic.py`: Add CLI parser tests for presets and new flags.

---

## 5. Structured JSON logging (`agent.py`, `cli.py`)

**What:** Add a `--log-json <path>` CLI flag. When set, the agent writes a single JSON record to the file after solve() completes, containing all config values, per-iteration metrics, and the final outcome.

**Schema:**
```json
{
  "timestamp": "2026-02-14T12:00:00Z",
  "problem_hash": "sha256:abc123...",
  "config": { "model": "...", "confidence_threshold": 0.90, ... },
  "iterations": [
    {
      "iteration": 1,
      "phases": [
        { "phase": "generate", "tokens": null },
        { "phase": "verify", "verdict": "minor_issues", "confidence": 0.72 },
        { "phase": "revise", "revision": 1 },
        { "phase": "verify", "verdict": "correct", "confidence": 0.93 }
      ]
    }
  ],
  "result": {
    "solved": true,
    "verdict": "correct",
    "confidence": 0.93,
    "iterations_used": 1,
    "total_revisions": 1,
    "escalated": false,
    "elapsed_seconds": 45.2
  }
}
```

**Mechanism:** The `history` list in `agent.py` already captures per-phase data. Add a `write_log(path, config, history, result)` function. The CLI calls it after `solve()` if `--log-json` is provided. Append mode (one JSON object per line, JSONL format) so multiple runs accumulate in the same file.

**Files changed:**
- `src/alethic/agent.py`: Add `write_log()` utility function.
- `src/alethic/cli.py`: Add `--log-json` flag, call `write_log()` after solve.
- `tests/test_alethic.py`: Test that log output is valid JSONL with expected fields.

---

## Test plan

After each change, run:
```bash
pytest --cov=alethic
ruff check src tests
mypy src/alethic
```

Existing tests must continue to pass (the `is_acceptable`/`needs_revision` change in step 1 will require updating existing test calls). Each step adds new tests for the feature it introduces.

---

## What this does NOT include (and why)

- **Majority-vote verification**: As discussed, same-model samples at T=0.2 are too correlated to provide meaningful consensus for math proofs. Deferred until formal verification backend exists.
- **Verification-aware difficulty classification**: Requires logging data to calibrate. Build logging first (step 5), revisit later.
- **Diverse proof strategy prompting**: Good idea but purely a prompt change — can be done independently without code architecture changes.
- **Cost budget**: Needs token counting from API responses, which the current `history` doesn't capture. Deferred to after logging is in place.
