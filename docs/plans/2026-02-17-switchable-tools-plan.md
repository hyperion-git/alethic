# Switchable Tool Guidance — Implementation Plan

> **For Claude:** Execute this plan task-by-task. Run `pytest` after all edits.

**Goal:** Make the Python tool guidance layer switchable — add NumPy/SciPy numerical verification guidance, extract all tool guidance into modular overlay files, and add a `--tools` flag that controls which tool guidance is included in sub-agent prompts.

**Architecture:** Tool guidance moves from inline reference files to modular overlay files in `{references_dir}/tools/`. The orchestrator conditionally loads overlays based on a `--tools` flag. The Python API gets a `tool_guidance` field on `AgentConfig`.

---

## File Layout

New tool overlay files (each ~10-15 lines of guidance):
```
skills/alethic-solve/references/tools/
├── sympy-generator.md     # SymPy toolkit for math generators
├── sympy-verifier.md      # SymPy mandatory re-derivation for math verifiers
├── numpy-generator.md     # NumPy/SciPy numerical verification for math generators
└── numpy-verifier.md      # NumPy/SciPy numerical spot-checks for math verifiers

skills/alethic-derive/references/tools/
├── sympy-generator.md     # SymPy toolkit for physics generators (+ sympy.physics.*)
├── sympy-verifier.md      # SymPy mandatory re-derivation for physics verifiers (+ sympy.physics.*)
├── numpy-generator.md     # NumPy/SciPy for physics generators (+ scipy.constants)
└── numpy-verifier.md      # NumPy/SciPy for physics verifiers (+ scipy.constants)
```

---

### Task 1: Create math tool overlay files

Create 4 files in `skills/alethic-solve/references/tools/`:

**`sympy-generator.md`** — move existing SymPy Verification Toolkit from `generator.md`:
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

**`sympy-verifier.md`** — move existing Mandatory SymPy Re-derivation from `verifier.md`:
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

**`numpy-generator.md`** — NEW NumPy/SciPy guidance for math generators:
```markdown
### NumPy/SciPy Numerical Verification

NumPy is pre-imported as `np`. Use numerical spot-checks to catch errors that symbolic verification might miss:

- **Random-point identity checks**: Evaluate both sides of a claimed identity at multiple random points — `np.allclose(lhs(xs), rhs(xs))` where `xs = np.random.uniform(a, b, 100)`
- **Numerical integration**: `from scipy.integrate import quad; quad(f, a, b)` to spot-check analytic integrals
- **Matrix computations**: `np.linalg.eigvals()`, `np.linalg.det()`, `np.linalg.inv()` for concrete matrix examples
- **Special function evaluation**: `from scipy.special import ...` (gamma, beta, erf, jv, legendre, etc.) to verify special function values at known points
- **Series convergence**: Compute partial sums numerically and compare against the claimed closed form
- **Edge cases**: Evaluate expressions at boundary values (0, 1, large N, small epsilon) to catch off-by-one or sign errors

Use numerical checks as a complement to symbolic verification — if the numbers disagree, something is wrong.
```

**`numpy-verifier.md`** — NEW NumPy/SciPy guidance for math verifiers:
```markdown
### Mandatory Numerical Spot-Checks

NumPy is pre-imported as `np`. You MUST use numerical evaluation to independently verify:

- **Every claimed identity or equality**: Evaluate both sides at 5+ random points with `np.allclose()`
- **Integrals**: Cross-check analytic results with `scipy.integrate.quad()`
- **Series and sums**: Compare partial sums against claimed closed forms for increasing N
- **Matrix results**: Verify eigenvalues, determinants, and inverses with `np.linalg` on concrete examples
- **Special functions**: Verify values at known points using `scipy.special`

If numerical evaluation disagrees with the claimed result at ANY test point, this is a RED FLAG — escalate to at least [MAJOR] severity. Numerical checks are especially valuable when symbolic simplification is inconclusive.
```

---

### Task 2: Create physics tool overlay files

Create 4 files in `skills/alethic-derive/references/tools/`:

**`sympy-generator.md`** — move existing SymPy Verification Toolkit from `generator.md`:
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

