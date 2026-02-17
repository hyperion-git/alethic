### NumPy/SciPy Numerical Verification

NumPy is pre-imported as `np`. Use numerical spot-checks to catch errors that symbolic verification might miss:

- **Random-point identity checks**: Evaluate both sides of a claimed identity at multiple random points — `np.allclose(lhs(xs), rhs(xs))` where `xs = np.random.uniform(a, b, 100)`
- **Numerical integration**: `from scipy.integrate import quad; quad(f, a, b)` to spot-check analytic integrals
- **Matrix computations**: `np.linalg.eigvals()`, `np.linalg.det()`, `np.linalg.inv()` for concrete matrix examples
- **Special function evaluation**: `from scipy.special import ...` (gamma, beta, erf, jv, legendre, etc.) to verify special function values at known points
- **Series convergence**: Compute partial sums numerically and compare against the claimed closed form
- **Edge cases**: Evaluate expressions at boundary values (0, 1, large N, small epsilon) to catch off-by-one or sign errors

Use numerical checks as a complement to symbolic verification — if the numbers disagree, something is wrong.
