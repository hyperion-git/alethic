# SymPy Verification Enhancement — Implementation Plan

> **For Claude:** Execute this plan task-by-task. All edits are additive (new subsections or expanded lines). Run `pytest` after all edits to verify.

**Goal:** Add SymPy-specific guidance to Generator and Verifier prompts across skills and Python API.

**Architecture:** Add subsections to existing reference files; expand single lines in Python API prompts and tool description. No structural changes.

---

### Task 1: Math Generator — Add SymPy Verification Toolkit

**Files:**
- Modify: `skills/alethic-solve/references/generator.md` (after the Tool Usage section, before ## Output)

**Step 1: Add subsection**

Insert this after the Tool Usage bullet list (after line 51, before `## Output`):

```markdown
### SymPy Verification Toolkit

SymPy is pre-imported as `sp`. Use it to verify your reasoning at critical steps:

- **Simplify and check equality**: `sp.simplify(expr1 - expr2) == 0` to verify algebraic manipulations
- **Expand/factor**: `sp.expand()`, `sp.factor()`, `sp.collect()` to confirm polynomial manipulations
- **Series expansion**: `sp.series(f, x, x0, n)` to verify Taylor/Laurent expansions
- **Symbolic integration**: `sp.integrate(f, x)` or `sp.integrate(f, (x, a, b))` to verify integrals
- **Symbolic sums**: `sp.summation(f, (n, a, b))` to verify closed-form series
- **Solve equations**: `sp.solve(eq, var)` to verify roots or solutions
- **Limits**: `sp.limit(f, x, x0)` to verify limiting behavior

Verify at least one key algebraic step symbolically when the solution involves non-trivial manipulation.
```

---

### Task 2: Math Verifier — Add Mandatory SymPy Re-derivation

**Files:**
- Modify: `skills/alethic-solve/references/verifier.md` (after the Tool Usage section, before ## Verdict Definitions)

**Step 1: Add subsection**

Insert this after the Tool Usage bullet list (after line 37, before `## Verdict Definitions`):

```markdown
### Mandatory SymPy Re-derivation

SymPy is pre-imported as `sp`. You MUST use it to independently verify:

- **Every non-trivial algebraic simplification**: `sp.simplify(claimed - rederived) == 0`
- **Closed-form sums and integrals**: Re-compute with `sp.summation()` / `sp.integrate()` and compare
- **Polynomial identities**: Verify with `sp.expand()` and `sp.factor()`
- **Solutions to equations**: Verify with `sp.solve()` and back-substitution
- **Limits and asymptotics**: Verify with `sp.limit()` and `sp.series()`

If SymPy cannot simplify an expression to match the claimed result, this is a RED FLAG — escalate to at least [MAJOR] severity unless you can verify by another method.
```

---

### Task 3: Physics Generator — Add SymPy Verification Toolkit

**Files:**
- Modify: `skills/alethic-derive/references/generator.md` (after the Tool Usage section, before ## Output)

**Step 1: Add subsection**

Insert this after the Tool Usage bullet list (after line 51, before `## Output`):

```markdown
### SymPy Verification Toolkit

SymPy is pre-imported as `sp`. Use it to verify your reasoning at critical steps:

- **Simplify and check equality**: `sp.simplify(expr1 - expr2) == 0` to verify algebraic manipulations
- **Series expansion**: `sp.series(f, x, x0, n)` to verify Taylor/Laurent expansions
- **Symbolic integration**: `sp.integrate(f, x)` or `sp.integrate(f, (x, a, b))` to verify integrals
- **Solve differential equations**: `sp.dsolve(ode, f(x))` to verify ODE solutions
- **Matrix algebra**: `sp.Matrix(...)` for eigenvalues, diagonalization, commutators
- **Dimensional checks**: Use `sympy.physics.units` to verify dimensional consistency
- **Quantum mechanics**: `sympy.physics.quantum` for commutators, bra-ket algebra, angular momentum coupling
- **Special functions**: `sp.besselj`, `sp.legendre`, `sp.assoc_laguerre`, `sp.Ynm` for known solutions
- **Physical constants**: `sympy.physics.units` for `hbar`, `c`, `e`, `m_e`, `k_B`, etc.

Verify at least one key algebraic step symbolically when the derivation involves non-trivial manipulation.
```

---

### Task 4: Physics Verifier — Add Mandatory SymPy Re-derivation

**Files:**
- Modify: `skills/alethic-derive/references/verifier.md` (after the Tool Usage section, before ## Verdict Definitions)

**Step 1: Add subsection**

Insert this after the Tool Usage bullet list (after line 37, before `## Verdict Definitions`):

```markdown
### Mandatory SymPy Re-derivation

SymPy is pre-imported as `sp`. You MUST use it to independently verify:

- **Every non-trivial algebraic simplification**: `sp.simplify(claimed - rederived) == 0`
- **ODE/PDE solutions**: Re-solve with `sp.dsolve()` and compare
- **Eigenvalue problems**: Verify with `sp.Matrix.eigenvals()` / `sp.Matrix.eigenvects()`
- **Integrals over configuration/momentum space**: Re-compute with `sp.integrate()`
- **Limiting cases**: Substitute limits symbolically (e.g., `expr.subs(hbar, 0)`) and verify known classical results
- **Dimensional consistency**: Use `sympy.physics.units` to verify all terms have matching dimensions
- **Special function identities**: Verify with SymPy's special function module (Bessel, Legendre, Laguerre, spherical harmonics)

If SymPy cannot simplify an expression to match the claimed result, this is a RED FLAG — escalate to at least [MAJOR] severity unless you can verify by another method.
```

---

### Task 5: Python API — Update prompts.py

**Files:**
- Modify: `src/alethic/prompts.py`

**Step 1: Update Generator instruction 6 (line 31-32)**

Change:
```
6. **If you need to verify a computation,** you can write Python code inside \
   <code> tags. The code will be executed and the output returned to you.
```
To:
```
6. **If you need to verify a computation,** you can write Python code inside \
   <code> tags. The code will be executed and the output returned to you. \
   SymPy is available as `sp` for symbolic computation — use it to verify \
   algebraic steps (`sp.simplify`), integrals (`sp.integrate`), series \
   (`sp.series`), and equation solving (`sp.solve`).
```

**Step 2: Update Verifier instruction 4 (line 68-69)**

Change:
```
4. **Verify computations.** If the solution includes calculations, re-derive \
   them independently. You can write Python code inside <code> tags to check.
```
To:
```
4. **Verify computations.** If the solution includes calculations, re-derive \
   them independently. You can write Python code inside <code> tags to check. \
   SymPy (available as `sp`) is strongly recommended for symbolic re-derivation \
   — verify algebraic steps with `sp.simplify(expr1 - expr2) == 0`.
```

---

### Task 6: Python API — Update physics_prompts.py

**Files:**
- Modify: `src/alethic/physics_prompts.py`

**Step 1: Update Generator instruction 7 (line 61-62)**

Change:
```
7. **If you need to verify a computation,** you can write Python code inside \
   <code> tags. The code will be executed and the output returned to you.
```
To:
```
7. **If you need to verify a computation,** you can write Python code inside \
   <code> tags. The code will be executed and the output returned to you. \
   SymPy is available as `sp` for symbolic computation — use it to verify \
   algebraic steps, solve ODEs (`sp.dsolve`), and check dimensional consistency \
   (`sympy.physics.units`). The `sympy.physics.quantum` module provides \
   commutator algebra and angular momentum coupling.
```

**Step 2: Update Verifier instruction 4 (line 98-99)**

Change:
```
4. **Verify computations.** If the derivation includes calculations, re-derive \
   them independently. You can write Python code inside <code> tags to check.
```
To:
```
4. **Verify computations.** If the derivation includes calculations, re-derive \
   them independently. You can write Python code inside <code> tags to check. \
   SymPy (available as `sp`) is strongly recommended for symbolic re-derivation \
   — verify algebraic steps with `sp.simplify(expr1 - expr2) == 0`. Use \
   `sympy.physics.units` for dimensional checks and `sp.dsolve` for ODEs.
```

---

### Task 7: Update PYTHON_TOOL description in tools.py

**Files:**
- Modify: `src/alethic/tools.py`

**Step 1: Expand description (lines 18-25)**

Change the description to:
```python
"description": (
    "Execute Python code for computational verification. "
    "Use this to check calculations, test conjectures with examples, "
    "verify formulas numerically, or perform symbolic computation. "
    "SymPy is pre-imported as `sp` — use it for symbolic simplification, "
    "integration, series expansion, equation solving, and matrix algebra. "
    "Available libraries: math, fractions, decimal, itertools, functools, "
    "collections, operator, random, statistics. "
    "NumPy, SymPy, SciPy, and mpmath are also available if installed."
),
```

---

### Task 8: Add SymPy tests to test_adversarial_skill.py

**Files:**
- Modify: `tests/test_adversarial_skill.py`

**Step 1: Add test class**

Add a new test class `TestSympyGuidance` that verifies:
- Math generator contains "SymPy" and "sp.simplify"
- Math verifier contains "Mandatory SymPy" and "RED FLAG"
- Physics generator contains "sympy.physics" and "sp.dsolve"
- Physics verifier contains "sympy.physics.units" and "RED FLAG"
- All 4 files mention `sp` is pre-imported
- Physics files mention `sympy.physics.quantum` or `sympy.physics.units` (physics-specific modules)
- Math files do NOT mention `sympy.physics` (domain separation)

---

### Task 9: Run tests and commit

**Step 1:** Run `pytest` from `/home/xeal/dev/alethic/`
**Step 2:** If all pass, commit all changes:
```bash
git add skills/alethic-solve/references/generator.md skills/alethic-solve/references/verifier.md \
      skills/alethic-derive/references/generator.md skills/alethic-derive/references/verifier.md \
      src/alethic/prompts.py src/alethic/physics_prompts.py src/alethic/tools.py \
      tests/test_adversarial_skill.py
git commit -m "Add SymPy verification guidance to Generator and Verifier prompts"
```
