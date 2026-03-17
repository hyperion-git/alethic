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
# not a per-candidate verifier verdict.
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
    arr = np.clip(arr, 0.001, 0.999)
    mu = np.mean(arr)
    var = np.var(arr, ddof=1) if len(arr) > 1 else 0.01
    var = min(var, mu * (1 - mu) - 0.001)
    if var <= 0:
        return BetaParams(a=mu * 10, b=(1 - mu) * 10)
    common = mu * (1 - mu) / var - 1
    return BetaParams(a=max(0.1, mu * common), b=max(0.1, (1 - mu) * common))


def classify_approach(atom_hash: str, error_category: str) -> str:
    """Classify a candidate's approach via two-signal method."""
    return f"{atom_hash}:{error_category}"


def compute_approach_count(approach_keys: list[str]) -> int:
    """Count distinct approaches from a list of approach keys."""
    return len(set(approach_keys))