**`sympy-verifier.md`** — move existing Mandatory SymPy Re-derivation from `verifier.md`:
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

**`numpy-generator.md`** — NEW NumPy/SciPy guidance for physics generators:
```markdown
### NumPy/SciPy Numerical Verification

NumPy is pre-imported as `np`. Use numerical spot-checks to catch errors that symbolic verification might miss:

- **Random-point identity checks**: Evaluate both sides of a claimed identity at multiple points — `np.allclose(lhs(xs), rhs(xs))`
- **Numerical integration**: `scipy.integrate.quad()` to spot-check analytic integrals; `scipy.integrate.dblquad()` / `scipy.integrate.nquad()` for multi-dimensional integrals
- **Numerical ODE solving**: `scipy.integrate.solve_ivp()` to verify analytic ODE solutions against numerical trajectories
- **Matrix exponentials**: `scipy.linalg.expm()` to verify time-evolution operators
- **Special functions**: `scipy.special` (spherical harmonics `sph_harm`, Bessel `jv`/`yv`, Legendre `lpmv`, Laguerre `genlaguerre`, Hermite `hermite`) to verify at known points
- **Physical constants**: `scipy.constants` for precise values (`scipy.constants.hbar`, `scipy.constants.c`, `scipy.constants.e`, `scipy.constants.m_e`, `scipy.constants.k`)
- **Eigenvalue problems**: `np.linalg.eigh()` for Hermitian matrices (quantum mechanics), compare against analytic eigenvalues
- **FFT verification**: `np.fft.fft()` / `np.fft.ifft()` to verify Fourier transform results numerically

Use numerical checks as a complement to symbolic verification — if the numbers disagree, something is wrong.
```

**`numpy-verifier.md`** — NEW NumPy/SciPy guidance for physics verifiers:
```markdown
### Mandatory Numerical Spot-Checks

NumPy is pre-imported as `np`. You MUST use numerical evaluation to independently verify:

- **Every claimed identity or equality**: Evaluate both sides at 5+ random points with `np.allclose()`
- **ODE solutions**: Integrate numerically with `scipy.integrate.solve_ivp()` and compare against the analytic solution at multiple time points
- **Integrals**: Cross-check analytic results with `scipy.integrate.quad()` / `scipy.integrate.dblquad()`
- **Eigenvalue problems**: Compute eigenvalues numerically with `np.linalg.eigh()` and compare against claimed spectrum
- **Physical constants**: Verify numerical prefactors against `scipy.constants` values
- **Limiting cases**: Evaluate the analytic expression numerically in known limits (large N, small coupling, classical limit) and compare against known results
- **Special functions**: Verify at tabulated values using `scipy.special`

If numerical evaluation disagrees with the claimed result at ANY test point, this is a RED FLAG — escalate to at least [MAJOR] severity. Numerical checks are especially valuable when symbolic simplification is inconclusive or times out.
```

---

### Task 3: Remove inline SymPy sections from reference files

Remove the `### SymPy Verification Toolkit` section from:
- `skills/alethic-solve/references/generator.md` (everything from `### SymPy Verification Toolkit` to just before `## Output`)
- `skills/alethic-derive/references/generator.md` (same)

Remove the `### Mandatory SymPy Re-derivation` section from:
- `skills/alethic-solve/references/verifier.md` (everything from `### Mandatory SymPy Re-derivation` to just before `## Verdict Definitions`)
- `skills/alethic-derive/references/verifier.md` (same)

---

### Task 4: Add `--tools` flag to orchestrator

Edit `skills/alethic-common/orchestrator.md`:

**4a. Add flag to Argument Parsing table** (around line 43, after `--model`):

Add this row:
```
| `--tools` | — | `sympy,numpy` | Comma-separated list of tool guidance to include (`sympy`, `numpy`, or `none`) |
```

**4b. Update Prompt Loading section** (around line 100-117).

After the existing reference file table (line 112), add:

