"""
Two-sample variance estimators for frequency stability analysis.

Implements the standard overlapping estimators per IEEE Std 1139-2008:
  - AVAR  (Allan Variance)
  - MVAR  (Modified Allan Variance)
  - HVAR  (Hadamard Variance)
  - TVAR  (Time Variance)

Four computation strategies, in order of recommendation:

1. **PSD / FFT** (``sweep_psd``): Compute the phase power spectrum *once*
   via FFT in O(N log N), then derive any estimator at any tau as a weighted
   sum — O(N) per (estimator, tau) query.  Exact for the overlapping
   estimator (circular approximation, negligible for m << N).  Also returns
   the PSD itself, enabling noise-type identification.

2. **Hybrid** (``sweep_hybrid``): Exact overlapping time-domain computation
   for small m (high statistical efficiency where data is abundant), seamless
   crossover to decimation for large m (where few degrees of freedom remain
   anyway).  Best of both worlds in practice.

3. **Exact overlapping** (``compute_all``): Time-domain overlapping at
   arbitrary m values — O(N) per m, O(N log N) total for octave taus.
   Maximum statistical efficiency at every scale.

4. **Decimation** (``sweep``): O(N) total via pairwise-average downsampling.
   Fast, but loses statistical efficiency at higher octaves because each
   decimation step discards half the data.

All functions accept phase data (time-error x_i at uniform spacing tau_0)
or fractional-frequency data (y_i averaged over tau_0).

References:
    [1] D.W. Allan, "Statistics of Atomic Frequency Standards," Proc. IEEE,
        54(2), 1966.
    [2] IEEE Std 1139-2008, "Standard Definitions of Physical Quantities for
        Fundamental Frequency and Time Metrology."
    [3] W.J. Riley, "Handbook of Frequency Stability Analysis," NIST SP 1065,
        2008.
    [4] D.B. Percival, "On estimation of the wavelet variance," Biometrika,
        82(3), 1995.  (Haar wavelet variance ≡ Allan variance.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VarianceResult:
    """Result of a single variance computation at one averaging factor."""

    tau: float
    """Analysis interval (seconds)."""

    variance: float
    """Estimated variance (dimensionless^2 for fractional frequency)."""

    deviation: float
    """Square root of variance (same units as variance^0.5)."""

    dof: float
    """Approximate degrees of freedom (EDF) for confidence intervals."""

    n_averages: int
    """Number of overlapping averages used in the estimate."""


@dataclass(frozen=True)
class NoiseID:
    """Noise-type identification from log-log slope of AVAR vs tau."""

    tau_lo: float
    tau_hi: float
    slope: float
    noise_type: str


# Slope → noise type mapping (log AVAR vs log tau slope for phase-data AVAR)
_NOISE_TYPES: list[tuple[float, str]] = [
    (-3.0, "white phase modulation (WPM)"),
    (-2.0, "flicker phase modulation (FPM)"),
    (-1.0, "white frequency modulation (WFM)"),
    (0.0, "flicker frequency modulation (FFM)"),
    (1.0, "random walk frequency modulation (RWFM)"),
    (2.0, "frequency drift"),
]


# ---------------------------------------------------------------------------
# Single-point overlapping estimators
# ---------------------------------------------------------------------------

def avar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Allan Variance at a single averaging factor *m*.

    O(N) for one (m, tau) point.
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 2 * m + 1, "AVAR")

    tau = m * tau_0
    d = phase[2 * m :] - 2.0 * phase[m : N - m] + phase[: N - 2 * m]
    n_avg = len(d)
    var = np.sum(d ** 2) / (2.0 * n_avg * tau ** 2)
    dof = _edf_avar(n_avg)

    return VarianceResult(
        tau=tau, variance=float(var), deviation=float(math.sqrt(var)),
        dof=dof, n_averages=n_avg,
    )


def mvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Modified Allan Variance at a single averaging factor *m*.

    O(N) for one (m, tau) point.
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 3 * m + 1, "MVAR")

    tau = m * tau_0
    d = phase[2 * m :] - 2.0 * phase[m : N - m] + phase[: N - 2 * m]
    cs = np.cumsum(d)
    s = np.empty(len(d) - m + 1)
    s[0] = cs[m - 1]
    s[1:] = cs[m:] - cs[: len(d) - m]

    n_avg = len(s)
    var = np.sum(s ** 2) / (2.0 * n_avg * m ** 2 * tau ** 2)
    dof = _edf_mvar(n_avg)

    return VarianceResult(
        tau=tau, variance=float(var), deviation=float(math.sqrt(var)),
        dof=dof, n_averages=n_avg,
    )


def hvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Hadamard Variance at a single averaging factor *m*.

    Insensitive to linear frequency drift.  O(N) for one (m, tau) point.
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 3 * m + 1, "HVAR")

    tau = m * tau_0
    d = (
        phase[3 * m :]
        - 3.0 * phase[2 * m : N - m]
        + 3.0 * phase[m : N - 2 * m]
        - phase[: N - 3 * m]
    )
    n_avg = len(d)
    var = np.sum(d ** 2) / (6.0 * n_avg * tau ** 2)
    dof = _edf_hvar(n_avg)

    return VarianceResult(
        tau=tau, variance=float(var), deviation=float(math.sqrt(var)),
        dof=dof, n_averages=n_avg,
    )


def tvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Time Variance at a single averaging factor *m*.

    TVAR = (tau^2 / 3) * MVAR.  Units of seconds^2.
    """
    mv = mvar(data, m, tau_0, data_type=data_type)
    tau = mv.tau
    var = (tau ** 2 / 3.0) * mv.variance
    return VarianceResult(
        tau=tau, variance=var, deviation=math.sqrt(var),
        dof=mv.dof, n_averages=mv.n_averages,
    )


