#!/usr/bin/env python3
"""Phase 2: Monte Carlo simulation runner for E vs F experiment.

Loads calibrated distributions from Phase 1, runs paired trials of
Model E (AtomGuidedSimulator) vs Model F (PUCTWidenSimulator),
performs Bayesian analysis, and generates a markdown report.

Usage:
    python scripts/e_vs_f_simulate.py -n 5000 -t 2000 --seed 42
    python scripts/e_vs_f_simulate.py --sweep -n 5000 -t 2000
    python scripts/e_vs_f_simulate.py -d data/calibration/e-vs-f-distributions.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from alethic.experiment.diagnostics import compute_crossover_table, compute_diagnostics
from alethic.experiment.distributions import CalibratedDistributions
from alethic.experiment.simulate import run_paired_trials, run_parameter_sweep


def generate_report(
    report: dict,
    sweep: dict | None = None,
    diagnostics: dict | None = None,
    crossover: list | None = None,
) -> str:
    """Generate markdown report from simulation results."""
    lines = []
    lines.append("# E vs F Monte Carlo Experiment Report\n")
    lines.append(
        f"**Trials:** {report['n_trials']} paired | **Traced:** {report['n_traced']}\n"
    )

    # Summary table
    lines.append("## Summary\n")
    lines.append("| Metric | Model E | Model F |")
    lines.append("|--------|---------|---------|")
    lines.append(
        f"| Solve rate | {report['model_e']['solve_rate']:.4f} | {report['model_f']['solve_rate']:.4f} |"
    )
    lines.append(
        f"| Mean confidence | {report['model_e']['mean_confidence']:.4f} | {report['model_f']['mean_confidence']:.4f} |"
    )
    lines.append(
        f"| Mean iterations | {report['model_e']['mean_iterations']:.2f} | {report['model_f']['mean_iterations']:.2f} |"
    )
    lines.append(
        f"| Mean cost (tokens) | {report['model_e']['mean_cost']:.0f} | {report['model_f']['mean_cost']:.0f} |"
    )
    lines.append("")

    # Bayesian analysis
    bay = report["bayesian"]
    lines.append("## Bayesian Posterior Analysis\n")
    lines.append(f"- **Mean delta (F - E):** {bay['mean_delta']:.4f}")
    lines.append(f"- **95% CI:** [{bay['ci_95'][0]:.4f}, {bay['ci_95'][1]:.4f}]")
    lines.append(f"- P(F better by >1pp): {bay['p_f_better_1pp']:.4f}")
    lines.append(f"- P(F better by >3pp): {bay['p_f_better_3pp']:.4f}")
    lines.append(f"- P(F better by >5pp): {bay['p_f_better_5pp']:.4f}")
    lines.append(f"- P(F better by >10pp): {bay['p_f_better_10pp']:.4f}")
    lines.append("")

    # McNemar's test
    mc = report["mcnemar"]
    lines.append("## McNemar's Test\n")
    lines.append(f"- E solves, F doesn't: {mc['b_e_only']}")
    lines.append(f"- F solves, E doesn't: {mc['c_f_only']}")
    lines.append(f"- Discordant pairs: {mc['discordant_pairs']}")
    lines.append(f"- Chi-squared: {mc['chi2']:.4f}")
    lines.append(f"- p-value: {mc['p_value']:.6f}")
    lines.append("")

    # NNT
    nnt = report["nnt"]
    lines.append("## Number Needed to Treat (NNT)\n")
    lines.append(f"- **Winner:** Model {nnt['winner']}")
    if nnt["point_estimate"] != float("inf"):
        lines.append(f"- **NNT:** {nnt['point_estimate']:.1f}")
        lines.append(f"- **95% CI:** [{nnt['ci_95'][0]:.1f}, {nnt['ci_95'][1]:.1f}]")
    else:
        lines.append("- **NNT:** No significant difference detected")
    lines.append("")

    # Per-archetype breakdown
    lines.append("## Per-Archetype Breakdown\n")
    lines.append("| Archetype | E solve rate | F solve rate | N | Winner |")
    lines.append("|-----------|-------------|-------------|---|--------|")
    for arch, data in report.get("per_archetype", {}).items():
        e_r = data["e_rate"]
        f_r = data["f_rate"]
        winner = "F" if f_r > e_r + 0.01 else "E" if e_r > f_r + 0.01 else "Tie"
        lines.append(
            f"| {arch} | {e_r:.4f} | {f_r:.4f} | {data['n']} | {winner} |"
        )
    lines.append("")

    # Diagnostics
    if diagnostics:
        lines.append("## Diagnostics (from traced trials)\n")
        lines.append("| Metric | Model E | Model F |")
        lines.append("|--------|---------|---------|")
        for metric in [
            "approach_discovery_rate",
            "wasted_iterations",
            "cost_per_solve",
            "candidate_diversity",
            "false_acceptance",
        ]:
            e_val = diagnostics.get(f"{metric}_e", "N/A")
            f_val = diagnostics.get(f"{metric}_f", "N/A")
            e_str = (
                f"{e_val:.4f}"
                if isinstance(e_val, float) and e_val != float("inf")
                else str(e_val)
            )
            f_str = (
                f"{f_val:.4f}"
                if isinstance(f_val, float) and f_val != float("inf")
                else str(f_val)
            )
            lines.append(f"| {metric} | {e_str} | {f_str} |")
        stall_val = diagnostics.get("stall_recovery_success_e", "N/A")
        stall_str = (
            f"{stall_val:.4f}"
            if isinstance(stall_val, float)
            else str(stall_val)
        )
        lines.append(f"| stall_recovery_success | {stall_str} | N/A |")
        lines.append("")

    # Crossover table
    if crossover:
        lines.append("## Crossover Analysis\n")
        lines.append("| Smooth % | Insight % | Winner | Margin |")
        lines.append("|----------|-----------|--------|--------|")
        for row in crossover:
            lines.append(
                f"| {row['smooth_weight'] * 100:.0f}% | {row['insight_weight'] * 100:.0f}% | {row['winner']} | {row['margin']:+.4f} |"
            )
        lines.append("")

    # Sweep results
    if sweep:
        lines.append("## Tier 2 Parameter Sensitivity\n")
        if sweep.get("model_f_sweep"):
            lines.append("### Model F (cpuct sweep)\n")
            lines.append("| cpuct | Solve rate |")
            lines.append("|-------|-----------|")
            for entry in sweep["model_f_sweep"]:
                lines.append(f"| {entry['cpuct']:.3f} | {entry['solve_rate']:.4f} |")
            lines.append(
                f"\n**Best:** cpuct={sweep['tier3_f_best']['cpuct']:.3f} → {sweep['tier3_f_best']['solve_rate']:.4f}"
            )
            lines.append("")
        if sweep.get("model_e_sweep"):
            lines.append("### Model E (stall_window sweep)\n")
            lines.append("| stall_window | Solve rate |")
            lines.append("|-------------|-----------|")
            for entry in sweep["model_e_sweep"]:
                lines.append(
                    f"| {entry['stall_window']} | {entry['solve_rate']:.4f} |"
                )
            lines.append(
                f"\n**Best:** stall_window={sweep['tier3_e_best']['stall_window']} → {sweep['tier3_e_best']['solve_rate']:.4f}"
            )
            lines.append("")
        if sweep.get("parameter_sensitive"):
            lines.append(
                "> **WARNING:** Tier 1 and Tier 3 disagree on winner. Conclusion is parameter-sensitive.\n"
            )

    # Decision recommendation
    lines.append("## Decision Recommendation\n")
    if bay["p_f_better_3pp"] > 0.95:
        lines.append("**Recommendation: Implement Model F (PUCT + progressive widening)**")
        lines.append(
            f"P(F better by >3pp) = {bay['p_f_better_3pp']:.4f} exceeds 0.95 threshold."
        )
    elif 1 - bay["p_f_better_3pp"] > 0.95:
        lines.append("**Recommendation: Keep Model E (atom-guided verification)**")
        lines.append(
            f"P(E better) = {1 - bay['p_f_better_3pp']:.4f} exceeds 0.95 threshold."
        )
    else:
        lines.append("**Recommendation: Indecisive — consult per-archetype breakdown**")
        lines.append(
            "Neither model achieves 0.95 posterior probability of >3pp advantage."
        )
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Phase 2: Monte Carlo Simulation for E vs F experiment"
    )
    parser.add_argument(
        "--distributions",
        "-d",
        default="data/calibration/e-vs-f-distributions.json",
        help="Path to calibrated distributions JSON",
    )
    parser.add_argument(
        "--trials", "-n", type=int, default=5000, help="Number of paired trials (default: 5000)"
    )
    parser.add_argument(
        "--traced", "-t", type=int, default=2000, help="Number of traced trials (default: 2000)"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        "-o",
        default="docs/results/e-vs-f-report.md",
        help="Output report path",
    )
    parser.add_argument("--sweep", action="store_true", help="Run Tier 2 parameter sweep")
    parser.add_argument(
        "--use-defaults",
        action="store_true",
        help="Use default (placeholder) distributions instead of loading from file",
    )
    args = parser.parse_args()

    # Load distributions
    if args.use_defaults:
        print("Using default (placeholder) distributions")
        dists = CalibratedDistributions.default()
    else:
        if not os.path.exists(args.distributions):
            print(
                f"Distributions file not found: {args.distributions}", file=sys.stderr
            )
            print(
                "Use --use-defaults for placeholder distributions, or run Phase 1 first.",
                file=sys.stderr,
            )
            sys.exit(1)
        with open(args.distributions) as f:
            dists = CalibratedDistributions.from_json(f.read())

    print(f"Running {args.trials} paired trials ({args.traced} traced)...")
    start = time.time()
    report = run_paired_trials(
        dists,
        n_trials=args.trials,
        n_traced=args.traced,
        seed=args.seed,
    )
    elapsed = time.time() - start
    print(f"Simulation completed in {elapsed:.1f}s")

    # Diagnostics from traced trials
    diagnostics = None
    crossover = None
    if report.get("traced_e") and report.get("traced_f"):
        diagnostics = compute_diagnostics(report["traced_e"], report["traced_f"])
        # Crossover table from per-archetype rates
        per_arch = report.get("per_archetype", {})
        if per_arch:
            e_rates = {a: d["e_rate"] for a, d in per_arch.items()}
            f_rates = {a: d["f_rate"] for a, d in per_arch.items()}
            crossover = compute_crossover_table(e_rates, f_rates)

    # Parameter sweep
    sweep = None
    if args.sweep:
        print("Running Tier 2 parameter sweep...")
        sweep_start = time.time()
        sweep = run_parameter_sweep(dists, n_trials=args.trials // 5, seed=args.seed)
        print(f"Sweep completed in {time.time() - sweep_start:.1f}s")

    # Generate report
    md = generate_report(report, sweep=sweep, diagnostics=diagnostics, crossover=crossover)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(md)
    print(f"\nReport written to {args.output}")

    # Also save raw JSON for programmatic access
    json_path = args.output.replace(".md", ".json")
    raw_report = {k: v for k, v in report.items() if k not in ("traced_e", "traced_f")}
    if diagnostics:
        raw_report["diagnostics"] = diagnostics
    if crossover:
        raw_report["crossover"] = crossover
    if sweep:
        raw_report["sweep"] = sweep
    with open(json_path, "w") as f:
        json.dump(raw_report, f, indent=2, default=str)
    print(f"Raw data written to {json_path}")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Model E solve rate: {report['model_e']['solve_rate']:.4f}")
    print(f"Model F solve rate: {report['model_f']['solve_rate']:.4f}")
    print(f"P(F better by >3pp): {report['bayesian']['p_f_better_3pp']:.4f}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
