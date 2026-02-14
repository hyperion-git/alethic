# Plan: Configurable Agent Settings + Adaptive Loop Improvements

Based on our discussion and insights from arxiv:2602.11865 (Intelligent AI Delegation).

## Changes overview

Five changes, ordered by dependency. Each is self-contained and testable independently.

---

## 1. Configurable confidence threshold + presets (`models.py`)

**What:** Add `confidence_threshold: float = 0.90` to `AgentConfig`. Replace hardcoded `0.90` literals. Add a `PRESETS` dict and `AgentConfig.from_preset()` classmethod so both the CLI and library users can select named configurations.

### 1a. AgentConfig changes

Add field:
```python
confidence_threshold: float = 0.90
```

Add classmethod:
```python
PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
    "quick": {
        "max_iterations": 2,
        "max_revisions_per_cycle": 1,
        "confidence_threshold": 0.85,
        "extended_thinking": False,
        "max_tokens": 16384,
    },
    "default": {
        "max_iterations": 5,
        "max_revisions_per_cycle": 3,
        "confidence_threshold": 0.90,
        "extended_thinking": False,
        "max_tokens": 16384,
    },
    "thorough": {
        "max_iterations": 8,
        "max_revisions_per_cycle": 5,
        "confidence_threshold": 0.95,
        "extended_thinking": True,
        "thinking_budget": 15000,
        "max_tokens": 32768,
    },
    "extreme": {
        "max_iterations": 12,
        "max_revisions_per_cycle": 5,
        "confidence_threshold": 0.97,
        "extended_thinking": True,
        "thinking_budget": 40000,
        "max_tokens": 65536,
    },
}

@classmethod
def from_preset(cls, name: str, **overrides) -> AgentConfig:
    """Create config from a named preset, with optional field overrides."""
    if name not in cls.PRESETS:
        raise ValueError(f"Unknown preset: {name!r}. Choose from: {list(cls.PRESETS)}")
    params = dict(cls.PRESETS[name])
    params.update(overrides)
    return cls(**params)
```

### 1b. VerificationResult threshold methods

Convert `is_acceptable` and `needs_revision` from `@property` to methods accepting `threshold`:

```python
def is_acceptable(self, threshold: float = 0.90) -> bool:
    return self.verdict == Verdict.CORRECT and self.confidence >= threshold

def needs_revision(self, threshold: float = 0.90) -> bool:
    return self.verdict in (Verdict.MINOR_ISSUES, Verdict.MAJOR_FLAW) or (
        self.verdict == Verdict.CORRECT and self.confidence < threshold
    )
```

Default stays 0.90 so any code calling without args still works.

**Files changed:**
- `src/alethic/models.py`: Add field, add `PRESETS` + `from_preset()`, convert properties to methods.
- `src/alethic/__init__.py`: No changes needed — `AgentConfig` is already exported.

---

## 2. Wire threshold through agent.py

**What:** Pass `self.config.confidence_threshold` at all 4 call sites where `is_acceptable` / `needs_revision` are checked.

Call sites (current line references):
- Line 149: `if verification.is_acceptable` → `if verification.is_acceptable(threshold)`
- Line 185: `if verification.needs_revision` → `if verification.needs_revision(threshold)`
- Line 226: `if verification.is_acceptable` → `if verification.is_acceptable(threshold)`
- Line 243–248: `verification.verdict == Verdict.MAJOR_FLAW` / `UNSOLVED` — no change, these don't use threshold.

Also update the log messages in Step 2c-equivalent logic (the confidence threshold check at correct-but-low-confidence) to use the configured threshold.

**Files changed:**
- `src/alethic/agent.py`: 4 call sites updated.

---

## 3. CLI flags for presets and new settings (`cli.py`)

**What:** Add `--preset`, `--confidence-threshold`, and per-subagent temperature flags.

### New CLI flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--preset` `-p` | choice | _(none)_ | Named preset: quick, default, thorough, extreme |
| `--confidence-threshold` | float | 0.90 | Minimum confidence for acceptance |
| `--temperature-generator` | float | 1.0 | Generator sampling temperature |
| `--temperature-verifier` | float | 0.2 | Verifier sampling temperature |
| `--temperature-reviser` | float | 0.7 | Reviser sampling temperature |

