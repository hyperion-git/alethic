"""Tests for two-sample variance estimators (AVAR, MVAR, HVAR, TVAR).

Covers both single-point overlapping functions and O(N)-total octave-decimation
sweep functions.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from alethic.variance_estimators import (
    VarianceResult,
    avar,
    avar_octave,
    compute_all,
    hvar,
    hvar_octave,
    mvar,
    mvar_octave,
    sweep,
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
    return rng.standard_normal(1000) * 1e-9  # 1 ns RMS


@pytest.fixture
def white_freq_noise():
    """Reproducible white frequency-noise dataset (1000 samples)."""
    rng = np.random.default_rng(99)
    return rng.standard_normal(1000) * 1e-12


@pytest.fixture
def linear_drift_phase():
    """Phase data with a pure linear frequency drift (no noise).

    Hadamard variance should be zero for all m (insensitive to linear drift).
    """
    D = 1e-12  # 1 ps/s drift rate
    tau_0 = 1.0
    t = np.arange(500) * tau_0
    return D * t, tau_0


# ===========================================================================
# Single-point estimators
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
        x = np.zeros(10)
        with pytest.raises(ValueError, match="at least"):
            avar(x, m=5, tau_0=1.0)


class TestMvar:
    def test_positive_variance(self, white_phase_noise):
        r = mvar(white_phase_noise, m=1, tau_0=1.0)
        assert r.variance > 0

    def test_m1_equals_avar(self, white_phase_noise):
        """At m=1 MVAR should equal AVAR (both reduce to the same sum)."""
        a = avar(white_phase_noise, m=1, tau_0=1.0)
        mv = mvar(white_phase_noise, m=1, tau_0=1.0)
        assert mv.variance == pytest.approx(a.variance, rel=1e-10)


class TestHvar:
    def test_positive_variance(self, white_phase_noise):
        r = hvar(white_phase_noise, m=1, tau_0=1.0)
        assert r.variance > 0

    def test_insensitive_to_linear_drift(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        r = hvar(phase, m=1, tau_0=tau_0)
        assert r.variance == pytest.approx(0.0, abs=1e-30)

    def test_insensitive_to_linear_drift_m4(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        r = hvar(phase, m=4, tau_0=tau_0)
        assert r.variance == pytest.approx(0.0, abs=1e-30)


class TestTvar:
    def test_relation_to_mvar(self, white_phase_noise):
        m = 4
        tau_0 = 1.0
        mv = mvar(white_phase_noise, m=m, tau_0=tau_0)
        tv = tvar(white_phase_noise, m=m, tau_0=tau_0)
        tau = m * tau_0
        expected = (tau ** 2 / 3.0) * mv.variance
        assert tv.variance == pytest.approx(expected, rel=1e-12)
        assert tv.tau == tau


class TestTauScaling:
    """For white phase noise, AVAR ~ 1/tau^2 and MVAR ~ 1/tau^3."""

    def test_avar_scales_as_tau_minus2(self, white_phase_noise):
        a1 = avar(white_phase_noise, m=1, tau_0=1.0)
        a2 = avar(white_phase_noise, m=2, tau_0=1.0)
        ratio = a1.variance / a2.variance
        assert ratio == pytest.approx(4.0, rel=0.3)

    def test_mvar_scales_as_tau_minus3(self, white_phase_noise):
        m1 = mvar(white_phase_noise, m=2, tau_0=1.0)
        m2 = mvar(white_phase_noise, m=4, tau_0=1.0)
        ratio = m1.variance / m2.variance
        assert ratio == pytest.approx(8.0, rel=0.4)


# ===========================================================================
# Octave-decimation sweep functions
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
        """The m=1 octave result should match avar(m=1) exactly."""
        octave = avar_octave(white_phase_noise, tau_0=1.0)
        single = avar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)

    def test_freq_input(self, white_freq_noise):
        results = avar_octave(white_freq_noise, tau_0=1.0, data_type="freq")
        assert len(results) > 0
        assert all(r.variance > 0 for r in results)

    def test_custom_tau0(self, white_phase_noise):
        results = avar_octave(white_phase_noise, tau_0=0.5)
        assert results[0].tau == 0.5


class TestMvarOctave:
    def test_returns_results(self, white_phase_noise):
        results = mvar_octave(white_phase_noise, tau_0=1.0)
        assert len(results) > 0

    def test_first_octave_matches_single_point(self, white_phase_noise):
        octave = mvar_octave(white_phase_noise, tau_0=1.0)
        single = mvar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)


class TestHvarOctave:
    def test_returns_results(self, white_phase_noise):
        results = hvar_octave(white_phase_noise, tau_0=1.0)
        assert len(results) > 0

    def test_first_octave_matches_single_point(self, white_phase_noise):
        octave = hvar_octave(white_phase_noise, tau_0=1.0)
        single = hvar(white_phase_noise, m=1, tau_0=1.0)
        assert octave[0].variance == pytest.approx(single.variance, rel=1e-12)

    def test_drift_insensitive(self, linear_drift_phase):
        phase, tau_0 = linear_drift_phase
        results = hvar_octave(phase, tau_0=tau_0)
        for r in results:
            assert r.variance == pytest.approx(0.0, abs=1e-30)


class TestTvarOctave:
    def test_tvar_mvar_relation(self, white_phase_noise):
        """TVAR = (tau^2/3) * MVAR at each octave."""
        mv_results = mvar_octave(white_phase_noise, tau_0=1.0)
        tv_results = tvar_octave(white_phase_noise, tau_0=1.0)
        assert len(tv_results) == len(mv_results)
        for mv, tv in zip(mv_results, tv_results):
            expected = (mv.tau ** 2 / 3.0) * mv.variance
            assert tv.variance == pytest.approx(expected, rel=1e-12)


class TestSweep:
    def test_all_estimators(self, white_phase_noise):
        results = sweep(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}
        for res_list in results.values():
            assert len(res_list) > 0

    def test_subset(self, white_phase_noise):
        results = sweep(white_phase_noise, tau_0=1.0, estimators=["avar", "hvar"])
        assert set(results.keys()) == {"avar", "hvar"}

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            sweep(white_phase_noise, estimators=["bogus"])

    def test_freq_input(self, white_freq_noise):
        results = sweep(white_freq_noise, tau_0=1.0, data_type="freq")
        assert all(len(v) > 0 for v in results.values())


# ===========================================================================
# Decimation agrees with overlapping (within expected tolerance)
# ===========================================================================

class TestDecimationVsOverlapping:
    """Octave decimation is an approximation (non-overlapping at decimated
    scales).  At the first octave (m=1) it should be exact; at higher octaves
    values should be in the same ballpark (within ~30% for this noise type).
    """

    def test_avar_close_at_m2(self, white_phase_noise):
        octave = avar_octave(white_phase_noise, tau_0=1.0)
        single = avar(white_phase_noise, m=2, tau_0=1.0)
        # Decimation is an approximation — allow generous tolerance
        assert octave[1].variance == pytest.approx(single.variance, rel=0.35)

    def test_hvar_close_at_m2(self, white_phase_noise):
        octave = hvar_octave(white_phase_noise, tau_0=1.0)
        single = hvar(white_phase_noise, m=2, tau_0=1.0)
        assert octave[1].variance == pytest.approx(single.variance, rel=0.35)


# ===========================================================================
# compute_all (legacy single-point sweep)
# ===========================================================================

class TestComputeAll:
    def test_default_estimators(self, white_phase_noise):
        results = compute_all(white_phase_noise, tau_0=1.0)
        assert set(results.keys()) == {"avar", "mvar", "hvar", "tvar"}
        for name, res_list in results.items():
            assert len(res_list) > 0
            assert all(isinstance(r, VarianceResult) for r in res_list)

    def test_subset_estimators(self, white_phase_noise):
        results = compute_all(
            white_phase_noise, tau_0=1.0, estimators=["avar", "hvar"]
        )
        assert set(results.keys()) == {"avar", "hvar"}

    def test_custom_m_values(self, white_phase_noise):
        results = compute_all(
            white_phase_noise, tau_0=1.0, m_values=[1, 5, 10]
        )
        for res_list in results.values():
            taus = [r.tau for r in res_list]
            assert taus == [1.0, 5.0, 10.0]

    def test_octave_spacing_default(self, white_phase_noise):
        results = compute_all(white_phase_noise, tau_0=1.0, estimators=["avar"])
        taus = [r.tau for r in results["avar"]]
        for t in taus:
            assert t == pytest.approx(2 ** round(math.log2(t)))

    def test_freq_data_type(self, white_phase_noise):
        freq = np.diff(white_phase_noise) / 1.0
        results = compute_all(freq, tau_0=1.0, data_type="freq", estimators=["avar"])
        assert len(results["avar"]) > 0

    def test_unknown_estimator(self, white_phase_noise):
        with pytest.raises(ValueError, match="Unknown estimator"):
            compute_all(white_phase_noise, estimators=["bogus"])


# ===========================================================================
# Edge cases & input validation
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
        r = avar(x, m=1, tau_0=1.0)
        assert r.variance > 0

    def test_list_input_octave(self):
        x = [float(i) * 1e-9 for i in range(100)]
        results = avar_octave(x, tau_0=1.0)
        assert len(results) > 0
