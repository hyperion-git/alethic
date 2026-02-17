### Mandatory Numerical Spot-Checks

NumPy is pre-imported as `np`. You MUST use numerical evaluation to independently verify:

- **Every claimed identity or equality**: Evaluate both sides at 5+ random points with `np.allclose()`
- **Integrals**: Cross-check analytic results with `scipy.integrate.quad()`
- **Series and sums**: Compare partial sums against claimed closed forms for increasing N
- **Matrix results**: Verify eigenvalues, determinants, and inverses with `np.linalg` on concrete examples
- **Special functions**: Verify values at known points using `scipy.special`

If numerical evaluation disagrees with the claimed result at ANY test point, this is a RED FLAG — escalate to at least [MAJOR] severity. Numerical checks are especially valuable when symbolic simplification is inconclusive.
