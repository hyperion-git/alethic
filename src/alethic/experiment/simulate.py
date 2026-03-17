"""Monte Carlo simulation engine for E vs F experiment.

AlethicSimulator: base class with shared per-iteration logic (thorough preset).
AtomGuidedSimulator (Model E): all candidates from same approach, atom-targeted revision.
PUCTWidenSimulator (Model F): to be added in Task 3.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

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

        stall_state = {
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
