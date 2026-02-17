### NumPy/SciPy Numerical Verification

NumPy is pre-imported as `np`. Use numerical spot-checks to catch errors that symbolic verification might miss:

- **Random-point identity checks**: Evaluate both sides of a claimed identity at multiple points — `np.allclose(lhs(xs), rhs(xs))`
- **Numerical integration**: `scipy.integrate.quad()` to spot-check analytic integrals; `scipy.integrate.dblquad()` / `scipy.integrate.nquad()` for multi-dimensional integrals
- **Numerical ODE solving**: `scipy.integrate.solve_ivp()` to verify analytic ODE solutions against numerical trajectories
- **Matrix exponentials**: `scipy.linalg.expm()` to verify time-evolution operators
- **Special functions**: `scipy.special` (spherical harmonics `sph_harm`, Bessel `jv`/`yv`, Legendre `lpmv`, Laguerre `genlaguerre`, Hermite `hermite`) to verify at known points
- **Physical constants**: `scipy.constants` for precise values (`scipy.constants.hbar`, `scipy.constants.c`, `scipy.constants.e`, `scipy.constants.m_e`, `scipy.constants.k`)
- **Eigenvalue problems**: `np.linalg.eigh()` for Hermitian matrices (quantum mechanics), compare against analytic eigenvalues
- **FFT verification**: `np.fft.fft()` / `np.fft.ifft()` to verify Fourier transform results numerically

Use numerical checks as a complement to symbolic verification — if the numbers disagree, something is wrong.