```markdown
### Tool Guidance Overlays

When loading Generator and Verifier prompts, also load tool-specific guidance overlays based on the `--tools` flag (default: `sympy,numpy`).

For each tool name in the `--tools` list:
1. Check if `{references_dir}/tools/{tool}-generator.md` exists
2. If it exists, read it and append its contents to the Generator prompt (after the balanced addendum, if any)
3. Check if `{references_dir}/tools/{tool}-verifier.md` exists
4. If it exists, read it and append its contents to the Verifier prompt

When `--tools none` is set, skip all tool overlays — sub-agents still have access to the Python sandbox but receive no specific tool guidance.

| Tool | Generator overlay | Verifier overlay |
|------|------------------|-----------------|
| `sympy` | `{references_dir}/tools/sympy-generator.md` | `{references_dir}/tools/sympy-verifier.md` |
| `numpy` | `{references_dir}/tools/numpy-generator.md` | `{references_dir}/tools/numpy-verifier.md` |
```

**4c. Update Step 2a** (around line 244). Change:

```
2. **Read the Generator prompt** from `{references_dir}/generator.md`. If `--no-balanced` is NOT set, append the `{balanced_addendum}` text to the prompt.
```

To:

```
2. **Read the Generator prompt** from `{references_dir}/generator.md`. If `--no-balanced` is NOT set, append the `{balanced_addendum}` text to the prompt. Then, for each tool in the `--tools` list, read `{references_dir}/tools/{tool}-generator.md` (if it exists) and append its contents to the prompt.
```

**4d. Update Step 2b** (around line 280). Change:

```
**Read the Verifier prompt** from `{references_dir}/verifier.md`.
```

To:

```
**Read the Verifier prompt** from `{references_dir}/verifier.md`. Then, for each tool in the `--tools` list, read `{references_dir}/tools/{tool}-verifier.md` (if it exists) and append its contents to the prompt.
```

**4e. Update Step 2d re-verification** (around line 402). The re-verify step also reads the Verifier prompt. Apply the same tool overlay logic there. Find:

```
6. **Re-verify the revision** — Read the Verifier prompt from `{references_dir}/verifier.md`.
```

Change to:

```
6. **Re-verify the revision** — Read the Verifier prompt from `{references_dir}/verifier.md`. Append tool overlays for each tool in `--tools` (same procedure as Step 2b).
```

---

### Task 5: Add `tool_guidance` field to `AgentConfig`

Edit `src/alethic/models.py`:

**5a. Add field** to `AgentConfig` (after `best_of_n: int = 1`, before `verbose: bool = True`):

```python
    tool_guidance: frozenset[str] = frozenset({"sympy", "numpy"})
```

**5b. Add validation** in `__post_init__` (after the `max_revisions_per_cycle` check):

```python
        _VALID_TOOLS = {"sympy", "numpy"}
        invalid = self.tool_guidance - _VALID_TOOLS
        if invalid:
            raise ValueError(
                f"Unknown tool_guidance values: {invalid}. "
                f"Valid values: {_VALID_TOOLS}"
            )
```

**5c. Update PRESETS** — all presets should include `tool_guidance`. Default is `frozenset({"sympy", "numpy"})` for all presets, but `quick` gets `frozenset({"numpy"})` (fast numerical checks only — SymPy can be slow):

Actually NO — keep all presets at the same default `frozenset({"sympy", "numpy"})`. The user can override with kwargs. Don't add `tool_guidance` to PRESETS at all — it will use the field default.

---

### Task 6: Extract tool guidance constants in Python API prompts

Edit `src/alethic/prompts.py`:

**6a. Revert the SymPy additions** from Generator instruction 6 and Verifier instruction 4 back to their pre-SymPy state (just mentioning `<code>` tags / Python).

Generator instruction 6 should go back to:
```python
6. **If you need to verify a computation,** you can write Python code inside \
   <code> tags. The code will be executed and the output returned to you.
```

Verifier instruction 4 should go back to:
```python
4. **Verify computations.** If the solution includes calculations, re-derive \
   them independently. You can write Python code inside <code> tags to check.
```

**6b. Add tool guidance constants** at the end of the file (before the balanced addendum or after it):

