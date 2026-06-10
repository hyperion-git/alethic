"""Benchmark evaluation harness for Alethic (feature 2.3).

Runs a curated set of problems through MathAgent or PhysicsAgent and
produces a score report: solve rate, average confidence, average iterations.

Usage:
    alethic eval run data/benchmarks/math-sample.json --preset quick
"""

from __future__ import annotations

import hashlib as _hashlib
import json
import math as _math
import time
from pathlib import Path
from typing import Any, cast

from alethic.agent import MathAgent
from alethic.models import AgentConfig, EventType, SearchConfig
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


def _ucb1_score(
    confidence: float,
    visit_count: int,
    total_visits: int,
    exploration_weight: float = 1.41,
) -> float:
    """UCB1 score for a candidate's approach type."""
    if visit_count == 0:
        return float("inf")
    return confidence + exploration_weight * _math.sqrt(
        _math.log(total_visits) / visit_count
    )


def compute_puct_comparison(events: list) -> dict:
    """Post-hoc PUCT scoring on the flat candidate pool.

    For each iteration with N>1 candidates, classify each candidate's
    approach type, compute UCB1 scores, and compare the PUCT-selected
    best to the confidence-selected best.

    Approach types use ``error_category`` from VERIFY events (added in v3.7).
    Passing solutions are hashed by verdict string for diversity.

    Returns dict with reordered_iterations, total_iterations, divergence_rate.
    """
    # Group VERIFY events by iteration
    iter_verifications: dict[int, list[dict]] = {}
    for e in events:
        if e.type.value == "verify":
            it = e.iteration
            iter_verifications.setdefault(it, []).append(e.data)

    # Track approach type visit counts across iterations
    approach_visits: dict[str, int] = {}
    total_visits = 0
    reordered = 0
    multi_candidate_iters = 0

    for it in sorted(iter_verifications):
        candidates = iter_verifications[it]
        if len(candidates) <= 1:
            continue

        multi_candidate_iters += 1

        scored: list[tuple[int, float, float, str]] = []
        for idx, cand in enumerate(candidates):
            conf = cand.get("confidence", 0.0)
            verdict = cand.get("verdict", "")
            error_cat = cand.get("error_category", "general")

            if verdict in ("correct", "minor_issues"):
                h = _hashlib.md5(f"{verdict}:{conf:.2f}".encode()).hexdigest()[:6]
                approach = f"pass:{h}"
            else:
                approach = f"fail:{error_cat}"

            visits = approach_visits.get(approach, 0)
            ucb1 = _ucb1_score(conf, visits, max(total_visits, 1))
            scored.append((idx, conf, ucb1, approach))

        for _, _, _, approach in scored:
            approach_visits[approach] = approach_visits.get(approach, 0) + 1
            total_visits += 1

        best_conf_idx = max(scored, key=lambda x: x[1])[0]
        best_ucb1_idx = max(scored, key=lambda x: x[2])[0]

        if best_conf_idx != best_ucb1_idx:
            reordered += 1

    return {
        "reordered_iterations": reordered,
        "total_iterations": multi_candidate_iters,
        "divergence_rate": reordered / max(multi_candidate_iters, 1),
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
    search_mode: str = "flat",
) -> dict[str, Any]:
    """Run all problems in a benchmark file and return a score report.

    Args:
        path: Path to the benchmark JSON file.
        api_key: Anthropic API key (default: ANTHROPIC_API_KEY env var).
        preset: AgentConfig preset to use for all problems.
        verbose: Print progress to stdout.
        search_mode: "flat" (default) or "tree" — runs every problem through
            the v3.8 hierarchical proof search for flat-vs-tree gate comparison.

    Returns:
        Dict with: name, preset, total, solved, solve_rate, avg_confidence,
                   avg_iterations, elapsed_seconds, results (list per problem).
    """
    if search_mode not in ("flat", "tree"):
        raise ValueError(
            f"search_mode must be 'flat' or 'tree', got {search_mode!r}"
        )
    benchmark = load_benchmark(path)
    config_overrides: dict[str, Any] = {"verbose": verbose}
    if search_mode == "tree":
        config_overrides["search_mode"] = "tree"
        config_overrides["search"] = (
            SearchConfig.from_preset(preset)
            if preset in SearchConfig.PRESETS
            else SearchConfig()
        )
    config = AgentConfig.from_preset(preset, **config_overrides)

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
            # PUCT divergence measurement
            puct_metrics = compute_puct_comparison(result.events)
            outcome["puct_divergence"] = puct_metrics
            if search_mode == "tree":
                # NOTE: these counts are event-derived and may undercount if events
                # were truncated (e.g. checkpoint-resume discards prior-segment events).
                outcome["bridges_used"] = sum(
                    1 for e in result.events if e.type == EventType.BRIDGE_GENERATED
                )
                outcome["gaps_filled"] = sum(
                    1 for e in result.events if e.type == EventType.GAP_FILLED
                )
            else:
                outcome["bridges_used"] = None
                outcome["gaps_filled"] = None
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
            outcome["bridges_used"] = None
            outcome["gaps_filled"] = None

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

    all_divergence_rates = [
        r["puct_divergence"]["divergence_rate"]
        for r in results
        if r.get("puct_divergence") is not None
    ]

    return {
        "benchmark": benchmark.get("name", Path(path).stem),
        "preset": preset,
        "search_mode": search_mode,
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
        "mean_puct_divergence": (
            sum(all_divergence_rates) / len(all_divergence_rates)
            if all_divergence_rates
            else 0.0
        ),
        "results": results,
    }
