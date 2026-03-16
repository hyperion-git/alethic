"""Benchmark evaluation harness for Alethic (feature 2.3).

Runs a curated set of problems through MathAgent or PhysicsAgent and
produces a score report: solve rate, average confidence, average iterations.

Usage:
    alethic eval run data/benchmarks/math-sample.json --preset quick
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

from alethic.agent import MathAgent
from alethic.models import AgentConfig
from alethic.physics_agent import PhysicsAgent

_REQUIRED_PROBLEM_FIELDS = {"id", "domain", "problem", "expected_solvable"}


def measure_atoms(
    events: list,
    n_iterations: int,
) -> dict[str, Any]:
    """Compute atom metrics from a completed run's event log.

    Extracts winning solutions per iteration from GENERATE events,
    parses atoms, and computes annotation_rate and per-iteration atom_counts.

    KNOWN LIMITATION: solution_preview in GENERATE events is truncated to
    500 chars (agent.py:1080). Atom counts may be understated for long
    solutions. A future improvement could store full solution text in events
    or increase the preview length.
    """
    from alethic.atoms import parse_atoms

    # Extract winning solution text per iteration.
    # For best-of-N, the VERIFY event with highest confidence per iteration
    # identifies the winning candidate. We then look up that candidate's
    # GENERATE event for the solution text.
    iter_candidates: dict[int, dict[int, str]] = {}  # iter -> {candidate -> text}
    iter_best: dict[int, tuple[int, float]] = {}  # iter -> (best_cand, best_conf)

    for e in events:
        if e.type.value == "generate":
            it = e.iteration
            cand = e.data.get("candidate", 1)
            text = e.data.get("solution_preview", "")
            iter_candidates.setdefault(it, {})[cand] = text
        elif e.type.value == "verify":
            it = e.iteration
            cand = e.data.get("candidate", 1)
            conf = e.data.get("confidence", 0.0)
            if it not in iter_best or conf > iter_best[it][1]:
                iter_best[it] = (cand, conf)

    iter_solutions: dict[int, str] = {}
    for it, (best_cand, _) in iter_best.items():
        candidates = iter_candidates.get(it, {})
        iter_solutions[it] = candidates.get(best_cand, "")

    atom_counts: list[int] = []
    annotated_iters = 0

    for it in range(1, n_iterations + 1):
        text = iter_solutions.get(it, "")
        atoms = parse_atoms(text)
        non_synthetic = [a for a in atoms if not a.synthetic]
        count = len(non_synthetic)
        atom_counts.append(count)

        if count > 0:
            annotated_iters += 1

    annotation_rate = annotated_iters / max(n_iterations, 1)

    return {
        "annotation_rate": annotation_rate,
        "atom_counts": atom_counts,
        "mean_atom_count": sum(atom_counts) / max(len(atom_counts), 1),
    }


def load_benchmark(path: str) -> dict[str, Any]:
    """Load and validate a benchmark JSON file.

    Args:
        path: Path to the benchmark JSON file.

    Returns:
        The benchmark dict with "name", "version", and "problems" keys.

    Raises:
        ValueError: If any problem is missing required fields.
        FileNotFoundError: If the file does not exist.
    """
    data = cast("dict[str, Any]", json.loads(Path(path).read_text(encoding="utf-8")))
    for problem in data.get("problems", []):
        missing = _REQUIRED_PROBLEM_FIELDS - problem.keys()
        if missing:
            raise ValueError(
                f"Problem {problem.get('id', '?')} missing required field(s): {missing}"
            )
    return data


def run_benchmark(
    path: str,
    *,
    api_key: str | None = None,
    preset: str = "quick",
    verbose: bool = False,
) -> dict[str, Any]:
    """Run all problems in a benchmark file and return a score report.

    Args:
        path: Path to the benchmark JSON file.
        api_key: Anthropic API key (default: ANTHROPIC_API_KEY env var).
        preset: AgentConfig preset to use for all problems.
        verbose: Print progress to stdout.

    Returns:
        Dict with: name, preset, total, solved, solve_rate, avg_confidence,
                   avg_iterations, elapsed_seconds, results (list per problem).
    """
    benchmark = load_benchmark(path)
    config = AgentConfig.from_preset(preset, verbose=verbose)

    results = []
    start = time.time()

    for problem_spec in benchmark["problems"]:
        pid = problem_spec["id"]
        domain = problem_spec.get("domain", "math")
        expected_solvable = problem_spec["expected_solvable"]

        if verbose:
            print(f"[{pid}] Running ({domain})...")

        agent_cls = PhysicsAgent if domain == "physics" else MathAgent
        agent = agent_cls(config=config, api_key=api_key)

        outcome: dict[str, Any] = {
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
            # Atom measurement
            atom_metrics = measure_atoms(result.events, result.iterations_used)
            outcome["atom_metrics"] = atom_metrics
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

        results.append(outcome)
        if verbose:
            status = "OK" if outcome["correct_prediction"] else "FAIL"
            print(f"  {status} verdict={outcome['verdict']} conf={outcome['confidence']:.2f}")

    elapsed = time.time() - start
    total = len(results)
    solved_count = sum(1 for r in results if r["solved"])

    all_annotation_rates = [
        r["atom_metrics"]["annotation_rate"]
        for r in results
        if r.get("atom_metrics") is not None
    ]

    return {
        "benchmark": benchmark.get("name", Path(path).stem),
        "preset": preset,
        "total": total,
        "solved": solved_count,
        "solve_rate": solved_count / total if total else 0.0,
        "avg_confidence": sum(r["confidence"] for r in results) / total if total else 0.0,
        "avg_iterations": sum(r["iterations_used"] for r in results) / total if total else 0.0,
        "elapsed_seconds": round(elapsed, 2),
        "mean_annotation_rate": (
            sum(all_annotation_rates) / len(all_annotation_rates)
            if all_annotation_rates
            else 0.0
        ),
        "results": results,
    }
