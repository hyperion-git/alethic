# Stochastic Reset Mechanism — Design Document

**Date:** 2026-02-18
**Status:** Proposed
**Scope:** Python library (`MathAgent`, `PhysicsAgent`). Skill-side orchestrator deferred to v2.

## Problem

The Alethic GVR loop can get trapped in two distinct failure modes:

**Mode 1 — Confidence plateau.** The Generator produces solutions in the same
neighborhood. Confidence oscillates (e.g., 0.72 → 0.74 → 0.71 → 0.73) without
meaningfully improving. The Reviser patches issue A, breaks thing B. The agent
burns its iteration budget making lateral moves.

**Mode 2 — Persistent structural flaw.** The Generator repeatedly produces
solutions with the same fundamental error. The Verifier flags `MAJOR_FLAW` every
iteration. The `failed_approaches` list grows, but the Generator cannot find a
categorically different proof technique.

### Why existing defenses are insufficient

| Defense | Limitation |
|---------|-----------|
| `failed_approaches` text injection | 200-char truncated summaries; Generator can misinterpret or ignore |
| `MAJOR_FLAW → break` from revision | Re-enters the Generator with the same prompt distribution |
| Best-of-N sampling | Helps within an iteration but doesn't prevent cross-iteration convergence |
| Balanced prompting | Steers away from confirmation bias, not away from exhausted strategies |

## Design: Dual-Trigger Stall Detection with Structural Reset

### Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Enhanced Orchestrator Loop                       │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │                     STALL MONITOR                          │      │
│  │                                                            │      │
│  │  ┌──────────────────┐    ┌───────────────────────┐        │      │
│  │  │ No-Progress       │    │ Major-Flaw Streak     │        │      │
│  │  │ Detector          │    │ Detector              │        │      │
│  │  │                   │    │                       │        │      │
│  │  │ "Has best conf.   │    │ "Were the last 2      │        │      │
│  │  │  improved by ≥ε   │    │  iteration-final      │        │      │
│  │  │  in the last W    │    │  verdicts both        │        │      │
│  │  │  iterations?"     │    │  MAJOR_FLAW?"         │        │      │
│  │  └────────┬──────────┘    └──────────┬────────────┘        │      │
│  │           │ NO                        │ YES                 │      │
│  │           └────────────┬──────────────┘                     │      │
│  │                        ▼                                    │      │
│  │               ┌─────────────────┐                           │      │
│  │               │  TRIGGER RESET  │                           │      │
│  │               │  (if cooldown=0 │                           │      │
│  │               │   & resets<max) │                           │      │
│  │               └────────┬────────┘                           │      │
│  └────────────────────────┼────────────────────────────────────┘      │
│                           │                                           │
│          ┌────────────────┼────────────────────┐                     │
│          │ NORMAL         │ RESET              │                     │
│          │ ITERATION      ▼ ITERATION          │                     │
│          │                                     │                     │
│   ┌──────┴──────┐   ┌─────────────────────┐   │                     │
│   │ Generator   │   │ Generator           │   │                     │
│   │ (N cands,   │   │ (N + boost cands,   │   │                     │
│   │  standard   │   │  STRATEGY RESET     │   │                     │
│   │  prompt)    │   │  prompt overlay,    │   │                     │
│   │             │   │  trimmed context)   │   │                     │
│   └──────┬──────┘   └──────────┬──────────┘   │                     │
│          │                     │               │                     │
│          ▼                     ▼               │                     │
│   ┌─────────────┐   ┌─────────────┐           │                     │
│   │  Verifier   │   │  Verifier   │           │                     │
│   │  (all N)    │   │  (all N+b)  │           │                     │
│   └──────┬──────┘   └──────┬──────┘           │                     │
│          │                  │                  │                     │
│          ▼                  ▼                  │                     │
│   ┌─────────────┐   ┌─────────────┐           │                     │
│   │  Reviser    │   │  Reviser    │           │                     │
│   │ (up to K    │   │ (max 1 rev, │           │                     │
│   │  revisions) │   │  catch errs │           │                     │
│   │             │   │  only)      │           │                     │
│   └──────┬──────┘   └──────┬──────┘           │                     │
│          │                  │                  │                     │
│          └──────────┬───────┘                  │                     │
│                     ▼                          │                     │
│          ┌──────────────────┐                  │                     │
│          │ Update RunState  │◄─────────────────┘                     │
│          │ • confidence     │                                        │
│          │ • verdicts       │                                        │
│          │ • stall counters │                                        │
│          │ • failed approach│  ← capture iteration-final             │
│          │   (post-revision)│     verification, not pre-revision     │
│          └────────┬─────────┘                                        │
│                   ▼                                                   │
│          Next iteration / Accept / Admit failure                     │
└──────────────────────────────────────────────────────────────────────┘
```

### Detection

The stall monitor runs at the **top of each iteration**, before generation.
It evaluates two independent conditions (either triggers a reset):

**Detector 1 — No Progress.** Tracks how many consecutive iterations have passed
without `best_confidence` improving by at least `stall_epsilon` (default 0.03).
When this counter reaches `stall_window` (default 2), a reset triggers.

This catches:
- Confidence plateaus (0.72 → 0.74 → 0.71 → 0.73 — best stuck at 0.74)
- Oscillation (0.3 → 0.9 → 0.3 → 0.9 — best stuck at 0.9)
- Creeping non-progress (0.41 → 0.42 → 0.43 — improvements below epsilon)

**Detector 2 — Major-Flaw Streak.** Tracks the verdict from each iteration's
final verification (post-revision if revision ran). If the last 2 iteration-final
verdicts are both `MAJOR_FLAW`, a reset triggers immediately — bypassing
`stall_window`. This fast-path catches structural inability to avoid a
fundamental error.

Both detectors are fully deterministic. No random draws, no probability ramping.
Stochasticity comes from the response (LLM generation with modified prompt),
not the trigger.

### Reset Actions (one iteration, auto-revert)

When a reset triggers, three things change for exactly one iteration:

**1. Wider candidate pool.** Generate `best_of_n + reset_n_boost` candidates.
For `default` preset (N=2), a reset iteration generates 3. This is structural
diversity — more independent samples — not sampling noise.

No temperature boost. Temperature is forced to 1.0 in extended-thinking mode
(`subagents.py:105`), making temp overrides dead for `thorough`/`extreme` presets.
Prompt instructions dominate over temperature for steering LLM strategy.

**2. Strategy reset prompt.** Instead of standard `failed_approaches` injection,
the Generator receives a restructured prompt block:

```markdown
## STRATEGY RESET — Previous approaches exhausted

