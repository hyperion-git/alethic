"""Validation criteria for Phase 3 of E vs F experiment."""
from __future__ import annotations

from scipy.stats import spearmanr  # type: ignore[import-untyped]


def check_validation_criteria(
    sim_rates: list[float],
    real_rates: list[float],
    aggregate_sim: float,
    aggregate_real: float,
    aggregate_threshold: float = 0.15,
    spearman_threshold: float = 0.3,
) -> dict:
    """Check validation criteria against Phase 2 simulation predictions.

    Three criteria:
    1. Aggregate solve rate: simulation within +/-threshold of observed.
    2. Spearman rank-order correlation: rho > spearman_threshold.
    3. Difficulty-bin ordering: easy-bin > hard-bin solve rate.

    Args:
        sim_rates: Per-problem predicted solve rates from Phase 2 simulation.
        real_rates: Per-problem observed solve rates from Phase 3 probes.
        aggregate_sim: Overall aggregate solve rate from simulation.
        aggregate_real: Overall aggregate solve rate observed in real probes.
        aggregate_threshold: Maximum allowed |aggregate_sim - aggregate_real|.
        spearman_threshold: Minimum acceptable Spearman rho.

    Returns:
        Dict with per-criterion results and an ``overall_passed`` key.
    """
    # Criterion 1: Aggregate solve rate within threshold
    aggregate_delta = abs(aggregate_sim - aggregate_real)
    aggregate_passed = aggregate_delta <= aggregate_threshold

    # Criterion 2: Spearman rank-order correlation
    if len(sim_rates) >= 3 and len(real_rates) >= 3:
        rho, p_value = spearmanr(sim_rates, real_rates)
        spearman_passed = float(rho) > spearman_threshold
    else:
        rho, p_value = 0.0, 1.0
        spearman_passed = False

    # Criterion 3: Difficulty-bin ordering
    # Sort problems by simulation difficulty (ascending sim_rate → hard first).
    # Easy bin = top half (higher sim_rate), hard bin = bottom half.
    n = len(real_rates)
    if n >= 4:
        sorted_indices = sorted(range(n), key=lambda i: sim_rates[i])
        mid = n // 2
        hard_bin = [real_rates[i] for i in sorted_indices[:mid]]
        easy_bin = [real_rates[i] for i in sorted_indices[mid:]]
        easy_mean = sum(easy_bin) / len(easy_bin) if easy_bin else 0.0
        hard_mean = sum(hard_bin) / len(hard_bin) if hard_bin else 0.0
        difficulty_passed = easy_mean > hard_mean
    else:
        easy_mean, hard_mean = 0.0, 0.0
        difficulty_passed = True  # not enough data to test

    return {
        "aggregate_passed": aggregate_passed,
        "aggregate_delta": float(aggregate_delta),
        "spearman_passed": spearman_passed,
        "spearman_rho": float(rho),
        "spearman_p_value": float(p_value),
        "difficulty_passed": difficulty_passed,
        "easy_bin_rate": float(easy_mean),
        "hard_bin_rate": float(hard_mean),
        "overall_passed": aggregate_passed and spearman_passed and difficulty_passed,
    }
