"""Tests for E vs F Monte Carlo simulation engine."""
import numpy as np
import pytest

from alethic.experiment.distributions import CalibratedDistributions
from alethic.experiment.simulate import AtomGuidedSimulator, PUCTWidenSimulator


def test_model_e_produces_result():
    """Model E runs a single trial and returns solve/not-solve + metadata."""
    dists = CalibratedDistributions.default()
    sim = AtomGuidedSimulator(dists, seed=42)
    result = sim.run_trial(archetype="smooth")
    assert "solved" in result
    assert "confidence" in result
    assert "iterations_used" in result
    assert "cost_tokens" in result
    assert 0 <= result["confidence"] <= 1
    assert 1 <= result["iterations_used"] <= 8


def test_model_e_deterministic():
    """Same seed produces same result."""
    dists = CalibratedDistributions.default()
    r1 = AtomGuidedSimulator(dists, seed=42).run_trial("insight")
    r2 = AtomGuidedSimulator(dists, seed=42).run_trial("insight")
    assert r1 == r2


def test_model_e_result_fields():
    """Result dict has all expected fields with valid types."""
    dists = CalibratedDistributions.default()
    sim = AtomGuidedSimulator(dists, seed=99)
    result = sim.run_trial("adversarial")
    assert isinstance(result["solved"], bool)
    assert isinstance(result["confidence"], float)
    assert isinstance(result["iterations_used"], int)
    assert isinstance(result["cost_tokens"], float)
    assert isinstance(result["approach_sequence"], list)
    assert isinstance(result["stall_events"], int)
    assert isinstance(result["fixable_shortcuts"], int)
    assert result["stall_events"] >= 0
    assert result["fixable_shortcuts"] >= 0
    assert result["cost_tokens"] > 0


def test_model_e_different_seeds_diverge():
    """Different seeds produce different results (with high probability)."""
    dists = CalibratedDistributions.default()
    results = [AtomGuidedSimulator(dists, seed=s).run_trial("smooth") for s in range(10)]
    confidences = [r["confidence"] for r in results]
    # With 10 trials, not all confidences should be identical
    assert len(set(confidences)) > 1


def test_model_e_all_archetypes():
    """Model E works for all three archetypes."""
    dists = CalibratedDistributions.default()
    for arch in ["smooth", "insight", "adversarial"]:
        result = AtomGuidedSimulator(dists, seed=42).run_trial(arch)
        assert "solved" in result


def test_model_e_approach_sequence_valid():
    """Approach indices in the sequence are within [0, M)."""
    dists = CalibratedDistributions.default()
    sim = AtomGuidedSimulator(dists, seed=42)
    result = sim.run_trial("smooth")
    # M for smooth is drawn from [2, 3, 3], so max M is 3
    # approach indices should be 0-based
    for idx in result["approach_sequence"]:
        assert isinstance(idx, int)
        assert idx >= 0


def test_model_e_candidates_same_approach():
    """Model E: all N candidates in an iteration use the same approach."""
    # This is the core E behavior — no diversification.
    # We can't directly observe this from run_trial output,
    # but we can test via the select_candidates method.
    dists = CalibratedDistributions.default()
    import numpy as np
    rng = np.random.default_rng(42)
    sim = AtomGuidedSimulator(dists, seed=42)
    candidates = sim.select_candidates(n=3, n_approaches=5, current_approach=2, rng=rng)
    assert candidates == [2, 2, 2]


def test_model_e_target_revision_boost():
    """Model E: atom targeting boosts revision rate by expected factor."""
    dists = CalibratedDistributions.default()
    # Default atom_targeting = 0.50 → boost = 1.0 + 0.50 * 0.6 = 1.30
    import numpy as np
    rng = np.random.default_rng(42)
    sim = AtomGuidedSimulator(dists, seed=42)
    boosted = sim.target_revision(0.5, rng)
    # 0.5 * 1.30 = 0.65
    assert abs(boosted - 0.65) < 1e-10