# ===================================================================
# Strategy 1: PSD / FFT  (recommended)
# ===================================================================

def sweep_psd(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    m_values: Sequence[int] | None = None,
    estimators: Sequence[str] | None = None,
) -> dict[str, list[VarianceResult]]:
    """Compute variance estimators via the power spectral density.

    Computes ``rfft(phase)`` *once* in O(N log N), then evaluates each
    estimator at each tau as a weighted sum of |X[k]|^2 — O(N) per query.

    Advantages over decimation
    --------------------------
    - **Exact** for the overlapping estimator (circular approximation,
      negligible when m << N; the first-octave result matches the
      time-domain overlapping value to machine precision).
    - **Full statistical efficiency** — no data discarded at any scale.
    - **Arbitrary tau values** — not limited to octave spacing.
    - **One-time FFT cost** amortised over all (estimator, tau) queries.

    Transfer functions (phase-domain, applied to |X[k]|^2)
    -------------------------------------------------------
    AVAR: |H|^2 = 16 sin^4(pi k m / N)
    HVAR: |H|^2 = 64 sin^6(pi k m / N)
    MVAR: |H|^2 = 16 sin^6(pi k m / N) / sin^2(pi k / N)
    TVAR: (tau^2 / 3) * MVAR

    Parameters
    ----------
    data : array-like
        Phase or frequency samples.
    tau_0 : float
        Base sampling interval (seconds).
    data_type : ``"phase"`` | ``"freq"``
    m_values : sequence of int, optional
        Averaging factors.  Defaults to octave-spaced (1, 2, 4, ...).
    estimators : sequence of str, optional
        Subset of ``{"avar", "mvar", "hvar", "tvar"}``.  Defaults to all.

    Returns
    -------
    dict mapping estimator name to list of VarianceResult.
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)

    # ---- one-time FFT cost: O(N log N) ----
    X = np.fft.rfft(phase)
    power = np.abs(X) ** 2          # |X[k]|^2, k = 0 .. N//2
    k = np.arange(len(power))       # frequency indices

    if m_values is None:
        m_values = _octave_m_values(N)

    _VALID = {"avar", "mvar", "hvar", "tvar"}
    if estimators is None:
        estimators = list(_VALID)
    for name in estimators:
        if name not in _VALID:
            raise ValueError(f"Unknown estimator {name!r}; choose from {_VALID}")

    # Pre-compute sin(pi k / N) once — reused across m values for MVAR
    sin_base = np.sin(np.pi * k / N)
    # Guard against division by zero at DC for MVAR
    sin_base_safe = np.where(sin_base != 0.0, sin_base, 1.0)

    results: dict[str, list[VarianceResult]] = {name: [] for name in estimators}

    for m in m_values:
        tau = m * tau_0
        n_avg_avar = N - 2 * m
        n_avg_hvar = N - 3 * m
        if n_avg_avar < 1:
            break

        # sin(pi k m / N) — the fundamental building block
        sin_km = np.sin(np.pi * k * m / N)

        # --- Parseval energy via rfft (accounts for conjugate symmetry) ---
        # Full-spectrum sum = power[0]*w[0] + 2*sum(power[1:-1]*w[1:-1])
        #                     + power[-1]*w[-1]   (last term only for even N)

        if "avar" in results:
            tf = 16.0 * sin_km ** 4
            energy = _rfft_weighted_energy(power, tf, N)
            var = energy / (2.0 * n_avg_avar * tau ** 2)
            results["avar"].append(_make_result(tau, var, _edf_avar(n_avg_avar), n_avg_avar))

        if "hvar" in results:
            if n_avg_hvar < 1:
                # Can't compute HVAR at this scale; stop adding
                del results["hvar"]
            else:
                tf = 64.0 * sin_km ** 6
                energy = _rfft_weighted_energy(power, tf, N)
                var = energy / (6.0 * n_avg_hvar * tau ** 2)
                results["hvar"].append(_make_result(tau, var, _edf_hvar(n_avg_hvar), n_avg_hvar))

        if "mvar" in results or "tvar" in results:
            n_avg_mvar = N - 3 * m + 1
            if n_avg_mvar < 1:
                results.pop("mvar", None)
                results.pop("tvar", None)
            else:
                # MVAR transfer function: 16 sin^6(pi k m / N) / sin^2(pi k / N)
                tf = np.zeros_like(sin_km)
                nz = sin_base != 0.0
                tf[nz] = 16.0 * sin_km[nz] ** 6 / sin_base_safe[nz] ** 2
                energy = _rfft_weighted_energy(power, tf, N)
                mvar_var = energy / (2.0 * n_avg_mvar * m ** 2 * tau ** 2)

                if "mvar" in results:
                    results["mvar"].append(
                        _make_result(tau, mvar_var, _edf_mvar(n_avg_mvar), n_avg_mvar)
                    )
                if "tvar" in results:
                    tvar_var = (tau ** 2 / 3.0) * mvar_var
                    results["tvar"].append(
                        _make_result(tau, tvar_var, _edf_mvar(n_avg_mvar), n_avg_mvar)
                    )

    return results


def _rfft_weighted_energy(
    power: NDArray[np.float64],
    tf: NDArray[np.float64],
    N: int,
) -> float:
    """Parseval-correct weighted energy from one-sided (rfft) spectrum.

    For a length-N real signal with rfft power |X[k]|^2:
        full_energy = (1/N) * (w[0]*P[0] + 2*sum(w[1:-1]*P[1:-1]) + eps*w[-1]*P[-1])
    where eps = 1 for even N (Nyquist bin is real), 0 for odd N.
    """
    w = tf * power
    energy = w[0] + 2.0 * np.sum(w[1:-1])
    if N % 2 == 0:
        energy += w[-1]
    else:
        energy += 2.0 * w[-1]
    return float(energy / N)


# ===================================================================
# Strategy 2: Hybrid  (exact low-m + decimation high-m)
# ===================================================================

def sweep_hybrid(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    estimators: Sequence[str] | None = None,
    crossover_m: int = 32,
    min_samples: int = 5,
) -> dict[str, list[VarianceResult]]:
    """Exact overlapping for small m, decimation above *crossover_m*.

    Rationale: at small m the phase array is long and overlapping gives
    a huge statistical-efficiency win over decimation.  At large m only a
    handful of averages remain, so the overlapping/decimated distinction
    barely matters — decimation's O(N) saving dominates.

    Parameters
    ----------
    data, tau_0, data_type, estimators :
        See :func:`compute_all`.
    crossover_m : int
        Averaging factor at which to switch from exact overlapping to
        decimation.  Default 32 (5 exact octaves, then decimation).
    min_samples : int
        Stop decimation when fewer than this many samples remain.

    Returns
    -------
    dict mapping estimator name to list of VarianceResult.
    """
    _VALID = {"avar", "mvar", "hvar", "tvar"}
    if estimators is None:
        estimators = list(_VALID)
    for name in estimators:
        if name not in _VALID:
            raise ValueError(f"Unknown estimator {name!r}; choose from {_VALID}")

    # Phase 1: exact overlapping for m = 1, 2, 4, ..., crossover_m
    exact_m = []
    m = 1
    while m <= crossover_m:
        exact_m.append(m)
        m *= 2

    results = compute_all(data, tau_0, data_type=data_type,
                          m_values=exact_m, estimators=estimators)

    # Phase 2: decimate down to the crossover scale, then continue
    # decimating for higher octaves
    phase = _to_phase(data, tau_0, data_type)
    tau = tau_0

    # Decimate until we reach the crossover scale
    decimations_needed = int(math.log2(crossover_m)) if crossover_m >= 2 else 0
    for _ in range(decimations_needed):
        phase = _decimate(phase)
        tau *= 2.0

    # Now continue decimating beyond the crossover
    _DECIMATE_FUNCS = {
        "avar": _avar_on_phase,
        "mvar": _mvar_on_phase,
        "hvar": _hvar_on_phase,
    }

    phase = _decimate(phase)
    tau *= 2.0

    while len(phase) >= max(min_samples, 5):
        for name in estimators:
            if name == "tvar":
                continue
            fn = _DECIMATE_FUNCS.get(name)
            if fn is None:
                continue
            r = fn(phase, tau)
            if r is not None:
                results[name].append(r)

        # Derive TVAR from MVAR if both requested
        if "tvar" in estimators and "mvar" in estimators:
            mvar_list = results["mvar"]
            if mvar_list and mvar_list[-1].tau == tau:
                mv = mvar_list[-1]
                tvar_var = (tau ** 2 / 3.0) * mv.variance
                results["tvar"].append(
                    _make_result(tau, tvar_var, mv.dof, mv.n_averages)
                )

        phase = _decimate(phase)
        tau *= 2.0

    return results


def _avar_on_phase(phase: NDArray[np.float64], tau: float) -> VarianceResult | None:
    """AVAR from already-decimated phase (m=1 at current scale)."""
    N = len(phase)
    if N < 3:
        return None
    d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
    n_avg = len(d)
    var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
    return _make_result(tau, var, _edf_avar(n_avg), n_avg)


def _mvar_on_phase(phase: NDArray[np.float64], tau: float) -> VarianceResult | None:
    """MVAR from already-decimated phase (m=1 at current scale)."""
    N = len(phase)
    if N < 4:
        return None
    d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
    n_avg = len(d)
    if n_avg < 1:
        return None
    var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
    return _make_result(tau, var, _edf_mvar(n_avg), n_avg)


def _hvar_on_phase(phase: NDArray[np.float64], tau: float) -> VarianceResult | None:
    """HVAR from already-decimated phase (m=1 at current scale)."""
    N = len(phase)
    if N < 4:
        return None
    d = phase[3:] - 3.0 * phase[2:N - 1] + 3.0 * phase[1:N - 2] - phase[:N - 3]
    n_avg = len(d)
    if n_avg < 1:
        return None
    var = float(np.mean(d ** 2) / (6.0 * tau ** 2))
    return _make_result(tau, var, _edf_hvar(n_avg), n_avg)


# ===================================================================
# Strategy 3: Exact overlapping at arbitrary m values
# ===================================================================

def compute_all(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    m_values: Sequence[int] | None = None,
    estimators: Sequence[str] | None = None,
) -> dict[str, list[VarianceResult]]:
    """Exact overlapping estimators at arbitrary m values.

    O(N) per m — O(N log N) total for octave-spaced taus, or O(N K) for
    K arbitrary m values.  Maximum statistical efficiency at every scale.

    Parameters
    ----------
    data : array-like
        Phase or fractional-frequency samples.
    tau_0 : float
        Base sampling interval (seconds).
    data_type : ``"phase"`` | ``"freq"``
    m_values : sequence of int, optional
        Averaging factors.  Defaults to octave-spaced (1, 2, 4, ...).
    estimators : sequence of str, optional
        Subset of ``{"avar", "mvar", "hvar", "tvar"}``.  Defaults to all.

    Returns
    -------
    dict mapping estimator name to list of VarianceResult.
    """
    arr = np.asarray(data, dtype=np.float64)

    _FUNCS = {
        "avar": avar, "mvar": mvar, "hvar": hvar, "tvar": tvar,
    }

    if estimators is None:
        estimators = list(_FUNCS.keys())
    for name in estimators:
        if name not in _FUNCS:
            raise ValueError(f"Unknown estimator {name!r}; choose from {set(_FUNCS)}")

    if m_values is None:
        m_values = _octave_m_values(len(arr))

    results: dict[str, list[VarianceResult]] = {}
    for name in estimators:
        fn = _FUNCS[name]
        res_list: list[VarianceResult] = []
        for m in m_values:
            try:
                res_list.append(fn(arr, m, tau_0, data_type=data_type))
            except ValueError:
                break
        results[name] = res_list

    return results


# ===================================================================
# Strategy 4: Decimation  (O(N) total, approximate at higher octaves)
# ===================================================================

def _decimate(phase: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pairwise-average decimation: halve the array length.

    If the array has odd length, the last sample is dropped before averaging.
    """
    n = len(phase)
    even = n - (n % 2)
    return (phase[:even:2] + phase[1:even:2]) / 2.0


