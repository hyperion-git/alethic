#!/usr/bin/env python3
"""Breadth vs Depth experiment: does independent restarts beat deep iteration?

Compares two strategies with the same approximate compute budget:
  - Depth: 1 run × N iterations (flat GVR with revision)
  - Breadth: K independent runs × M iterations (K×M ≈ N), pick best

Designed for free OpenRouter models. Crash-safe (incremental JSON writes).
Run multiple models in parallel using separate terminals.

Usage:
    OPENROUTER_API_KEY=sk-... python scripts/breadth_vs_depth.py \
        -m "stepfun/step-3.5-flash:free" -r 5

    # Extreme budget (12 iters):
    OPENROUTER_API_KEY=sk-... python scripts/breadth_vs_depth.py \
        -m "nvidia/nemotron-3-nano-30b-a3b:free" --depth-iters 12 --breadth-runs 6 -r 5

    # Run all four models in parallel (4 terminals):
    # Terminal 1: ... -m "stepfun/step-3.5-flash:free"
    # Terminal 2: ... -m "qwen/qwen3.6-plus-preview:free"
    # Terminal 3: ... -m "nvidia/nemotron-3-super-120b-a12b:free"
    # Terminal 4: ... -m "nvidia/nemotron-3-nano-30b-a3b:free"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from alethic import AgentConfig, MathAgent, PhysicsAgent
from alethic.eval.harness import load_benchmark

# Default budget: depth gets 8 iters, breadth gets 4×2=8 iters (configurable via CLI)
DEFAULT_DEPTH_ITERS = 8
DEFAULT_BREADTH_RUNS = 4
DEFAULT_BREADTH_ITERS = 2

_VERDICT_RANK = {
    "correct": 0, "minor_issues": 1, "fixable": 2,
    "major_flaw": 3, "unsolved": 4, "error": 5,
}


def _make_agent(domain: str, api_key: str, model: str, max_iterations: int):
    """Create an agent with OpenRouter-safe overrides."""
    overrides = {
        "max_iterations": max_iterations,
        "verbose": False,
        "variant_b": None,
        "adversarial_breaker": False,
        "best_of_n": 1,
        "model": model,
    }
    config = AgentConfig.from_preset("default", **overrides)
    cls = PhysicsAgent if domain == "physics" else MathAgent
    return cls(api_key=api_key, config=config)


def _solve_safe(agent, problem_text: str) -> dict:
    """Run agent.solve() with error handling. Sessions disabled for parallel safety."""
    try:
        result = agent.solve(problem_text, create_session=False)
        return {
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "solved": result.verdict.value == "correct",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "error",
            "confidence": 0.0,
            "solved": False,
            "error": str(exc)[:200],
        }


def run_depth(problem_text: str, domain: str, api_key: str, model: str,
              depth_iters: int = DEFAULT_DEPTH_ITERS) -> dict:
    """Depth condition: 1 run × depth_iters iterations."""
    agent = _make_agent(domain, api_key, model, depth_iters)
    return _solve_safe(agent, problem_text)


def run_breadth(problem_text: str, domain: str, api_key: str, model: str,
                breadth_runs: int = DEFAULT_BREADTH_RUNS,
                breadth_iters: int = DEFAULT_BREADTH_ITERS) -> dict:
    """Breadth condition: breadth_runs independent runs, pick best."""
    runs = []
    for _ in range(breadth_runs):
        agent = _make_agent(domain, api_key, model, breadth_iters)
        runs.append(_solve_safe(agent, problem_text))

    best_idx = min(
        range(len(runs)),
        key=lambda i: (_VERDICT_RANK.get(runs[i]["verdict"], 5), -runs[i]["confidence"]),
    )
    return {
        "verdict": runs[best_idx]["verdict"],
        "confidence": runs[best_idx]["confidence"],
        "solved": runs[best_idx]["solved"],
        "any_solved": any(r["solved"] for r in runs),
        "n_solved": sum(r["solved"] for r in runs),
        "runs": runs,
    }


def _format_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m = int(s) // 60
    return f"{m}m {int(s) % 60:02d}s" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def _build_report(results: list[dict], model: str,
                  depth_iters: int = DEFAULT_DEPTH_ITERS,
                  breadth_runs: int = DEFAULT_BREADTH_RUNS,
                  breadth_iters: int = DEFAULT_BREADTH_ITERS) -> dict:
    n = len(results)
    if n == 0:
        return {"model": model, "n": 0}

    depth_solves = sum(r["depth_solved"] for r in results)
    breadth_solves = sum(r["breadth_any_solved"] for r in results)

    # McNemar contingency table
    both = sum(r["depth_solved"] and r["breadth_any_solved"] for r in results)
    depth_only = sum(r["depth_solved"] and not r["breadth_any_solved"] for r in results)
    breadth_only = sum(not r["depth_solved"] and r["breadth_any_solved"] for r in results)
    neither = n - both - depth_only - breadth_only

    # McNemar chi-squared (with continuity correction)
    disc = depth_only + breadth_only
    if disc > 0:
        chi2 = (abs(depth_only - breadth_only) - 1) ** 2 / disc
    else:
        chi2 = 0.0

    # Per-problem aggregation
    per_problem: dict[str, dict] = {}
    for r in results:
        pid = r["problem_id"]
        if pid not in per_problem:
            per_problem[pid] = {"depth_solves": 0, "breadth_solves": 0, "n": 0}
        per_problem[pid]["n"] += 1
        per_problem[pid]["depth_solves"] += r["depth_solved"]
        per_problem[pid]["breadth_solves"] += r["breadth_any_solved"]

    per_problem_rates = {
        pid: {
            "depth_rate": d["depth_solves"] / d["n"],
            "breadth_rate": d["breadth_solves"] / d["n"],
            "n": d["n"],
            "winner": (
                "breadth" if d["breadth_solves"] > d["depth_solves"]
                else "depth" if d["depth_solves"] > d["breadth_solves"]
                else "tie"
            ),
        }
        for pid, d in per_problem.items()
    }

    return {
        "model": model,
        "n_observations": n,
        "depth_config": f"1×{depth_iters} iters",
        "breadth_config": f"{breadth_runs}×{breadth_iters} iters",
        "summary": {
            "depth_solve_rate": depth_solves / n,
            "breadth_solve_rate": breadth_solves / n,
            "delta": (breadth_solves - depth_solves) / n,
            "depth_mean_conf": sum(r["depth_confidence"] for r in results) / n,
            "breadth_mean_conf": sum(r["breadth_confidence"] for r in results) / n,
        },
        "mcnemar": {
            "both": both,
            "depth_only": depth_only,
            "breadth_only": breadth_only,
            "neither": neither,
            "chi2": chi2,
            "discordant": disc,
        },
        "per_problem": per_problem_rates,
        "raw": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Breadth vs Depth experiment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--model", "-m", required=True, help="OpenRouter model ID")
    parser.add_argument("--replications", "-r", type=int, default=3,
                        help="Replications per problem (default: 3)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON path (default: auto from model name)")
    parser.add_argument("--depth-iters", type=int, default=DEFAULT_DEPTH_ITERS,
                        help=f"Iterations for depth condition (default: {DEFAULT_DEPTH_ITERS})")
    parser.add_argument("--breadth-runs", type=int, default=DEFAULT_BREADTH_RUNS,
                        help=f"Independent runs for breadth condition (default: {DEFAULT_BREADTH_RUNS})")
    parser.add_argument("--breadth-iters", type=int, default=DEFAULT_BREADTH_ITERS,
                        help=f"Iterations per breadth run (default: {DEFAULT_BREADTH_ITERS})")
    parser.add_argument("--resume", action="store_true",
                        help="Skip problem×rep pairs already in output file")
    args = parser.parse_args()

    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        print("ERROR: OPENROUTER_API_KEY required", file=sys.stderr)
        sys.exit(1)

    # Setup OpenRouter client factory
    from alethic.client_factory import set_client_factory
    from alethic.openrouter import OpenRouterClient

    set_client_factory(lambda api_key: OpenRouterClient(api_key=or_key, model=args.model))

    # Force sequential for free models
    if ":free" in args.model:
        print(f"Free model detected — throttling enabled (4s between requests)")

    # Load benchmark
    problems = []
    for path in ["data/benchmarks/math-sample.json", "data/benchmarks/physics-sample.json"]:
        if os.path.exists(path):
            problems.extend(load_benchmark(path)["problems"])

    if not problems:
        print("ERROR: No benchmark problems found", file=sys.stderr)
        sys.exit(1)

    # Output path
    slug = args.model.split("/")[-1].replace(":free", "").replace(":", "-")
    output_path = args.output or f"data/calibration/bvd-{slug}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Resume: load existing results
    existing_results: list[dict] = []
    done_keys: set[tuple[str, int]] = set()
    if args.resume and os.path.exists(output_path):
        with open(output_path) as f:
            old = json.load(f)
        existing_results = old.get("raw", [])
        done_keys = {(r["problem_id"], r["replication"]) for r in existing_results}
        print(f"Resuming: {len(done_keys)} observations already done")

    results = list(existing_results)

    # Build work list
    work = [
        (prob, rep)
        for prob in problems
        for rep in range(args.replications)
        if (prob["id"], rep) not in done_keys
    ]
    total = len(work)
    total_all = len(problems) * args.replications

    depth_iters = args.depth_iters
    breadth_runs = args.breadth_runs
    breadth_iters = args.breadth_iters

    model_short = args.model.split("/")[-1]
    print(f"\nBreadth vs Depth: {model_short}")
    print(f"  Problems: {len(problems)}, Reps: {args.replications}, Remaining: {total}/{total_all}")
    print(f"  Depth:  1 run × {depth_iters} iters")
    print(f"  Breadth: {breadth_runs} runs × {breadth_iters} iters (={breadth_runs*breadth_iters} total)")
    print(f"  Output: {output_path}\n")

    t0 = time.time()
    depth_wins = sum(r["depth_solved"] and not r.get("breadth_any_solved", False) for r in results)
    breadth_wins = sum(not r["depth_solved"] and r.get("breadth_any_solved", False) for r in results)
    depth_total = sum(r["depth_solved"] for r in results)
    breadth_total = sum(r.get("breadth_any_solved", False) for r in results)

    for i, (prob, rep) in enumerate(work):
        pid = prob["id"]
        domain = prob.get("domain", "math")
        obs_num = len(done_keys) + i + 1
        elapsed = time.time() - t0
        eta = (elapsed / (i + 1)) * (total - i - 1) if i > 0 else 0
        problem_start = time.time()

        print(f"\n{'─'*60}")
        print(f"  Problem {obs_num}/{total_all} │ {pid} (rep {rep+1}/{args.replications}) │ "
              f"elapsed: {_format_dur(elapsed)} │ ETA: {_format_dur(eta)}")

        # Depth
        print(f"  depth ({depth_iters} iters)...", end=" ", flush=True)
        t1 = time.time()
        d = run_depth(prob["problem"], domain, or_key, args.model, depth_iters)
        print(f"{d['verdict']}@{d['confidence']:.2f} ({time.time()-t1:.0f}s)")

        # Breadth
        print(f"  breadth ({breadth_runs}×{breadth_iters})...", end=" ", flush=True)
        t1 = time.time()
        b = run_breadth(prob["problem"], domain, or_key, args.model, breadth_runs, breadth_iters)
        n_ok = b["n_solved"]
        print(f"{b['verdict']}@{b['confidence']:.2f} "
              f"({n_ok}/{breadth_runs} solved, {time.time()-t1:.0f}s)")

        # Running totals
        depth_total += d["solved"]
        breadth_total += b.get("any_solved", b["solved"])
        if d["solved"] and not b.get("any_solved", b["solved"]):
            depth_wins += 1
        elif not d["solved"] and b.get("any_solved", b["solved"]):
            breadth_wins += 1
        problem_dur = time.time() - problem_start
        print(f"  ── problem time: {_format_dur(problem_dur)} │ "
              f"running: D={depth_total}/{obs_num} B={breadth_total}/{obs_num} │ "
              f"discordant: D-only={depth_wins} B-only={breadth_wins}")

        row = {
            "problem_id": pid,
            "domain": domain,
            "replication": rep,
            "depth_verdict": d["verdict"],
            "depth_confidence": d["confidence"],
            "depth_solved": d["solved"],
            "breadth_verdict": b["verdict"],
            "breadth_confidence": b["confidence"],
            "breadth_any_solved": b["any_solved"],
            "breadth_n_solved": b["n_solved"],
        }
        results.append(row)

        # Incremental write
        report = _build_report(results, args.model, depth_iters, breadth_runs, breadth_iters)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

    # Final summary
    report = _build_report(results, args.model)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    s = report["summary"]
    m = report["mcnemar"]
    print(f"\n{'='*60}")
    print(f"Model: {model_short}")
    print(f"Depth solve rate:   {s['depth_solve_rate']:.3f}")
    print(f"Breadth solve rate: {s['breadth_solve_rate']:.3f}")
    print(f"Delta (B-D):        {s['delta']:+.3f}")
    print(f"McNemar: depth_only={m['depth_only']} breadth_only={m['breadth_only']} "
          f"chi2={m['chi2']:.2f}")
    print(f"Elapsed: {_format_dur(time.time()-t0)}")
    print(f"Report: {output_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
