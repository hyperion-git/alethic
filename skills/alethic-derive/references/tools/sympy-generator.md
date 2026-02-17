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
