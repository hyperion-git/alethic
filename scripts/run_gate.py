#!/usr/bin/env python3
"""Gate benchmark runner — wraps the alethic eval harness with a progress bar
and gate decision output.

Requires the alethic package and an ANTHROPIC_API_KEY.

Setup (one-time):
    python scripts/run_gate.py --setup-env          # creates alethic-gate micromamba env

Usage:
    micromamba run -n alethic-gate python scripts/run_gate.py              # default preset
    micromamba run -n alethic-gate python scripts/run_gate.py -p quick     # quick sanity check
    micromamba run -n alethic-gate python scripts/run_gate.py -p thorough  # full-fidelity run

Or use an existing env with alethic installed (e.g., micromamba run -n alethic ...).
Set ANTHROPIC_API_KEY in your environment before running.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Project root (scripts/ is one level below)
ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = ROOT / "data" / "benchmarks" / "gate-v38.json"
MICROMAMBA = "/home/xeal/.local/bin/micromamba"
ENV_NAME = "alethic-gate"

# Embedded environment spec — keeps the script self-contained.
# Python 3.13 + pip; alethic installed in editable mode from project root.
_ENV_YAML = """\
name: {env_name}
channels:
  - conda-forge
dependencies:
  - python=3.13
  - pip
"""


def setup_env() -> None:
    """Create a clean micromamba environment for running the gate benchmark."""
    env_yaml = _ENV_YAML.format(env_name=ENV_NAME)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False
    ) as f:
        f.write(env_yaml)
        env_path = f.name

    print(f"Creating micromamba environment '{ENV_NAME}'...")
    result = subprocess.run(
        [MICROMAMBA, "create", "-f", env_path, "-y"],
        check=False,
    )
    if result.returncode != 0:
        print("ERROR: micromamba create failed.", file=sys.stderr)
        raise SystemExit(1)

    print(f"Installing alethic package from {ROOT}...")
    result = subprocess.run(
        [MICROMAMBA, "run", "-n", ENV_NAME, "pip", "install", "-e", str(ROOT)],
        check=False,
    )
    if result.returncode != 0:
        print("ERROR: pip install failed.", file=sys.stderr)
        raise SystemExit(1)

    Path(env_path).unlink(missing_ok=True)
    print(f"\nEnvironment '{ENV_NAME}' ready. Run with:")
    print(f"  ANTHROPIC_API_KEY=sk-... {MICROMAMBA} run -n {ENV_NAME} python scripts/run_gate.py")


# ---------------------------------------------------------------------------
# Progress bar helpers
# ---------------------------------------------------------------------------

def _progress_bar(current: int, total: int, width: int = 30, *, extra: str = "") -> str:
    """Render an inline progress bar: [████████░░░░░░░░] 42/100 (42%) extra."""
    frac = current / total if total else 0
    filled = int(width * frac)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    pct = f"{frac:.0%}"
    line = f"[{bar}] {current}/{total} ({pct})"
    if extra:
        line += f"  {extra}"
    return line


def _format_eta(seconds: float) -> str:
    """Format seconds as human-readable ETA."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


# ---------------------------------------------------------------------------
# Main runner — wraps run_benchmark with progress and gate decision
# ---------------------------------------------------------------------------

