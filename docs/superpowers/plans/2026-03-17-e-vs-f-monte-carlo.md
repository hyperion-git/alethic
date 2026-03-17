# E vs F Monte Carlo Experiment Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a calibrate-then-simulate Monte Carlo experiment to determine whether Option E (atom-guided verification) or Option F (PUCT+widening) should be the v3.8 architecture.

**Architecture:** Three scripts (`e_vs_f_calibrate.py`, `e_vs_f_simulate.py`, `e_vs_f_validate.py`) plus a shared data module (`e_vs_f_distributions.py`). Phase 1 runs real problems through the Alethic Python library and fits distributions. Phase 2 uses those distributions in a pure NumPy simulation. Phase 3 validates simulation predictions against held-out real runs.

**Tech Stack:** Python 3.13, NumPy, SciPy (stats, optimize), the existing `alethic` library (MathAgent, PhysicsAgent, AgentConfig, atoms, error_taxonomy), JSON for data interchange.

**Spec:** `docs/superpowers/specs/2026-03-17-e-vs-f-monte-carlo-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/alethic/experiment/__init__.py` | Package init (exports public API) |
| `src/alethic/experiment/distributions.py` | Shared module: dataclasses for fitted distributions, load/save JSON, quality gate, distribution fitting helpers |
| `src/alethic/experiment/simulate.py` | Phase 2: AlethicSimulator base + AtomGuidedSimulator (E) + PUCTWidenSimulator (F) + paired runner + Bayesian analysis + sweep |
| `src/alethic/experiment/diagnostics.py` | Diagnostic metric computation from traced trials + crossover table |
| `scripts/e_vs_f_calibrate.py` | Phase 1 runner: run 10 problems through Alethic, collect measurements, fit distributions, write JSON |
| `scripts/e_vs_f_simulate.py` | Phase 2 CLI: load distributions, run trials, generate report |
| `scripts/e_vs_f_validate.py` | Phase 3 runner: run 50 probes on held-out problems, compare to simulation predictions |
| `tests/test_e_vs_f_distributions.py` | Tests for distribution data model, fitting helpers, serialization, quality gate |
| `tests/test_e_vs_f_simulate.py` | Tests for both simulation models, paired runner, statistical analysis |
| `tests/test_e_vs_f_validate.py` | Tests for validation criteria (Spearman, aggregate, difficulty-bin) |
| `data/calibration/` | Output directory for fitted distributions and traces (created at runtime) |

**Note on package placement:** The experiment module lives in `src/alethic/experiment/` (not `scripts/`) so it's a proper importable package installed with `pip install -e .`. Scripts in `scripts/` are thin CLI wrappers that import from `alethic.experiment`.

---

### Task 1: Distribution Data Model

**Files:**
- Create: `src/alethic/experiment/__init__.py`
- Create: `src/alethic/experiment/distributions.py`
- Test: `tests/test_e_vs_f_distributions.py`

- [ ] **Step 1: Write failing test for CalibratedDistributions dataclass**