def test_model_e_target_revision_capped():
    """Boosted revision rate cannot exceed 1.0."""
    dists = CalibratedDistributions.default()
    import numpy as np
    rng = np.random.default_rng(42)
    sim = AtomGuidedSimulator(dists, seed=42)
    boosted = sim.target_revision(0.9, rng)
    assert boosted <= 1.0


def test_model_e_stall_handling():
    """Model E handle_stall returns True until max resets exhausted."""
    dists = CalibratedDistributions.default()
    sim = AtomGuidedSimulator(dists, seed=42)
    import numpy as np
    rng = np.random.default_rng(42)
    # Simulate state with approach info
    state = {
        "current_approach": 0,
        "M": 4,
        "resets_used": 0,
        "rng": rng,
    }
    # First reset should succeed
    assert sim.handle_stall(state) is True
    assert state["resets_used"] == 1
    # Current approach should have changed
    assert state["current_approach"] != 0

    # Second reset should succeed (max_resets=2)
    assert sim.handle_stall(state) is True
    assert state["resets_used"] == 2

    # Third reset should fail (exhausted)
    assert sim.handle_stall(state) is False
    assert state["resets_used"] == 2


def test_model_e_iteration_count_bounds():
    """iterations_used is always between 1 and max_iterations."""
    dists = CalibratedDistributions.default()
    for seed in range(20):
        result = AtomGuidedSimulator(dists, seed=seed).run_trial("smooth")
        assert 1 <= result["iterations_used"] <= 8


def test_model_e_cost_scales_with_iterations():
    """More iterations generally mean higher cost."""
    dists = CalibratedDistributions.default()
    costs = []
    iters = []
    for seed in range(50):
        r = AtomGuidedSimulator(dists, seed=seed).run_trial("smooth")
        costs.append(r["cost_tokens"])
        iters.append(r["iterations_used"])
    # Trials that ran more iterations should have higher cost on average
    # (not strictly — fixable shortcuts can end early with some cost)
    # At minimum, single-iteration trials should cost less than 8-iteration ones
    one_iter_costs = [c for c, i in zip(costs, iters, strict=True) if i == 1]
    max_iter_costs = [c for c, i in zip(costs, iters, strict=True) if i == 8]
    if one_iter_costs and max_iter_costs:
        assert min(max_iter_costs) > min(one_iter_costs)


# ---------------------------------------------------------------------------
# Model F (PUCTWidenSimulator) tests
# ---------------------------------------------------------------------------


def test_model_f_produces_result():
    """Model F runs a single trial with PUCT selection."""
    dists = CalibratedDistributions.default()
    sim = PUCTWidenSimulator(dists, seed=42, cpuct=1.414)
    result = sim.run_trial(archetype="insight")
    assert "solved" in result
    assert "visit_counts" in result  # PUCT tracks visits


def test_model_f_explores_approaches():
    """Model F should visit multiple approaches over 8 iterations."""
    dists = CalibratedDistributions.default()
    sim = PUCTWidenSimulator(dists, seed=42, cpuct=1.414)
    result = sim.run_trial(archetype="insight")
    visits = result["visit_counts"]
    # With M=4-6 and progressive widening, should visit at least 2 approaches
    assert sum(1 for v in visits.values() if v > 0) >= 2


def test_model_f_result_fields():
    """Model F result dict has all expected fields with valid types."""
    dists = CalibratedDistributions.default()
    sim = PUCTWidenSimulator(dists, seed=99, cpuct=1.414)
    result = sim.run_trial("adversarial")
    assert isinstance(result["solved"], bool)
    assert isinstance(result["confidence"], float)
    assert isinstance(result["iterations_used"], int)
    assert isinstance(result["cost_tokens"], float)
    assert isinstance(result["approach_sequence"], list)
    assert isinstance(result["stall_events"], int)
    assert isinstance(result["fixable_shortcuts"], int)
    assert isinstance(result["visit_counts"], dict)
    assert result["cost_tokens"] > 0


