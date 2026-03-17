"""Diagnostic metric computation from traced trials + crossover table."""
from __future__ import annotations

import numpy as np


def compute_diagnostics(traces_e: list[dict], traces_f: list[dict]) -> dict:
    """Compute 8 diagnostic metrics from traced trial event logs.

    Args:
        traces_e: Full result dicts from Model E traced trials
        traces_f: Full result dicts from Model F traced trials

    Returns dict with:
    - approach_discovery_rate_e/f: mean iterations to find best approach (iter of max confidence)
    - stall_recovery_success_e: fraction of stalls that led to a solve
    - wasted_iterations_e/f: fraction of iterations with no confidence gain
    - cost_per_solve_e/f: mean tokens / solved problems (inf if 0 solves)
    - candidate_diversity_e/f: mean unique approaches per trial
    - false_acceptance_estimate_e/f: fraction solved where confidence < 0.85
    """

    def _approach_discovery(traces):
        """Mean iteration index where best confidence was first achieved."""
        if not traces:
            return 0.0
        discoveries = []
        for t in traces:
            seq = t.get("approach_sequence", [])
            if not seq:
                continue
            # Best approach = the approach used in the iteration with highest confidence
            # Approximate from approach_sequence: first occurrence of most common approach
            discoveries.append(min(len(seq), max(1, len(set(seq)))))
        return float(np.mean(discoveries)) if discoveries else 0.0

    def _wasted_iterations(traces):
        """Fraction of iterations that provided no confidence improvement."""
        if not traces:
            return 0.0
        total_iters = 0
        wasted = 0
        for t in traces:
            n_iters = t.get("iterations_used", 1)
            total_iters += n_iters
            # If not solved and used all iterations, most were "wasted"
            if not t.get("solved", False):
                wasted += n_iters
            else:
                # If solved, iterations before the solving one contributed
                wasted += max(0, n_iters - 1)  # last iteration solved it
        return wasted / total_iters if total_iters > 0 else 0.0

    def _cost_per_solve(traces):
        """Mean tokens per solved problem."""
        solved_costs = [t["cost_tokens"] for t in traces if t.get("solved")]
        return float(np.mean(solved_costs)) if solved_costs else float("inf")

    def _diversity(traces):
        """Mean unique approaches explored per trial."""
        if not traces:
            return 0.0
        divs = [len(set(t.get("approach_sequence", []))) for t in traces]
        return float(np.mean(divs)) if divs else 0.0

    def _false_acceptance(traces):
        """Fraction of solved trials where confidence is suspiciously low."""
        solved = [t for t in traces if t.get("solved")]
        if not solved:
            return 0.0
        false_accepts = sum(1 for t in solved if t.get("confidence", 1.0) < 0.85)
        return false_accepts / len(solved)

    def _stall_recovery(traces):
        """Fraction of trials with stalls that ultimately solved."""
        stalled = [t for t in traces if t.get("stall_events", 0) > 0]
        if not stalled:
            return 0.0
        recovered = sum(1 for t in stalled if t.get("solved"))
        return recovered / len(stalled)

    return {
        "approach_discovery_rate_e": _approach_discovery(traces_e),
        "approach_discovery_rate_f": _approach_discovery(traces_f),
        "stall_recovery_success_e": _stall_recovery(traces_e),
        "wasted_iterations_e": _wasted_iterations(traces_e),
        "wasted_iterations_f": _wasted_iterations(traces_f),
        "cost_per_solve_e": _cost_per_solve(traces_e),
        "cost_per_solve_f": _cost_per_solve(traces_f),
        "candidate_diversity_e": _diversity(traces_e),
        "candidate_diversity_f": _diversity(traces_f),
        "false_acceptance_e": _false_acceptance(traces_e),
        "false_acceptance_f": _false_acceptance(traces_f),
    }


def compute_crossover_table(
    per_archetype_e: dict[str, float],
    per_archetype_f: dict[str, float],
    step: float = 0.05,
) -> list[dict]:
    """Sweep smooth/insight weights (adversarial fixed at 10%) and report winner.

    Args:
        per_archetype_e: {archetype: solve_rate} for Model E
        per_archetype_f: {archetype: solve_rate} for Model F
        step: weight increment for sweep (default 0.05)

    Returns list of {smooth_weight, insight_weight, winner, margin}.
    """
    results = []
    adversarial_weight = 0.10
    remaining = 1.0 - adversarial_weight

    smooth_steps = int(remaining / step) + 1
    for i in range(smooth_steps + 1):
        smooth_w = round(i * step, 4)
        if smooth_w > remaining:
            break
        insight_w = round(remaining - smooth_w, 4)

        e_rate = (
            smooth_w * per_archetype_e.get("smooth", 0)
            + insight_w * per_archetype_e.get("insight", 0)
            + adversarial_weight * per_archetype_e.get("adversarial", 0)
        )
        f_rate = (
            smooth_w * per_archetype_f.get("smooth", 0)
            + insight_w * per_archetype_f.get("insight", 0)
            + adversarial_weight * per_archetype_f.get("adversarial", 0)
        )

        margin = f_rate - e_rate
        winner = "F" if margin > 0.001 else "E" if margin < -0.001 else "tie"
        results.append({
            "smooth_weight": smooth_w,
            "insight_weight": insight_w,
            "winner": winner,
            "margin": round(margin, 6),
        })

    return results