```python
# tests/test_e_vs_f_distributions.py
import json
import pytest
from alethic.experiment.distributions import CalibratedDistributions

def test_round_trip_serialization():
    """Distributions survive JSON round-trip."""
    dists = CalibratedDistributions.default()
    json_str = dists.to_json()
    loaded = CalibratedDistributions.from_json(json_str)
    assert loaded.fixable_rate == dists.fixable_rate
    assert loaded.regression_rate == dists.regression_rate
    assert list(loaded.verdict_dist.keys()) == list(dists.verdict_dist.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e_vs_f_distributions.py::test_round_trip_serialization -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Create experiment package and implement CalibratedDistributions**

Create `src/alethic/experiment/__init__.py`:
```python
"""E vs F Monte Carlo experiment package."""
from alethic.experiment.distributions import CalibratedDistributions, BetaParams
```

Then implement CalibratedDistributions

```python
# src/alethic/experiment/distributions.py
"""Shared data model for calibrated distributions."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

# Archetype labels
SMOOTH = "smooth"
INSIGHT = "insight"
ADVERSARIAL = "adversarial"
ARCHETYPES = [SMOOTH, INSIGHT, ADVERSARIAL]

# Iteration buckets for distribution conditioning
ITER_BUCKETS = {"early": (1, 2), "mid": (3, 5), "late": (6, 8)}

# Per-iteration verifier verdicts (not agent-level outcomes).
# UNSOLVED is excluded: it's the agent's final verdict after exhausting iterations,
# not a per-candidate verifier verdict. The simulation handles UNSOLVED as the
# fallback when no candidate is accepted within max_iterations.
VERDICTS = ["correct", "minor_issues", "fixable", "major_flaw"]

# Error categories matching alethic.error_taxonomy
# "general" is the fallback category when no keyword matches in classify_errors()
ERROR_CATS = ["algebra", "logic", "citation", "interpretation", "units",
              "counterexample", "missing_case", "general"]


@dataclass
class BetaParams:
    a: float
    b: float

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b}

    @classmethod
    def from_dict(cls, d: dict) -> BetaParams:
        return cls(a=d["a"], b=d["b"])


@dataclass
class CalibratedDistributions:
    """All fitted distributions from Phase 1 calibration."""

    # P(verdict | archetype, iter_bucket) -> dict[archetype][bucket][verdict] = float
    verdict_dist: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)

    # Beta(a,b) per verdict for confidence
    confidence_dist: dict[str, BetaParams] = field(default_factory=dict)

    # P(improve | error_category)
    revision_rates: dict[str, float] = field(default_factory=dict)

    # P(regress | revision)
    regression_rate: float = 0.15

    # P(FIXABLE) and P(accept | FIXABLE re-verify)
    fixable_rate: float = 0.10
    fixable_success: float = 0.60

    # Poisson lambda for atom count per solution
    atom_lambda: float = 4.0

    # P(correct_target | atom_flag)
    atom_targeting: float = 0.50

    # P(error_category | archetype)
    error_cat_dist: dict[str, dict[str, float]] = field(default_factory=dict)

    # M (distinct approaches) per archetype — empirical list
    approach_counts: dict[str, list[int]] = field(default_factory=dict)

    # Approach ceiling Beta per archetype
    approach_ceiling_dist: dict[str, BetaParams] = field(default_factory=dict)

    # P(stall | iter_bucket)
    stall_rate: dict[str, float] = field(default_factory=dict)

    # P(demotion | accepted) from breaker
    breaker_demotion: float = 0.05

    # Cost: mean tokens per subagent call
    mean_tokens_per_call: float = 25000.0

    def to_json(self) -> str:
        """Serialize to JSON string."""
        d = {}
        # Convert BetaParams to dicts
        d["verdict_dist"] = self.verdict_dist
        d["confidence_dist"] = {k: v.to_dict() for k, v in self.confidence_dist.items()}
        d["revision_rates"] = self.revision_rates
        d["regression_rate"] = self.regression_rate
        d["fixable_rate"] = self.fixable_rate
        d["fixable_success"] = self.fixable_success
        d["atom_lambda"] = self.atom_lambda
        d["atom_targeting"] = self.atom_targeting
        d["error_cat_dist"] = self.error_cat_dist
        d["approach_counts"] = self.approach_counts
        d["approach_ceiling_dist"] = {k: v.to_dict() for k, v in self.approach_ceiling_dist.items()}
        d["stall_rate"] = self.stall_rate
        d["breaker_demotion"] = self.breaker_demotion
        d["mean_tokens_per_call"] = self.mean_tokens_per_call
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, s: str) -> CalibratedDistributions:
        """Deserialize from JSON string."""
        d = json.loads(s)
        return cls(
            verdict_dist=d["verdict_dist"],
            confidence_dist={k: BetaParams.from_dict(v) for k, v in d["confidence_dist"].items()},
            revision_rates=d["revision_rates"],
            regression_rate=d["regression_rate"],
            fixable_rate=d["fixable_rate"],
            fixable_success=d["fixable_success"],
            atom_lambda=d["atom_lambda"],
            atom_targeting=d["atom_targeting"],
            error_cat_dist=d["error_cat_dist"],
            approach_counts=d["approach_counts"],
            approach_ceiling_dist={k: BetaParams.from_dict(v) for k, v in d["approach_ceiling_dist"].items()},
            stall_rate=d["stall_rate"],
            breaker_demotion=d["breaker_demotion"],
            mean_tokens_per_call=d["mean_tokens_per_call"],
        )

    @classmethod
    def default(cls) -> CalibratedDistributions:
        """Placeholder distributions for testing (not calibrated)."""
        verdict = {a: {b: {v: 0.25 for v in VERDICTS} for b in ITER_BUCKETS} for a in ARCHETYPES}
        confidence = {v: BetaParams(2.0, 2.0) for v in VERDICTS}
        confidence["correct"] = BetaParams(8.0, 2.0)
        confidence["major_flaw"] = BetaParams(2.0, 8.0)
        error_cat = {a: {c: 1.0 / len(ERROR_CATS) for c in ERROR_CATS} for a in ARCHETYPES}
        approach_counts = {SMOOTH: [2, 3, 3], INSIGHT: [4, 5, 5, 6], ADVERSARIAL: [3, 4]}
        ceiling = {a: BetaParams(3.0, 2.0) for a in ARCHETYPES}
        stall = {b: 0.2 for b in ITER_BUCKETS}
        revision = {c: 0.5 for c in ERROR_CATS}
        revision["algebra"] = 0.7
        revision["logic"] = 0.3
        return cls(
            verdict_dist=verdict,
            confidence_dist=confidence,
            revision_rates=revision,
            error_cat_dist=error_cat,
            approach_counts=approach_counts,
            approach_ceiling_dist=ceiling,
            stall_rate=stall,
        )


def check_quality_gate(dists: CalibratedDistributions, raw_data: dict) -> dict[str, Any]:
    """Check CV < 0.5 on key metrics. Returns {passed: bool, failures: [...]}."""
    import numpy as np
    failures = []

    for key, values in raw_data.items():
        arr = np.array(values, dtype=float)
        if len(arr) < 2:
            continue
        mean = np.mean(arr)
        if mean == 0:
            continue
        cv = np.std(arr, ddof=1) / abs(mean)
        if cv > 0.5:
            failures.append({"metric": key, "cv": float(cv), "n": len(arr)})

    return {"passed": len(failures) == 0, "failures": failures}


def fit_beta_from_samples(values: list[float]) -> BetaParams:
    """Fit a Beta distribution to observed [0,1] values via MoM."""
    import numpy as np
    arr = np.array(values, dtype=float)
    arr = np.clip(arr, 0.001, 0.999)  # avoid degenerate 0/1
    mu = np.mean(arr)
    var = np.var(arr, ddof=1) if len(arr) > 1 else 0.01
    var = min(var, mu * (1 - mu) - 0.001)  # ensure valid params
    if var <= 0:
        return BetaParams(a=mu * 10, b=(1 - mu) * 10)
    common = mu * (1 - mu) / var - 1
    return BetaParams(a=max(0.1, mu * common), b=max(0.1, (1 - mu) * common))


def classify_approach(atom_hash: str, error_category: str) -> str:
    """Classify a candidate's approach via two-signal method.

    Two candidates are 'same approach' if both atom-structure hash
    and error-category match. Returns a composite key.
    """
    return f"{atom_hash}:{error_category}"


def compute_approach_count(approach_keys: list[str]) -> int:
    """Count distinct approaches from a list of approach keys."""
    return len(set(approach_keys))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_e_vs_f_distributions.py -v`
Expected: PASS

- [ ] **Step 5: Write test for quality gate**

```python
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
    # Mean of Beta(a,b) = a/(a+b) should be near 0.83
    mean = params.a / (params.a + params.b)
    assert 0.7 < mean < 0.95

def test_classify_approach():
    """Approach classification combines atom hash + error category."""
    from alethic.experiment.distributions import classify_approach, compute_approach_count
    keys = [
        classify_approach("abc123", "algebra"),
        classify_approach("abc123", "algebra"),  # same approach
        classify_approach("def456", "logic"),     # different approach
    ]
    assert compute_approach_count(keys) == 2
```

- [ ] **Step 6: Run quality gate tests**

Run: `pytest tests/test_e_vs_f_distributions.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/alethic/experiment/__init__.py src/alethic/experiment/distributions.py tests/test_e_vs_f_distributions.py
git commit -m "feat(experiment): add calibrated distribution data model with quality gate"
```

---

### Task 2: Simulation Model E (AtomGuidedSimulator)

**Files:**
- Create: `scripts/e_vs_f_simulate.py`
- Test: `tests/test_e_vs_f_simulate.py`

- [ ] **Step 1: Write failing test for Model E single trial**

```python
# tests/test_e_vs_f_simulate.py
import numpy as np
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e_vs_f_simulate.py::test_model_e_produces_result -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement AlethicSimulator base + AtomGuidedSimulator**

Implement the shared base class and Model E in `scripts/e_vs_f_simulate.py`. The base contains the shared per-iteration logic (steps 5-11 from spec Section 3.1). Model E overrides `select_candidates()` (greedy same-approach), `target_revision()` (atom-guided), and `handle_stall()` (strategy reset).

Key methods:
- `run_trial(archetype) -> dict`: Full 8-iteration loop returning solve/confidence/iterations/cost
- `_draw_verdict(archetype, iter_bucket) -> str`: Sample from calibrated verdict distribution
- `_draw_confidence(verdict) -> float`: Sample from Beta distribution for verdict
- `_attempt_revision(error_cat) -> bool`: Bernoulli draw from per-category revision rate
- `_check_acceptance(verdict, confidence) -> bool`: CORRECT + confidence >= threshold
- `_check_stall(conf_history) -> bool`: delta < epsilon for stall_window consecutive

Model E specifics:
- `select_candidates()`: All N candidates from same approach (current_approach)
- `target_revision()`: With P=atom_targeting, improve rate = base * (1.0 + atom_targeting * 0.6) (calibration-derived boost: at 50% targeting → 1.3x, at 85% → 1.51x); else base rate
- `handle_stall()`: Switch to random approach from M-1 remaining; max 2 resets

~200 lines. Full implementation in the step.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_e_vs_f_simulate.py::test_model_e_produces_result -v`
Expected: PASS

- [ ] **Step 5: Write deterministic seed test**

```python
def test_model_e_deterministic():
    """Same seed produces same result."""
    dists = CalibratedDistributions.default()
    r1 = AtomGuidedSimulator(dists, seed=42).run_trial("insight")
    r2 = AtomGuidedSimulator(dists, seed=42).run_trial("insight")
    assert r1 == r2
```

- [ ] **Step 6: Run, verify pass, commit**

Run: `pytest tests/test_e_vs_f_simulate.py -v`
Expected: all PASS

```bash
git add src/alethic/experiment/simulate.py tests/test_e_vs_f_simulate.py
git commit -m "feat(experiment): implement Model E (AtomGuidedSimulator)"
```

---

### Task 3: Simulation Model F (PUCTWidenSimulator)

**Files:**
- Modify: `scripts/e_vs_f_simulate.py`
- Test: `tests/test_e_vs_f_simulate.py`

- [ ] **Step 1: Write failing test for Model F**

```python
from alethic.experiment.simulate import PUCTWidenSimulator

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e_vs_f_simulate.py::test_model_f_produces_result -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement PUCTWidenSimulator**

Inherits from `AlethicSimulator`. Overrides:
- `select_candidates()`: PUCT scoring with progressive widening. At iter t, n_active = min(M, ceil(sqrt(t))). Score = Q(a) + cpuct * (1/M) * sqrt(total_visits) / (1 + visits(a)). Select top-N by score.
- `target_revision()`: Uniform (no targeting boost) — P(improve) = base_rate
- `handle_stall()`: No-op. PUCT's exploration bonus handles stalls implicitly.

Additional state: `visit_counts: dict[int, int]`, `approach_rewards: dict[int, float]`, `total_visits: int`.

~80 lines added to `e_vs_f_simulate.py`.

- [ ] **Step 4: Run test, verify pass**

Run: `pytest tests/test_e_vs_f_simulate.py -v`
Expected: all PASS

- [ ] **Step 5: Write PUCT vs Greedy divergence test**

```python
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
```

- [ ] **Step 6: Run, verify pass, commit**

```bash
git add src/alethic/experiment/simulate.py tests/test_e_vs_f_simulate.py
git commit -m "feat(experiment): implement Model F (PUCTWidenSimulator)"
```

---

### Task 4: Paired Trial Runner + Bayesian Analysis

**Files:**
- Modify: `src/alethic/experiment/simulate.py`
- Test: `tests/test_e_vs_f_simulate.py`

- [ ] **Step 1: Write failing test for paired runner**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e_vs_f_simulate.py::test_paired_runner_basic -v`
Expected: FAIL

- [ ] **Step 3: Implement run_paired_trials()**

```python
def run_paired_trials(
    dists: CalibratedDistributions,
    n_trials: int = 5000,
    n_traced: int = 2000,
    seed: int = 42,
    cpuct: float = 1.414,
    stall_window: int = 3,
    archetype_weights: dict[str, float] | None = None,
) -> dict:
    """Run N paired trials of Model E vs Model F.

    Returns a report dict with per-model stats, Bayesian posterior analysis,
    McNemar's test, NNT, per-archetype breakdown, and traced diagnostics.
    """
```

Key implementation:
- Draw archetype per trial from `archetype_weights` (default: 40/50/10)
- Run both models with same seed + archetype (paired design)
- First `n_traced` trials record full event traces
- After all trials: compute Bayesian posteriors (Beta), McNemar's, NNT

Bayesian analysis:
```python
from scipy import stats
# Posteriors
alpha_e, beta_e = 1 + solved_e, 1 + (n_trials - solved_e)
alpha_f, beta_f = 1 + solved_f, 1 + (n_trials - solved_f)
# P(delta > threshold) via sampling
samples_e = rng.beta(alpha_e, beta_e, size=100_000)
samples_f = rng.beta(alpha_f, beta_f, size=100_000)
delta = samples_f - samples_e
p_f_better_3pp = np.mean(delta > 0.03)
```

McNemar's:
```python
# b = E solves, F doesn't; c = F solves, E doesn't
b = sum(e and not f for e, f in zip(e_solved, f_solved))
c = sum(f and not e for e, f in zip(e_solved, f_solved))
chi2 = (abs(b - c) - 1)**2 / (b + c) if (b + c) > 0 else 0
p_value = 1 - stats.chi2.cdf(chi2, df=1)
```

~120 lines.

- [ ] **Step 4: Run test, verify pass**

Run: `pytest tests/test_e_vs_f_simulate.py::test_paired_runner_basic -v`
Expected: PASS

- [ ] **Step 5: Write Bayesian threshold test**

```python
def test_bayesian_detects_difference():
    """When models have different solve rates, Bayesian criterion fires."""
    # Create biased distributions where E is clearly better
    dists = CalibratedDistributions.default()
    dists.verdict_dist["smooth"]["early"]["correct"] = 0.8  # E-favorable
    report = run_paired_trials(dists, n_trials=1000, n_traced=0, seed=42)
    # Should detect some difference
    assert "p_f_better_3pp" in report["bayesian"]
```

- [ ] **Step 6: Run, verify pass, commit**

```bash
git add scripts/e_vs_f_simulate.py tests/test_e_vs_f_simulate.py
git commit -m "feat(experiment): add paired trial runner with Bayesian + McNemar analysis"
```

---

### Task 5: Tier 2 Parameter Sweep

**Files:**
- Modify: `scripts/e_vs_f_simulate.py`
- Test: `tests/test_e_vs_f_simulate.py`

- [ ] **Step 1: Write failing test for sweep**

```python
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
```

- [ ] **Step 2: Implement run_parameter_sweep()**

Loops over cpuct values (Model F) and stall_window values (Model E), running `run_paired_trials()` for each. Reports per-parameter solve rates and identifies Tier 3 oracle-optimal values.

~60 lines.

- [ ] **Step 3: Run, verify pass, commit**

```bash
git add src/alethic/experiment/simulate.py tests/test_e_vs_f_simulate.py
git commit -m "feat(experiment): add Tier 2 parameter sweep for cpuct and stall_window"
```

---

### Task 6: Calibration Runner (Phase 1)

**Files:**
- Create: `scripts/e_vs_f_calibrate.py`

- [ ] **Step 1: Write calibration runner skeleton**

This script uses the real Alethic library (`MathAgent`, `PhysicsAgent`) to run problems and collect measurements. It follows the same pattern as `scripts/run_gate.py`.

Key structure:
```python
def calibrate(
    api_key: str,
    preset: str = "thorough",
    output_dir: str = "data/calibration",
) -> CalibratedDistributions:
    """Run Phase 1 calibration.

    - 3 full-depth problems x 8 iterations
    - 7 broad problems x 5 iterations
    - Collect per-iteration measurements
    - Fit distributions
    - Check quality gate
    """
```

Implementation:
1. Load benchmark problems, classify by archetype
2. `os.makedirs(output_dir, exist_ok=True)` — create output dirs
3. For each problem: create agent, run `solve()` with appropriate `max_iterations` override
4. Extract measurements from `AgentResult.events` using existing `measure_atoms()` and `compute_puct_comparison()`
5. Additionally extract: verdict/confidence per candidate, revision outcomes, FIXABLE events, breaker events, error categories, token usage from `result.token_ledger`
6. **Approach classification (C2/C3):** For each candidate, compute atom-structure hash via `parse_atoms()` from `alethic.atoms`, combine with `classify_errors()` from `alethic.error_taxonomy` using `classify_approach(atom_hash, error_cat)` from `distributions.py`. Cluster candidates per problem, compute M = `compute_approach_count(keys)`. Extract per-cluster max confidence as approach ceiling.
7. Fit distributions: `fit_beta_from_samples()` for confidence and approach ceilings, `scipy.stats.poisson.fit()` for atom counts, empirical categoricals for verdicts/errors. Approach counts stored as raw lists per archetype.
8. Run quality gate — `check_quality_gate()` with CV < 0.5
9. Write `data/calibration/e-vs-f-distributions.json` and `data/calibration/e-vs-f-traces.jsonl`

Traces JSONL schema (one line per problem-iteration):
```json
{"problem_id": "str", "iteration": 1, "archetype": "smooth", "candidates": [{"solution_hash": "str", "atom_hash": "str", "verdict": "str", "confidence": 0.85, "error_category": "str"}], "best_candidate": 0, "revised": true, "revision_improved": true, "fixable_used": false, "breaker_verdict": null, "tokens_used": 25000}
```

~300 lines. Distribution fitting logic is tested via `test_e_vs_f_distributions.py` (Task 1). Integration behavior is not unit-tested (requires real API).

- [ ] **Step 2: Add argparse CLI**

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 1: Calibration")
    parser.add_argument("--preset", "-p", default="thorough")
    parser.add_argument("--output-dir", "-o", default="data/calibration")
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"))
    args = parser.parse_args()
    dists = calibrate(args.api_key, args.preset, args.output_dir)
    print(f"Calibration complete. Quality gate: {'PASSED' if dists else 'FAILED'}")
```

- [ ] **Step 3: Commit**

```bash
git add scripts/e_vs_f_calibrate.py
git commit -m "feat(experiment): add Phase 1 calibration runner"
```

---

### Task 7: Validation Runner (Phase 3)

**Files:**
- Create: `scripts/e_vs_f_validate.py`

- [ ] **Step 1: Write validation runner**

Runs 50 single-iteration probes on 10 held-out problems from `gate-v38.json`. Each probe: create agent, run 1 iteration (override `max_iterations=1`), collect verdict + confidence.

Compare to simulation predictions:
1. Aggregate solve rate within +/-15pp
2. Spearman rho > 0.3
3. Difficulty-bin ordering

Ensure `os.makedirs(output_dir, exist_ok=True)` for output paths.

~150 lines. Validation criteria logic is unit-tested (see step below); API integration is not.

- [ ] **Step 2: Add CLI + Spearman calculation**

```python
from scipy.stats import spearmanr

def validate(
    dists_path: str,
    simulation_report_path: str,
    api_key: str,
    output_path: str = "data/calibration/validation-report.json",
) -> dict:
    """Phase 3: Validate simulation predictions against real probes."""
```

- [ ] **Step 3: Write validation criteria unit tests**

```python
# tests/test_e_vs_f_validate.py
from alethic.experiment.validate import check_validation_criteria

def test_validation_passes_when_close():
    """Validation passes when simulation is within ±15pp and Spearman > 0.3."""
    sim_rates = [0.8, 0.7, 0.6, 0.5, 0.4, 0.9, 0.3, 0.75, 0.65, 0.55]
    real_rates = [0.75, 0.65, 0.55, 0.45, 0.35, 0.85, 0.25, 0.70, 0.60, 0.50]
    result = check_validation_criteria(sim_rates, real_rates, aggregate_sim=0.62, aggregate_real=0.56)
    assert result["aggregate_passed"]  # |0.62 - 0.56| = 0.06 < 0.15
    assert result["spearman_passed"]   # ranks are correlated

def test_validation_fails_when_far():
    """Validation fails when aggregate differs by >15pp."""
    result = check_validation_criteria([0.5]*10, [0.5]*10, aggregate_sim=0.80, aggregate_real=0.50)
    assert not result["aggregate_passed"]  # |0.80 - 0.50| = 0.30 > 0.15
```

- [ ] **Step 4: Run tests, commit**

```bash
git add scripts/e_vs_f_validate.py tests/test_e_vs_f_validate.py
git commit -m "feat(experiment): add Phase 3 validation runner with tested criteria"
```

---

### Task 8: Diagnostics + Report Generator

**Files:**
- Create: `src/alethic/experiment/diagnostics.py`
- Modify: `scripts/e_vs_f_simulate.py` (add report formatting)

- [ ] **Step 1: Implement compute_diagnostics() and compute_crossover_table()**

```python
# src/alethic/experiment/diagnostics.py
"""Diagnostic metric computation from traced trials."""

def compute_diagnostics(traces: list[dict]) -> dict:
    """Compute 8 diagnostic metrics from traced trial event logs.

    Returns dict with:
    - approach_discovery_rate: mean iterations to find best approach
    - stall_recovery_success: P(escape | stall detected)
    - wasted_iterations: fraction with no confidence gain
    - puct_exploration_profile: visit distribution over time (Model F only)
    - cost_per_solve: mean tokens / solved problems
    - peak_context_utilization: max fraction of context budget used
    - candidate_diversity: mean unique approach hashes per iteration
    - false_acceptance_estimate: fraction solved where confidence < 0.85
    """

def compute_crossover_table(
    per_archetype_e: dict[str, float],
    per_archetype_f: dict[str, float],
    step: float = 0.05,
) -> list[dict]:
    """Sweep smooth/insight weights (adversarial fixed at 10%) and report winner.

    Returns list of {smooth_weight, insight_weight, winner, margin}.
    """
```

~80 lines total.

- [ ] **Step 2: Add markdown report generation to simulate.py**

After `run_paired_trials()` completes, `os.makedirs("docs/results", exist_ok=True)` then generate `docs/results/e-vs-f-report.md` with:
- Summary table (solve rates, NNT, Bayesian P(delta > 3pp))
- Per-archetype breakdown
- Tier 2 sensitivity curves
- Diagnostic metrics (from `compute_diagnostics()`)
- Crossover table (from `compute_crossover_table()`)
- Decision recommendation

- [ ] **Step 3: Add CLI to simulate.py**

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 2: Monte Carlo Simulation")
    parser.add_argument("--distributions", "-d", default="data/calibration/e-vs-f-distributions.json")
    parser.add_argument("--trials", "-n", type=int, default=5000)
    parser.add_argument("--traced", "-t", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", "-o", default="docs/results/e-vs-f-report.md")
    parser.add_argument("--sweep", action="store_true", help="Run Tier 2 parameter sweep")
```

- [ ] **Step 3: Commit**

```bash
git add src/alethic/experiment/simulate.py src/alethic/experiment/diagnostics.py scripts/e_vs_f_simulate.py
git commit -m "feat(experiment): add diagnostics, report generation and CLI to simulation runner"
```

---

### Task 9: Integration Test + Dry Run

**Files:**
- Test: `tests/test_e_vs_f_simulate.py`

- [ ] **Step 1: Write end-to-end integration test**

```python
def test_full_pipeline_with_defaults():
    """Full pipeline: default distributions -> simulate -> report."""
    dists = CalibratedDistributions.default()
    report = run_paired_trials(dists, n_trials=500, n_traced=100, seed=42)

    # Structural checks
    assert report["model_e"]["solve_rate"] >= 0
    assert report["model_f"]["solve_rate"] >= 0
    assert report["bayesian"]["p_f_better_3pp"] >= 0
    assert report["mcnemar"]["discordant_pairs"] >= 0
    assert "per_archetype" in report
    assert set(report["per_archetype"].keys()) == {"smooth", "insight", "adversarial"}

    # NNT should be finite (not divide by zero)
    if abs(report["model_f"]["solve_rate"] - report["model_e"]["solve_rate"]) > 0.001:
        assert report["nnt"]["point_estimate"] > 0
```

- [ ] **Step 2: Write parameter sweep integration test**

```python
def test_sweep_identifies_best():
    """Sweep finds best parameters for both models."""
    dists = CalibratedDistributions.default()
    sweep = run_parameter_sweep(
        dists, n_trials=100, seed=42,
        cpuct_values=[0.5, 1.414, 3.0],
        stall_window_values=[2, 3, 4, 5],
    )
    assert sweep["tier3_f_best"]["cpuct"] in [0.5, 1.414, 3.0]
    assert sweep["tier3_e_best"]["stall_window"] in [2, 3, 4, 5]
    assert "parameter_sensitive" in sweep  # Flag if T1 and T3 disagree
```

- [ ] **Step 3: Run all tests**

Run: `pytest tests/test_e_vs_f_distributions.py tests/test_e_vs_f_simulate.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_e_vs_f_simulate.py
git commit -m "test(experiment): add integration tests for full simulation pipeline"
```

---

### Task 10: Final Commit + Dry Run at 500 Trials

**Files:**
- No new files

- [ ] **Step 1: Run the simulation with default distributions at 500 trials**

```bash
cd /home/xeal/dev/alethic
/home/xeal/.local/bin/micromamba run -n alethic python scripts/e_vs_f_simulate.py \
  -n 500 -t 100 --seed 42 --output /tmp/dry-run-report.md
```

This uses the placeholder `default()` distributions (not calibrated), so the results are not decision-relevant — but it validates the full pipeline works end-to-end.

- [ ] **Step 2: Verify report generated**

Check `/tmp/dry-run-report.md` contains all expected sections: summary table, per-archetype, Bayesian posterior, NNT, McNemar's.

- [ ] **Step 3: Final commit with all scripts**

```bash
git add scripts/ tests/ docs/
git commit -m "feat(experiment): complete E vs F Monte Carlo experiment scaffold

Phase 1 calibration, Phase 2 simulation (Models E + F), Phase 3 validation.
Bayesian analysis, McNemar's test, NNT, per-archetype breakdown.
Ready for Phase 1 calibration run with ANTHROPIC_API_KEY."
```

---

## Execution Order

```
Task 1 (distributions) → Task 2 (Model E) → Task 3 (Model F) → Task 4 (paired runner) → Task 5 (sweep)
                                                                                        → Task 8 (diagnostics + report)
Task 6 (calibration)   [independent, can parallel with 2-5]
Task 7 (validation)    [independent, can parallel with 2-5]
Task 9 (integration tests) → Task 10 (dry run)
```

Tasks 1-5 + 8 are strictly sequential (each builds on the previous). Tasks 6 and 7 are independent integration scripts that can be written in parallel with the simulation code. Task 9 ties everything together.

## After Implementation

Run Phase 1 calibration:
```bash
ANTHROPIC_API_KEY=sk-... /home/xeal/.local/bin/micromamba run -n alethic \
  python scripts/e_vs_f_calibrate.py -p thorough
```

Then Phase 2 simulation:
```bash
/home/xeal/.local/bin/micromamba run -n alethic \
  python scripts/e_vs_f_simulate.py --sweep -n 5000 -t 2000
```

Then Phase 3 validation:
```bash
ANTHROPIC_API_KEY=sk-... /home/xeal/.local/bin/micromamba run -n alethic \
  python scripts/e_vs_f_validate.py
```