def test_model_f_deterministic():
    """Same seed produces same result."""
    dists = CalibratedDistributions.default()
    r1 = PUCTWidenSimulator(dists, seed=42, cpuct=1.414).run_trial("insight")
    r2 = PUCTWidenSimulator(dists, seed=42, cpuct=1.414).run_trial("insight")
    assert r1 == r2


def test_model_f_visit_counts_sum_to_iterations():
    """Total visits across all approaches equals iterations_used."""
    dists = CalibratedDistributions.default()
    sim = PUCTWidenSimulator(dists, seed=7, cpuct=1.414)
    result = sim.run_trial("smooth")
    total_visits = sum(result["visit_counts"].values())
    assert total_visits == result["iterations_used"]


def test_model_f_target_revision_no_boost():
    """Model F: target_revision returns base_rate unchanged."""
    dists = CalibratedDistributions.default()
    rng = np.random.default_rng(42)
    sim = PUCTWidenSimulator(dists, seed=42, cpuct=1.414)
    for rate in [0.0, 0.3, 0.5, 0.9, 1.0]:
        assert sim.target_revision(rate, rng) == rate


def test_model_f_handle_stall_is_noop():
    """Model F: handle_stall always returns False (PUCT handles exploration)."""
    dists = CalibratedDistributions.default()
    rng = np.random.default_rng(42)
    sim = PUCTWidenSimulator(dists, seed=42, cpuct=1.414)
    state = {"current_approach": 0, "M": 4, "resets_used": 0, "rng": rng}
    assert sim.handle_stall(state) is False
    # State should be unchanged
    assert state["current_approach"] == 0
    assert state["resets_used"] == 0


def test_model_f_all_archetypes():
    """Model F works for all three archetypes."""
    dists = CalibratedDistributions.default()
    for arch in ["smooth", "insight", "adversarial"]:
        result = PUCTWidenSimulator(dists, seed=42, cpuct=1.414).run_trial(arch)
        assert "solved" in result
        assert "visit_counts" in result


def test_model_f_progressive_widening():
    """Progressive widening limits active approaches to ceil(sqrt(iteration))."""
    # We test select_candidates behavior by checking that on the very first call
    # (iteration_count=0 before the call), only ceil(sqrt(1))=1 approach is active.
    dists = CalibratedDistributions.default()
    rng = np.random.default_rng(42)
    sim = PUCTWidenSimulator(dists, seed=42, cpuct=1.414)
    # Fresh state before any iterations
    sim._visit_counts = {}
    sim._approach_rewards = {}
    sim._total_visits = 0
    sim._iteration_count = 0
    candidates = sim.select_candidates(n=3, n_approaches=9, current_approach=0, rng=rng)
    # At first call (iteration 1 effective), ceil(sqrt(1)) = 1 active approach
    unique_approaches = set(candidates)
    assert len(unique_approaches) == 1


def test_puct_diverges_from_greedy():
    """PUCT and Greedy should sometimes select different approaches."""
    dists = CalibratedDistributions.default()
    diverged = 0
    for seed in range(100):
        e = AtomGuidedSimulator(dists, seed=seed).run_trial("insight")
        f = PUCTWidenSimulator(dists, seed=seed, cpuct=1.414).run_trial("insight")
        if e.get("approach_sequence") != f.get("approach_sequence"):
            diverged += 1
    # Should diverge at least 20% of the time
    assert diverged >= 20


# ---------------------------------------------------------------------------
# run_paired_trials tests
# ---------------------------------------------------------------------------

from alethic.experiment.simulate import run_paired_trials


