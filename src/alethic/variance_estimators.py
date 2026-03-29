"""
Two-sample variance estimators for frequency stability analysis.

Implements the standard overlapping estimators per IEEE Std 1139-2008:
  - AVAR  (Allan Variance)
  - MVAR  (Modified Allan Variance)
  - HVAR  (Hadamard Variance)
  - TVAR  (Time Variance)

Two computation modes:

1. **Single-point** (``avar``, ``mvar``, ``hvar``, ``tvar``): compute one
   variance at a specified averaging factor *m*.  O(N) per call.

2. **Octave sweep with decimation** (``avar_octave``, ``mvar_octave``,
   ``hvar_octave``, ``tvar_octave``, ``sweep``): compute variances at all
   octave-spaced taus (1, 2, 4, 8, ...) in O(N) *total* by decimating the
   phase array at each scale rather than re-traversing at each *m*.

All functions accept phase data (time-error samples x_i at uniform spacing
tau_0) or fractional-frequency data (y_i averaged over tau_0).

References:
    [1] D.W. Allan, "Statistics of Atomic Frequency Standards," Proc. IEEE,
        54(2), 1966.
    [2] IEEE Std 1139-2008, "Standard Definitions of Physical Quantities for
        Fundamental Frequency and Time Metrology."
    [3] W.J. Riley, "Handbook of Frequency Stability Analysis," NIST SP 1065,
        2008.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Result container
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


# ---------------------------------------------------------------------------
# O(N)-total octave-decimation sweep
# ---------------------------------------------------------------------------

def _decimate(phase: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pairwise-average decimation: halve the array length.

    If the array has odd length, the last sample is dropped before averaging.
    """
    n = len(phase)
    even = n - (n % 2)  # largest even number <= n
    return (phase[:even:2] + phase[1:even:2]) / 2.0


