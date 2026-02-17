### Mandatory SymPy Re-derivation

SymPy is pre-imported as `sp`. You MUST use it to independently verify:

- **Every non-trivial algebraic simplification**: `sp.simplify(claimed - rederived) == 0`
- **Closed-form sums and integrals**: Re-compute with `sp.summation()` / `sp.integrate()` and compare
- **Polynomial identities**: Verify with `sp.expand()` and `sp.factor()`
- **Solutions to equations**: Verify with `sp.solve()` and back-substitution
- **Limits and asymptotics**: Verify with `sp.limit()` and `sp.series()`

If SymPy cannot simplify an expression to match the claimed result, this is a RED FLAG — escalate to at least [MAJOR] severity unless you can verify by another method.