### Precedence logic

Preset sets the base config; explicit flags override. Implementation:

1. If `--preset` given, start with `AgentConfig.from_preset(name)`.
2. For each other flag, check if it was explicitly provided (not just the argparse default). Use a sentinel approach: set the default of overridable flags to `None`, then only apply non-None values.
3. Build final `AgentConfig`.

```python
def _build_config(args: argparse.Namespace) -> AgentConfig:
    if args.preset:
        config = AgentConfig.from_preset(args.preset)
    else:
        config = AgentConfig()

    # Explicit flags override preset values.
    # Only override if the user actually provided the flag.
    if args.model is not None:
        config = dataclasses.replace(config, model=args.model)
    if args.iterations is not None:
        config = dataclasses.replace(config, max_iterations=args.iterations)
    # ... etc for each overridable flag
```

**Note:** Existing flags (`--iterations`, `--revisions`, `--max-tokens`, `--thinking`, `--thinking-budget`) need their defaults changed to `None` so we can detect "user didn't pass this" vs "user explicitly passed the default value". Then fall through to the preset/default value.

**Files changed:**
- `src/alethic/cli.py`: Add flags, refactor config construction.

---

## 4. Update tests (`tests/test_alethic.py`)

**What:** Fix breaking changes and add new coverage.

### Tests to update (breaking from property→method change)

- `test_verification_result_properties`: Change `.is_acceptable` → `.is_acceptable()`, `.needs_revision` → `.needs_revision()`.
- `test_correct_but_low_confidence_needs_revision`: Same change.
- `test_correct_at_threshold_is_acceptable`: Same change.

### New tests to add

1. **`test_preset_from_preset_quick`**: `AgentConfig.from_preset("quick")` returns correct values.
2. **`test_preset_from_preset_thorough`**: `AgentConfig.from_preset("thorough")` returns correct values.
3. **`test_preset_from_preset_with_overrides`**: `AgentConfig.from_preset("quick", max_iterations=10)` overrides iterations.
4. **`test_preset_unknown_raises`**: `AgentConfig.from_preset("nonexistent")` raises `ValueError`.
5. **`test_custom_confidence_threshold`**: Create `VerificationResult(CORRECT, 0.88)`, verify `is_acceptable(0.85)` is True, `is_acceptable(0.90)` is False.
6. **`test_config_confidence_threshold_field`**: `AgentConfig(confidence_threshold=0.85).confidence_threshold == 0.85`.
7. **`test_cli_preset_flag`**: Parser with `--preset quick "problem"` yields correct args.
8. **`test_cli_preset_with_override`**: Parser with `--preset quick --iterations 10 "problem"` yields iterations=10.
9. **`test_cli_temperature_flags`**: Parser with temperature flags.

### Integration test updates

The existing integration tests (`test_solve_correct_on_first_try`, `test_solve_with_revision`, `test_admit_failure`) use `.is_acceptable` as a property internally via `agent.solve()` — these don't call it directly, so they should work unchanged as long as `agent.py` is updated correctly. But verify they pass.

**Files changed:**
- `tests/test_alethic.py`

---

## 5. Skill presets (`skills/solve/SKILL.md`)

**What:** Add `--preset` flag to the skill's argument parsing and make the confidence threshold configurable. The skill can't control temperature or extended thinking (Task sub-agent limitation), but it CAN control iterations, revisions, budget, and acceptance threshold.

### 5a. Argument parsing table

Add `--preset` / `-p` flag to the table in the "Argument Parsing" section:

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--preset` | `-p` | _(none)_ | Named preset: quick, default, thorough, extreme |
| `--iterations` | `-i` | 5 | Maximum generate-verify-revise iterations |
| `--revisions` | `-r` | 3 | Maximum revision attempts per iteration |
| `--budget` | `-b` | 50 | Maximum total Task sub-agent calls |
| `--threshold` | `-t` | 0.90 | Minimum confidence for solution acceptance |

### 5b. Preset definitions (inline in SKILL.md)

Add a "Presets" subsection after argument parsing:

```markdown
### Presets

If `--preset` is provided, it sets base values that explicit flags can override:

