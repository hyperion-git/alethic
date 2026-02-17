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