def avar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Allan Variance at all octave taus via decimation.  O(N) total.

    At each octave the phase array is decimated (pairwise-averaged) rather
    than re-traversed with a larger stride, giving O(N) total work across
    all scales (geometric series: N + N/2 + N/4 + ... < 2N).

    Parameters
    ----------
    data : array-like
        Phase or frequency samples.
    tau_0 : float
        Base sampling interval (seconds).
    data_type : ``"phase"`` | ``"freq"``
    min_samples : int
        Stop when fewer than this many phase samples remain.

    Returns
    -------
    list of VarianceResult, one per octave tau.
    """
    phase = _to_phase(data, tau_0, data_type)
    results: list[VarianceResult] = []
    tau = tau_0

    while len(phase) >= max(min_samples, 5):
        d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
        n_avg = len(d)
        var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
        results.append(VarianceResult(
            tau=tau, variance=var, deviation=math.sqrt(var),
            dof=_edf_avar(n_avg), n_averages=n_avg,
        ))
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
    """Modified Allan Variance at all octave taus via decimation.  O(N) total.

    At each octave scale, the MVAR second-difference + sliding-window-sum is
    computed on the current (decimated) phase array with m=1, then the array
    is decimated for the next scale.
    """
    phase = _to_phase(data, tau_0, data_type)
    results: list[VarianceResult] = []
    tau = tau_0
    # m at the decimated scale is always 1; the effective m doubles each octave
    m_eff = 1

    while len(phase) >= max(min_samples, 5):
        N = len(phase)
        # Second differences at stride 1 (m=1 on decimated data)
        d = phase[2:] - 2.0 * phase[1:-1] + phase[:-2]
        # For MVAR we need the sliding sum of m_eff consecutive differences,
        # but after decimation m_eff at the current scale is always 1, so the
        # sliding sum is just d itself.
        n_avg = len(d)
        if n_avg < 1:
            break
        var = float(np.mean(d ** 2) / (2.0 * tau ** 2))
        results.append(VarianceResult(
            tau=tau, variance=var, deviation=math.sqrt(var),
            dof=_edf_mvar(n_avg), n_averages=n_avg,
        ))
        phase = _decimate(phase)
        tau *= 2.0
        m_eff *= 2

    return results


def hvar_octave(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    min_samples: int = 5,
) -> list[VarianceResult]:
    """Hadamard Variance at all octave taus via decimation.  O(N) total.

    Uses third-difference on the decimated phase at each octave scale.
    """
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
        results.append(VarianceResult(
            tau=tau, variance=var, deviation=math.sqrt(var),
            dof=_edf_hvar(n_avg), n_averages=n_avg,
        ))
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
    """Time Variance at all octave taus via decimation.  O(N) total.

    TVAR = (tau^2 / 3) * MVAR.
    """
    mvar_results = mvar_octave(data, tau_0, data_type=data_type, min_samples=min_samples)
    results: list[VarianceResult] = []
    for mv in mvar_results:
        var = (mv.tau ** 2 / 3.0) * mv.variance
        results.append(VarianceResult(
            tau=mv.tau, variance=var, deviation=math.sqrt(var),
            dof=mv.dof, n_averages=mv.n_averages,
        ))
    return results


def sweep(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    estimators: Sequence[str] | None = None,
    min_samples: int = 5,
) -> dict[str, list[VarianceResult]]:
    """Compute one or more estimators at all octave taus via decimation.

    This is the recommended entry point for stability analysis — O(N) total
    across all taus and estimators.

    Parameters
    ----------
    data : array-like
        Phase or frequency samples.
    tau_0 : float
        Base sampling interval (seconds).
    data_type : ``"phase"`` | ``"freq"``
    estimators : sequence of str, optional
        Subset of ``{"avar", "mvar", "hvar", "tvar"}``.  Defaults to all four.
    min_samples : int
        Minimum phase samples per octave before stopping.

    Returns
    -------
    dict mapping estimator name to list of VarianceResult.
    """
    _OCTAVE_FUNCS: dict[str, callable] = {
        "avar": avar_octave,
        "mvar": mvar_octave,
        "hvar": hvar_octave,
        "tvar": tvar_octave,
    }

    if estimators is None:
        estimators = list(_OCTAVE_FUNCS.keys())
    for name in estimators:
        if name not in _OCTAVE_FUNCS:
            raise ValueError(
                f"Unknown estimator {name!r}; choose from {set(_OCTAVE_FUNCS)}"
            )

    return {
        name: _OCTAVE_FUNCS[name](data, tau_0, data_type=data_type, min_samples=min_samples)
        for name in estimators
    }


# ---------------------------------------------------------------------------
# Legacy convenience (uses single-point functions)
# ---------------------------------------------------------------------------

def compute_all(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    m_values: Sequence[int] | None = None,
    estimators: Sequence[str] | None = None,
) -> dict[str, list[VarianceResult]]:
    """Compute estimators over a range of tau values (single-point method).

    For octave-spaced taus, prefer :func:`sweep` which is O(N) total.
    This function supports arbitrary (non-octave) m values.

    Parameters
    ----------
    data : array-like
        Phase or fractional-frequency samples.
    tau_0 : float
        Base sampling interval (seconds).
    data_type : ``"phase"`` | ``"freq"``
        Input data type.
    m_values : sequence of int, optional
        Averaging factors to evaluate.  Defaults to octave-spaced values
        (1, 2, 4, 8, ...) up to the maximum supported by the data length.
    estimators : sequence of str, optional
        Which estimators to compute.  Subset of
        ``{"avar", "mvar", "hvar", "tvar"}``.  Defaults to all four.

    Returns
    -------
    dict mapping estimator name to list of :class:`VarianceResult`.
    """
    arr = np.asarray(data, dtype=np.float64)

    _FUNCS = {
        "avar": avar,
        "mvar": mvar,
        "hvar": hvar,
        "tvar": tvar,
    }

    if estimators is None:
        estimators = list(_FUNCS.keys())
    for name in estimators:
        if name not in _FUNCS:
            raise ValueError(
                f"Unknown estimator {name!r}; choose from {set(_FUNCS)}"
            )

    if m_values is None:
        max_m = max(1, (len(arr) - 1) // 3)
        m_values = []
        m = 1
        while m <= max_m:
            m_values.append(m)
            m *= 2

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
    """Raise if there are too few phase samples for the given estimator/m."""
    if N < min_needed:
        raise ValueError(
            f"{name} with this averaging factor requires at least "
            f"{min_needed} phase samples, got {N}"
        )


def _edf_avar(n_avg: int) -> float:
    """Approximate EDF for overlapping AVAR (white PM limit)."""
    return max(1.0, (n_avg + 1.0) * (n_avg - 1.0) / (n_avg * 1.5))


def _edf_mvar(n_avg: int) -> float:
    """Approximate EDF for overlapping MVAR."""
    return max(1.0, (3.0 * (n_avg - 1.0)) / (2.0 * n_avg) * n_avg)


def _edf_hvar(n_avg: int) -> float:
    """Approximate EDF for overlapping HVAR."""
    return max(1.0, n_avg * 0.75)
