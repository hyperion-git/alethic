# SymPy Verification Enhancement — Design

**Goal:** Enhance Generator and Verifier prompts with explicit SymPy guidance so that sub-agents use symbolic computation at the right moments instead of relying solely on numerical Python.

**Problem:** All 4 skill reference files and 2 Python API prompt modules say "use Python" for computation but give zero SymPy-specific guidance — no mention of available functions, no verification patterns, no physics-specific modules. The sandbox already has SymPy allowlisted and pre-imported as `sp`, but the agents don't know to use it.

## Approach: Targeted Verification Recipes

Add a domain-specific **SymPy subsection** to each reference file with recipes for when and how to use SymPy. Not a full API manual (the model already knows SymPy) — just permission and patterns.

### Key Design Decisions

1. **Generator vs Verifier differentiation:**
   - Generators get "verify your work as you go" patterns (advisory)
   - Verifiers get "mandatory independent re-derivation" patterns (imperative — this is where SymPy adds the most value for decoupled verification)

2. **Math vs Physics differentiation:**
   - Math files focus on: `simplify`, `factor`, `series`, `integrate`, `solve`, `summation`, `limit`
   - Physics files additionally cover: `sympy.physics.units`, `sympy.physics.quantum`, `sp.dsolve`, special functions, physical constants

3. **Python API parity:**
   - Update `prompts.py` and `physics_prompts.py` to mention SymPy in `<code>` tag instructions
   - Lighter touch than reference files (1-2 sentences, not full subsections)

4. **Tool description update:**
   - Update `PYTHON_TOOL` description in `tools.py` to highlight SymPy's symbolic capabilities

## Files Modified

| File | Change |
|------|--------|
| `skills/alethic-solve/references/generator.md` | Add `### SymPy Verification Toolkit` after Tool Usage |
| `skills/alethic-solve/references/verifier.md` | Add `### Mandatory SymPy Re-derivation` after Tool Usage |
| `skills/alethic-derive/references/generator.md` | Add physics-specific `### SymPy Verification Toolkit` after Tool Usage |
| `skills/alethic-derive/references/verifier.md` | Add physics-specific `### Mandatory SymPy Re-derivation` after Tool Usage |
| `src/alethic/prompts.py` | Update Generator instruction 6, Verifier instruction 4 to mention SymPy |
| `src/alethic/physics_prompts.py` | Update Generator instruction 7, Verifier instruction 4 to mention SymPy + physics modules |
| `src/alethic/tools.py` | Expand `PYTHON_TOOL` description to highlight SymPy |
| `tests/test_adversarial_skill.py` | Add tests for SymPy mentions in reference files |

## What NOT to Change

- No changes to `orchestrator.md` (it reads reference files, not SymPy logic)
- No changes to sandbox allowlist (SymPy already allowed)
- No changes to SKILL.md thin configurators
- No new dependencies