def avar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Allan Variance at all octave taus via decimation.  O(N) total."""
    phase = _to_phase(data, tau_0, data_type)
    results: list[VarianceResult] = []
    tau = tau_0

    while len(phase) >= max(min_samples, 5):
        d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
        n_avg = len(d)
        var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
        results.append(_make_result(tau, var, _edf_avar(n_avg), n_avg))
        phase = _decimate(phase)
        tau *= 2.0

    return results


def mvar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Modified Allan Variance at all octave taus via decimation.  O(N) total."""
    phase = _to_phase(data, tau_0, data_type)
    results: list[VarianceResult] = []
    tau = tau_0

    while len(phase) >= max(min_samples, 5):
        d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
        n_avg = len(d)
        if n_avg < 1:
            break
        var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
        results.append(_make_result(tau, var, _edf_mvar(n_avg), n_avg))
        phase = _decimate(phase)
        tau *= 2.0

    return results


def hvar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Hadamard Variance at all octave taus via decimation.  O(N) total."""
    phase = _to_phase(data, tau_0, data_type)
    results: list[VarianceResult] = []
    tau = tau_0

    while len(phase) >= max(min_samples, 5):
        N = len(phase)
        if N < 4:
            break
        d = phase[3:] - 3.0 * phase[2:N - 1] + 3.0 * phase[1:N - 2] - phase[:N - 3]
        n_avg = len(d)
        if n_avg < 1:
            break
        var = float(np.mean(d ** 2) / (6.0 * tau ** 2))
        results.append(_make_result(tau, var, _edf_hvar(n_avg), n_avg))
        phase = _decimate(phase)
        tau *= 2.0

    return results


def tvar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Time Variance at all octave taus via decimation.  O(N) total."""
    mvar_results = mvar_octave(data, tau_0, data_type=data_type, min_samples=min_samples)
    results: list[VarianceResult] = []
    for mv in mvar_results:
        var = (mv.tau ** 2 / 3.0) * mv.variance
        results.append(_make_result(mv.tau, var, mv.dof, mv.n_averages))
    return results