```python
# ---------------------------------------------------------------------------
# Tool guidance (conditionally appended based on AgentConfig.tool_guidance)
# ---------------------------------------------------------------------------

SYMPY_GENERATOR_GUIDANCE = """

## SymPy Verification Toolkit

SymPy is available as `sp` for symbolic computation. Use it to verify your \
reasoning at critical steps:
- Simplify and check equality: `sp.simplify(expr1 - expr2) == 0`
- Expand/factor: `sp.expand()`, `sp.factor()`, `sp.collect()`
- Series expansion: `sp.series(f, x, x0, n)`
- Symbolic integration: `sp.integrate(f, x)` or `sp.integrate(f, (x, a, b))`
- Symbolic sums: `sp.summation(f, (n, a, b))`
- Solve equations: `sp.solve(eq, var)`
- Limits: `sp.limit(f, x, x0)`

Verify at least one key algebraic step symbolically when the solution involves \
non-trivial manipulation.
"""

SYMPY_VERIFIER_GUIDANCE = """

## Mandatory SymPy Re-derivation

SymPy is available as `sp`. You MUST use it to independently verify:
- Every non-trivial algebraic simplification: `sp.simplify(claimed - rederived) == 0`
- Closed-form sums and integrals: re-compute with `sp.summation()` / `sp.integrate()`
- Polynomial identities: verify with `sp.expand()` and `sp.factor()`
- Solutions to equations: verify with `sp.solve()` and back-substitution
- Limits and asymptotics: verify with `sp.limit()` and `sp.series()`

If SymPy cannot simplify an expression to match the claimed result, this is a \
RED FLAG — escalate to at least [MAJOR] severity unless you can verify by \
another method.
"""

NUMPY_GENERATOR_GUIDANCE = """

## NumPy/SciPy Numerical Verification

NumPy is available as `np`. Use numerical spot-checks to catch errors that \
symbolic verification might miss:
- Random-point identity checks: evaluate both sides at multiple random points \
  with `np.allclose(lhs(xs), rhs(xs))`
- Numerical integration: `from scipy.integrate import quad; quad(f, a, b)`
- Matrix computations: `np.linalg.eigvals()`, `np.linalg.det()`, `np.linalg.inv()`
- Special function evaluation: `from scipy.special import ...` (gamma, beta, \
  erf, jv, legendre, etc.)
- Series convergence: compute partial sums numerically and compare against the \
  claimed closed form

Use numerical checks as a complement to symbolic verification — if the numbers \
disagree, something is wrong.
"""

NUMPY_VERIFIER_GUIDANCE = """

## Mandatory Numerical Spot-Checks

NumPy is available as `np`. You MUST use numerical evaluation to independently verify:
- Every claimed identity: evaluate both sides at 5+ random points with `np.allclose()`
- Integrals: cross-check analytic results with `scipy.integrate.quad()`
- Series and sums: compare partial sums against claimed closed forms for increasing N
- Matrix results: verify eigenvalues, determinants with `np.linalg` on concrete examples
- Special functions: verify values at known points using `scipy.special`

If numerical evaluation disagrees with the claimed result at ANY test point, this \
is a RED FLAG — escalate to at least [MAJOR] severity. Numerical checks are \
especially valuable when symbolic simplification is inconclusive.
"""

TOOL_GUIDANCE = {
    "sympy": {"generator": SYMPY_GENERATOR_GUIDANCE, "verifier": SYMPY_VERIFIER_GUIDANCE},
    "numpy": {"generator": NUMPY_GENERATOR_GUIDANCE, "verifier": NUMPY_VERIFIER_GUIDANCE},
}
```

**6c. Do the same for `src/alethic/physics_prompts.py`**:

Revert the SymPy additions from Generator instruction 7 and Verifier instruction 4.

Add physics-specific tool guidance constants:

