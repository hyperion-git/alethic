"""Tests for E vs F Monte Carlo simulation engine."""
from alethic.experiment.distributions import CalibratedDistributions
from alethic.experiment.simulate import AtomGuidedSimulator


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
