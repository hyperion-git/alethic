from alethic.experiment.distributions import CalibratedDistributions


def test_round_trip_serialization():
    """Distributions survive JSON round-trip."""
    dists = CalibratedDistributions.default()
    json_str = dists.to_json()
    loaded = CalibratedDistributions.from_json(json_str)
    assert loaded.fixable_rate == dists.fixable_rate
    assert loaded.regression_rate == dists.regression_rate
    assert list(loaded.verdict_dist.keys()) == list(dists.verdict_dist.keys())


def test_quality_gate_passes():
    """Quality gate passes with low-variance data."""
    from alethic.experiment.distributions import check_quality_gate
    raw = {"confidence": [0.8, 0.82, 0.79, 0.81, 0.80]}
    result = check_quality_gate(CalibratedDistributions.default(), raw)
    assert result["passed"]


def test_quality_gate_fails():
    """Quality gate fails with high-variance data."""
    from alethic.experiment.distributions import check_quality_gate
    raw = {"confidence": [0.1, 0.9, 0.2, 0.8, 0.5]}
    result = check_quality_gate(CalibratedDistributions.default(), raw)
    assert not result["passed"]
    assert result["failures"][0]["metric"] == "confidence"


def test_fit_beta_from_samples():
    """Beta fitting produces valid parameters."""
    from alethic.experiment.distributions import fit_beta_from_samples
    params = fit_beta_from_samples([0.8, 0.85, 0.9, 0.78, 0.82])
    assert params.a > 0 and params.b > 0
    mean = params.a / (params.a + params.b)
    assert 0.7 < mean < 0.95


def test_classify_approach():
    """Approach classification combines atom hash + error category."""
    from alethic.experiment.distributions import classify_approach, compute_approach_count
    keys = [
        classify_approach("abc123", "algebra"),
        classify_approach("abc123", "algebra"),
        classify_approach("def456", "logic"),
    ]
    assert compute_approach_count(keys) == 2