```python
# ---------------------------------------------------------------------------
# Tool guidance (conditionally appended based on AgentConfig.tool_guidance)
# ---------------------------------------------------------------------------

PHYSICS_SYMPY_GENERATOR_GUIDANCE = """

## SymPy Verification Toolkit

SymPy is available as `sp` for symbolic computation. Use it to verify your \
reasoning at critical steps:
- Simplify and check equality: `sp.simplify(expr1 - expr2) == 0`
- Series expansion: `sp.series(f, x, x0, n)`
- Symbolic integration: `sp.integrate(f, x)` or `sp.integrate(f, (x, a, b))`
- Solve differential equations: `sp.dsolve(ode, f(x))`
- Matrix algebra: `sp.Matrix(...)` for eigenvalues, diagonalization, commutators
- Dimensional checks: `sympy.physics.units` for dimensional consistency
- Quantum mechanics: `sympy.physics.quantum` for commutators, bra-ket algebra
- Special functions: `sp.besselj`, `sp.legendre`, `sp.assoc_laguerre`, `sp.Ynm`
- Physical constants: `sympy.physics.units` for `hbar`, `c`, `e`, `m_e`, `k_B`

Verify at least one key algebraic step symbolically when the derivation involves \
non-trivial manipulation.
"""

PHYSICS_SYMPY_VERIFIER_GUIDANCE = """

## Mandatory SymPy Re-derivation

SymPy is available as `sp`. You MUST use it to independently verify:
- Every non-trivial algebraic simplification: `sp.simplify(claimed - rederived) == 0`
- ODE/PDE solutions: re-solve with `sp.dsolve()` and compare
- Eigenvalue problems: verify with `sp.Matrix.eigenvals()` / `sp.Matrix.eigenvects()`
- Integrals over configuration/momentum space: re-compute with `sp.integrate()`
- Limiting cases: substitute limits symbolically (e.g., `expr.subs(hbar, 0)`)
- Dimensional consistency: `sympy.physics.units` to verify matching dimensions
- Special function identities: verify with SymPy (Bessel, Legendre, Laguerre, \
  spherical harmonics)

If SymPy cannot simplify an expression to match the claimed result, this is a \
RED FLAG — escalate to at least [MAJOR] severity unless you can verify by \
another method.
"""

PHYSICS_NUMPY_GENERATOR_GUIDANCE = """

## NumPy/SciPy Numerical Verification

NumPy is available as `np`. Use numerical spot-checks to catch errors that \
symbolic verification might miss:
- Random-point identity checks: `np.allclose(lhs(xs), rhs(xs))`
- Numerical integration: `scipy.integrate.quad()`, `dblquad()`, `nquad()`
- Numerical ODE solving: `scipy.integrate.solve_ivp()` to verify analytic solutions
- Matrix exponentials: `scipy.linalg.expm()` to verify time-evolution operators
- Special functions: `scipy.special` (sph_harm, jv/yv, lpmv, genlaguerre, hermite)
- Physical constants: `scipy.constants.hbar`, `scipy.constants.c`, \
  `scipy.constants.e`, `scipy.constants.m_e`, `scipy.constants.k`
- Eigenvalue problems: `np.linalg.eigh()` for Hermitian matrices
- FFT verification: `np.fft.fft()` / `np.fft.ifft()`

Use numerical checks as a complement to symbolic verification — if the numbers \
disagree, something is wrong.
"""

PHYSICS_NUMPY_VERIFIER_GUIDANCE = """

## Mandatory Numerical Spot-Checks

NumPy is available as `np`. You MUST use numerical evaluation to independently verify:
- Every claimed identity: evaluate both sides at 5+ random points with `np.allclose()`
- ODE solutions: integrate with `scipy.integrate.solve_ivp()` and compare
- Integrals: cross-check with `scipy.integrate.quad()` / `dblquad()`
- Eigenvalue problems: compute with `np.linalg.eigh()` and compare against claimed spectrum
- Physical constants: verify prefactors against `scipy.constants` values
- Limiting cases: evaluate numerically in known limits (large N, small coupling, classical)
- Special functions: verify at tabulated values using `scipy.special`

If numerical evaluation disagrees with the claimed result at ANY test point, this \
is a RED FLAG — escalate to at least [MAJOR] severity. Numerical checks are \
especially valuable when symbolic simplification is inconclusive or times out.
"""

PHYSICS_TOOL_GUIDANCE = {
    "sympy": {"generator": PHYSICS_SYMPY_GENERATOR_GUIDANCE, "verifier": PHYSICS_SYMPY_VERIFIER_GUIDANCE},
    "numpy": {"generator": PHYSICS_NUMPY_GENERATOR_GUIDANCE, "verifier": PHYSICS_NUMPY_VERIFIER_GUIDANCE},
}
```