| Preset | Iters | Revs | Budget | Threshold |
|--------|-------|------|--------|-----------|
| `quick` | 2 | 1 | 10 | 0.85 |
| `default` | 5 | 3 | 50 | 0.90 |
| `thorough` | 8 | 5 | 80 | 0.95 |
| `extreme` | 12 | 5 | 120 | 0.97 |

Precedence: preset values first, then explicit flags override.
```

Budget values are scaled to match the expected Task call count per preset:
- `quick`: 2 iter × (2 + 1×2) + 1 beautify = 9, so budget 10
- `default`: 5 × (2 + 3×2) + 1 = 41, so budget 50
- `thorough`: 8 × (2 + 5×2) + 1 = 97, so budget 80 (tighter — stall detection assumed)
- `extreme`: 12 × (2 + 5×2) + 1 = 145, so budget 120 (same reasoning)

### 5c. Make confidence threshold configurable

Currently SKILL.md hardcodes `0.90` in multiple places:
- Step 2c: `confidence >= 0.90`
- Step 2c: "confidence below the 0.90 threshold"
- Step 2d.9: `confidence >= 0.90`
- Step 5 solved header: `confidence >= 0.90`

Replace all with `{confidence_threshold}` that gets set from the parsed preset/flag value.

### 5d. Add examples for presets

```
- `/solve -p quick "Is 17 prime?"` — fast, 2 iterations, 85% bar
- `/solve -p thorough "Prove the Cayley-Hamilton theorem"` — extended
- `/solve -p extreme "Prove Sylvester-Gallai theorem"` — competition level
- `/solve -p quick -i 4 "Check: sum of 1 to 100"` — quick preset but 4 iterations
```

### 5e. Update Known Limitations

Update the Known Limitations section to note that presets control iteration/revision/threshold/budget parameters. Temperature and extended thinking remain Task sub-agent limitations.

**Files changed:**
- `skills/solve/SKILL.md`

---

## Preset reference table (both API and skill)

| Preset | Iters | Revs | Threshold | Thinking | Think budget | Max tokens | Skill budget |
|--------|-------|------|-----------|----------|-------------|------------|--------------|
| `quick` | 2 | 1 | 0.85 | off | — | 16,384 | 10 |
| `default` | 5 | 3 | 0.90 | off | — | 16,384 | 50 |
| `thorough` | 8 | 5 | 0.95 | on | 15,000 | 32,768 | 80 |
| `extreme` | 12 | 5 | 0.97 | on | 40,000 | 65,536 | 120 |

The API presets include `extended_thinking`, `thinking_budget`, and `max_tokens` (used by `_call_model()`). The skill presets include `budget` (used by the orchestrator's Task call counter). Temperature and thinking are API-only — the skill notes this in Known Limitations.

---

## What this does NOT include (and why)

- **Diminishing-returns detection (stall detection)**: Planned separately — needs its own `stall_threshold` and `stall_window` config fields, plus logic in the revision loop. Doing it in the same PR would bloat the diff.
- **Escalation ladder**: Also separate — auto-enables thinking when stuck. Depends on stall detection to trigger. Will add `escalated: bool` to `AgentResult`.
- **Structured JSON logging**: Separate concern. Will add `--log-json` flag after presets are in.
- **Majority-vote verification**: Same-model samples at T=0.2 are too correlated. Deferred until formal verification backend.
- **Cost budget**: Needs token counting from API responses. Deferred to after logging.

---

## Test plan

After each step, run:
```bash
pytest --cov=alethic
ruff check src tests
mypy src/alethic
```

Existing tests must continue to pass. The `is_acceptable`/`needs_revision` property→method change (step 1b) will break 3 existing tests — step 4 fixes them.

---

## Implementation order

1. `models.py` — threshold field, property→method, PRESETS dict, from_preset()
2. `agent.py` — wire threshold through 4 call sites
3. `cli.py` — new flags, preset logic, config builder refactor
4. `tests/test_alethic.py` — fix broken tests, add new tests
5. `skills/solve/SKILL.md` — preset flag, threshold parameterization, examples

Steps 1-2 are tightly coupled. Step 3 depends on 1. Step 4 depends on 1-3. Step 5 is independent of 1-4.
