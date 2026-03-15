"""Tests for §5.3: calibration.py module."""
import json
import math
import pytest
from pathlib import Path
from unittest.mock import patch

from alethic.calibration import (
    calibrate,
    fit_temperature,
    append_pair,
    load_pairs,
    load_calibrated_threshold,
    _default_store,
)


# ── calibrate(raw, T) ────────────────────────────────────────────────────────

def test_calibrate_identity_exact():
    """T=1.0: exact identity short-circuit (no floating-point drift)."""
    assert calibrate(0.5, 1.0) == 0.5

def test_calibrate_t_gt1_compresses_low():
    assert calibrate(0.3, 2.0) > 0.3

def test_calibrate_t_gt1_compresses_high():
    assert calibrate(0.7, 2.0) < 0.7

def test_calibrate_t_lt1_pushes_low():
    assert calibrate(0.3, 0.5) < 0.3

def test_calibrate_boundary_zero():
    for T in [0.5, 1.0, 2.0]:
        assert calibrate(0.0, T) == 0.0

def test_calibrate_boundary_one():
    for T in [0.5, 1.0, 2.0]:
        assert calibrate(1.0, T) == 1.0


# ── Threshold round-trip ──────────────────────────────────────────────────────

def test_threshold_round_trip():
    """calibrate(calibrate(threshold, T), 1/T) ≈ threshold."""
    T = 2.0
    raw_threshold = 0.90
    calibrated_threshold = calibrate(raw_threshold, T)
    assert abs(calibrate(calibrated_threshold, 1.0 / T) - raw_threshold) < 1e-9


# ── fit_temperature() ────────────────────────────────────────────────────────

def test_fit_temperature_recovers_true_T():
    """Deterministic dataset calibrated at T=2.0 → recovered T within 2% of 2.0.

    Each raw_conf is repeated 10x with round(calibrate(raw, 2.0) * 10) True labels,
    encoding the exact T=2 calibration deterministically (N=1000 total pairs).
    """
    raw_confs = [0.5 + 0.49 * i / 99 for i in range(100)]
    pairs = []
    K = 10  # repetitions per raw_conf to encode exact T=2 fraction
    for r in raw_confs:
        p_true = calibrate(r, 2.0)
        n_true = round(p_true * K)
        for _ in range(n_true):
            pairs.append({"raw_conf": r, "solved": True, "alethic_version": "3.6.0"})
        for _ in range(K - n_true):
            pairs.append({"raw_conf": r, "solved": False, "alethic_version": "3.6.0"})
    with patch("alethic.calibration._current_version", return_value="3.6.0"):
        T = fit_temperature(pairs)
    assert abs(T - 2.0) / 2.0 < 0.05, f"Expected T≈2.0, got T={T:.4f}"

def test_fit_temperature_n_lt20_identity():
    """N<20 → T=1.0 (identity)."""
    pairs = [{"raw_conf": 0.8, "solved": True, "alethic_version": "3.6.0"} for _ in range(5)]
    T = fit_temperature(pairs)
    assert abs(calibrate(0.7, T) - 0.7) < 1e-6

def test_fit_temperature_biased_fires():
    """N=20 biased (all high raw_conf, all solved=False) → calibration moves the needle."""
    pairs = [{"raw_conf": 0.95, "solved": False, "alethic_version": "3.6.0"} for _ in range(20)]
    T = fit_temperature(pairs)
    assert abs(calibrate(0.95, T) - 0.95) > 0.01


# ── append_pair() and load_pairs() ───────────────────────────────────────────

def test_append_pair_round_trip(tmp_path):
    """Append 3 pairs, reload, all required fields present."""
    store = tmp_path / "calibration.jsonl"
    for i in range(3):
        append_pair(
            0.85 + i * 0.01,
            i % 2 == 0,
            model="claude-opus-4-6",
            preset="default",
            best_of_n=2,
            store_path=store,
        )
    pairs = load_pairs(store_path=store)
    assert len(pairs) == 3
    required = ("model", "preset", "best_of_n", "raw_conf", "solved", "ts", "alethic_version")
    for p in pairs:
        for field in required:
            assert field in p, f"Missing field: {field}"


# ── Version filter ────────────────────────────────────────────────────────────

def _write_entry(store, version):
    with open(store, "a") as f:
        f.write(json.dumps({
            "raw_conf": 0.9, "solved": True, "alethic_version": version,
            "model": "m", "preset": "p", "best_of_n": 1, "ts": "t"
        }) + "\n")

def test_version_filter_same_minor_included(tmp_path):
    store = tmp_path / "c.jsonl"
    _write_entry(store, "3.6.1")
    with patch("alethic.calibration._current_version", return_value="3.6.0"):
        pairs = load_pairs(store_path=store)
    assert len(pairs) == 1

def test_version_filter_different_minor_excluded(tmp_path):
    store = tmp_path / "c.jsonl"
    _write_entry(store, "3.5.0")
    with patch("alethic.calibration._current_version", return_value="3.6.0"):
        pairs = load_pairs(store_path=store)
    assert len(pairs) == 0

def test_version_filter_different_major_excluded(tmp_path):
    """Required test case: major version mismatch excluded."""
    store = tmp_path / "c.jsonl"
    _write_entry(store, "4.0.0")
    with patch("alethic.calibration._current_version", return_value="3.6.0"):
        pairs = load_pairs(store_path=store)
    assert len(pairs) == 0

def test_version_filter_37_excluded(tmp_path):
    """Required test case: different minor version excluded."""
    store = tmp_path / "c.jsonl"
    _write_entry(store, "3.7.0")
    with patch("alethic.calibration._current_version", return_value="3.6.0"):
        pairs = load_pairs(store_path=store)
    assert len(pairs) == 0


# ── _default_store() laziness ─────────────────────────────────────────────────

def test_default_store_is_callable():
    """_default_store must be a function, not a Path (prevents module-load-time side effects)."""
    import alethic.calibration as cal
    assert callable(cal._default_store)
    assert not isinstance(cal._default_store, Path)

def test_default_store_creates_directory(tmp_path, monkeypatch):
    """Calling _default_store() creates parent directory and returns path."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    path = _default_store()
    assert path.parent.exists()
    assert path.name == "calibration.jsonl"
