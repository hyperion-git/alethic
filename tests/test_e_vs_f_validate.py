"""Tests for Phase 3 validation criteria (check_validation_criteria)."""
from __future__ import annotations

import pytest

from alethic.experiment.validate import check_validation_criteria


def test_validation_passes_when_close():
    """Validation passes when simulation is within +/-15pp and Spearman > 0.3."""
    sim_rates = [0.8, 0.7, 0.6, 0.5, 0.4, 0.9, 0.3, 0.75, 0.65, 0.55]
    real_rates = [0.75, 0.65, 0.55, 0.45, 0.35, 0.85, 0.25, 0.70, 0.60, 0.50]
    result = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.62, aggregate_real=0.56,
    )
    assert result["aggregate_passed"]   # |0.62 - 0.56| = 0.06 < 0.15
    assert result["spearman_passed"]    # ranks are well-correlated


def test_validation_fails_when_far():
    """Validation fails when aggregate differs by >15pp."""
    result = check_validation_criteria(
        [0.5] * 10, [0.5] * 10,
        aggregate_sim=0.80, aggregate_real=0.50,
    )
    assert not result["aggregate_passed"]  # |0.80 - 0.50| = 0.30 > 0.15


def test_aggregate_delta_at_threshold():
    """Validation passes when aggregate delta is clearly within threshold."""
    result = check_validation_criteria(
        [0.5] * 10, [0.5] * 10,
        aggregate_sim=0.60, aggregate_real=0.50,  # delta = 0.10 < 0.15
    )
    assert result["aggregate_passed"]  # 0.10 <= 0.15 passes


def test_aggregate_delta_just_above_threshold():
    """Validation fails when aggregate delta is just above the threshold."""
    result = check_validation_criteria(
        [0.5] * 10, [0.5] * 10,
        aggregate_sim=0.651, aggregate_real=0.50,  # delta = 0.151 > 0.15
    )
    assert not result["aggregate_passed"]


def test_spearman_fails_on_anticorrelated():
    """Validation Spearman criterion fails on perfectly anti-correlated rates."""
    sim_rates = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    real_rates = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    result = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.55, aggregate_real=0.55,
    )
    assert not result["spearman_passed"]  # rho = -1.0 < 0.3


def test_spearman_passes_on_well_correlated():
    """Validation Spearman criterion passes on perfectly correlated rates."""
    sim_rates = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    real_rates = [0.12, 0.18, 0.32, 0.38, 0.52, 0.58, 0.72, 0.78, 0.88, 0.95]
    result = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.55, aggregate_real=0.54,
    )
    assert result["spearman_passed"]  # strong positive correlation


def test_difficulty_bin_ordering_passed_when_easy_gt_hard():
    """Difficulty bin criterion passes when easy bin has higher solve rate than hard bin."""
    # sim_rates: higher = easier; sort ascending → first half hard, second half easy
    sim_rates = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]
    # real_rates track the same ordering
    real_rates = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]
    result = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.5, aggregate_real=0.5,
    )
    assert result["difficulty_passed"]
    assert result["easy_bin_rate"] > result["hard_bin_rate"]


def test_difficulty_bin_ordering_fails_when_easy_lt_hard():
    """Difficulty bin criterion fails when easy bin has lower solve rate than hard bin."""
    # sim_rates say problems 0-3 are easy (high sim_rate), but real_rates are inverted
    sim_rates = [0.9, 0.8, 0.7, 0.6, 0.4, 0.3, 0.2, 0.1]
    # real_rates: the "easy" problems (high sim_rate) actually have low real solve rates
    real_rates = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]
    result = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.5, aggregate_real=0.5,
    )
    assert not result["difficulty_passed"]


def test_overall_passed_requires_all_criteria():
    """overall_passed is True only when all three criteria pass."""
    # All pass
    sim_rates = [0.8, 0.7, 0.6, 0.5, 0.4, 0.9, 0.3, 0.75, 0.65, 0.55]
    real_rates = [0.75, 0.65, 0.55, 0.45, 0.35, 0.85, 0.25, 0.70, 0.60, 0.50]
    result_pass = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.62, aggregate_real=0.56,
    )
    assert result_pass["overall_passed"]

    # Aggregate fails
    result_fail = check_validation_criteria(
        sim_rates, real_rates,
        aggregate_sim=0.80, aggregate_real=0.50,
    )
    assert not result_fail["overall_passed"]


def test_result_keys_present():
    """Result dict contains all required keys."""
    result = check_validation_criteria(
        [0.5] * 5, [0.5] * 5,
        aggregate_sim=0.5, aggregate_real=0.5,
    )
    required_keys = {
        "aggregate_passed",
        "aggregate_delta",
        "spearman_passed",
        "spearman_rho",
        "spearman_p_value",
        "difficulty_passed",
        "easy_bin_rate",
        "hard_bin_rate",
        "overall_passed",
    }
    assert required_keys.issubset(result.keys())


def test_custom_thresholds():
    """Custom thresholds are respected."""
    result = check_validation_criteria(
        [0.5] * 10, [0.5] * 10,
        aggregate_sim=0.60, aggregate_real=0.50,  # delta = 0.10
        aggregate_threshold=0.05,  # stricter: 0.10 > 0.05 → fail
        spearman_threshold=0.3,
    )
    assert not result["aggregate_passed"]

    result2 = check_validation_criteria(
        [0.5] * 10, [0.5] * 10,
        aggregate_sim=0.60, aggregate_real=0.50,  # delta = 0.10
        aggregate_threshold=0.20,  # relaxed: 0.10 <= 0.20 → pass
        spearman_threshold=0.3,
    )
    assert result2["aggregate_passed"]


def test_too_few_samples_spearman_not_passed():
    """With fewer than 3 samples, Spearman criterion returns not passed."""
    result = check_validation_criteria(
        [0.5, 0.6], [0.5, 0.6],
        aggregate_sim=0.55, aggregate_real=0.55,
    )
    assert not result["spearman_passed"]
    assert result["spearman_rho"] == 0.0


def test_too_few_samples_difficulty_bin_skipped():
    """With fewer than 4 samples, difficulty bin criterion defaults to True (not enough data)."""
    result = check_validation_criteria(
        [0.5, 0.6, 0.7], [0.5, 0.6, 0.7],
        aggregate_sim=0.6, aggregate_real=0.6,
    )
    assert result["difficulty_passed"]  # not enough data to test
