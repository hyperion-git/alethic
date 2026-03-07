# Verification Ladder — Structured Programmatic Checks

You are auditing a solution for internal consistency. Before forming your semantic
verdict, you MUST execute structured checks at each applicable layer using your
Python sandbox. Embed outputs inline as `[Layer N check]: {result}`.

## Layer 0 — Structural (physics: dimensional balance; math: type/degree check)

Write and run a function that checks the key structural constraint of the solution.

**Physics solutions:**
```python
def check_dimensions():
    import sympy.physics.units as u
    # Extract the key equation from the solution and check dimensional balance
    # Flag as [MAJOR] if LHS dimensions != RHS dimensions
    print("[Layer 0 check]: DIMENSIONS verified")
check_dimensions()
```

**Math solutions:**
```python
def check_structure():
    import sympy as sp
    n = sp.Symbol('n')
    # Extract the claimed formula and check degree/polynomial structure
    # Flag as [MAJOR] if structural constraint violated
    print("[Layer 0 check]: STRUCTURE verified")
check_structure()
```

A Layer 0 failure is immediately `[MAJOR]` regardless of how plausible the rest looks.

## Layer 1 — Behavioral (physics: known limits; math: base cases)

Write and run a function that checks the solution against known instances.

**Physics:** Test at least one known limiting case (c→∞, ℏ→0, T→0, small angle, etc.)
**Math:** Test at least n=0, n=1, n=2, and one non-trivial case.

```python
def check_behavioral():
    # Direct numerical verification against known values
    # Flag as [MAJOR] if any instance fails
    print("[Layer 1 check]: BEHAVIORAL verified")
check_behavioral()
```

## Layer 2 — Consistency (two representations agree)

If the solution contains two ways to compute the same quantity, verify they agree.

```python
def check_consistency():
    # Compare two representations numerically
    # Flag as [MAJOR] if they disagree by more than 1e-8
    print("[Layer 2 check]: CONSISTENCY verified")
check_consistency()
```

## Handling Alethic-Generated Solutions

If the solution already contains `ALETHIC_L{N}_CHECK:` sentinel lines (from the
generator's sandbox), treat those as ground truth and skip re-running the corresponding
layer. Focus your programmatic checks on layers NOT already covered by sentinels.

## Verdict Integration

After all applicable layers:
- Any `[MAJOR]` layer failure → verdict cannot be CORRECT or MINOR_ISSUES
- All layers passing does NOT guarantee CORRECT — semantic verification still required
- Report layer results explicitly in your CRITIQUE section
