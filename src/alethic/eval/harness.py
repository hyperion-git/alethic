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
        missing = _REQUIRED_PROBLEM_FIELDS - set(problem.keys())
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
        problem_text = problem_spec["problem"]
        expected_solvable = problem_spec["expected_solvable"]

        if verbose:
            print(f"[{pid}] Running ({domain})...")

        agent: MathAgent
        if domain == "physics":
            agent = PhysicsAgent(config=config, api_key=api_key)
        else:
            agent = MathAgent(config=config, api_key=api_key)

        try:
            result = agent.solve(problem_text)
            outcome = {
                "id": pid,
                "domain": domain,
                "expected_solvable": expected_solvable,
                "solved": result.solved,
                "verdict": result.verdict.value,
                "confidence": result.confidence,
                "iterations_used": result.iterations_used,
                "correct_prediction": result.solved == expected_solvable,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            outcome = {
                "id": pid,
                "domain": domain,
                "expected_solvable": expected_solvable,
                "solved": False,
                "verdict": "error",
                "confidence": 0.0,
                "iterations_used": 0,
                "correct_prediction": False,
                "error": str(exc),
            }

        results.append(outcome)
        if verbose:
            status = "OK" if outcome["correct_prediction"] else "FAIL"
            print(f"  {status} verdict={outcome['verdict']} conf={outcome['confidence']:.2f}")

    elapsed = time.time() - start
    solved_count = sum(1 for r in results if r["solved"])
    total = len(results)
    confidences = [r["confidence"] for r in results]
    iterations = [r["iterations_used"] for r in results]

    return {
        "benchmark": benchmark.get("name", Path(path).stem),
        "preset": preset,
        "total": total,
        "solved": solved_count,
        "solve_rate": solved_count / total if total > 0 else 0.0,
        "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "avg_iterations": sum(iterations) / len(iterations) if iterations else 0.0,
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
    }
