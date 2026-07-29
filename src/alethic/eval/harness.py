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
from alethic.models import AgentConfig
from alethic.physics_agent import PhysicsAgent

_REQUIRED_PROBLEM_FIELDS = {"id", "domain", "problem", "expected_solvable"}

GATE_EPOCH = 2
"""Scoring-semantics epoch — bump whenever the meaning of a gate metric changes.

Distinct from ``anchor_sha256``: the digest says *which problem set* was run,
this says *how the outcomes were scored*. A verifier-prompt or verifier-model
change never touches the benchmark file, so only the epoch can invalidate a
comparison across such a change.

- Epoch 1 (implicit, pre-v3.8): ``solve_rate`` = solved / all problems, mixing
  solvable problems and false-claim anchors into one denominator.
- Epoch 2: metric split — ``solve_rate`` covers solvable problems only, and
  false-claim anchors are scored separately by ``false_claim_accept_rate``.

Reports from different epochs are not comparable.
"""


def anchor_sha256(benchmark: dict[str, Any]) -> str:
    """Return a stable digest of a benchmark's frozen anchor set.

    Hashes ``(id, domain, problem, expected_solvable)`` for every problem,
    sorted by id — so the digest is invariant to problem ordering in the file
    but changes if any problem's text, domain, or solvability flag is edited,
    or if a problem is added or removed. ``domain`` is included because it
    selects the agent class and is therefore part of what was measured.

    Two benchmark reports are only comparable when both their ``anchor_sha256``
    and their ``gate_epoch`` match.
    """
    payload = json.dumps(
        [
            [
                p["id"],
                p.get("domain", "math"),
                p["problem"],
                bool(p["expected_solvable"]),
            ]
            for p in sorted(benchmark.get("problems", []), key=lambda q: str(q["id"]))
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Split benchmark outcomes into a solve rate and a false-claim accept rate.

    ``expected_solvable`` partitions a benchmark into two populations that
    answer different questions, and pooling them into one rate hides both:

    - **Solvable problems** measure capability. An errored run is a failure to
      solve, so it stays in the ``solve_rate`` denominator.
    - **False-claim anchors** measure verifier bias. Every "proof" of a false
      claim is wrong by construction, so a ``solved`` outcome is a false
      positive. An errored run produced no verdict — it is a non-observation
      and is excluded from the denominator, because counting it as a rejection
      would bias the result toward the reassuring answer.

    ``false_claim_accept_rate`` is the primary number: it is the only metric in
    this repo that responds to verifier *bias* rather than verifier *variance*
    (K-consensus reduces the latter, not the former).

    ``false_claim_reject_rate`` is its complement and is **not** a false-premise
    *detection* rate. ``AgentResult.solved`` is false whenever the agent failed
    to reach a CORRECT verdict, so it lumps genuine premise rejection together
    with plain budget exhaustion. ``false_claim_verdicts`` exposes that split.

    Both anchor rates are ``None`` — not ``0.0`` — when nothing was scored, so
    "no anchors in this benchmark" cannot be misread as "accepted nothing".
    """
    solvable = [r for r in results if bool(r.get("expected_solvable"))]
    anchors = [r for r in results if not bool(r.get("expected_solvable"))]

    solved = sum(1 for r in solvable if r.get("solved"))

    scored = [r for r in anchors if not r.get("error")]
    accepted = sum(1 for r in scored if r.get("solved"))
    n_scored = len(scored)

    verdicts: dict[str, int] = {}
    for r in anchors:
        verdict = str(r.get("verdict", "unknown"))
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

    return {
        "n_solvable": len(solvable),
        "n_false_claim": len(anchors),
        "n_false_claim_scored": n_scored,
        "n_errors": sum(1 for r in results if r.get("error")),
        "solved": solved,
        "solve_rate": solved / len(solvable) if solvable else 0.0,
        "false_claims_accepted": accepted,
        "false_claim_accept_rate": (accepted / n_scored if n_scored else None),
        "false_claim_reject_rate": ((n_scored - accepted) / n_scored if n_scored else None),
        "false_claim_verdicts": verdicts,
    }


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
) -> dict[str, Any]:
    """Run all problems in a benchmark file and return a score report.

    Args:
        path: Path to the benchmark JSON file.
        api_key: Anthropic API key (default: ANTHROPIC_API_KEY env var).
        preset: AgentConfig preset to use for all problems.
        verbose: Print progress to stdout.

    Returns:
        Dict with: benchmark, preset, anchor_sha256, gate_epoch, total,
                   avg_confidence, avg_iterations, elapsed_seconds, results
                   (list per problem), plus every key from ``split_metrics``
                   (solve_rate, false_claim_accept_rate, ...).

        ``solve_rate`` covers solvable problems only (epoch 2 semantics); it is
        not comparable to a pre-split report. See ``GATE_EPOCH``.
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
                "error": None,
            }
            # Atom measurement
            atom_metrics = measure_atoms(result.events, result.iterations_used)
            outcome["atom_metrics"] = atom_metrics
            # PUCT divergence measurement
            puct_metrics = compute_puct_comparison(result.events)
            outcome["puct_divergence"] = puct_metrics
        except Exception as exc:  # noqa: BLE001
            outcome |= {
                "solved": False,
                "verdict": "error",
                "confidence": 0.0,
                "iterations_used": 0,
                "error": str(exc),
            }
            outcome["atom_metrics"] = None
            outcome["puct_divergence"] = None

        results.append(outcome)
        if verbose:
            # Population-aware: a single pass/fail boolean would mean two
            # different things across the two populations — the conflation
            # split_metrics() exists to remove.
            if outcome["error"]:
                status = "ERROR"
            elif expected_solvable:
                status = "SOLVED" if outcome["solved"] else "unsolved"
            else:
                status = "ACCEPTED(FP)" if outcome["solved"] else "rejected"
            print(f"  {status} verdict={outcome['verdict']} conf={outcome['confidence']:.2f}")

    elapsed = time.time() - start
    total = len(results)

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
        "anchor_sha256": anchor_sha256(benchmark),
        "gate_epoch": GATE_EPOCH,
        "total": total,
        **split_metrics(results),
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
