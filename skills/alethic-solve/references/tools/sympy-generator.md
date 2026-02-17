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