---

### Task 7: Update subagents.py to conditionally include tool guidance

Edit `src/alethic/subagents.py`:

**7a. Import the new constants:**

Add to the existing imports from `alethic.prompts`:
```python
from alethic.prompts import (
    BALANCED_GENERATOR_ADDENDUM,
    GENERATOR_SYSTEM,
    GENERATOR_USER,
    REVISER_SYSTEM,
    REVISER_USER,
    TOOL_GUIDANCE,
    VERIFIER_SYSTEM,
    VERIFIER_USER,
)
```

**7b. Update `generate()` function** — after the balanced addendum is appended (around where `system += addendum`), add tool guidance:

```python
    system = system_prompt if system_prompt is not None else GENERATOR_SYSTEM
    if balanced:
        addendum = balanced_addendum if balanced_addendum is not None else BALANCED_GENERATOR_ADDENDUM
        system += addendum

    # Append tool guidance based on config
    tool_guide = kwargs.get("tool_guidance_map", TOOL_GUIDANCE)
    for tool in sorted(config.tool_guidance):
        if tool in tool_guide and "generator" in tool_guide[tool]:
            system += tool_guide[tool]["generator"]
```

Wait, this won't work cleanly because `generate()` doesn't take `**kwargs` for the tool guidance map. Let me reconsider.

Better approach: The `generate()` function already accepts `system_prompt` as an override. The caller (`agent.py` / `physics_agent.py`) builds the system prompt including tool guidance. So the tool guidance assembly should happen in `agent.py`.

**Revised approach for 7b**: Don't modify `generate()`/`verify()` signatures. Instead, modify `agent.py` to assemble the system prompt with tool guidance before calling `generate()`/`verify()`.

Edit `src/alethic/agent.py`. Find where `generate()` is called. It currently passes no `system_prompt` override (using the default). Add logic to build the system prompt with tool guidance:

```python
# Build generator system prompt with tool guidance
from alethic.prompts import GENERATOR_SYSTEM, BALANCED_GENERATOR_ADDENDUM, TOOL_GUIDANCE

gen_system = GENERATOR_SYSTEM
if self.config.balanced:  # or however balanced is tracked
    gen_system += BALANCED_GENERATOR_ADDENDUM
for tool in sorted(self.config.tool_guidance):
    if tool in TOOL_GUIDANCE and "generator" in TOOL_GUIDANCE[tool]:
        gen_system += TOOL_GUIDANCE[tool]["generator"]
```

Actually, let me read agent.py first to understand the call pattern, then adjust.

