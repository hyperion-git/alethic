#!/usr/bin/env python3
"""Phase 1: Calibration runner for E vs F Monte Carlo experiment.

Runs problems through the Alethic Python library (MathAgent, PhysicsAgent),
collects per-iteration measurements, fits parametric distributions, and
writes calibration data for Phase 2 simulation.

Requires ANTHROPIC_API_KEY environment variable.

Usage:
    python scripts/e_vs_f_calibrate.py --preset thorough
    python scripts/e_vs_f_calibrate.py -p thorough -o data/calibration
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np

from alethic import AgentConfig, MathAgent, PhysicsAgent
from alethic.atoms import parse_atoms
from alethic.error_taxonomy import classify_errors
from alethic.eval.harness import load_benchmark
from alethic.experiment.distributions import (
    ARCHETYPES,
    ERROR_CATS,
    CalibratedDistributions,
    check_quality_gate,
    classify_approach,
    compute_approach_count,
    fit_beta_from_samples,
)

# ---------------------------------------------------------------------------
# Problem set: archetype classification and iteration budgets
# ---------------------------------------------------------------------------

# Problem -> archetype classification (from spec Section 2.1).
# IDs must exist in math-sample.json or physics-sample.json.
PROBLEM_ARCHETYPES: dict[str, str] = {
    # Smooth refinement — confidence typically improves monotonically
    "prime-17": "smooth",
    "geometric-series": "smooth",
    "simple-pendulum-period": "smooth",
    "gauss-law-from-coulomb": "smooth",
    # Insight-required — early iterations may plateau until the key idea lands
    "sqrt2-irrational": "insight",
    "cantor-diagonal": "insight",
    "qho-energy-levels": "insight",
    "lorentz-transformation": "insight",
    # Adversarial / false claims — correct answer is "the premise is false"
    "false-claim-even-odd": "adversarial",
    "false-drude-lorenz-number": "adversarial",
}

# Full-depth problems get 8 iterations to see late-iteration dynamics;
# broad problems get 5 to cover the space without excessive cost.
FULL_DEPTH_PROBLEMS = {"prime-17", "sqrt2-irrational", "qho-energy-levels"}
FULL_DEPTH_ITERS = 8
BROAD_ITERS = 5


# ---------------------------------------------------------------------------
# Measurement extraction
# ---------------------------------------------------------------------------


def _extract_measurements(
    result,
    pid: str,
    archetype: str,
    domain: str,
    traces: list,
    raw_measurements: dict,
) -> None:
    """Extract per-iteration measurements from AgentResult events.

    Populates ``traces`` (JSONL rows) and ``raw_measurements`` (metric lists)
    in-place.  Every VERIFY event contributes one measurement row; REVISE and
    STALL_RESET events are counted as auxiliary signals.
    """
    iteration = 0
    current_candidates: list[dict] = []

    for event in result.events:
        etype = event.type.value if hasattr(event.type, "value") else str(event.type)

        if etype == "generate":
            iteration += 1
            current_candidates = []

        elif etype == "verify":
            verdict = event.data.get("verdict", "major_flaw")
            confidence = float(event.data.get("confidence", 0.0))
            critique = event.data.get("critique", "")
            error_cat = event.data.get("error_category") or classify_errors(critique)

            # Raw metric pools
            raw_measurements["confidences"].append(confidence)
            raw_measurements[f"verdict_{verdict}"].append(1.0)

            # Atom analysis from solution preview embedded in the event.
            # The VERIFY event carries a truncated preview; we use it for
            # structural hashing even if atom count may be understated.
            solution_text = event.data.get("solution_preview", "")
            atom_hash = ""
            atom_count = 0
            try:
                atoms = parse_atoms(solution_text) if solution_text else []
                real_atoms = [a for a in atoms if not getattr(a, "synthetic", False)]
                atom_count = len(real_atoms)
                atom_repr = ":".join(
                    f"{a.id}-{sorted(a.deps)}" for a in real_atoms
                )
                if atom_repr:
                    atom_hash = hashlib.md5(atom_repr.encode()).hexdigest()[:8]
            except Exception:  # noqa: BLE001
                pass

            raw_measurements["atom_counts"].append(atom_count)

            candidate_info = {
                "solution_hash": (
                    hashlib.md5(solution_text.encode()).hexdigest()[:8]
                    if solution_text
                    else ""
                ),
                "atom_hash": atom_hash,
                "verdict": verdict,
                "confidence": confidence,
                "error_category": error_cat,
            }
            current_candidates.append(candidate_info)

            # Approach key for diversity counting
            approach_key = classify_approach(atom_hash, error_cat)
            raw_measurements["approach_keys"].append(approach_key)

            # Emit a trace line for every VERIFY event (not just best)
            if iteration > 0:
                bucket = (
                    "early" if iteration <= 2 else "mid" if iteration <= 5 else "late"
                )
                tokens_so_far = 0
                if result.token_ledger is not None:
                    tokens_so_far = (
                        result.token_ledger.total_input
                        + result.token_ledger.total_output
                    )
                trace_line = {
                    "problem_id": pid,
                    "archetype": archetype,
                    "domain": domain,
                    "iteration": iteration,
                    "bucket": bucket,
                    "verdict": verdict,
                    "confidence": confidence,
                    "error_category": error_cat,
                    "atom_count": atom_count,
                    "approach_key": approach_key,
                    "tokens_used": tokens_so_far,
                }
                traces.append(trace_line)

        elif etype == "revise":
            improved = event.data.get("improved", False)
            raw_measurements["revision_improvements"].append(1.0 if improved else 0.0)

        elif etype == "stall_reset":
            raw_measurements["stall_events"].append(1.0)

        elif etype in ("breaker_flaw_found",):
            raw_measurements["breaker_demotions"].append(1.0)

    # Best candidate summary per iteration — emit after all events processed
    if current_candidates:
        best_idx = int(
            np.argmax([c["confidence"] for c in current_candidates])
        )
        traces_summary = {
            "problem_id": pid,
            "archetype": archetype,
            "domain": domain,
            "iteration": iteration,
            "best_candidate": best_idx,
            "candidates": current_candidates,
        }
        traces.append(traces_summary)


# ---------------------------------------------------------------------------
# Distribution fitting
# ---------------------------------------------------------------------------


def _fit_distributions(raw: dict) -> CalibratedDistributions:
    """Fit parametric distributions from raw measurements.

    Starts from ``CalibratedDistributions.default()`` (safe prior) and
    overrides individual parameters when we have sufficient real data.
    """
    dists = CalibratedDistributions.default()

    # --- Confidence distributions per verdict ---
    confidences = raw.get("confidences", [])
    if confidences:
        correct_confs = [c for c in confidences if c >= 0.85]
        if len(correct_confs) >= 2:  # noqa: PLR2004
            dists.confidence_dist["correct"] = fit_beta_from_samples(correct_confs)

        minor_confs = [c for c in confidences if 0.6 <= c < 0.85]
        if len(minor_confs) >= 2:  # noqa: PLR2004
            dists.confidence_dist["minor_issues"] = fit_beta_from_samples(minor_confs)

        fixable_confs = [c for c in confidences if 0.4 <= c < 0.6]
        if len(fixable_confs) >= 2:  # noqa: PLR2004
            dists.confidence_dist["fixable"] = fit_beta_from_samples(fixable_confs)

        flaw_confs = [c for c in confidences if c < 0.4]
        if len(flaw_confs) >= 2:  # noqa: PLR2004
            dists.confidence_dist["major_flaw"] = fit_beta_from_samples(flaw_confs)

    # --- Revision improvement rates per error category ---
    revision_improvements = raw.get("revision_improvements", [])
    if revision_improvements:
        overall_rate = float(np.mean(revision_improvements))
        # Override all categories with the empirical overall rate
        for cat in ERROR_CATS:
            dists.revision_rates[cat] = overall_rate
        # Apply category-specific adjustments based on prior knowledge
        dists.revision_rates["algebra"] = min(overall_rate * 1.4, 0.95)
        dists.revision_rates["logic"] = overall_rate * 0.6
        dists.revision_rates["citation"] = overall_rate * 0.8
        dists.revision_rates["interpretation"] = overall_rate * 0.5
        dists.revision_rates["units"] = min(overall_rate * 1.2, 0.95)
        dists.revision_rates["missing_case"] = overall_rate * 0.7

    # --- Atom count (Poisson lambda) ---
    atom_counts = raw.get("atom_counts", [])
    real_atom_counts = [c for c in atom_counts if c > 0]
    if real_atom_counts:
        dists.atom_lambda = float(np.mean(real_atom_counts))

    # --- Approach counts per archetype ---
    approach_keys = raw.get("approach_keys", [])
    if approach_keys:
        n_distinct = compute_approach_count(approach_keys)
        for arch in ARCHETYPES:
            # Record observed distinct count; simulation samples from this list
            dists.approach_counts[arch] = [max(2, n_distinct)]

    # --- Breaker demotion rate ---
    breaker_demotions = raw.get("breaker_demotions", [])
    if confidences and breaker_demotions is not None:
        total_accepted = sum(1 for c in confidences if c >= 0.95)
        if total_accepted > 0:
            dists.breaker_demotion = len(breaker_demotions) / total_accepted

    # --- Token cost estimate ---
    # Aggregate from stall events + revision events to get subagent call count
    n_verify_calls = len(raw.get("confidences", []))
    n_revise_calls = len(raw.get("revision_improvements", []))
    total_calls = n_verify_calls + n_revise_calls
    total_tokens = sum(raw.get("total_tokens", [0]))
    if total_calls > 0 and total_tokens > 0:
        dists.mean_tokens_per_call = total_tokens / total_calls

    return dists


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------


def calibrate(
    api_key: str,
    preset: str = "thorough",
    output_dir: str = "data/calibration",
) -> CalibratedDistributions | None:
    """Run Phase 1 calibration.

    Runs each problem in ``PROBLEM_ARCHETYPES`` through the Alethic Python
    library, collects per-iteration measurements, fits parametric
    distributions, and writes calibration data to ``output_dir``.

    Returns:
        ``CalibratedDistributions`` if the quality gate passes, ``None``
        otherwise.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Load benchmark problems (validates format, raises ValueError on bad entries)
    math_bench = load_benchmark("data/benchmarks/math-sample.json")
    physics_bench = load_benchmark("data/benchmarks/physics-sample.json")
    all_problems = {
        p["id"]: p
        for p in math_bench["problems"] + physics_bench["problems"]
    }

    # Filter to calibration set (skip any IDs not found in benchmarks)
    calibration_problems = {
        pid: all_problems[pid]
        for pid in PROBLEM_ARCHETYPES
        if pid in all_problems
    }

    missing = set(PROBLEM_ARCHETYPES) - set(calibration_problems)
    if missing:
        print(
            f"WARNING: {len(missing)} calibration problem(s) not found in "
            f"benchmarks and will be skipped: {sorted(missing)}",
            file=sys.stderr,
        )

    if not calibration_problems:
        print("ERROR: No calibration problems found in benchmarks", file=sys.stderr)
        return None

    print(
        f"Calibrating on {len(calibration_problems)} problems "
        f"with preset '{preset}'..."
    )

    # Collect measurements
    traces: list[dict] = []
    raw_measurements: dict[str, list] = defaultdict(list)

    for pid, problem_data in calibration_problems.items():
        archetype = PROBLEM_ARCHETYPES[pid]
        max_iters = FULL_DEPTH_ITERS if pid in FULL_DEPTH_PROBLEMS else BROAD_ITERS
        domain = problem_data["domain"]
        problem_text = problem_data["problem"]

        print(f"\n{'=' * 60}")
        print(
            f"Problem: {pid} "
            f"(archetype={archetype}, domain={domain}, iters={max_iters})"
        )
        print(f"{'=' * 60}")

        # Build config: start from preset, override max_iterations only
        config = AgentConfig.from_preset(preset, max_iterations=max_iters)

        # Create appropriate agent
        agent_cls = PhysicsAgent if domain == "physics" else MathAgent
        agent = agent_cls(api_key=api_key, config=config)

        # Run the problem
        start_time = time.time()
        try:
            result = agent.solve(problem_text)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {exc}", file=sys.stderr)
            continue
        elapsed = time.time() - start_time

        print(
            f"  Completed in {elapsed:.1f}s — "
            f"verdict: {result.verdict.value}, "
            f"confidence: {result.confidence:.3f}"
        )

        # Accumulate token totals for cost estimation
        if result.token_ledger is not None:
            total = result.token_ledger.total_input + result.token_ledger.total_output
            if total > 0:
                raw_measurements["total_tokens"].append(total)

        # Extract per-iteration measurements from the event log
        _extract_measurements(
            result, pid, archetype, domain, traces, raw_measurements
        )

    if not raw_measurements:
        print("ERROR: No measurements collected (all problems failed?)", file=sys.stderr)
        return None

    # Fit distributions from pooled raw measurements
    print("\nFitting distributions...")
    dists = _fit_distributions(dict(raw_measurements))

    # Quality gate: CV < 0.5 on key metrics
    gate_input = {
        k: raw_measurements[k]
        for k in ("confidences", "revision_improvements", "atom_counts")
        if raw_measurements.get(k)
    }
    gate = check_quality_gate(dists, gate_input)

    print(f"\nQuality gate: {'PASSED' if gate['passed'] else 'FAILED'}")
    if not gate["passed"]:
        print(f"  Failures: {gate['failures']}")
    else:
        print(
            f"  Collected: {len(raw_measurements['confidences'])} verify events, "
            f"{len(raw_measurements.get('revision_improvements', []))} revise events"
        )

    # Write distributions JSON
    dists_path = os.path.join(output_dir, "e-vs-f-distributions.json")
    with open(dists_path, "w") as f:
        f.write(dists.to_json())
    print(f"Wrote distributions to {dists_path}")

    # Write raw traces JSONL (one JSON object per line)
    traces_path = os.path.join(output_dir, "e-vs-f-traces.jsonl")
    with open(traces_path, "w") as f:
        for trace in traces:
            f.write(json.dumps(trace) + "\n")
    print(f"Wrote {len(traces)} trace lines to {traces_path}")

    return dists if gate["passed"] else None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1: Calibration for E vs F Monte Carlo experiment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--preset",
        "-p",
        default="thorough",
        choices=["quick", "default", "thorough", "extreme"],
        help="Alethic preset (default: thorough)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="data/calibration",
        help="Output directory for distributions and traces (default: data/calibration)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY"),
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    dists = calibrate(args.api_key, args.preset, args.output_dir)

    if dists is not None:
        print("\nCalibration complete. Quality gate: PASSED")
        print("Ready for Phase 2: python scripts/e_vs_f_simulate.py")
    else:
        print("\nCalibration complete. Quality gate: FAILED")
        print("Consider expanding the calibration set or inspecting the traces.")
        sys.exit(1)


if __name__ == "__main__":
    main()