def run_gate(benchmark_path: Path, *, preset: str = "default", api_key: str | None = None) -> dict:
    """Run the gate benchmark with a progress bar and return results."""
    try:
        from alethic import MathAgent, PhysicsAgent
        from alethic.eval.harness import (
            GATE_EPOCH,
            anchor_sha256,
            compute_puct_comparison,
            load_benchmark,
            measure_atoms,
            split_metrics,
        )
        from alethic.models import AgentConfig
    except ImportError:
        print(
            "ERROR: alethic package not found. Run with:\n"
            f"  {MICROMAMBA} run -n {ENV_NAME} python scripts/run_gate.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY not set. Export it or pass --api-key.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    benchmark = load_benchmark(str(benchmark_path))
    problems = benchmark["problems"]
    config = AgentConfig.from_preset(preset)
    total = len(problems)
    results = []
    elapsed_times: list[float] = []
    run_start = time.time()

    for completed, problem_spec in enumerate(problems):
        pid = problem_spec["id"]
        domain = problem_spec.get("domain", "math")
        expected_solvable = problem_spec["expected_solvable"]

        # ETA estimate
        if elapsed_times:
            avg = sum(elapsed_times) / len(elapsed_times)
            remaining = total - completed
            eta_str = f"ETA {_format_eta(avg * remaining)}"
        else:
            eta_str = ""

        print(
            f"\r{_progress_bar(completed, total, extra=f'{pid} ({domain}) {eta_str}')}"
            + " " * 10,
            end="",
            flush=True,
        )

        start = time.time()
        agent_cls = PhysicsAgent if domain == "physics" else MathAgent
        agent = agent_cls(config=config, api_key=api_key)

        outcome: dict = {
            "id": pid,
            "domain": domain,
            "expected_solvable": expected_solvable,
        }

        try:
            result = agent.solve(problem_spec["problem"])
            outcome |= {
                "solved": result.solved,
                "verdict": result.verdict.value,
                "confidence": result.confidence,
                "iterations_used": result.iterations_used,
                "correct_prediction": result.solved == expected_solvable,
                "error": None,
            }
            outcome["atom_metrics"] = measure_atoms(result.events, result.iterations_used)
            outcome["puct_divergence"] = compute_puct_comparison(result.events)
        except Exception as exc:  # noqa: BLE001
            outcome |= {
                "solved": False,
                "verdict": "error",
                "confidence": 0.0,
                "iterations_used": 0,
                "correct_prediction": False,
                "error": str(exc),
            }
            outcome["atom_metrics"] = None
            outcome["puct_divergence"] = None
            print(f"\n  ERROR {pid}: {exc}", flush=True)

        results.append(outcome)
        elapsed = time.time() - start
        elapsed_times.append(elapsed)

    # Final progress bar
    wall = time.time() - run_start
    print(f"\r{_progress_bar(total, total, extra='DONE')}" + " " * 20)
    print(f"\nCompleted {total} problems in {_format_eta(wall)}")

    # Aggregate metrics
    ann_rates = [
        r["atom_metrics"]["annotation_rate"]
        for r in results if r.get("atom_metrics")
    ]
    puct_rates = [
        r["puct_divergence"]["divergence_rate"]
        for r in results if r.get("puct_divergence")
    ]
    atom_counts = [
        r["atom_metrics"]["mean_atom_count"]
        for r in results if r.get("atom_metrics")
    ]

    return {
        "benchmark": benchmark.get("name", benchmark_path.stem),
        "preset": preset,
        "anchor_sha256": anchor_sha256(benchmark),
        "gate_epoch": GATE_EPOCH,
        "total": total,
        **split_metrics(results),
        "mean_annotation_rate": sum(ann_rates) / len(ann_rates) if ann_rates else 0.0,
        "mean_atom_count": sum(atom_counts) / len(atom_counts) if atom_counts else 0.0,
        "mean_puct_divergence": sum(puct_rates) / len(puct_rates) if puct_rates else 0.0,
        "elapsed_seconds": wall,
        "results": results,
    }


def report(gate_data: dict) -> None:
    """Print gate decision report and save JSON."""
    print("\n" + "=" * 60)
    print("GATE EXPERIMENT RESULTS")
    print("=" * 60)
    accept = gate_data.get("false_claim_accept_rate")
    accept_str = "n/a (no anchors scored)" if accept is None else f"{accept:.1%}"

    print(f"Problems:        {gate_data['total']}")
    print(
        f"Solved:          {gate_data['solved']}/{gate_data['n_solvable']} solvable "
        f"({gate_data['solve_rate']:.1%})"
    )
    print(
        f"False-claim FPR: {gate_data['false_claims_accepted']}"
        f"/{gate_data['n_false_claim_scored']} accepted ({accept_str})"
    )
    print(f"Anchor verdicts: {gate_data['false_claim_verdicts']}")
    print(f"Errors:          {gate_data['n_errors']}")
    print(f"Annotation rate: {gate_data['mean_annotation_rate']:.2f}")
    print(f"Mean atom count: {gate_data['mean_atom_count']:.1f}")
    print(f"PUCT divergence: {gate_data['mean_puct_divergence']:.2f}")
    print(f"Elapsed:         {_format_eta(gate_data['elapsed_seconds'])}")
    print(f"Anchor sha256:   {gate_data['anchor_sha256'][:16]}...")
    print(f"Gate epoch:      {gate_data['gate_epoch']}  (reports differ across epochs)")
    print()

    ann = gate_data["mean_annotation_rate"]
    puct = gate_data["mean_puct_divergence"]

    if ann >= 0.50:
        print("Option E signal: STRONG (annotation_rate >= 0.50)")
    elif ann >= 0.30:
        print("Option E signal: MODERATE (annotation_rate >= 0.30)")
    else:
        print("Option E signal: WEAK (annotation_rate < 0.30)")

    if puct >= 0.20:
        print("Option F signal: STRONG (puct_divergence >= 0.20)")
    elif puct >= 0.10:
        print("Option F signal: MODERATE (puct_divergence >= 0.10)")
    else:
        print("Option F signal: WEAK (puct_divergence < 0.10)")

    print("=" * 60)

    report_path = ROOT / "data" / "benchmarks" / "gate-v38-results.json"
    with open(report_path, "w") as f:
        json.dump(gate_data, f, indent=2)
    print(f"\nFull report: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Gate benchmark runner")
    parser.add_argument(
        "--setup-env",
        action="store_true",
        help=f"Create micromamba environment '{ENV_NAME}' and exit",
    )
    parser.add_argument(
        "-p", "--preset",
        default="default",
        choices=["quick", "default", "thorough", "extreme"],
        help="AgentConfig preset (default: 'default')",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=BENCHMARK_PATH,
        help="Path to benchmark JSON file",
    )
    args = parser.parse_args()

    if args.setup_env:
        setup_env()
        return

    gate_data = run_gate(
        args.benchmark,
        preset=args.preset,
        api_key=args.api_key,
    )
    report(gate_data)


if __name__ == "__main__":
    main()