**IMPORTANT**: Read `src/alethic/agent.py` before making changes. Find every call to `generate()`, `verify()`, and `revise()`, and understand how `system_prompt` is (or isn't) passed. Then add tool guidance assembly at each call site.

The pattern should be:
1. For each `generate()` call: build system prompt = base + balanced addendum + tool overlays for "generator"
2. For each `verify()` call: build system prompt = base + tool overlays for "verifier"
3. `revise()` doesn't need tool overlays (the Reviser follows the critique, not tool guidance)

For `PhysicsAgent` in `physics_agent.py`: it overrides prompts via kwargs. The tool guidance map should be `PHYSICS_TOOL_GUIDANCE` instead of `TOOL_GUIDANCE`. The simplest approach: `PhysicsAgent` overrides a method or property that returns the tool guidance map.

---

### Task 8: Update agent.py to assemble prompts with tool guidance

Read `src/alethic/agent.py` first. Then:

**8a.** Add a method `_get_tool_guidance_map(self)` to `MathAgent` that returns `TOOL_GUIDANCE`. Override it in `PhysicsAgent` to return `PHYSICS_TOOL_GUIDANCE`.

**8b.** Add a method `_build_system_prompt(self, role: str, base: str) -> str` to `MathAgent`:
```python
def _build_system_prompt(self, role: str, base: str) -> str:
    """Append tool guidance overlays to a base system prompt."""
    system = base
    guide_map = self._get_tool_guidance_map()
    for tool in sorted(self.config.tool_guidance):
        if tool in guide_map and role in guide_map[tool]:
            system += guide_map[tool][role]
    return system
```

**8c.** At every call to `generate()`: pass `system_prompt=self._build_system_prompt("generator", GENERATOR_SYSTEM + addendum)` (where addendum is the balanced prompting text if applicable).

**8d.** At every call to `verify()`: pass `system_prompt=self._build_system_prompt("verifier", VERIFIER_SYSTEM)`.

**8e.** Leave `revise()` calls unchanged (no tool guidance for the Reviser).

---

### Task 9: Update PhysicsAgent

Edit `src/alethic/physics_agent.py`:

**9a.** Import `PHYSICS_TOOL_GUIDANCE`:
```python
from alethic.physics_prompts import (
    ...,
    PHYSICS_TOOL_GUIDANCE,
)
```

**9b.** Override `_get_tool_guidance_map`:
```python
def _get_tool_guidance_map(self):
    return PHYSICS_TOOL_GUIDANCE
```

---

### Task 10: Add `--tools` to CLI

Edit `src/alethic/cli.py`. Add a `--tools` argument:
```python
parser.add_argument(
    "--tools",
    default="sympy,numpy",
    help="Comma-separated tool guidance to include (sympy, numpy, none). Default: sympy,numpy",
)
```

Parse and pass to AgentConfig:
```python
tool_guidance = frozenset() if args.tools == "none" else frozenset(args.tools.split(","))
config = AgentConfig(..., tool_guidance=tool_guidance)
```

---

### Task 11: Update PYTHON_TOOL description

Edit `src/alethic/tools.py`. The description should mention both SymPy and NumPy:
```python
"description": (
    "Execute Python code for computational verification. "
    "Use this to check calculations, test conjectures with examples, "
    "verify formulas numerically, or perform symbolic computation. "
    "SymPy is pre-imported as `sp` for symbolic math; "
    "NumPy is pre-imported as `np` for numerical computation. "
    "SciPy provides numerical integration (scipy.integrate), "
    "special functions (scipy.special), and physical constants (scipy.constants). "
    "Available libraries: math, fractions, decimal, itertools, functools, "
    "collections, operator, random, statistics. "
    "NumPy, SymPy, SciPy, and mpmath are also available if installed."
),
```

---

### Task 12: Update tests

Edit `tests/test_adversarial_skill.py`:

**12a.** Rewrite `TestSympyGuidance` to become `TestToolOverlays` that tests:
- Tool overlay files exist in both `alethic-solve/references/tools/` and `alethic-derive/references/tools/`
- Each overlay file contains the expected content (SymPy mentions, NumPy mentions, RED FLAG for verifiers)
- Physics overlays mention `sympy.physics.*` and `scipy.constants`; math ones don't
- Base reference files (generator.md, verifier.md) do NOT contain inline SymPy sections anymore
- Orchestrator mentions `--tools` flag

**12b.** Update any existing tests that check for SymPy content in reference files.

---

### Task 13: Run tests and commit

Run: `cd /home/xeal/dev/alethic && pytest -x -v`

If all pass, commit:
```bash
cd /home/xeal/dev/alethic
git add -A
git commit -m "$(cat <<'COMMITEOF'
Add switchable tool guidance layer with NumPy/SciPy support

- Extract SymPy guidance from reference files into modular overlay files
- Add NumPy/SciPy numerical verification overlays (math + physics)
- New --tools flag for skills (default: sympy,numpy) and CLI
- AgentConfig.tool_guidance field for Python API
- Orchestrator conditionally loads tool overlays from {ref_dir}/tools/
- Physics overlays include scipy.constants, scipy.integrate.solve_ivp,
  scipy.special, scipy.linalg.expm
- Update PYTHON_TOOL description to mention both SymPy and NumPy
- Rewrite tool overlay tests

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
COMMITEOF
)"
```