def sweep(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    estimators: Sequence[str] | None = None,
    min_samples: int = 5,
) -> dict[str, list[VarianceResult]]:
    """All estimators at octave taus via decimation.  O(N) total."""
    _OCTAVE_FUNCS: dict[str, callable] = {
        "avar": avar_octave, "mvar": mvar_octave,
        "hvar": hvar_octave, "tvar": tvar_octave,
    }

    if estimators is None:
        estimators = list(_OCTAVE_FUNCS.keys())
    for name in estimators:
        if name not in _OCTAVE_FUNCS:
            raise ValueError(f"Unknown estimator {name!r}; choose from {set(_OCTAVE_FUNCS)}")

    return {
        name: _OCTAVE_FUNCS[name](data, tau_0, data_type=data_type, min_samples=min_samples)
        for name in estimators
    }


# ===================================================================
# Noise-type identification
# ===================================================================

def noise_id(
    results: list[VarianceResult],
) -> list[NoiseID]:
    """Identify noise types from the log-log slope of AVAR vs tau.

    Fits a piecewise slope between consecutive octave points on the
    log(AVAR) vs log(tau) plot and matches each segment to the nearest
    power-law noise type:

        slope   noise type
        -----   ----------
        -3      white phase modulation      (WPM)
        -2      flicker phase modulation    (FPM)
        -1      white frequency modulation  (WFM)
         0      flicker frequency modulation (FFM)
        +1      random walk FM              (RWFM)
        +2      frequency drift

    Parameters
    ----------
    results : list of VarianceResult
        AVAR results at successive tau values (e.g. from ``avar_octave``
        or ``sweep_psd``).

    Returns
    -------
    list of NoiseID, one per adjacent pair of tau values.
    """
    if len(results) < 2:
        return []

    ids: list[NoiseID] = []
    for i in range(len(results) - 1):
        r0, r1 = results[i], results[i + 1]
        if r0.variance <= 0 or r1.variance <= 0 or r0.tau <= 0 or r1.tau <= 0:
            continue
        slope = (math.log10(r1.variance) - math.log10(r0.variance)) / (
            math.log10(r1.tau) - math.log10(r0.tau)
        )
        # Find nearest canonical slope
        best_name = _NOISE_TYPES[0][1]
        best_dist = abs(slope - _NOISE_TYPES[0][0])
        for ref_slope, name in _NOISE_TYPES[1:]:
            d = abs(slope - ref_slope)
            if d < best_dist:
                best_dist = d
                best_name = name
        ids.append(NoiseID(
            tau_lo=r0.tau, tau_hi=r1.tau, slope=slope, noise_type=best_name,
        ))
    return ids


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_phase(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float,
    data_type: str,
) -> NDArray[np.float64]:
    """Convert input to phase (time-error) array."""
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"data must be 1-D, got shape {arr.shape}")
    if len(arr) < 3:
        raise ValueError("Need at least 3 data points")

    if data_type == "phase":
        return arr
    elif data_type == "freq":
        return np.concatenate(([0.0], np.cumsum(arr) * tau_0))
    else:
        raise ValueError(f"data_type must be 'phase' or 'freq', got {data_type!r}")


def _check_min_phase(N: int, min_needed: int, name: str) -> None:
    if N < min_needed:
        raise ValueError(
            f"{name} with this averaging factor requires at least "
            f"{min_needed} phase samples, got {N}"
        )


def _octave_m_values(N: int) -> list[int]:
    """Octave-spaced averaging factors: 1, 2, 4, ... up to N//4."""
    max_m = max(1, (N - 1) // 3)
    m_values: list[int] = []
    m = 1
    while m <= max_m:
        m_values.append(m)
        m *= 2
    return m_values


def _make_result(tau: float, var: float, dof: float, n_avg: int) -> VarianceResult:
    return VarianceResult(
        tau=tau, variance=var, deviation=math.sqrt(var), dof=dof, n_averages=n_avg,
    )


def _edf_avar(n_avg: int) -> float:
    return max(1.0, (n_avg + 1.0) * (n_avg - 1.0) / (n_avg * 1.5))


def _edf_mvar(n_avg: int) -> float:
    return max(1.0, (3.0 * (n_avg - 1.0)) / (2.0 * n_avg) * n_avg)


def _edf_hvar(n_avg: int) -> float:
    return max(1.0, n_avg * 0.75)