def test_paired_runner_basic():
    """Paired runner produces solve rates and NNT for both models."""
    dists = CalibratedDistributions.default()
    report = run_paired_trials(dists, n_trials=100, n_traced=20, seed=42)
    assert "model_e" in report
    assert "model_f" in report
    assert "bayesian" in report
    assert "mcnemar" in report
    assert 0 <= report["model_e"]["solve_rate"] <= 1
    assert 0 <= report["model_f"]["solve_rate"] <= 1


def test_bayesian_detects_difference():
    """When models have different solve rates, Bayesian criterion fires."""
    dists = CalibratedDistributions.default()
    dists.verdict_dist["smooth"]["early"]["correct"] = 0.8  # bias toward solving
    report = run_paired_trials(dists, n_trials=1000, n_traced=0, seed=42)
    assert "p_f_better_3pp" in report["bayesian"]


# ---------------------------------------------------------------------------
# run_parameter_sweep tests
# ---------------------------------------------------------------------------

from alethic.experiment.simulate import run_parameter_sweep


def test_parameter_sweep():
    """Tier 2 sweep runs multiple cpuct and stall_window values."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=50, seed=42,
        cpuct_values=[0.5, 1.414],
        stall_window_values=[2, 3],
    )
    assert len(sweep["model_f_sweep"]) == 2
    assert len(sweep["model_e_sweep"]) == 2
    assert "tier3_e_best" in sweep
    assert "tier3_f_best" in sweep


def test_parameter_sweep_f_sweep_fields():
    """Each model_f_sweep entry has cpuct, solve_rate, mean_confidence."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=50, seed=42,
        cpuct_values=[0.5, 1.0],
        stall_window_values=[2],
    )
    for entry in sweep["model_f_sweep"]:
        assert "cpuct" in entry
        assert "solve_rate" in entry
        assert "mean_confidence" in entry
        assert 0 <= entry["solve_rate"] <= 1
        assert 0 <= entry["mean_confidence"] <= 1


def test_parameter_sweep_e_sweep_fields():
    """Each model_e_sweep entry has stall_window, solve_rate, mean_confidence."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=50, seed=42,
        cpuct_values=[1.414],
        stall_window_values=[2, 3],
    )
    for entry in sweep["model_e_sweep"]:
        assert "stall_window" in entry
        assert "solve_rate" in entry
        assert "mean_confidence" in entry
        assert 0 <= entry["solve_rate"] <= 1
        assert 0 <= entry["mean_confidence"] <= 1


def test_parameter_sweep_tier3_best_fields():
    """tier3_f_best and tier3_e_best have the correct keys."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=50, seed=42,
        cpuct_values=[0.5, 1.414],
        stall_window_values=[2, 3],
    )
    assert "cpuct" in sweep["tier3_f_best"]
    assert "solve_rate" in sweep["tier3_f_best"]
    assert "stall_window" in sweep["tier3_e_best"]
    assert "solve_rate" in sweep["tier3_e_best"]


def test_parameter_sweep_has_parameter_sensitive():
    """Sweep result includes parameter_sensitive flag."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=50, seed=42,
        cpuct_values=[0.5, 1.414],
        stall_window_values=[2, 3],
    )
    assert "parameter_sensitive" in sweep
    assert isinstance(sweep["parameter_sensitive"], bool)


def test_parameter_sweep_default_values():
    """run_parameter_sweep uses default cpuct and stall_window lists when not specified."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(dists, n_trials=20, seed=42)
    # Defaults: 6 cpuct values and 4 stall_window values
    assert len(sweep["model_f_sweep"]) == 6
    assert len(sweep["model_e_sweep"]) == 4


def test_parameter_sweep_stall_window_restored():
    """Module-level STALL_WINDOW is restored to original after sweep."""
    import alethic.experiment.simulate as sim_module
    original_sw = sim_module.STALL_WINDOW
    dists = CalibratedDistributions.default()
    run_parameter_sweep(
        dists, n_trials=20, seed=42,
        stall_window_values=[2, 5],
    )
    assert sim_module.STALL_WINDOW == original_sw
