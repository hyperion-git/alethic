"""Monte Carlo simulation engine for E vs F experiment.

AlethicSimulator: base class with shared per-iteration logic (thorough preset).
AtomGuidedSimulator (Model E): all candidates from same approach, atom-targeted revision.
PUCTWidenSimulator (Model F): to be added in Task 3.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from alethic.experiment.distributions import (
    ARCHETYPES,
    ITER_BUCKETS,
    CalibratedDistributions,
)

# Thorough preset fixed parameters
MAX_ITERATIONS = 8
MAX_REVISIONS = 5
CONFIDENCE_THRESHOLD = 0.95
BASE_N = 3
STALL_WINDOW = 3
STALL_EPSILON = 0.02
MAX_RESETS = 2  # max(1, 8 // 4)
RESET_N_BOOST = 1


def _iter_bucket(iteration: int) -> str:
    """Map 1-based iteration number to bucket key."""
    for bucket, (lo, hi) in ITER_BUCKETS.items():
        if lo <= iteration <= hi:
            return bucket
    return "late"  # fallback for out-of-range


def _draw_categorical(probs: dict[str, float], rng: np.random.Generator) -> str:
    """Draw a single sample from a categorical distribution."""
    keys = list(probs.keys())
    vals = np.array([probs[k] for k in keys], dtype=float)
    total = vals.sum()
    if total <= 0:
        return keys[0]
    vals /= total
    return keys[rng.choice(len(keys), p=vals)]


class AlethicSimulator(ABC):
    """Base class for Monte Carlo simulation of the alethic agent loop.

    Models the thorough preset: 8 iterations, 5 revisions, N=3 best-of-N,
    confidence threshold 0.95. Subclasses override candidate selection,
    revision targeting, and stall handling to model different strategies.
    """

    def __init__(self, dists: CalibratedDistributions, *, seed: int = 0):
        self.dists = dists
        self.seed = seed

    # -- Override points for subclasses --

    @abstractmethod
    def select_candidates(
        self, n: int, n_approaches: int, current_approach: int,
        rng: np.random.Generator,
    ) -> list[int]:
        """Select which approach each of N candidates uses.

        Returns a list of N approach indices (0-based, all < n_approaches).
        """

    @abstractmethod
    def target_revision(self, base_rate: float, rng: np.random.Generator) -> float:
        """Apply model-specific targeting boost to the revision improvement rate.

        Returns the (possibly boosted) improvement probability.
        """

    @abstractmethod
    def handle_stall(self, state: dict) -> bool:
        """Handle a stall event. Mutates state in-place.

        Returns True if reset was applied, False if resets exhausted.
        """

    # -- Shared simulation logic --

    def run_trial(self, archetype: str) -> dict:
        """Run a single Monte Carlo trial for the given problem archetype.

        Returns a result dict with: solved, confidence, iterations_used,
        cost_tokens, approach_sequence, stall_events, fixable_shortcuts.
        """
        assert archetype in ARCHETYPES, f"Unknown archetype: {archetype}"
        rng = np.random.default_rng(self.seed)

        # Draw n_approaches (number of viable approaches) from empirical list
        approach_list = self.dists.approach_counts[archetype]
        n_approaches = int(rng.choice(approach_list))

        # Draw approach ceilings once at trial start (Beta per approach)
        ceiling_params = self.dists.approach_ceiling_dist[archetype]
        approach_ceilings = [
            float(rng.beta(ceiling_params.a, ceiling_params.b))
            for _ in range(n_approaches)
        ]

        # Trial state
        current_approach = 0
        best_confidence = 0.0
        confidence_history: list[float] = []
        approach_sequence: list[int] = []
        stall_events = 0
        fixable_shortcuts = 0
        total_calls = 0
        resets_used = 0
        cooldown_remaining = 0  # iterations until next stall check allowed
        solved = False

        stall_state: dict[str, Any] = {
            "current_approach": current_approach,
            "M": n_approaches,
            "resets_used": resets_used,
            "rng": rng,
        }

        for iteration in range(1, MAX_ITERATIONS + 1):
            bucket = _iter_bucket(iteration)
            n_this_iter = BASE_N

            # Check for stall (after enough history)
            is_reset = False
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
            elif len(confidence_history) >= STALL_WINDOW:
                recent = confidence_history[-STALL_WINDOW:]
                delta = max(recent) - min(recent)
                if delta < STALL_EPSILON:
                    stall_state["current_approach"] = current_approach
                    stall_state["resets_used"] = resets_used
                    if self.handle_stall(stall_state):
                        current_approach = stall_state["current_approach"]
                        resets_used = stall_state["resets_used"]
                        n_this_iter += RESET_N_BOOST
                        stall_events += 1
                        is_reset = True
                        cooldown_remaining = 1

            # Generate N candidates
            candidate_approaches = self.select_candidates(
                n_this_iter, n_approaches, current_approach, rng
            )
            total_calls += n_this_iter  # generation calls

            # Verify each candidate
            candidate_verdicts: list[str] = []
            candidate_confidences: list[float] = []

            for c_idx in range(n_this_iter):
                approach_idx = candidate_approaches[c_idx]

                # Draw verdict from archetype+bucket distribution
                verdict = _draw_categorical(
                    self.dists.verdict_dist[archetype][bucket], rng
                )

                # Draw raw confidence from verdict-specific Beta
                conf_params = self.dists.confidence_dist[verdict]
                raw_conf = float(rng.beta(conf_params.a, conf_params.b))

                # Cap at approach ceiling
                ceiling = approach_ceilings[approach_idx]
                conf = min(raw_conf, ceiling)

                candidate_verdicts.append(verdict)
                candidate_confidences.append(conf)

            total_calls += n_this_iter  # verification calls

            # Select best candidate (highest confidence)
            best_idx = int(np.argmax(candidate_confidences))
            iter_verdict = candidate_verdicts[best_idx]
            iter_confidence = candidate_confidences[best_idx]
            chosen_approach = candidate_approaches[best_idx]
            approach_sequence.append(chosen_approach)
            current_approach = chosen_approach

            # Draw error category for this iteration
            error_cat = _draw_categorical(
                self.dists.error_cat_dist[archetype], rng
            )

            # Acceptance gate: verdict + confidence + breaker must all pass.
            # The breaker is a side-effecting RNG draw, so we keep it separate.
            if iter_verdict == "correct" and iter_confidence >= CONFIDENCE_THRESHOLD:  # noqa: SIM102
                # Breaker is a side-effecting RNG draw; must not evaluate
                # unless the outer condition passes.
                if rng.random() >= self.dists.breaker_demotion:
                    solved = True
                    best_confidence = iter_confidence
                    confidence_history.append(iter_confidence)
                    break
                # Breaker demoted — continue as if not solved

            # FIXABLE shortcut
            if iter_verdict == "fixable":
                fixable_shortcuts += 1
                if rng.random() < self.dists.fixable_success:
                    # Re-verify the corrected solution
                    total_calls += 1
                    re_conf_params = self.dists.confidence_dist["correct"]
                    re_conf = float(rng.beta(re_conf_params.a, re_conf_params.b))
                    re_conf = min(re_conf, approach_ceilings[chosen_approach])
                    if (  # noqa: SIM102
                        re_conf >= CONFIDENCE_THRESHOLD
                        and rng.random() >= self.dists.breaker_demotion
                    ):
                        solved = True
                        best_confidence = re_conf
                        confidence_history.append(re_conf)
                        break

            # Track best seen
            if iter_confidence > best_confidence:
                best_confidence = iter_confidence

            # Revision sub-loop
            max_revs = 1 if is_reset else MAX_REVISIONS
            for _rev in range(max_revs):
                base_rate = self.dists.revision_rates.get(error_cat, 0.5)
                improve_rate = self.target_revision(base_rate, rng)

                total_calls += 1  # revision call

                if rng.random() < improve_rate:
                    # Improvement: re-draw verdict and confidence
                    new_verdict = _draw_categorical(
                        self.dists.verdict_dist[archetype][bucket], rng
                    )
                    new_conf_params = self.dists.confidence_dist[new_verdict]
                    new_conf = float(rng.beta(new_conf_params.a, new_conf_params.b))
                    new_conf = min(new_conf, approach_ceilings[chosen_approach])

                    total_calls += 1  # re-verification call

                    # Check if revision solved it (breaker is side-effecting)
                    if new_verdict == "correct" and new_conf >= CONFIDENCE_THRESHOLD:  # noqa: SIM102
                        if rng.random() >= self.dists.breaker_demotion:
                            solved = True
                            best_confidence = new_conf
                            break
                    if new_conf > iter_confidence:
                        iter_confidence = new_conf
                        iter_verdict = new_verdict
                        if new_conf > best_confidence:
                            best_confidence = new_conf
                else:
                    # Check for regression
                    if rng.random() < self.dists.regression_rate:
                        iter_confidence *= 0.9  # mild regression

            if solved:
                confidence_history.append(iter_confidence)
                break

            confidence_history.append(iter_confidence)

        return {
            "solved": solved,
            "confidence": float(best_confidence),
            "iterations_used": len(confidence_history),
            "cost_tokens": float(total_calls * self.dists.mean_tokens_per_call),
            "approach_sequence": approach_sequence,
            "stall_events": stall_events,
            "fixable_shortcuts": fixable_shortcuts,
        }


class PUCTWidenSimulator(AlethicSimulator):
    """Model F: PUCT scoring with progressive widening for approach selection.

    Key properties:
    - Candidates are drawn from PUCT-scored approaches (exploration + exploitation).
    - Progressive widening: active approaches = min(M, ceil(sqrt(iteration))).
    - Uniform revision targeting: no boost applied to base revision rate.
    - No explicit stall handling: PUCT naturally shifts to under-explored approaches.
    """

    def __init__(
        self,
        dists: CalibratedDistributions,
        *,
        seed: int = 0,
        cpuct: float = 1.414,
    ):
        super().__init__(dists, seed=seed)
        self.cpuct = cpuct
        # Per-trial PUCT state (reset in run_trial)
        self._visit_counts: dict[int, int] = {}
        self._approach_rewards: dict[int, float] = {}
        self._total_visits: int = 0
        self._iteration_count: int = 0

    def run_trial(self, archetype: str) -> dict:
        """Run a trial, initializing PUCT state first, then appending visit_counts."""
        # Reset PUCT state for this trial
        self._visit_counts = {}
        self._approach_rewards = {}
        self._total_visits = 0
        self._iteration_count = 0

        result = super().run_trial(archetype)
        result["visit_counts"] = dict(self._visit_counts)
        return result

    def select_candidates(
        self,
        n: int,
        n_approaches: int,
        current_approach: int,
        rng: np.random.Generator,
    ) -> list[int]:
        """Select N candidates via PUCT scoring with progressive widening.

        Progressive widening: at the current iteration, only
        ceil(sqrt(iteration_count)) approaches are active. This bounds early
        exploration and widens over time.

        One visit is recorded per call (per iteration) to the top-ranked approach.
        """
        import math

        # Use current count before incrementing to determine active width.
        # iteration_count=0 on first call → n_active = min(M, ceil(sqrt(1))) = 1.
        n_active = min(n_approaches, math.ceil(math.sqrt(self._iteration_count + 1)))
        active_approaches = list(range(n_active))

        # PUCT score for each active approach:
        #   Q(a) + cpuct * prior * sqrt(total_visits) / (1 + visits(a))
        # where prior = 1/n_approaches (uniform).
        prior = 1.0 / n_approaches
        sqrt_total = math.sqrt(self._total_visits) if self._total_visits > 0 else 1.0

        scores = []
        for a in active_approaches:
            visits_a = self._visit_counts.get(a, 0)
            q_a = self._approach_rewards.get(a, 0.0)
            u_a = self.cpuct * prior * sqrt_total / (1 + visits_a)
            scores.append(q_a + u_a)

        # Select top-N by PUCT score; break ties deterministically (argmax order)
        ranked = sorted(range(len(active_approaches)), key=lambda i: scores[i], reverse=True)
        # Assign each of the N candidates to an approach (cycle if N > n_active)
        selected_approaches: list[int] = []
        for c_idx in range(n):
            approach_slot = ranked[c_idx % len(ranked)]
            selected_approaches.append(active_approaches[approach_slot])

        # Record one visit per iteration (to the top-ranked approach).
        # This keeps sum(visit_counts) == iterations_used.
        top_approach = active_approaches[ranked[0]]
        self._visit_counts[top_approach] = self._visit_counts.get(top_approach, 0) + 1
        self._total_visits += 1
        self._iteration_count += 1

        return selected_approaches

    def _update_approach_reward(self, approach: int, confidence: float) -> None:
        """Update running Q-value for an approach (called externally after select)."""
        # Incremental mean: Q_new = Q_old + (reward - Q_old) / visits
        visits = self._visit_counts.get(approach, 1)
        old_q = self._approach_rewards.get(approach, 0.0)
        self._approach_rewards[approach] = old_q + (confidence - old_q) / visits

    def target_revision(self, base_rate: float, rng: np.random.Generator) -> float:
        """Uniform revision targeting: no boost applied."""
        return base_rate

    def handle_stall(self, state: dict) -> bool:
        """No-op: PUCT naturally explores under-visited approaches."""
        return False


class AtomGuidedSimulator(AlethicSimulator):
    """Model E: Atom-guided verification with focused approach selection.

    Key properties:
    - All N candidates use the same approach (no diversification).
    - Atom targeting boosts revision improvement rate by (1 + atom_targeting * 0.6).
    - Stall handling: switch to random remaining approach, reduce revision budget.
    """

    def select_candidates(
        self, n: int, n_approaches: int, current_approach: int,
        rng: np.random.Generator,
    ) -> list[int]:
        """All N candidates from the same approach."""
        return [current_approach] * n

    def target_revision(self, base_rate: float, rng: np.random.Generator) -> float:
        """Boost revision rate by atom targeting factor."""
        boost = 1.0 + self.dists.atom_targeting * 0.6
        return min(base_rate * boost, 1.0)

    def handle_stall(self, state: dict) -> bool:
        """Switch to a random remaining approach.

        Mutates state: current_approach, resets_used.
        Returns True if reset applied, False if exhausted.
        """
        if state["resets_used"] >= MAX_RESETS:
            return False

        n_approaches = state["M"]
        current = state["current_approach"]
        rng = state["rng"]

        # Pick a different approach
        if n_approaches > 1:
            alternatives = [i for i in range(n_approaches) if i != current]
            state["current_approach"] = int(rng.choice(alternatives))
        # else: only one approach, switch is a no-op but reset still counts

        state["resets_used"] += 1
        return True


def run_parameter_sweep(
    dists: CalibratedDistributions,
    n_trials: int = 1000,
    seed: int = 42,
    cpuct_values: list[float] | None = None,
    stall_window_values: list[int] | None = None,
) -> dict:
    """Tier 2 parameter sensitivity sweep.

    Sweeps cpuct for Model F and stall_window for Model E. For each parameter value,
    runs paired trials and reports solve rates. Identifies Tier 3 oracle-optimal values.

    Returns dict with model_f_sweep, model_e_sweep, tier3_f_best, tier3_e_best,
    and parameter_sensitive flag.
    """
    import alethic.experiment.simulate as _self_module

    if cpuct_values is None:
        cpuct_values = [0.25, 0.5, 1.0, 1.414, 2.0, 3.0]
    if stall_window_values is None:
        stall_window_values = [2, 3, 4, 5]

    # --- Model F sweep: vary cpuct ---
    model_f_sweep: list[dict] = []
    for cpuct_val in cpuct_values:
        report = run_paired_trials(dists, n_trials=n_trials, n_traced=0, seed=seed, cpuct=cpuct_val)
        model_f_sweep.append({
            "cpuct": cpuct_val,
            "solve_rate": report["model_f"]["solve_rate"],
            "mean_confidence": report["model_f"]["mean_confidence"],
        })

    # --- Model E sweep: vary stall_window via module global override ---
    model_e_sweep: list[dict] = []
    original_stall_window = _self_module.STALL_WINDOW
    try:
        for sw_val in stall_window_values:
            _self_module.STALL_WINDOW = sw_val
            report = run_paired_trials(dists, n_trials=n_trials, n_traced=0, seed=seed)
            model_e_sweep.append({
                "stall_window": sw_val,
                "solve_rate": report["model_e"]["solve_rate"],
                "mean_confidence": report["model_e"]["mean_confidence"],
            })
    finally:
        _self_module.STALL_WINDOW = original_stall_window

    # --- Identify Tier 3 best parameters ---
    best_f_entry = max(model_f_sweep, key=lambda x: x["solve_rate"])
    best_e_entry = max(model_e_sweep, key=lambda x: x["solve_rate"])

    tier3_f_best = {"cpuct": best_f_entry["cpuct"], "solve_rate": best_f_entry["solve_rate"]}
    tier3_e_best = {"stall_window": best_e_entry["stall_window"], "solve_rate": best_e_entry["solve_rate"]}

    # --- Determine if Tier 1 defaults and Tier 3 bests disagree on the winner ---
    # Tier 1 winner is based on default parameters (cpuct=1.414, stall_window=3).
    # Find the sweep entries closest to Tier 1 defaults to get Tier 1 solve rates.
    tier1_f_entry = min(model_f_sweep, key=lambda x: abs(x["cpuct"] - 1.414))
    tier1_sw = original_stall_window
    tier1_e_entry = min(model_e_sweep, key=lambda x: abs(x["stall_window"] - tier1_sw))

    tier1_winner = "F" if tier1_f_entry["solve_rate"] > tier1_e_entry["solve_rate"] else "E"
    tier3_winner = "F" if tier3_f_best["solve_rate"] > tier3_e_best["solve_rate"] else "E"
    parameter_sensitive = tier1_winner != tier3_winner

    return {
        "model_f_sweep": model_f_sweep,
        "model_e_sweep": model_e_sweep,
        "tier3_f_best": tier3_f_best,
        "tier3_e_best": tier3_e_best,
        "parameter_sensitive": parameter_sensitive,
    }


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

    Each trial uses the same seed and archetype for both models (paired design).
    Returns a report dict with per-model stats, Bayesian posterior analysis,
    McNemar's test, NNT, per-archetype breakdown, and traced diagnostics.
    """
    from scipy.stats import chi2 as chi2_dist  # type: ignore[import-untyped]

    if archetype_weights is None:
        archetype_weights = {"smooth": 0.40, "insight": 0.50, "adversarial": 0.10}

    rng = np.random.default_rng(seed)

    # Pre-draw archetypes for all trials
    arch_keys = list(archetype_weights.keys())
    arch_vals = np.array([archetype_weights[k] for k in arch_keys], dtype=float)
    arch_vals /= arch_vals.sum()
    archetype_choices = [
        arch_keys[int(rng.choice(len(arch_keys), p=arch_vals))]
        for _ in range(n_trials)
    ]

    # Accumulators
    e_results: list[dict] = []
    f_results: list[dict] = []
    e_solved: list[bool] = []
    f_solved: list[bool] = []
    traced_e: list[dict] = []
    traced_f: list[dict] = []

    for trial_idx in range(n_trials):
        trial_seed = seed + trial_idx
        archetype = archetype_choices[trial_idx]

        model_e = AtomGuidedSimulator(dists, seed=trial_seed)
        model_f = PUCTWidenSimulator(dists, seed=trial_seed, cpuct=cpuct)

        res_e = model_e.run_trial(archetype)
        res_f = model_f.run_trial(archetype)

        e_results.append(res_e)
        f_results.append(res_f)
        e_solved.append(res_e["solved"])
        f_solved.append(res_f["solved"])

        if trial_idx < n_traced:
            traced_e.append(res_e)
            traced_f.append(res_f)

    # --- Per-model stats ---
    solved_e = sum(e_solved)
    solved_f = sum(f_solved)

    def _model_stats(results: list[dict], solved_count: int) -> dict:
        return {
            "solve_rate": solved_count / n_trials,
            "mean_confidence": float(np.mean([r["confidence"] for r in results])),
            "mean_iterations": float(np.mean([r["iterations_used"] for r in results])),
            "mean_cost": float(np.mean([r["cost_tokens"] for r in results])),
        }

    # --- Bayesian analysis (Beta-Bernoulli conjugate) ---
    alpha_e = 1 + solved_e
    beta_e = 1 + (n_trials - solved_e)
    alpha_f = 1 + solved_f
    beta_f = 1 + (n_trials - solved_f)

    bayes_rng = np.random.default_rng(seed + 999999)
    samples_e = bayes_rng.beta(alpha_e, beta_e, size=100_000)
    samples_f = bayes_rng.beta(alpha_f, beta_f, size=100_000)
    delta = samples_f - samples_e

    bayesian = {
        "p_f_better_1pp": float(np.mean(delta > 0.01)),
        "p_f_better_3pp": float(np.mean(delta > 0.03)),
        "p_f_better_5pp": float(np.mean(delta > 0.05)),
        "p_f_better_10pp": float(np.mean(delta > 0.10)),
        "mean_delta": float(np.mean(delta)),
        "ci_95": [float(np.percentile(delta, 2.5)), float(np.percentile(delta, 97.5))],
    }

    # --- McNemar's test ---
    b = sum(1 for e, f in zip(e_solved, f_solved) if e and not f)
    c = sum(1 for e, f in zip(e_solved, f_solved) if f and not e)
    discordant = b + c
    if discordant > 0:
        chi2_stat = (abs(b - c) - 1) ** 2 / discordant
        p_value = 1 - chi2_dist.cdf(chi2_stat, df=1)
    else:
        chi2_stat = 0.0
        p_value = 1.0

    mcnemar = {
        "b_e_only": b,
        "c_f_only": c,
        "discordant_pairs": discordant,
        "chi2": float(chi2_stat),
        "p_value": float(p_value),
    }

    # --- NNT (Number Needed to Treat) ---
    rate_e = solved_e / n_trials
    rate_f = solved_f / n_trials
    delta_rate = rate_f - rate_e

    if abs(delta_rate) > 0.001:
        nnt_point = 1.0 / abs(delta_rate)
        nonzero_delta = delta[delta != 0]
        if len(nonzero_delta) > 0:
            nnt_samples = 1.0 / np.abs(nonzero_delta)
            nnt_ci = [
                float(np.percentile(nnt_samples, 2.5)),
                float(np.percentile(nnt_samples, 97.5)),
            ]
        else:
            nnt_ci = [float("inf"), float("inf")]
    else:
        nnt_point = float("inf")
        nnt_ci = [float("inf"), float("inf")]

    nnt = {
        "point_estimate": nnt_point,
        "ci_95": nnt_ci,
        "winner": "F" if delta_rate > 0 else ("E" if delta_rate < 0 else "tie"),
    }

    # --- Per-archetype breakdown ---
    per_archetype: dict[str, dict] = {}
    for arch in ARCHETYPES:
        arch_indices = [i for i, a in enumerate(archetype_choices) if a == arch]
        if arch_indices:
            e_rate = sum(e_solved[i] for i in arch_indices) / len(arch_indices)
            f_rate = sum(f_solved[i] for i in arch_indices) / len(arch_indices)
            per_archetype[arch] = {
                "e_rate": float(e_rate),
                "f_rate": float(f_rate),
                "n": len(arch_indices),
            }

    return {
        "model_e": _model_stats(e_results, solved_e),
        "model_f": _model_stats(f_results, solved_f),
        "bayesian": bayesian,
        "mcnemar": mcnemar,
        "nnt": nnt,
        "per_archetype": per_archetype,
        "n_trials": n_trials,
        "n_traced": n_traced,
        "traced_e": traced_e,
        "traced_f": traced_f,
    }
