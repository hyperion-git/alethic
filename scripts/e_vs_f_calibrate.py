#!/usr/bin/env python3
"""Phase 1: Calibration runner for E vs F Monte Carlo experiment.

Runs problems through the Alethic Python library (MathAgent, PhysicsAgent),
collects per-iteration measurements, fits parametric distributions, and
writes calibration data for Phase 2 simulation.

Supports --resume to skip already-completed problems and continue from
a crashed or interrupted run.  Traces are written incrementally after each
problem so that progress is never lost.

Uses --workers to run problems concurrently (default 3).  Each problem
gets its own agent instance; results are merged in the main thread.

Requires ANTHROPIC_API_KEY environment variable.

Usage:
    python scripts/e_vs_f_calibrate.py --preset thorough
    python scripts/e_vs_f_calibrate.py -p thorough -w 4   # 4 concurrent problems
    python scripts/e_vs_f_calibrate.py --resume            # continue from crash
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Verdict priority for best-candidate selection (lower = better).
# Mirrors oracle_router.rank_candidates() logic.
_VERDICT_RANK = {
    "correct": 0,
    "minor_issues": 1,
    "fixable": 2,
    "major_flaw": 3,
    "unsolved": 4,
}


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------


def _completed_problems(traces_path: str) -> set[str]:
    """Read traces JSONL and return problem IDs that have a summary line.

    A problem is considered complete if there exists a trace line with a
    ``best_candidate`` key for that problem_id.
    """
    completed: set[str] = set()
    if not os.path.exists(traces_path):
        return completed
    with open(traces_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "best_candidate" in obj:
                completed.add(obj["problem_id"])
    return completed


def _reconstruct_raw(traces_path: str) -> dict[str, list]:
    """Reconstruct raw_measurements from existing traces for distribution fitting.

    Recovers: confidences, verdict counts, atom_counts, approach_keys, total_tokens.
    Cannot recover: revision_improvements, stall_events, breaker_demotions.
    """
    raw: dict[str, list] = defaultdict(list)
    if not os.path.exists(traces_path):
        return dict(raw)

    # Track max tokens per problem for total_tokens (avoid double counting)
    max_tokens_per_problem: dict[str, int] = {}

    with open(traces_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip summary lines (those with best_candidate key)
            if "best_candidate" in obj:
                continue

            # Non-summary lines have per-candidate data
            pid = obj.get("problem_id", "")
            verdict = obj.get("verdict", "")
            confidence = float(obj.get("confidence", 0.0))

            raw["confidences"].append(confidence)
            if verdict:
                raw[f"verdict_{verdict}"].append(1.0)

            raw["atom_counts"].append(obj.get("atom_count", 0))

            approach_key = obj.get("approach_key", "")
            if approach_key:
                raw["approach_keys"].append(approach_key)

            tokens = obj.get("tokens_used", 0)
            if tokens > 0 and pid:
                max_tokens_per_problem[pid] = max(
                    max_tokens_per_problem.get(pid, 0), tokens
                )

    # Use max tokens per problem as the total (each trace line has cumulative)
    for total in max_tokens_per_problem.values():
        raw["total_tokens"].append(total)

    return dict(raw)


def _write_traces(traces_path: str, traces: list[dict]) -> None:
    """Append trace lines to JSONL file."""
    with open(traces_path, "a") as f:
        for trace in traces:
            f.write(json.dumps(trace) + "\n")


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------

# Lock for interleaved print output from concurrent workers
_print_lock = threading.Lock()


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins:02d}m"


# ---------------------------------------------------------------------------
# Per-problem worker (runs in thread pool)
# ---------------------------------------------------------------------------


def _run_problem(
    pid: str,
    problem_data: dict,
    api_key: str,
    preset: str,
    model: str | None,
) -> dict:
    """Run a single calibration problem and return results.

    Each call creates its own agent instance (thread-safe — the Anthropic
    client uses httpx under the hood).  Returns a dict with traces,
    raw_measurements, timing, and verdict info.
    """
    archetype = PROBLEM_ARCHETYPES[pid]
    max_iters = FULL_DEPTH_ITERS if pid in FULL_DEPTH_PROBLEMS else BROAD_ITERS
    domain = problem_data["domain"]
    problem_text = problem_data["problem"]

    with _print_lock:
        print(f"  Starting: {pid} (archetype={archetype}, domain={domain}, iters={max_iters})")

    overrides: dict = {"max_iterations": max_iters}
    if model is not None:
        overrides["model"] = model
    config = AgentConfig.from_preset(preset, **overrides)

    agent_cls = PhysicsAgent if domain == "physics" else MathAgent
    agent = agent_cls(api_key=api_key, config=config)

    start_time = time.time()
    try:
        result = agent.solve(problem_text)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - start_time
        with _print_lock:
            print(f"  FAILED:   {pid} — {elapsed:.0f}s — {exc}", file=sys.stderr)
        return {
            "pid": pid,
            "archetype": archetype,
            "domain": domain,
            "traces": [],
            "raw": {},
            "elapsed": elapsed,
            "verdict": "error",
            "confidence": 0.0,
            "error": str(exc),
        }
    elapsed = time.time() - start_time

    traces: list[dict] = []
    raw: dict[str, list] = defaultdict(list)
    _extract_measurements(result, pid, archetype, domain, traces, raw)

    if result.token_ledger is not None:
        total = result.token_ledger.input_tokens + result.token_ledger.output_tokens
        if total > 0:
            raw["total_tokens"].append(total)

    with _print_lock:
        print(
            f"  Done:     {pid} — {elapsed:.0f}s — "
            f"{result.verdict.value} @ {result.confidence:.2f}"
        )

    return {
        "pid": pid,
        "archetype": archetype,
        "domain": domain,
        "traces": traces,
        "raw": dict(raw),
        "elapsed": elapsed,
        "verdict": result.verdict.value,
        "confidence": result.confidence,
        "error": None,
    }


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
                        result.token_ledger.input_tokens
                        + result.token_ledger.output_tokens
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

    # Best candidate summary — verdict-aware ranking (matches oracle_router)
    if current_candidates:
        best_idx = min(
            range(len(current_candidates)),
            key=lambda i: (
                _VERDICT_RANK.get(current_candidates[i]["verdict"], 5),
                -current_candidates[i]["confidence"],
            ),
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
    model: str | None = None,
    resume: bool = False,
    workers: int = 3,
) -> CalibratedDistributions | None:
    """Run Phase 1 calibration.

    Runs each problem in ``PROBLEM_ARCHETYPES`` through the Alethic Python
    library, collects per-iteration measurements, fits parametric
    distributions, and writes calibration data to ``output_dir``.

    Problems run concurrently with up to ``workers`` threads.  Each thread
    creates its own agent instance.  Results are merged in the main thread.

    When ``resume=True``, skips problems that already have completed traces
    and appends new results to existing trace files.

    Returns:
        ``CalibratedDistributions`` if the quality gate passes, ``None``
        otherwise.
    """
    os.makedirs(output_dir, exist_ok=True)
    traces_path = os.path.join(output_dir, "e-vs-f-traces.jsonl")

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

    # Resume: identify already-completed problems
    already_done: set[str] = set()
    raw_measurements: dict[str, list] = defaultdict(list)

    if resume:
        already_done = _completed_problems(traces_path)
        if already_done:
            print(f"Resuming: {len(already_done)} problem(s) already completed")
            for pid in sorted(already_done):
                print(f"  - {pid}")
            # Reconstruct raw_measurements from existing traces
            existing_raw = _reconstruct_raw(traces_path)
            for k, v in existing_raw.items():
                raw_measurements[k].extend(v)
    else:
        # Fresh run: truncate existing traces
        if os.path.exists(traces_path):
            os.remove(traces_path)

    # Filter to remaining problems
    remaining = {
        pid: data
        for pid, data in calibration_problems.items()
        if pid not in already_done
    }
    total_all = len(calibration_problems)
    n_done = len(already_done)
    n_remaining = len(remaining)

    if n_remaining == 0:
        print("All problems already completed. Fitting distributions from traces...")
    else:
        effective_workers = min(workers, n_remaining)
        print(
            f"Calibrating {n_remaining} problem(s) "
            f"({n_done} already done, {total_all} total) "
            f"with preset '{preset}', {effective_workers} worker(s)..."
        )

    # Run remaining problems concurrently with incremental trace writing
    wall_start = time.time()
    n_completed = 0

    if n_remaining > 0:
        effective_workers = min(workers, n_remaining)
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(
                    _run_problem, pid, data, api_key, preset, model,
                ): pid
                for pid, data in remaining.items()
            }

            for future in as_completed(futures):
                res = future.result()
                n_completed += 1
                progress_num = n_done + n_completed

                # Merge raw measurements (main thread — no lock needed)
                for k, v in res["raw"].items():
                    raw_measurements[k].extend(v)

                # Write traces incrementally (crash-safe)
                if res["traces"]:
                    _write_traces(traces_path, res["traces"])

                # Progress
                wall_elapsed = time.time() - wall_start
                avg_wall = wall_elapsed / n_completed
                eta = avg_wall * (n_remaining - n_completed)
                pct = (progress_num / total_all) * 100
                print(
                    f"  [{progress_num}/{total_all}] "
                    f"Progress: {pct:.0f}% | "
                    f"Wall: {_format_duration(wall_elapsed)} | "
                    f"ETA: ~{_format_duration(eta)}"
                )

    if not raw_measurements:
        print("ERROR: No measurements collected (all problems failed?)", file=sys.stderr)
        return None

    # Fit distributions from pooled raw measurements
    print(f"\nFitting distributions from {len(raw_measurements.get('confidences', []))} verify events...")
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

    # Summary
    wall_total = time.time() - wall_start if n_remaining > 0 else 0.0
    if n_completed > 0:
        print(
            f"\nRan {n_completed} problem(s) in {_format_duration(wall_total)} wall time "
            f"({workers} worker(s))"
        )

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
        "--model",
        "-m",
        default=None,
        help="Override model (e.g. claude-sonnet-4-6). Default: preset's model",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=3,
        help="Concurrent problem workers (default: 3). Use 1 for sequential.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a crashed/interrupted run (skip completed problems)",
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

    dists = calibrate(
        args.api_key,
        args.preset,
        args.output_dir,
        model=args.model,
        resume=args.resume,
        workers=args.workers,
    )

    if dists is not None:
        print("\nCalibration complete. Quality gate: PASSED")
        print("Ready for Phase 2: python scripts/e_vs_f_simulate.py")
    else:
        print("\nCalibration complete. Quality gate: FAILED")
        print("Consider expanding the calibration set or inspecting the traces.")
        sys.exit(1)


if __name__ == "__main__":
    main()
