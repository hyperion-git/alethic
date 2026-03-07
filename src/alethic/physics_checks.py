"""Programmatic verifier stack — Verification Ladder Layers 0-2 (feature 2.1).

Provides:
- PHYSICS_CHECK_GUIDANCE: prompt addendum for physics generator (Layer 0-2 templates)
- MATH_CHECK_GUIDANCE: prompt addendum for math generator (Layer 0-2 templates)
- parse_layer_results(): extract ALETHIC_L{N}_CHECK: sentinel lines from solution text
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Physics: Layer 0-2 generator guidance
# ---------------------------------------------------------------------------

PHYSICS_CHECK_GUIDANCE = """
## Verification Function Requirements (Physics — Layers 0-2)

After completing your derivation, you MUST emit the following verification functions
as executable Python code blocks. Run each one using your Python sandbox tool and
embed the output verbatim. The verifier treats these outputs as ground truth.

All outputs must begin with `ALETHIC_L{N}_CHECK:` where N is the layer number.

### Layer 0 — Structural: Dimensional Analysis

```python
def verify_dimensions():
    \"\"\"Check that key equations are dimensionally consistent.\"\"\"
    import sympy.physics.units as u
    # Replace with the actual dimensions of your key result
    # Example: for E = p²/2m (kinetic energy from momentum)
    E_dim = u.Dimension(u.energy)
    p_dim = u.Dimension(u.momentum)
    m_dim = u.Dimension(u.mass)
    # Check [p²/2m] == [energy]
    rhs_dim = u.Dimension(p_dim**2 / m_dim)
    assert E_dim == rhs_dim, f"Dimensional mismatch: {E_dim} != {rhs_dim}"
    print("ALETHIC_L0_CHECK: DIMENSIONS OK")

verify_dimensions()
```

### Layer 1 — Behavioral: Known Limits

For each relevant limit (c→∞, ℏ→0, T→0, weak coupling, etc.):

```python
def verify_limit_nonrelativistic():
    \"\"\"Test that result recovers classical mechanics in c→∞ limit.\"\"\"
    import sympy as sp
    # Replace symbolic expression and expected classical limit
    c, m, v = sp.symbols('c m v', positive=True)
    relativistic = m * c**2 / sp.sqrt(1 - v**2/c**2)
    series = sp.series(relativistic, v/c, 0, 3)
    classical_ke = sp.Rational(1, 2) * m * v**2
    residual = sp.simplify(series.removeO() - m*c**2 - classical_ke)
    assert residual == 0, f"Non-relativistic limit failed: {residual}"
    print("ALETHIC_L1_CHECK: LIMIT nonrelativistic OK")

verify_limit_nonrelativistic()
```

### Layer 2 — Consistency: Symbolic-Numeric Agreement

```python
def verify_symbolic_numeric(params=None):
    \"\"\"Check symbolic expression agrees with numeric evaluation.\"\"\"
    import sympy as sp
    import numpy as np
    if params is None:
        params = (1.0, 2.0, 3.0)  # replace with meaningful test values
    # Replace with your actual symbolic result and variables
    x, y, z = sp.symbols('x y z')
    symbolic_expr = x**2 + y**2 + z**2  # replace with your expression
    sym_val = float(symbolic_expr.subs({x: params[0], y: params[1], z: params[2]}))
    num_val = float(params[0]**2 + params[1]**2 + params[2]**2)  # direct computation
    assert abs(sym_val - num_val) < 1e-10, f"Mismatch: {sym_val} vs {num_val}"
    print(f"ALETHIC_L2_CHECK: CONSISTENCY OK ({sym_val:.6f}=={num_val:.6f})")

verify_symbolic_numeric()
```
"""


# ---------------------------------------------------------------------------
# Math: Layer 0-2 generator guidance
# ---------------------------------------------------------------------------

MATH_CHECK_GUIDANCE = """
## Verification Function Requirements (Math — Layers 0-2)

After completing your solution, you MUST emit the following verification functions
as executable Python code blocks. Run each one using your Python sandbox tool and
embed the output verbatim. The verifier treats these outputs as ground truth.

All outputs must begin with `ALETHIC_L{N}_CHECK:` where N is the layer number.

### Layer 0 — Structural: Degree/Type Consistency

```python
def verify_structure():
    \"\"\"Check that the result is structurally well-typed.\"\"\"
    import sympy as sp
    n = sp.Symbol('n', positive=True, integer=True)
    # Replace with your actual formula and expected properties
    result = n * (n + 1) * (2*n + 1) / 6  # example: sum of squares
    # Check degree
    poly = sp.Poly(result, n)
    assert poly.degree() == 3, f"Expected degree 3, got {poly.degree()}"
    # Check it's a polynomial (no negative powers)
    assert result.is_polynomial(n), "Formula contains non-polynomial terms"
    print("ALETHIC_L0_CHECK: STRUCTURE OK")

verify_structure()
```

### Layer 1 — Behavioral: Base Cases

```python
def verify_base_cases():
    \"\"\"Verify the formula gives correct results for small instances.\"\"\"
    # Replace formula and expected values with your actual result
    formula = lambda k: k * (k + 1) * (2*k + 1) // 6  # example: sum of squares
    expected = {0: 0, 1: 1, 2: 5, 3: 14, 4: 30}      # direct computation
    for k, exp in expected.items():
        got = int(formula(k))
        assert got == exp, f"n={k}: expected {exp}, got {got}"
    print(f"ALETHIC_L1_CHECK: BASE CASES OK (n=0..{max(expected)})")

verify_base_cases()
```

### Layer 2 — Consistency: Dual Representation

```python
def verify_dual_representation(n_test=10):
    \"\"\"Verify closed form matches direct computation for concrete n.\"\"\"
    # Replace with your closed form and direct computation
    formula = lambda k: k * (k + 1) * (2*k + 1) // 6
    direct = sum(i**2 for i in range(n_test + 1))
    closed = formula(n_test)
    assert direct == closed, f"Dual check failed: direct={direct}, closed={closed}"
    print(f"ALETHIC_L2_CHECK: CONSISTENCY OK at n={n_test} ({direct}=={closed})")

verify_dual_representation()
```
"""


# ---------------------------------------------------------------------------
# Sentinel parser
# ---------------------------------------------------------------------------

_SENTINEL_RE = re.compile(r"ALETHIC_L(\d+)_CHECK:\s*(.+)")


def parse_layer_results(solution_text: str) -> dict[int, list[str]]:
    """Extract ALETHIC_L{N}_CHECK: sentinel lines from embedded solution text.

    Returns a dict mapping layer number (int) to list of result strings.
    Returns {} if no sentinels are found.

    Args:
        solution_text: Full solution text from the generator (with embedded check outputs).
    """
    results: dict[int, list[str]] = {}
    for line in solution_text.splitlines():
        m = _SENTINEL_RE.search(line)
        if m:
            layer = int(m.group(1))
            result = m.group(2).strip()
            results.setdefault(layer, []).append(result)
    return results