The following high-level strategies have been tried and failed:
- [last 2 failed approach summaries only]

You MUST use a categorically different proof technique.
Do NOT refine, extend, or repair any previous approach.
Start from a completely different mathematical foundation.
```

Built as a prompt-only overlay — `state.failed_approaches` is not mutated
(it's returned in `AgentResult` for diagnostics). Truncation to 2 entries
prevents over-constraining the Generator when 5+ approaches have accumulated.

**3. Reduced revision budget.** The Reviser gets at most 1 revision attempt
instead of the usual 3-5. After a reset, the Generator's fresh approach should
stand on its own merit. Deep revision risks re-anchoring to the old strategy.
One pass catches mechanical errors (sign mistakes, missing edge cases) without
deep anchoring.

### Guardrails

- **Cooldown:** 1 iteration after each reset runs normally regardless of signals.
  Prevents reset/normal/reset thrashing.
- **Max resets:** Scales with horizon: `max(1, max_iterations // 4)`.
  `default` (5 iters) → 1 reset. `extreme` (12 iters) → 3 resets.
- **Quick preset:** Sets `stall_reset=False` (2 iterations, no meaningful signal).

### Configuration

4 new fields on `AgentConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `stall_window` | `int` | `2` | Iterations without meaningful improvement before reset |
| `stall_epsilon` | `float` | `0.03` | Minimum confidence improvement to count as progress |
| `stall_reset` | `bool` | `True` | Enable/disable stall detection and reset |
| `reset_n_boost` | `int` | `1` | Extra candidates generated during reset iteration |

Preset defaults:

| Preset | stall_window | stall_epsilon | stall_reset | reset_n_boost |
|--------|-------------|---------------|-------------|---------------|
| `quick` | 2 | 0.03 | `False` | 0 |
| `default` | 2 | 0.03 | `True` | 1 |
| `thorough` | 3 | 0.02 | `True` | 1 |
| `extreme` | 3 | 0.02 | `True` | 2 |

### State Additions to RunState

```python
iterations_since_meaningful_improvement: int = 0
iteration_final_verdicts: deque[Verdict] = field(
    default_factory=lambda: deque(maxlen=3)
)
resets_used: int = 0
reset_cooldown_remaining: int = 0
```

Cheap: a counter, a bounded deque, two ints. No unbounded growth.

### New Event Type

Add `STALL_RESET = "stall_reset"` to `EventType` enum. Emit with metadata:

```python
log.emit(
    EventType.STALL_RESET,
    iteration,
    reason="no_progress" | "major_flaw_streak",
    n_override=effective_n,
    max_revisions_override=1,
    resets_used=state.resets_used,
    stall_counter=state.iterations_since_meaningful_improvement,
)
```

### Code Changes

| File | Change |
|------|--------|
| `models.py` | Add 4 fields to `AgentConfig`, update `PRESETS`, add `STALL_RESET` to `EventType`, add validation in `__post_init__` |
| `agent.py` | Add 4 fields to `RunState`, add `_check_stall()` method to `MathAgent`, compute `n_this_iter` inside loop, add `max_revisions` param to `_run_revision_loop`, add `reset_context` param to `_generate_candidates`, update state after each iteration with final verdict and progress check |
| `subagents.py` | Add `reset_context: str \| None` param to `generate()`, inject before failed_approaches block |
| `cli.py` | Add `--no-stall-reset` flag, add `--stall-window` and `--stall-epsilon` to `_FLAG_TO_CONFIG` |
| `physics_agent.py` | No changes (inherits from `MathAgent`) |
| `prompts.py` | Add `STRATEGY_RESET_ADDENDUM` template |
| `physics_prompts.py` | Add `PHYSICS_STRATEGY_RESET_ADDENDUM` template (physics-flavored wording) |

### Bugfix Included

Move `failed_approaches` capture from pre-revision verification
(`agent.py:566`) to iteration-final verification. Currently the summary is
taken before revision runs, which can record a stale failure cause if the
Reviser changes the failure mode. After the fix, the summary reflects the
actual final state of the iteration.

### Test Impact

| Test file | Impact |
|-----------|--------|
| `tests/test_new_types.py` | Update `EventType` member count assertion |
| `tests/test_alethic.py` | Add new config/preset assertions for stall fields. Existing tests unaffected (stall_window can't trigger in 1-2 iteration runs) |
| `tests/test_best_of_n.py` | Add targeted reset tests: mock confidence plateau trajectory, mock MAJOR_FLAW streak, verify N boost and revision cap |
| New: `tests/test_stall_reset.py` | Dedicated tests: plateau detection, oscillation detection, creeping non-progress, major-flaw fast-path, cooldown enforcement, max-reset cap, prompt overlay construction, bugfix verification |

### Deliberately Excluded

| Feature | Rationale |
|---------|-----------|
| Temperature boost | Dead in extended-thinking mode. Instructions dominate over temperature for strategy steering. |
| Approach-family classification | Infeasible with free-form LLM proofs. Regex misses hybrids and novel framings. |
| Issue fingerprinting | Confidence trajectory + verdict pattern subsume this signal. |
| Stochastic trigger | Non-deterministic = hard to test. Deterministic trigger with stochastic response achieves the same exploration. |
| 18-field config | Maintenance burden disproportionate to feature value. |
| Skip revision entirely | Loses error-catching on near-miss solutions. Reducing to 1 revision is the right compromise. |
| Skill-side implementation | Different state model, no temperature control. Separate design pass needed for v2. |

### Expected Impact

For a `default` preset run (5 iterations, N=2) where the agent stalls at
iteration 3:

- **Without reset:** Iterations 3-5 burn budget on minor variations of the same
  approach. Result: `UNSOLVED` at ~0.74 confidence.
- **With reset:** Iteration 3 stalls. Iteration 4 triggers reset — 3 candidates
  with strategy-reset prompt, 1 revision max. If any candidate finds a new
  approach at confidence >=0.90, solved. If not, iteration 5 runs normally with
  the new best. The agent gets 1-2 genuinely fresh attempts instead of 3 wasted
  incremental ones.

### Design Process

This design was produced through multi-agent analysis:

1. Three parallel Opus agents (code-explorer, code-architect, code-reviewer)
   analyzed the codebase and two initial proposals
2. Codex (gpt-5.3-codex, xhigh reasoning) provided the initial design and
   served as adversarial critic, identifying failure cases in the confidence
   detector and the temperature-boost cargo cult
3. The final design takes the best ideas from each source and resolves
   disagreements (temperature: rejected per Codex critique; skip revision:
   compromised to reduce-not-skip per reviewer; stochastic trigger: rejected
   per reviewer's testability argument)
