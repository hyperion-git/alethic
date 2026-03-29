"""Tests for two-sample variance estimators (AVAR, MVAR, HVAR, TVAR).

Covers four computation strategies:
  1. PSD / FFT        (sweep_psd)
  2. Hybrid            (sweep_hybrid)
  3. Exact overlapping (compute_all / single-point functions)
  4. Decimation        (sweep / *_octave)
Plus noise-type identification.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from alethic.variance_estimators import (
    NoiseID,
    VarianceResult,
    avar,
    avar_octave,
    compute_all,
    hvar,
    hvar_octave,
    mvar,
    mvar_octave,
    noise_id,
    sweep,
    sweep_hybrid,
    sweep_psd,
    tvar,
    tvar_octave,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def white_phase_noise():
    """Reproducible white phase-noise dataset (1000 samples)."""
    rng = np.random.default_rng(42)
    return rng.standard_normal(1000) * 1e-9


@pytest.fixture
def white_phase_noise_large():
    """Larger dataset for tighter PSD validation."""
    rng = np.random.default_rng(7)
    return rng.standard_normal(8192) * 1e-9


@pytest.fixture
def white_freq_noise():
    """Reproducible white frequency-noise dataset (1000 samples)."""
    rng = np.random.default_rng(99)
    return rng.standard_normal(1000) * 1e-12


@pytest.fixture
def linear_drift_phase():
    """Phase with pure linear frequency drift (HVAR should be zero)."""
    D = 1e-12
    tau_0 = 1.0
    t = np.arange(500) * tau_0
    return D * t, tau_0


# ===========================================================================
# Single-point estimators (Strategy 3)
# ===========================================================================

class TestAvar:
    def test_returns_variance_result(self, white_phase_noise):
        r = avar(white_phase_noise, m=1, tau_0=1.0)
        assert isinstance(r, VarianceResult)
        assert r.tau == 1.0
        assert r.variance > 0
        assert r.deviation == pytest.approx(math.sqrt(r.variance))
        assert r.n_averages > 0

    def test_m2(self, white_phase_noise):
        r = avar(white_phase_noise, m=2, tau_0=1.0)
        assert r.tau == 2.0
        assert r.variance > 0

    def test_freq_input(self, white_phase_noise):
        freq = np.diff(white_phase_noise) / 1.0
        r = avar(freq, m=1, tau_0=1.0, data_type="freq")
        assert r.variance > 0

    def test_too_few_samples(self):
        with pytest.raises(ValueError, match="at least"):
            avar(np.array([1.0, 2.0]), m=1, tau_0=1.0)

    def test_m_too_large(self):
        with pytest.raises(ValueError, match="at least"):
            avar(np.zeros(10), m=5, tau_0=1.0)


class TestMvar:
    def test_positive_variance(self, white_phase_noise):
        assert mvar(white_phase_noise, m=1, tau_0=1.0).variance > 0

    def test_m1_equals_avar(self, white_phase_noise):
        a = avar(white_phase_noise, m=1, tau_0=1.0)
        mv = mvar(white_phase_noise, m=1, tau_0=1.0)
        assert mv.variance == pytest.approx(a.variance, rel=1e-10)


class TestHvar:
    def test_positive_variance(self, white_phase_noise):
        assert hvar(white_phase_noise, m=1, tau_0=1.0).variance > 0

    def test_insensitive_to_linear_drift(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        assert hvar(phase, m=1, tau_0=tau_0).variance == pytest.approx(0.0, abs=1e-30)

    def test_insensitive_to_linear_drift_m4(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        assert hvar(phase, m=4, tau_0=tau_0).variance == pytest.approx(0.0, abs=1e-30)


class TestTvar:
    def test_relation_to_mvar(self, white_phase_noise):
        mv = mvar(white_phase_noise, m=4, tau_0=1.0)
        tv = tvar(white_phase_noise, m=4, tau_0=1.0)
        expected = (mv.tau ** 2 / 3.0) * mv.variance
        assert tv.variance == pytest.approx(expected, rel=1e-12)


class TestTauScaling:
    def test_avar_scales_as_tau_minus2(self, white_phase_noise):
        a1 = avar(white_phase_noise, m=1, tau_0=1.0)
        a2 = avar(white_phase_noise, m=2, tau_0=1.0)
        assert a1.variance / a2.variance == pytest.approx(4.0, rel=0.3)

    def test_mvar_scales_as_tau_minus3(self, white_phase_noise):
        m1 = mvar(white_phase_noise, m=2, tau_0=1.0)
        m2 = mvar(white_phase_noise, m=4, tau_0=1.0)
        assert m1.variance / m2.variance == pytest.approx(8.0, rel=0.4)


# ===========================================================================
# Strategy 1: PSD / FFT
# ===========================================================================

class TestSweepPsd:
    def test_all_estimators(self, white_phase_noise):
        results = sweep_psd(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}
        for res_list in results.values():
            assert len(res_list) > 0

    def test_subset(self, white_phase_noise):
        results = sweep_psd(white_phase_noise, tau_0=1.0, estimators=["avar"])
        assert set(results.keys()) == {"avar"}

    def test_custom_m_values(self, white_phase_noise):
        results = sweep_psd(white_phase_noise, tau_0=1.0,
                            m_values=[1, 3, 7], estimators=["avar"])
        taus = [r.tau for r in results["avar"]]
        assert taus == [1.0, 3.0, 7.0]

    def test_avar_m1_matches_exact(self, white_phase_noise):
        """At m=1 the circular approx is exact — PSD must match time-domain."""
        psd = sweep_psd(white_phase_noise, tau_0=1.0,
                        m_values=[1], estimators=["avar"])
        exact = avar(white_phase_noise, m=1, tau_0=1.0)
        assert psd["avar"][0].variance == pytest.approx(exact.variance, rel=1e-6)

    def test_avar_matches_exact_large(self, white_phase_noise_large):
        """PSD AVAR should closely match exact overlapping on large data."""
        psd = sweep_psd(white_phase_noise_large, tau_0=1.0, estimators=["avar"])
        exact = compute_all(white_phase_noise_large, tau_0=1.0, estimators=["avar"])
        # Compare at each octave: circular approx improves with N/m ratio
        for p, e in zip(psd["avar"], exact["avar"]):
            assert p.tau == pytest.approx(e.tau)
            assert p.variance == pytest.approx(e.variance, rel=0.05)

    def test_hvar_matches_exact_large(self, white_phase_noise_large):
        psd = sweep_psd(white_phase_noise_large, tau_0=1.0, estimators=["hvar"])
        exact = compute_all(white_phase_noise_large, tau_0=1.0, estimators=["hvar"])
        for p, e in zip(psd["hvar"], exact["hvar"]):
            assert p.variance == pytest.approx(e.variance, rel=0.05)

    def test_mvar_matches_exact_large(self, white_phase_noise_large):
        psd = sweep_psd(white_phase_noise_large, tau_0=1.0, estimators=["mvar"])
        exact = compute_all(white_phase_noise_large, tau_0=1.0, estimators=["mvar"])
        for p, e in zip(psd["mvar"], exact["mvar"]):
            assert p.variance == pytest.approx(e.variance, rel=0.10)

    def test_tvar_derived_from_mvar(self, white_phase_noise):
        results = sweep_psd(white_phase_noise, tau_0=1.0,
                            estimators=["mvar", "tvar"])
        for mv, tv in zip(results["mvar"], results["tvar"]):
            expected = (mv.tau ** 2 / 3.0) * mv.variance
            assert tv.variance == pytest.approx(expected, rel=1e-12)

    def test_freq_input(self, white_freq_noise):
        results = sweep_psd(white_freq_noise, tau_0=1.0, data_type="freq",
                            estimators=["avar"])
        assert len(results["avar"]) > 0

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            sweep_psd(white_phase_noise, estimators=["bogus"])


# ===========================================================================
# Strategy 2: Hybrid
# ===========================================================================

class TestSweepHybrid:
    def test_all_estimators(self, white_phase_noise):
        results = sweep_hybrid(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}
        for res_list in results.values():
            assert len(res_list) > 0

    def test_low_m_matches_exact(self, white_phase_noise):
        """Below crossover, hybrid should give exact overlapping results."""
        hybrid = sweep_hybrid(white_phase_noise, tau_0=1.0,
                              crossover_m=16, estimators=["avar"])
        exact = compute_all(white_phase_noise, tau_0=1.0,
                            m_values=[1, 2, 4, 8, 16], estimators=["avar"])
        for h, e in zip(hybrid["avar"][:5], exact["avar"]):
            assert h.variance == pytest.approx(e.variance, rel=1e-12)

    def test_extends_beyond_crossover(self, white_phase_noise):
        """Hybrid should produce results at taus beyond the crossover."""
        hybrid = sweep_hybrid(white_phase_noise, tau_0=1.0,
                              crossover_m=4, estimators=["avar"])
        exact = compute_all(white_phase_noise, tau_0=1.0,
                            m_values=[1, 2, 4], estimators=["avar"])
        assert len(hybrid["avar"]) > len(exact["avar"])

    def test_subset(self, white_phase_noise):
        results = sweep_hybrid(white_phase_noise, tau_0=1.0,
                               estimators=["avar", "hvar"])
        assert set(results.keys()) == {"avar", "hvar"}

    def test_tvar_derived_from_mvar(self, white_phase_noise):
        results = sweep_hybrid(white_phase_noise, tau_0=1.0,
                               estimators=["mvar", "tvar"])
        # At least the exact-region TVAR should match
        for mv, tv in zip(results["mvar"], results["tvar"]):
            if mv.tau != tv.tau:
                break
            expected = (mv.tau ** 2 / 3.0) * mv.variance
            assert tv.variance == pytest.approx(expected, rel=1e-10)

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            sweep_hybrid(white_phase_noise, estimators=["bogus"])


# ===========================================================================
# PSD vs Exact vs Decimation comparison
# ===========================================================================

class TestStrategyComparison:
    """The three strategies should agree within their expected tolerances."""

    def test_psd_beats_decimation_at_m2(self, white_phase_noise_large):
        """PSD should be closer to exact overlapping than decimation at m=2."""
        exact = avar(white_phase_noise_large, m=2, tau_0=1.0)
        psd = sweep_psd(white_phase_noise_large, tau_0=1.0,
                        m_values=[2], estimators=["avar"])
        dec = avar_octave(white_phase_noise_large, tau_0=1.0)

        psd_err = abs(psd["avar"][0].variance - exact.variance) / exact.variance
        dec_err = abs(dec[1].variance - exact.variance) / exact.variance
        assert psd_err < dec_err or psd_err < 0.02

    def test_hybrid_exact_region_matches_compute_all(self, white_phase_noise):
        """Below crossover, hybrid = compute_all (both exact overlapping)."""
        hybrid = sweep_hybrid(white_phase_noise, tau_0=1.0,
                              crossover_m=8, estimators=["avar"])
        exact = compute_all(white_phase_noise, tau_0=1.0,
                            m_values=[1, 2, 4, 8], estimators=["avar"])
        for h, e in zip(hybrid["avar"][:4], exact["avar"]):
            assert h.variance == pytest.approx(e.variance, rel=1e-12)


# ===========================================================================
# Decimation sweep (Strategy 4) — retained tests
# ===========================================================================

class TestAvarOctave:
    def test_returns_list_of_results(self, white_phase_noise):
        results = avar_octave(white_phase_noise, tau_0=1.0)
        assert len(results) > 0
        assert all(isinstance(r, VarianceResult) for r in results)

    def test_taus_are_octave_spaced(self, white_phase_noise):
        results = avar_octave(white_phase_noise, tau_0=1.0)
        taus = [r.tau for r in results]
        for i in range(1, len(taus)):
            assert taus[i] == pytest.approx(taus[i - 1] * 2.0)

    def test_first_octave_matches_single_point(self, white_phase_noise):
        octave = avar_octave(white_phase_noise, tau_0=1.0)
        single = avar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)

    def test_freq_input(self, white_freq_noise):
        results = avar_octave(white_freq_noise, tau_0=1.0, data_type="freq")
        assert len(results) > 0

    def test_custom_tau0(self, white_phase_noise):
        results = avar_octave(white_phase_noise, tau_0=0.5)
        assert results[0].tau == 0.5


class TestMvarOctave:
    def test_returns_results(self, white_phase_noise):
        assert len(mvar_octave(white_phase_noise, tau_0=1.0)) > 0

    def test_first_octave_matches_single_point(self, white_phase_noise):
        octave = mvar_octave(white_phase_noise, tau_0=1.0)
        single = mvar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)


class TestHvarOctave:
    def test_returns_results(self, white_phase_noise):
        assert len(hvar_octave(white_phase_noise, tau_0=1.0)) > 0

    def test_first_octave_matches_single_point(self, white_phase_noise):
        octave = hvar_octave(white_phase_noise, tau_0=1.0)
        single = hvar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)

    def test_drift_insensitive(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        for r in hvar_octave(phase, tau_0=tau_0):
            assert r.variance == pytest.approx(0.0, abs=1e-30)


class TestTvarOctave:
    def test_tvar_mvar_relation(self, white_phase_noise):
        mv_results = mvar_octave(white_phase_noise, tau_0=1.0)
        tv_results = tvar_octave(white_phase_noise, tau_0=1.0)
        for mv, tv in zip(mv_results, tv_results):
            expected = (mv.tau ** 2 / 3.0) * mv.variance
            assert tv.variance == pytest.approx(expected, rel=1e-12)


class TestSweep:
    def test_all_estimators(self, white_phase_noise):
        results = sweep(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}

    def test_subset(self, white_phase_noise):
        results = sweep(white_phase_noise, tau_0=1.0, estimators=["avar", "hvar"])
        assert set(results.keys()) == {"avar", "hvar"}

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            sweep(white_phase_noise, estimators=["bogus"])

    def test_freq_input(self, white_freq_noise):
        results = sweep(white_freq_noise, tau_0=1.0, data_type="freq")
        assert all(len(v) > 0 for v in results.values())


class TestDecimationVsOverlapping:
    def test_avar_close_at_m2(self, white_phase_noise):
        octave = avar_octave(white_phase_noise, tau_0=1.0)
        single = avar(white_phase_noise, m=2, tau_0=1.0)
        assert octave[1].variance == pytest.approx(single.variance, rel=0.35)

    def test_hvar_close_at_m2(self, white_phase_noise):
        octave = hvar_octave(white_phase_noise, tau_0=1.0)
        single = hvar(white_phase_noise, m=2, tau_0=1.0)
        assert octave[1].variance == pytest.approx(single.variance, rel=0.35)


# ===========================================================================
# compute_all
# ===========================================================================

class TestComputeAll:
    def test_default_estimators(self, white_phase_noise):
        results = compute_all(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}
        for res_list in results.values():
            assert len(res_list) > 0

    def test_custom_m_values(self, white_phase_noise):
        results = compute_all(white_phase_noise, tau_0=1.0, m_values=[1, 5, 10])
        for res_list in results.values():
            assert [r.tau for r in res_list] == [1.0, 5.0, 10.0]

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            compute_all(white_phase_noise, estimators=["bogus"])


# ===========================================================================
# Noise identification
# ===========================================================================

class TestNoiseID:
    def test_white_phase_noise(self, white_phase_noise):
        """WPM has AVAR slope ~ -2 (or -3 for very pure WPM)."""
        results = avar_octave(white_phase_noise, tau_0=1.0)
        ids = noise_id(results)
        assert len(ids) > 0
        assert all(isinstance(nid, NoiseID) for nid in ids)
        # First segment should identify as WPM or FPM (slope ~ -2 to -3)
        assert ids[0].slope < -1.0

    def test_random_walk_fm(self):
        """Integrated white noise → random walk FM → AVAR slope ~ +1."""
        rng = np.random.default_rng(123)
        wfm = rng.standard_normal(4096) * 1e-12
        # Integrate once: WFM → RWFM (in frequency), which is AVAR slope +1
        rwfm_phase = np.cumsum(np.cumsum(wfm))
        results = avar_octave(rwfm_phase, tau_0=1.0)
        ids = noise_id(results)
        # Mid-range segments should show positive slope
        mid = ids[len(ids) // 2]
        assert mid.slope > -0.5  # clearly not WPM

    def test_too_few_points(self):
        assert noise_id([]) == []
        r = VarianceResult(tau=1.0, variance=1e-20, deviation=1e-10, dof=10, n_averages=100)
        assert noise_id([r]) == []

    def test_slope_values(self):
        """Check slope computation on synthetic results."""
        r1 = VarianceResult(tau=1.0, variance=1e-20, deviation=0, dof=10, n_averages=100)
        r2 = VarianceResult(tau=2.0, variance=1e-20 / 4, deviation=0, dof=10, n_averages=50)
        ids = noise_id([r1, r2])
        # log(1/4)/log(2) = -2
        assert ids[0].slope == pytest.approx(-2.0, rel=1e-10)
        assert "flicker phase" in ids[0].noise_type


# ===========================================================================
# Input validation
# ===========================================================================

class TestValidation:
    def test_2d_input_rejected(self):
        with pytest.raises(ValueError, match="1-D"):
            avar(np.zeros((5, 2)), m=1, tau_0=1.0)

    def test_bad_data_type(self):
        with pytest.raises(ValueError, match="data_type"):
            avar(np.zeros(10), m=1, tau_0=1.0, data_type="power")

    def test_list_input(self):
        x = [0.0, 1e-9, -0.5e-9, 2e-9, 0.3e-9, -1e-9, 0.7e-9, 1.5e-9]
        assert avar(x, m=1, tau_0=1.0).variance > 0

    def test_list_input_octave(self):
        x = [float(i) * 1e-9 for i in range(100)]
        assert len(avar_octave(x, tau_0=1.0)) > 0

    def test_list_input_psd(self):
        x = [float(i) * 1e-9 for i in range(100)]
        results = sweep_psd(x, tau_0=1.0, estimators=["avar"])
        assert len(results["avar"]) > 0
