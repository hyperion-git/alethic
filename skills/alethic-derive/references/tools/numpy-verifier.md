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
