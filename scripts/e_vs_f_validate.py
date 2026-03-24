#!/usr/bin/env python3
"""Phase 3: Validation runner for E vs F Monte Carlo experiment.

Runs N probes per held-out problem, then compares observed per-problem solve
rates to Phase 2 simulation predictions via three criteria:
  1. Aggregate solve rate within +/-15pp of simulation prediction.
  2. Spearman rank-order correlation rho > 0.3.
  3. Difficulty-bin ordering: easy-bin real rate > hard-bin real rate.

Requires ANTHROPIC_API_KEY and a Phase 2 simulation report.

Usage:
    python scripts/e_vs_f_validate.py --simulation-report data/calibration/simulation-report.json
    python scripts/e_vs_f_validate.py -s data/calibration/simulation-report.json -n 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

from alethic import AgentConfig, MathAgent, PhysicsAgent
from alethic.eval.harness import load_benchmark
from alethic.experiment.validate import check_validation_criteria


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# IDs of calibration problems (Phase 1) — excluded from held-out set.
# When calibration uses all 20 problems, validation re-probes a subset
# of the same problems (cross-validation style) rather than held-out.
CALIBRATION_PROBLEM_IDS: set[str] = set()  # Empty = no exclusion; validate on same problems

# Default paths
DEFAULT_BENCH = "data/benchmarks/gate-v38.json"
FALLBACK_BENCHES = [
    "data/benchmarks/math-sample.json",
    "data/benchmarks/physics-sample.json",
]


# ---------------------------------------------------------------------------
# Probe runner
# ---------------------------------------------------------------------------


def _load_held_out_problems(n_problems: int = 10) -> list[dict]:
    """Load held-out benchmark problems, excluding calibration IDs.

    Tries gate-v38.json first; falls back to the smaller sample benchmarks if
    the gate file is not available.
    """
    if os.path.exists(DEFAULT_BENCH):
        bench = load_benchmark(DEFAULT_BENCH)
        problems = bench["problems"]
    else:
        print(
            f"WARNING: {DEFAULT_BENCH} not found; using sample benchmarks",
            file=sys.stderr,
        )
        problems = []
        for path in FALLBACK_BENCHES:
            if os.path.exists(path):
                b = load_benchmark(path)
                problems.extend(b["problems"])
            else:
                print(f"WARNING: {path} not found, skipping", file=sys.stderr)

    held_out = [p for p in problems if p["id"] not in CALIBRATION_PROBLEM_IDS]

    if len(held_out) < n_problems:
        print(
            f"WARNING: Only {len(held_out)} held-out problem(s) available "
            f"(requested {n_problems})",
            file=sys.stderr,
        )

    return held_out[:n_problems]


def _run_probes(
    problems: list[dict],
    api_key: str,
    preset: str,
    n_probes: int,
    openrouter: bool = False,
    model: str | None = None,
) -> dict[str, dict]:
    """Run n_probes single-iteration probes for each problem.

    Returns a dict mapping problem_id -> {"solve_rate": float, "n_probes": int}.
    """
    per_problem: dict[str, dict] = {}

    for prob in problems:
        pid = prob["id"]
        domain = prob.get("domain", "math")
        problem_text = prob["problem"]

        print(f"\nProbing: {pid} ({domain})")
        solves = 0
        errors = 0

        for probe_idx in range(n_probes):
            overrides: dict = {"max_iterations": 1, "verbose": False}
            if model is not None:
                overrides["model"] = model
            if openrouter:
                overrides["variant_b"] = None
                overrides["adversarial_breaker"] = False
                overrides["best_of_n"] = 1
            config = AgentConfig.from_preset(preset, **overrides)
            agent_cls = PhysicsAgent if domain == "physics" else MathAgent
            agent = agent_cls(api_key=api_key, config=config)

            try:
                result = agent.solve(problem_text)
                solved = result.verdict.value == "correct"
                if solved:
                    solves += 1
                print(
                    f"  Probe {probe_idx + 1}/{n_probes}: "
                    f"{result.verdict.value} "
                    f"(conf={result.confidence:.3f})"
                )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(
                    f"  Probe {probe_idx + 1}/{n_probes}: ERROR — {exc}",
                    file=sys.stderr,
                )

        effective_n = n_probes - errors
        solve_rate = solves / effective_n if effective_n > 0 else 0.0
        per_problem[pid] = {
            "solve_rate": solve_rate,
            "n_probes": n_probes,
            "n_errors": errors,
            "n_solves": solves,
        }
        print(
            f"  -> {pid}: solve_rate={solve_rate:.3f} "
            f"({solves}/{effective_n} effective probes)"
        )

    return per_problem


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------


def validate(
    simulation_report_path: str,
    api_key: str,
    output_path: str = "data/calibration/validation-report.json",
    n_probes_per_problem: int = 5,
    n_problems: int = 10,
    preset: str = "thorough",
    openrouter: bool = False,
    model: str | None = None,
) -> dict:
    """Phase 3: Validate simulation predictions against real probes.

    Args:
        simulation_report_path: Path to Phase 2 simulation JSON report.
        api_key: Anthropic API key.
        output_path: Where to write the validation report JSON.
        n_probes_per_problem: Number of single-iteration probes per problem.
        n_problems: Number of held-out problems to evaluate.
        preset: Alethic preset to use for each probe.

    Returns:
        Validation report dict with per_problem results and criteria.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Load simulation report to get predicted per-problem rates
    with open(simulation_report_path) as f:
        sim_report = json.load(f)

    print(f"Loaded simulation report from {simulation_report_path}")

    # Load held-out problems
    held_out = _load_held_out_problems(n_problems)

    if not held_out:
        print("ERROR: No held-out problems available", file=sys.stderr)
        sys.exit(1)

    print(
        f"\nValidating on {len(held_out)} held-out problems "
        f"with {n_probes_per_problem} probes each (preset={preset})..."
    )

    # Run probes
    t0 = time.time()
    per_problem = _run_probes(held_out, api_key, preset, n_probes_per_problem,
                              openrouter=openrouter, model=model)
    elapsed = time.time() - t0

    # Extract simulation predictions.
    # Phase 2 report may contain per-problem solve rates or just aggregate.
    # Fall back to aggregate solve rate as flat prediction if per-problem data absent.
    sim_per_problem: dict[str, float] = sim_report.get("per_problem_solve_rates", {})
    if not sim_per_problem:
        # Try nested structure: model_e / model_f solve rates
        model_e_rate = sim_report.get("model_e", {}).get("solve_rate", 0.5)
        sim_per_problem = {pid: model_e_rate for pid in per_problem}

    # Build aligned lists (same order)
    problem_ids = list(per_problem.keys())
    sim_rates = [float(sim_per_problem.get(pid, 0.5)) for pid in problem_ids]
    real_rates = [per_problem[pid]["solve_rate"] for pid in problem_ids]

    aggregate_sim = float(np.mean(sim_rates)) if sim_rates else 0.0
    aggregate_real = float(np.mean(real_rates)) if real_rates else 0.0

    print(f"\nAggregate: sim={aggregate_sim:.3f}, real={aggregate_real:.3f}")

    # Check validation criteria
    criteria = check_validation_criteria(
        sim_rates,
        real_rates,
        aggregate_sim=aggregate_sim,
        aggregate_real=aggregate_real,
    )

    report = {
        "per_problem": per_problem,
        "criteria": criteria,
        "n_problems": len(per_problem),
        "n_probes_per_problem": n_probes_per_problem,
        "aggregate_sim": aggregate_sim,
        "aggregate_real": aggregate_real,
        "elapsed_seconds": round(elapsed, 1),
        "preset": preset,
    }

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nValidation report written to {output_path}")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3: Validation for E vs F Monte Carlo experiment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--simulation-report",
        "-s",
        default="data/calibration/simulation-report.json",
        help="Path to Phase 2 simulation report (default: data/calibration/simulation-report.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="data/calibration/validation-report.json",
        help="Output path for validation report (default: data/calibration/validation-report.json)",
    )
    parser.add_argument(
        "--n-probes",
        "-n",
        type=int,
        default=5,
        help="Probes per held-out problem (default: 5; use 10 for full 50-probe run)",
    )
    parser.add_argument(
        "--n-problems",
        type=int,
        default=10,
        help="Number of held-out problems to evaluate (default: 10)",
    )
    parser.add_argument(
        "--preset",
        "-p",
        default="thorough",
        choices=["quick", "default", "thorough", "extreme"],
        help="Alethic preset (default: thorough)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY"),
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--openrouter",
        action="store_true",
        help="Use OpenRouter API instead of Anthropic. Requires OPENROUTER_API_KEY env var.",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Override model (e.g. nvidia/nemotron-3-nano-30b-a3b:free)",
    )
    args = parser.parse_args()

    if args.openrouter:
        from alethic.client_factory import set_client_factory
        from alethic.openrouter import OpenRouterClient

        or_key = os.environ.get("OPENROUTER_API_KEY")
        if not or_key:
            print("ERROR: --openrouter requires OPENROUTER_API_KEY environment variable", file=sys.stderr)
            sys.exit(1)
        model = args.model or "nvidia/nemotron-3-nano-30b-a3b:free"
        set_client_factory(lambda api_key: OpenRouterClient(api_key=or_key, model=model))
        print(f"Using OpenRouter: {model}")
    elif not args.api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.simulation_report):
        print(
            f"ERROR: Simulation report not found: {args.simulation_report}\n"
            "Run Phase 2 first: python scripts/e_vs_f_simulate.py",
            file=sys.stderr,
        )
        sys.exit(1)

    report = validate(
        simulation_report_path=args.simulation_report,
        api_key=args.api_key,
        output_path=args.output,
        n_probes_per_problem=args.n_probes,
        n_problems=args.n_problems,
        preset=args.preset,
        openrouter=args.openrouter,
        model=args.model,
    )

    crit = report["criteria"]
    print("\n" + "=" * 60)
    print(f"Spearman rho:    {crit['spearman_rho']:.3f} (p={crit['spearman_p_value']:.3f})")
    print(f"Aggregate delta: {crit['aggregate_delta']:.3f}")
    print(f"Easy-bin rate:   {crit['easy_bin_rate']:.3f}")
    print(f"Hard-bin rate:   {crit['hard_bin_rate']:.3f}")
    print("=" * 60)

    if crit["overall_passed"]:
        print("Validation: PASSED (all 3 criteria met)")
    else:
        print("Validation: FAILED")
        if not crit["aggregate_passed"]:
            print(
                f"  [FAIL] Aggregate delta {crit['aggregate_delta']:.3f} > 0.15 threshold"
            )
        if not crit["spearman_passed"]:
            print(
                f"  [FAIL] Spearman rho {crit['spearman_rho']:.3f} <= 0.3 threshold"
            )
        if not crit["difficulty_passed"]:
            print(
                f"  [FAIL] Difficulty ordering: easy={crit['easy_bin_rate']:.3f} "
                f"not > hard={crit['hard_bin_rate']:.3f}"
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
