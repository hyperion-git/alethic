"""Confidence calibration via temperature scaling.

Accumulates (raw_confidence, solved) pairs across sessions in a user-local JSONL
store (~/.alethic/calibration.jsonl), fits a temperature scalar T that minimizes
NLL on historical data, and applies it to correct systematic verifier bias.

T > 1: compresses probabilities toward 0.5 (corrects overconfidence).
T < 1: pushes toward extremes (corrects underconfidence).
T = 1: identity (no calibration effect).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import alethic


def _current_version() -> str:
    """Return current alethic version (injectable in tests via patch)."""
    return alethic.__version__


def _default_store() -> Path:
    """Lazy path — evaluated at call time, never at module import.

    Returns ~/.alethic/calibration.jsonl, creating the directory if needed.
    """
    p = Path.home() / ".alethic" / "calibration.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def calibrate(raw: float, T: float) -> float:
    """Apply temperature T to a raw confidence score.

    Identity short-circuit at T=1.0 for exact floating-point equality.
    Boundary fixpoints: calibrate(0.0, T)==0.0, calibrate(1.0, T)==1.0 (exact).
    """
    if T == 1.0:
        return raw
    if raw == 0.0:
        return 0.0
    if raw == 1.0:
        return 1.0
    clipped = max(1e-7, min(1.0 - 1e-7, raw))
    return _sigmoid(_logit(clipped) / T)


def _version_matches(entry_ver: str, current: str) -> bool:
    """True if entry and current share the same major.minor version."""
    try:
        e = entry_ver.split(".")
        c = current.split(".")
        return e[0] == c[0] and e[1] == c[1]
    except (IndexError, AttributeError):
        return False


def load_pairs(*, store_path: Path | None = None) -> list[dict]:
    """Load calibration pairs from JSONL store, filtered to current major.minor version."""
    path = store_path if store_path is not None else _default_store()
    current = _current_version()
    if not path.exists():
        return []
    pairs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if _version_matches(entry.get("alethic_version", ""), current):
                    pairs.append(entry)
            except json.JSONDecodeError:
                continue
    return pairs


def append_pair(
    raw_conf: float,
    solved: bool,
    *,
    model: str,
    preset: str,
    best_of_n: int,
    store_path: Path | None = None,
) -> None:
    """Append a (raw_confidence, solved) pair to the calibration store."""
    path = store_path if store_path is not None else _default_store()
    entry = {
        "model": model,
        "preset": preset,
        "best_of_n": best_of_n,
        "raw_conf": raw_conf,
        "solved": solved,
        "ts": datetime.now(timezone.utc).isoformat(),
        "alethic_version": _current_version(),
    }
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _nll(pairs: list[dict], T: float) -> float:
    """Negative log-likelihood of temperature-calibrated confidences."""
    total = 0.0
    for p in pairs:
        cal = calibrate(p["raw_conf"], T)
        cal = max(1e-10, min(1.0 - 1e-10, cal))
        label = float(p["solved"])
        total -= label * math.log(cal) + (1.0 - label) * math.log(1.0 - cal)
    return total


def fit_temperature(pairs: list[dict], *, grid_points: int = 50) -> float:
    """Find temperature T minimizing NLL on pairs.

    Returns 1.0 (identity) if fewer than 20 version-filtered pairs.
    Grid: 50 log-space points in [0.05, 20.0], followed by Brent's refinement.
    """
    if len(pairs) < 20:
        return 1.0

    T_min, T_max = 0.05, 20.0
    log_min, log_max = math.log(T_min), math.log(T_max)
    grid = [
        math.exp(log_min + (log_max - log_min) * i / (grid_points - 1))
        for i in range(grid_points)
    ]
    nll_vals = [_nll(pairs, T) for T in grid]
    best_idx = min(range(len(nll_vals)), key=lambda i: nll_vals[i])

    lo = grid[max(0, best_idx - 1)]
    hi = grid[min(len(grid) - 1, best_idx + 1)]

    try:
        from scipy.optimize import minimize_scalar
        result = minimize_scalar(lambda T: _nll(pairs, T), bounds=(lo, hi), method="bounded")
        return float(result.x)
    except ImportError:
        return grid[best_idx]


def load_calibrated_threshold(
    raw_threshold: float, *, store_path: Path | None = None
) -> float:
    """Load T from store and return calibrate(raw_threshold, T).

    Returns raw_threshold unchanged if insufficient data (T=1.0).
    """
    pairs = load_pairs(store_path=store_path)
    T = fit_temperature(pairs)
    return calibrate(raw_threshold, T)
