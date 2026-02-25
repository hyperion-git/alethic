### Mandatory SciPy Cross-Checks

SciPy is available for advanced numerical verification. You MUST use it when applicable:

- **Physical constants**: Use `scipy.constants` to verify numerical values of physical constants (speed of light, Planck's constant, Boltzmann constant, etc.). Compare claimed values: `from scipy import constants; print(constants.c, constants.hbar, constants.k)`
- **Numerical integration**: Use `scipy.integrate.quad()` for definite integrals, `scipy.integrate.dblquad()` for double integrals, and `scipy.integrate.solve_ivp()` for ODE solutions
- **Special functions**: Use `scipy.special` for Bessel functions (`jv`, `yv`, `kv`, `iv`), Legendre polynomials (`legendre`, `lpmv`), spherical harmonics (`sph_harm`), Airy functions (`airy`), gamma/beta functions, error functions (`erf`, `erfc`), and elliptic integrals
- **Linear algebra**: Use `scipy.linalg` for matrix decompositions (`eig`, `svd`, `lu`, `cholesky`, `qr`), matrix functions (`expm`, `logm`, `sqrtm`), and specialized solvers (`solve_banded`, `solve_triangular`)
- **Optimization**: Use `scipy.optimize.minimize` or `scipy.optimize.root` to verify claimed extrema or roots

If SciPy's numerical result disagrees with the claimed analytic result beyond reasonable floating-point tolerance (rtol=1e-8 for well-conditioned problems), this is a RED FLAG --- escalate to at least [MAJOR] severity.
