"""
Two-sample variance estimators for frequency stability analysis.

Implements the standard overlapping estimators per IEEE Std 1139-2008:
  - AVAR  (Allan Variance)
  - MVAR  (Modified Allan Variance)
  - HVAR  (Hadamard Variance)
  - TVAR  (Time Variance)

All functions accept phase data (time-error samples x_i at uniform spacing
tau_0) or fractional-frequency data (y_i averaged over tau_0), plus an
averaging-factor m so that the analysis interval is tau = m * tau_0.

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
# Core estimators — operate on numpy arrays
# ---------------------------------------------------------------------------

def avar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Allan Variance (AVAR).

    .. math::

        \\sigma_y^2(\\tau) = \\frac{1}{2 m^2 \\tau_0^2 (N - 2m)}
            \\sum_{i=0}^{N-2m-1} (x_{i+2m} - 2 x_{i+m} + x_i)^2

    Parameters
    ----------
    data : array-like
        Phase (time-error) samples **or** fractional-frequency samples,
        controlled by *data_type*.
    m : int
        Averaging factor.  The analysis interval is ``tau = m * tau_0``.
    tau_0 : float
        Base sampling interval in seconds.
    data_type : ``"phase"`` | ``"freq"``
        Whether *data* contains phase (x_i) or fractional-frequency (y_i)
        samples.

    Returns
    -------
    VarianceResult
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 2 * m + 1, "AVAR")

    tau = m * tau_0

    # Second difference of phase: x_{i+2m} - 2 x_{i+m} + x_i
    d = phase[2 * m :] - 2.0 * phase[m : N - m] + phase[: N - 2 * m]
    n_avg = len(d)
    var = np.sum(d ** 2) / (2.0 * n_avg * tau ** 2)

    # EDF approximation (Riley, NIST SP 1065, Table B.3 — white PM limit)
    dof = max(1.0, (n_avg + 1.0) * (n_avg - 1.0) / (n_avg * 1.5))

    return VarianceResult(
        tau=tau,
        variance=float(var),
        deviation=float(math.sqrt(var)),
        dof=dof,
        n_averages=n_avg,
    )


def mvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Modified Allan Variance (MVAR).

    .. math::

        \\text{Mod}\\,\\sigma_y^2(\\tau) = \\frac{1}{2 m^4 \\tau_0^2 (N - 3m + 1)}
            \\sum_{j=0}^{N-3m} \\left(\\sum_{i=j}^{j+m-1}
            (x_{i+2m} - 2 x_{i+m} + x_i)\\right)^2

    Parameters
    ----------
    data, m, tau_0, data_type : see :func:`avar`.

    Returns
    -------
    VarianceResult
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 3 * m + 1, "MVAR")

    tau = m * tau_0

    # Second differences
    d = phase[2 * m :] - 2.0 * phase[m : N - m] + phase[: N - 2 * m]
    # Sliding sum of m consecutive second differences
    cs = np.cumsum(d)
    s = np.empty(len(d) - m + 1)
    s[0] = cs[m - 1]
    s[1:] = cs[m:] - cs[: len(d) - m]

    n_avg = len(s)
    var = np.sum(s ** 2) / (2.0 * n_avg * m ** 2 * tau ** 2)

    dof = max(1.0, (3.0 * (n_avg - 1.0)) / (2.0 * n_avg) * n_avg)

    return VarianceResult(
        tau=tau,
        variance=float(var),
        deviation=float(math.sqrt(var)),
        dof=dof,
        n_averages=n_avg,
    )


def hvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Overlapping Hadamard Variance (HVAR).

    .. math::

        H\\sigma_y^2(\\tau) = \\frac{1}{6 m^2 \\tau_0^2 (N - 3m)}
            \\sum_{i=0}^{N-3m-1}
            (x_{i+3m} - 3 x_{i+2m} + 3 x_{i+m} - x_i)^2

    The Hadamard variance is insensitive to linear frequency drift, making it
    useful for oscillators with significant drift.

    Parameters
    ----------
    data, m, tau_0, data_type : see :func:`avar`.

    Returns
    -------
    VarianceResult
    """
    phase = _to_phase(data, tau_0, data_type)
    N = len(phase)
    _check_min_phase(N, 3 * m + 1, "HVAR")

    tau = m * tau_0

    # Third difference of phase
    d = (
        phase[3 * m :]
        - 3.0 * phase[2 * m : N - m]
        + 3.0 * phase[m : N - 2 * m]
        - phase[: N - 3 * m]
    )
    n_avg = len(d)
    var = np.sum(d ** 2) / (6.0 * n_avg * tau ** 2)

    dof = max(1.0, n_avg * 0.75)

    return VarianceResult(
        tau=tau,
        variance=float(var),
        deviation=float(math.sqrt(var)),
        dof=dof,
        n_averages=n_avg,
    )


def tvar(
    data: NDArray[np.floating],
    m: int = 1,
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
) -> VarianceResult:
    """Time Variance (TVAR).

    .. math::

        \\sigma_x^2(\\tau) = \\frac{\\tau^2}{3}\\,
            \\text{Mod}\\,\\sigma_y^2(\\tau)

    TVAR characterises time (phase) stability and has units of seconds^2.
    It is derived directly from MVAR.

    Parameters
    ----------
    data, m, tau_0, data_type : see :func:`avar`.

    Returns
    -------
    VarianceResult
        The *variance* field has units of time^2 (seconds^2).
    """
    mv = mvar(data, m, tau_0, data_type=data_type)
    tau = mv.tau
    var = (tau ** 2 / 3.0) * mv.variance

    return VarianceResult(
        tau=tau,
        variance=var,
        deviation=math.sqrt(var),
        dof=mv.dof,
        n_averages=mv.n_averages,
    )


# ---------------------------------------------------------------------------
# Convenience: compute over a range of averaging factors
# ---------------------------------------------------------------------------

def compute_all(
    data: Sequence[float] | NDArray[np.floating],
    tau_0: float = 1.0,
    *,
    data_type: str = "phase",
    m_values: Sequence[int] | None = None,
    estimators: Sequence[str] | None = None,
) -> dict[str, list[VarianceResult]]:
    """Compute one or more variance estimators over a range of tau values.

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
        # Octave spacing: 1, 2, 4, 8, ...
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
                break  # not enough data for this m
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
        # Integrate fractional frequency to phase: x_i = tau_0 * sum(y_0..y_{i-1})
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
