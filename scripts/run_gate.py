#!/usr/bin/env python3
"""Gate benchmark runner via Claude Code subscription.

Runs the 100-problem gate-v38.json benchmark through /alethic-solve and
/alethic-derive skills via `claude -p`, then harvests session directories
to compute gate metrics (annotation_rate, puct_divergence, solve_rate).

Requires the alethic package (for atom parsing and error classification in harvest mode).

Setup (one-time):
    python scripts/run_gate.py --setup-env          # creates alethic-gate micromamba env

Usage:
    micromamba run -n alethic-gate python scripts/run_gate.py              # run all
    micromamba run -n alethic-gate python scripts/run_gate.py --harvest-only  # harvest only
    micromamba run -n alethic-gate python scripts/run_gate.py --dry-run       # preview

Or use an existing env with alethic installed (e.g., micromamba run -n alethic ...).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path

# Project root (scripts/ is one level below)
ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = ROOT / "data" / "benchmarks" / "gate-v38.json"
ALETHIC_DIR = ROOT / ".alethic"
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
    import sys

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
    print(f"  {MICROMAMBA} run -n {ENV_NAME} python scripts/run_gate.py")


def load_benchmark(path: Path) -> list[dict]:
    """Load and return the problems list from a benchmark JSON file."""
    with open(path) as f:
        data = json.load(f)
    return data["problems"]


def normalize_text(text: str) -> str:
    """Normalize text for comparison: strip whitespace, NFC unicode."""
    return unicodedata.normalize("NFC", text.strip())


def find_existing_sessions(problems: list[dict]) -> dict[str, Path]:
    """Map problem IDs to existing session dirs (for resume support).

    Matches sessions by normalized problem text. If multiple sessions match,
    prefers solved over unsolved, then latest by created_at.
    """
    if not ALETHIC_DIR.exists():
        return {}

    # Build normalized problem text -> ID lookup
    text_to_id: dict[str, str] = {}
    for p in problems:
        text_to_id[normalize_text(p["problem"])] = p["id"]

    # Scan session dirs
    matches: dict[str, list[tuple[Path, dict]]] = {}
    for session_dir in ALETHIC_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        session_json = session_dir / "session.json"
        if not session_json.exists():
            continue
        try:
            with open(session_json) as f:
                session = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        problem_text = normalize_text(session.get("problem", ""))
        pid = text_to_id.get(problem_text)
        if pid is not None:
            matches.setdefault(pid, []).append((session_dir, session))

    # Dedup: prefer solved, then latest
    result: dict[str, Path] = {}
    for pid, candidates in matches.items():
        candidates.sort(
            key=lambda x: (
                x[1].get("status") == "solved",  # solved first
                x[1].get("created_at", ""),  # then latest
            ),
            reverse=True,
        )
        result[pid] = candidates[0][0]

    return result


def driver(problems: list[dict], *, dry_run: bool = False) -> None:
    """Run each problem through claude -p with the appropriate skill."""
    existing = find_existing_sessions(problems)
    total = len(problems)

    for i, problem in enumerate(problems, 1):
        pid = problem["id"]
        domain = problem["domain"]
        skill = "/alethic-solve" if domain == "math" else "/alethic-derive"

        if pid in existing:
            print(f"[{i}/{total}] SKIP {pid} (session exists)")
            continue

        print(f"[{i}/{total}] {pid} ({domain}) ...", flush=True)
        start = time.time()

        # Write problem to temp file to avoid shell quoting issues
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as pf:
            pf.write(problem["problem"])
            prob_path = pf.name

        prompt = f'{skill} -p default --file {prob_path}'

        if dry_run:
            print(f"  DRY RUN: claude -p {prompt!r}")
            continue

        try:
            subprocess.run(
                ["claude", "-p", prompt],
                check=False,
                timeout=1800,  # 30 min per problem max
            )
        except subprocess.TimeoutExpired:
            print("  TIMEOUT after 30 min")
        except Exception as e:
            print(f"  ERROR: {e}")

        elapsed = time.time() - start
        print(f"  done ({elapsed:.0f}s)")


def harvest(problems: list[dict]) -> dict:
    """Read .alethic/ session dirs and compute gate metrics."""
    try:
        from alethic.atoms import parse_atoms  # noqa: F401
    except ImportError:
        print(
            "ERROR: alethic package not found. Run with:\n"
            "  micromamba run -n alethic python scripts/run_gate.py --harvest-only",
            file=__import__("sys").stderr,
        )
        raise SystemExit(1) from None

    sessions = find_existing_sessions(problems)
    results = []
    all_annotation_rates = []
    all_puct_divergences = []

    for problem in problems:
        pid = problem["id"]
        session_dir = sessions.get(pid)

        if session_dir is None:
            results.append({
                "id": pid,
                "domain": problem["domain"],
                "expected_solvable": problem["expected_solvable"],
                "solved": False,
                "error": "no session found",
            })
            continue

        # Read session.json
        with open(session_dir / "session.json") as f:
            session = json.load(f)

        solved = session.get("status") == "solved"
        confidence = session.get("best_confidence", 0.0)

        # Read events.jsonl
        events_path = session_dir / "worklog" / "events.jsonl"
        events = []
        if events_path.exists():
            with open(events_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        # Atom metrics: read full candidate files (not truncated previews)
        atom_metrics = _measure_atoms_from_files(session_dir, events)
        if atom_metrics and atom_metrics["annotation_rate"] is not None:
            all_annotation_rates.append(atom_metrics["annotation_rate"])

        # PUCT metrics from events
        puct_metrics = _compute_puct_from_events(events)
        if puct_metrics and puct_metrics["total_iterations"] > 0:
            all_puct_divergences.append(puct_metrics["divergence_rate"])

        results.append({
            "id": pid,
            "domain": problem["domain"],
            "expected_solvable": problem["expected_solvable"],
            "solved": solved,
            "confidence": confidence,
            "atom_metrics": atom_metrics,
            "puct_divergence": puct_metrics,
        })

    total = len(problems)
    solved_count = sum(1 for r in results if r.get("solved"))

    # Aggregate atom counts across all problems
    all_atom_counts = []
    for r in results:
        am = r.get("atom_metrics")
        if am and am.get("mean_atom_count") is not None:
            all_atom_counts.append(am["mean_atom_count"])

    return {
        "benchmark": "gate-v38",
        "preset": "default",
        "total": total,
        "solved": solved_count,
        "solve_rate": solved_count / total if total else 0.0,
        "mean_annotation_rate": (
            sum(all_annotation_rates) / len(all_annotation_rates)
            if all_annotation_rates
            else 0.0
        ),
        "mean_atom_count": (
            sum(all_atom_counts) / len(all_atom_counts)
            if all_atom_counts
            else 0.0
        ),
        "mean_puct_divergence": (
            sum(all_puct_divergences) / len(all_puct_divergences)
            if all_puct_divergences
            else 0.0
        ),
        "results": results,
    }


def _measure_atoms_from_files(
    session_dir: Path, events: list[dict]
) -> dict | None:
    """Measure atom annotations from full solution files in worklog."""
    from alethic.atoms import parse_atoms

    worklog = session_dir / "worklog"
    if not worklog.exists():
        return None

    # Find winning candidate per iteration from VERIFY events
    iter_best: dict[int, tuple[int, float]] = {}
    for e in events:
        if e.get("type") == "verify":
            it = e.get("iteration", 0)
            cand = e.get("candidate", 1)
            conf = e.get("confidence", 0.0)
            if it not in iter_best or conf > iter_best[it][1]:
                iter_best[it] = (cand, conf)

    if not iter_best:
        return {"annotation_rate": 0.0, "atom_counts": [], "mean_atom_count": 0.0}

    # Read winning candidate solutions and parse atoms
    atom_counts = []
    annotated = 0
    for it in sorted(iter_best):
        cand_idx = iter_best[it][0]
        # Try candidate file first, fall back to solution.md
        cand_file = worklog / f"candidate_{cand_idx}.md"
        if not cand_file.exists():
            cand_file = worklog / "solution.md"
        if not cand_file.exists():
            atom_counts.append(0)
            continue

        text = cand_file.read_text()
        atoms = parse_atoms(text)
        non_synthetic = [a for a in atoms if not a.synthetic]
        count = len(non_synthetic)
        atom_counts.append(count)
        if count > 0:
            annotated += 1

    n_iters = len(iter_best)
    return {
        "annotation_rate": annotated / n_iters if n_iters else 0.0,
        "atom_counts": atom_counts,
        "mean_atom_count": (
            sum(atom_counts) / len(atom_counts) if atom_counts else 0.0
        ),
    }


def _compute_puct_from_events(events: list[dict]) -> dict | None:
    """Compute PUCT divergence from flat JSONL events.

    Reimplements the UCB1 algorithm from eval/harness.py for flat dicts.
    Must match harness.py exactly: visit counts updated AFTER scoring
    all candidates, exploration_weight=1.41, visit_count==0 → inf,
    approach hashed by verdict:confidence (not iteration:candidate).
    """
    import hashlib
    import math

    exploration_weight = 1.41

    def _ucb1_score(
        confidence: float, visit_count: int, total_visits: int
    ) -> float:
        if visit_count == 0:
            return float("inf")
        exploitation = confidence
        exploration = exploration_weight * math.sqrt(
            math.log(max(total_visits, 1)) / visit_count
        )
        return exploitation + exploration

    # Group VERIFY events by iteration
    iter_verifications: dict[int, list[dict]] = {}
    for e in events:
        if e.get("type") == "verify":
            it = e.get("iteration", 0)
            iter_verifications.setdefault(it, []).append(e)

    approach_visits: dict[str, int] = {}
    total_visits = 0
    reordered = 0
    counted = 0

    for it in sorted(iter_verifications):
        candidates = iter_verifications[it]
        if len(candidates) < 2:
            continue  # Need N>=2 for PUCT to diverge

        counted += 1

        # Score all candidates using PRE-iteration visit counts
        scored: list[tuple[float, float, str]] = []
        for cand in candidates:
            verdict = cand.get("verdict", "")
            conf = cand.get("confidence", 0.0)
            error_cat = cand.get("error_category", "general")

            if verdict in ("correct", "minor_issues"):
                # Hash by verdict:confidence (matches harness.py)
                h = hashlib.md5(
                    f"{verdict}:{conf:.2f}".encode()
                ).hexdigest()[:6]
                approach = f"pass:{h}"
            else:
                approach = f"fail:{error_cat}"

            visits = approach_visits.get(approach, 0)
            ucb1 = _ucb1_score(conf, visits, total_visits)
            scored.append((conf, ucb1, approach))

        # Update visit counts AFTER scoring (matches harness.py)
        for _, _, approach in scored:
            approach_visits[approach] = approach_visits.get(approach, 0) + 1
            total_visits += 1

        # Compare: does PUCT pick a different best than confidence?
        conf_best = max(range(len(scored)), key=lambda i: scored[i][0])
        puct_best = max(range(len(scored)), key=lambda i: scored[i][1])
        if conf_best != puct_best:
            reordered += 1

    return {
        "reordered_iterations": reordered,
        "total_iterations": counted,
        "divergence_rate": reordered / counted if counted else 0.0,
    }


def report(gate_data: dict) -> None:
    """Print gate decision report."""
    print("\n" + "=" * 60)
    print("GATE EXPERIMENT RESULTS")
    print("=" * 60)
    print(f"Problems: {gate_data['total']}")
    print(f"Solved:   {gate_data['solved']} ({gate_data['solve_rate']:.1%})")
    print(f"Annotation rate: {gate_data['mean_annotation_rate']:.2f}")
    print(f"PUCT divergence: {gate_data['mean_puct_divergence']:.2f}")
    print()

    # Gate decision
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

    # Write JSON report
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
        "--harvest-only",
        action="store_true",
        help="Skip driver, just harvest existing sessions",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
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

    problems = load_benchmark(args.benchmark)
    print(f"Loaded {len(problems)} problems from {args.benchmark}")

    if not args.harvest_only:
        driver(problems, dry_run=args.dry_run)

    if not args.dry_run:
        gate_data = harvest(problems)
        report(gate_data)


if __name__ == "__main__":
    main()
