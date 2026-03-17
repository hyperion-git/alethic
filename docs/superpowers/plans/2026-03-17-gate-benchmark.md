# Gate Benchmark Suite Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a 100-problem benchmark suite, patch the skill orchestrator to emit `error_category` in verify events, and build a subscription runner script that executes the benchmark via `claude -p`.

**Architecture:** Three independent deliverables — a JSON benchmark file (data-only), an orchestrator patch (skill file edits), and a Python driver/harvester script. Each can be built and tested independently.

**Tech Stack:** JSON (benchmark data), Markdown (orchestrator patch), Python 3.13 (runner script), pytest (testing).

**Spec:** `docs/superpowers/specs/2026-03-17-gate-benchmark-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `data/benchmarks/gate-v38.json` | Create | 100-problem benchmark (45 math + 45 physics + 10 false claims) |
| `tests/test_eval.py` | Modify | Add `test_gate_benchmark_loads()` |
| `skills/alethic-common/orchestrator.md` | Modify | Align classifier (split counterexample), add error_category to verify events |
| `scripts/run_gate.py` | Create | Driver (iterate problems via `claude -p`) + harvester (read sessions, compute metrics) |
| `tests/test_gate_runner.py` | Create | Tests for harvester session matching, dedup, metric extraction |

---

## Chunk 1: Benchmark JSON + Loading Test

### Task 1: Write the benchmark loading test

**Files:**
- Modify: `tests/test_eval.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_eval.py` at the end of the file:

```python
class TestGateBenchmark:
    def test_gate_benchmark_loads(self):
        """gate-v38.json loads successfully with all 100 problems."""
        from alethic.eval.harness import load_benchmark
        import os

        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "benchmarks",
            "gate-v38.json",
        )
        bench = load_benchmark(path)
        assert bench["name"] == "gate-v38"
        assert len(bench["problems"]) == 100

        # Domain split
        math_count = sum(1 for p in bench["problems"] if p["domain"] == "math")
        physics_count = sum(1 for p in bench["problems"] if p["domain"] == "physics")
        assert math_count == 45, f"Expected 45 math, got {math_count}"
        assert physics_count == 45, f"Expected 45 physics, got {physics_count}"

        # All IDs unique
        ids = [p["id"] for p in bench["problems"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

        # False claims count
        false_count = sum(1 for p in bench["problems"] if not p["expected_solvable"])
        assert false_count == 10, f"Expected 10 false claims, got {false_count}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_eval.py::TestGateBenchmark::test_gate_benchmark_loads -v`
Expected: FAIL (file not found or FileNotFoundError)

- [ ] **Step 3: Commit test**

```bash
git add tests/test_eval.py
git commit -m "test(eval): add gate benchmark loading test

Validates gate-v38.json has 100 problems (45 math, 45 physics),
all IDs unique, 10 false claims."
```

### Task 2: Create gate-v38.json

**Files:**
- Create: `data/benchmarks/gate-v38.json`

- [ ] **Step 1: Create the benchmark file**

Create `data/benchmarks/gate-v38.json` with all 100 problems from the spec (Section 5). The file must follow the exact schema from existing benchmarks:

```json
{
  "name": "gate-v38",
  "version": "1.0",
  "description": "100-problem gate benchmark for v3.8 E-vs-F decision and v4.0 regression baseline. 45 math + 45 physics + 10 false claims.",
  "problems": [
    ... all 100 problems from spec Section 5 ...
  ]
}
```

Each problem entry has:
- Required: `"id"`, `"domain"` ("math"/"physics"), `"problem"`, `"expected_solvable"` (bool)
- Optional: `"tags"` (array), `"difficulty"` (string), `"source"` (string), `"note"` (string for false claims)

Source the exact problem texts from the spec at `docs/superpowers/specs/2026-03-17-gate-benchmark-design.md`, Section 5.1 (math) and Section 5.2 (physics).

**Critical:** For the 20 problems shared with existing `math-sample.json` and `physics-sample.json`, **the existing file text takes precedence** over the spec table. Copy `id` and `problem` fields verbatim from the existing files (some spec table entries have minor text divergence, e.g., `infinite-square-well`). For the 80 new problems, use the spec text.

- [ ] **Step 2: Validate JSON is well-formed**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -c "import json; d=json.load(open('data/benchmarks/gate-v38.json')); print(f'{len(d[\"problems\"])} problems loaded')"`
Expected: `100 problems loaded`

- [ ] **Step 3: Run the loading test**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_eval.py::TestGateBenchmark::test_gate_benchmark_loads -v`
Expected: PASS

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest -q --tb=line`
Expected: All tests pass (1247+ passed)

- [ ] **Step 5: Commit**

```bash
git add data/benchmarks/gate-v38.json
git commit -m "feat(eval): add 100-problem gate benchmark suite

45 math (10 easy, 17 medium, 8 hard, 5 competition, 5 false claims)
+ 45 physics (10 easy, 16 medium, 9 hard, 5 graduate, 5 false claims).

Includes all 20 problems from existing sample benchmarks verbatim.
15 physics problems tagged multi-method for PUCT signal."
```

---

## Chunk 2: Orchestrator error_category Patch

### Task 3: Align orchestrator classifier — split counterexample

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

The orchestrator has two inline classifiers that both need updating. Read the file first and locate:
1. The adaptive compute classifier (search for `algebra.*logic.*citation` or `error_category` near lines 340-360)
2. The revision strategy addendum classifier (search for the second occurrence of category keywords near lines 580-600)

- [ ] **Step 1: Read the orchestrator to find both classifiers**

Read: `skills/alethic-common/orchestrator.md`
Search for: both inline classifier locations and the Dynamic N escalation category list

- [ ] **Step 2: Update classifier 1 — split counterexample from missing_case**

In the first inline classifier (adaptive compute, ~lines 343-350):
- Remove `counterexample` from the `missing_case` keyword list
- Add a new `counterexample` category with keywords: `counterexample`, `flaw found`, `breaker found`, `regime failure`, `falsif`
- Place `counterexample` before `missing_case` in priority order (matching `error_taxonomy.py`)

- [ ] **Step 3: Update Dynamic N escalation list**

In the Dynamic N escalation table (~lines 356-361), add `counterexample` to the escalation row alongside `logic`, `missing_case`, `interpretation`, `units`. This preserves the existing behavior where counterexample critiques triggered N-escalation.

- [ ] **Step 4: Update classifier 2 — revision strategy addendum**

In the second inline classifier (revision addendum, ~lines 589-596):
- This classifier already lacks `counterexample` in its `missing_case` keywords (unlike classifier 1). Simply add a new `counterexample` category entry with keywords: `counterexample`, `flaw found`, `breaker found`, `regime failure`, `falsif`.
- Add a corresponding revision addendum for `counterexample` matching `error_taxonomy.py`'s `REVISION_ADDENDA["counterexample"]`. Read `src/alethic/error_taxonomy.py` to get the exact addendum text.
- Note: classifier 2 has its own keyword sets that differ from classifier 1 and `error_taxonomy.py` in other ways — do NOT attempt full alignment, only add the `counterexample` entry.

- [ ] **Step 5: Add error_category to verify event emission**

Find all verify event emission points in the orchestrator (search for `"type":"verify"` in event JSON). At each one, add `"error_category":"{classified_category}"` to the JSON line. The category comes from the inline classifier applied to the CRITIQUE text parsed from the verifier output.

There are 2 existing verify event emission points, plus the FIXABLE shortcut which emits an `"accept"` event:
1. Initial verify (after verifying a generated candidate) — emits `"type":"verify"`
2. Re-verify after revision — emits `"type":"verify"`
3. FIXABLE shortcut — emits `"type":"accept"` with `"via":"fixable_shortcut"` (NOT a verify event)

Add `error_category` to both verify events (#1 and #2). For the FIXABLE accept event (#3), also add `error_category` — PUCT analysis benefits from knowing the error category of the re-verification.

- [ ] **Step 6: Commit**

```bash
git add skills/alethic-common/orchestrator.md
git commit -m "feat(skills): add error_category to verify events

Split counterexample from missing_case in both inline classifiers.
Add counterexample to Dynamic N escalation list and revision addendum.
Emit error_category in all 3 verify event emission points.

Enables PUCT approach classification for gate experiment."
```

---

## Chunk 3: Subscription Runner Script

### Task 4: Create scripts directory and runner skeleton

**Files:**
- Create: `scripts/run_gate.py`

- [ ] **Step 1: Create scripts directory**

Run: `ls /home/xeal/dev/alethic/scripts/ 2>/dev/null || echo "need to create"`

- [ ] **Step 2: Write runner skeleton**

Create `scripts/run_gate.py` with the three-function architecture:

```python
#!/usr/bin/env python3
"""Gate benchmark runner via Claude Code subscription.

Runs the 100-problem gate-v38.json benchmark through /alethic-solve and
/alethic-derive skills via `claude -p`, then harvests session directories
to compute gate metrics (annotation_rate, puct_divergence, solve_rate).

Usage:
    python scripts/run_gate.py                    # run all problems
    python scripts/run_gate.py --harvest-only      # skip driver, just harvest
    python scripts/run_gate.py --dry-run           # print commands without executing
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

# Project root (scripts/ is one level below)
ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = ROOT / "data" / "benchmarks" / "gate-v38.json"
ALETHIC_DIR = ROOT / ".alethic"


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
            print(f"  TIMEOUT after 30 min")
        except Exception as e:
            print(f"  ERROR: {e}")

        elapsed = time.time() - start
        print(f"  done ({elapsed:.0f}s)")


def harvest(problems: list[dict]) -> dict:
    """Read .alethic/ session dirs and compute gate metrics."""
    from alethic.atoms import parse_atoms
    from alethic.error_taxonomy import classify_errors

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

    EXPLORATION_WEIGHT = 1.41

    def _ucb1_score(
        confidence: float, visit_count: int, total_visits: int
    ) -> float:
        if visit_count == 0:
            return float("inf")
        exploitation = confidence
        exploration = EXPLORATION_WEIGHT * math.sqrt(
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

    problems = load_benchmark(args.benchmark)
    print(f"Loaded {len(problems)} problems from {args.benchmark}")

    if not args.harvest_only:
        driver(problems, dry_run=args.dry_run)

    if not args.dry_run:
        gate_data = harvest(problems)
        report(gate_data)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify script is syntactically valid**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -c "import ast; ast.parse(open('scripts/run_gate.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit skeleton**

```bash
git add scripts/run_gate.py
git commit -m "feat: add gate benchmark subscription runner

Driver iterates problems via claude -p, harvester reads .alethic/
session dirs and computes gate metrics (annotation_rate, puct_divergence).

Supports --harvest-only, --dry-run, and resume (skips existing sessions)."
```

### Task 5: Write harvester tests

**Files:**
- Create: `tests/test_gate_runner.py`

- [ ] **Step 1: Write harvester unit tests**

Create `tests/test_gate_runner.py`:

```python
"""Tests for scripts/run_gate.py harvester logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts/ to path so we can import run_gate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_gate


class TestNormalizeText(unittest.TestCase):
    def test_strips_whitespace(self):
        assert run_gate.normalize_text("  hello  ") == "hello"

    def test_nfc_normalization(self):
        # ℏ can be represented as single char or combining sequence
        import unicodedata
        text = unicodedata.normalize("NFD", "ℏω")
        result = run_gate.normalize_text(text)
        assert result == unicodedata.normalize("NFC", "ℏω")


class TestFindExistingSessions(unittest.TestCase):
    def test_matches_by_problem_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            session_dir = alethic_dir / "test-session-20260317-abcd"
            session_dir.mkdir(parents=True)

            session_json = {
                "problem": "Prove that 17 is prime.",
                "status": "solved",
                "created_at": "2026-03-17T10:00:00",
            }
            (session_dir / "session.json").write_text(json.dumps(session_json))

            problems = [{"id": "prime-17", "problem": "Prove that 17 is prime."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert "prime-17" in result
            assert result["prime-17"] == session_dir

    def test_dedup_prefers_solved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"

            # Unsolved session (earlier)
            s1 = alethic_dir / "session-1"
            s1.mkdir(parents=True)
            (s1 / "session.json").write_text(json.dumps({
                "problem": "Prove X.",
                "status": "unsolved",
                "created_at": "2026-03-17T10:00:00",
            }))

            # Solved session (later)
            s2 = alethic_dir / "session-2"
            s2.mkdir(parents=True)
            (s2 / "session.json").write_text(json.dumps({
                "problem": "Prove X.",
                "status": "solved",
                "created_at": "2026-03-17T11:00:00",
            }))

            problems = [{"id": "test-x", "problem": "Prove X."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert result["test-x"] == s2  # solved wins

    def test_ignores_unrelated_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            s1 = alethic_dir / "unrelated-session"
            s1.mkdir(parents=True)
            (s1 / "session.json").write_text(json.dumps({
                "problem": "Something else entirely.",
                "status": "solved",
                "created_at": "2026-03-17T10:00:00",
            }))

            problems = [{"id": "prime-17", "problem": "Prove that 17 is prime."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert len(result) == 0


class TestComputePuctFromEvents(unittest.TestCase):
    def test_no_events_returns_zero(self):
        result = run_gate._compute_puct_from_events([])
        assert result["divergence_rate"] == 0.0
        assert result["total_iterations"] == 0

    def test_single_candidate_not_counted(self):
        events = [
            {"type": "verify", "iteration": 1, "candidate": 1,
             "verdict": "correct", "confidence": 0.95, "error_category": "general"},
        ]
        result = run_gate._compute_puct_from_events(events)
        assert result["total_iterations"] == 0  # N=1, no PUCT signal


class TestMeasureAtomsFromFiles(unittest.TestCase):
    def test_no_worklog_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_gate._measure_atoms_from_files(Path(tmpdir), [])
            assert result is None

    def test_reads_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            worklog = session_dir / "worklog"
            worklog.mkdir()

            # Write a candidate file with atom annotations
            (worklog / "candidate_1.md").write_text(
                "ATOM[1] deps=[] oracle=L1\nStep 1: proof.\n"
                "ATOM[2] deps=[1] oracle=L2\nStep 2: conclusion.\n"
            )

            events = [
                {"type": "verify", "iteration": 1, "candidate": 1,
                 "confidence": 0.95},
            ]

            result = run_gate._measure_atoms_from_files(session_dir, events)
            assert result is not None
            assert result["annotation_rate"] == 1.0
            assert result["atom_counts"] == [2]
```

- [ ] **Step 2: Add end-to-end harvest test (spec requirement 3)**

Add to `tests/test_gate_runner.py`:

```python
class TestHarvestEndToEnd(unittest.TestCase):
    def test_harvest_session(self):
        """Full pipeline: session.json + events.jsonl + candidate file → metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            session_dir = alethic_dir / "prime-17-20260317-abcd"
            worklog = session_dir / "worklog"
            worklog.mkdir(parents=True)

            # session.json
            (session_dir / "session.json").write_text(json.dumps({
                "problem": "Prove that 17 is prime.",
                "status": "solved",
                "best_confidence": 0.95,
                "created_at": "2026-03-17T10:00:00",
            }))

            # events.jsonl (2 candidates, iteration 1)
            events = [
                {"type": "verify", "iteration": 1, "candidate": 1,
                 "verdict": "correct", "confidence": 0.95,
                 "error_category": "general"},
                {"type": "verify", "iteration": 1, "candidate": 2,
                 "verdict": "minor_issues", "confidence": 0.80,
                 "error_category": "algebra"},
            ]
            with open(worklog / "events.jsonl", "w") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")

            # candidate file with atoms
            (worklog / "candidate_1.md").write_text(
                "ATOM[1] deps=[] oracle=L1\nStep 1: 17 is odd.\n"
            )

            problems = [{"id": "prime-17", "domain": "math",
                         "problem": "Prove that 17 is prime.",
                         "expected_solvable": True}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.harvest(problems)

            assert result["solved"] == 1
            assert result["solve_rate"] == 1.0
            assert result["mean_annotation_rate"] == 1.0
            assert result["results"][0]["solved"] is True


class TestErrorCategoryClassification(unittest.TestCase):
    def test_counterexample_is_separate_from_missing_case(self):
        """Verify counterexample is its own category (spec requirement 2)."""
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("counterexample found at x=5") == "counterexample"
        assert classify_errors("breaker found a flaw") == "counterexample"
        assert classify_errors("missing case: n=0 not handled") == "missing_case"
```

- [ ] **Step 3: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_gate_runner.py -v`
Expected: All tests pass

- [ ] **Step 4: Run full suite**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest -q --tb=line`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_gate_runner.py
git commit -m "test: add harvester tests for gate runner

Tests session matching, dedup (solved preferred), unrelated session
filtering, PUCT computation, and atom measurement from worklog files."
```

### Task 6: Lint and final verification

- [ ] **Step 1: Run ruff check**

Run: `/home/xeal/.local/bin/micromamba run -n alethic ruff check scripts/run_gate.py tests/test_gate_runner.py`
Expected: No errors (fix any that appear)

- [ ] **Step 2: Run full test suite one final time**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest -q --tb=line`
Expected: All tests pass

- [ ] **Step 3: Verify dry-run mode works**

Run: `/home/xeal/.local/bin/micromamba run -n alethic python scripts/run_gate.py --dry-run`
Expected: Prints 100 DRY RUN lines with `claude -p` commands, no actual execution

- [ ] **Step 4: Smoke test — run driver on one problem (spec requirement 5)**

This step requires `claude` CLI authenticated with a Claude Code subscription. If not available, skip.

Run: `/home/xeal/.local/bin/micromamba run -n alethic python -c "
import subprocess, tempfile, json
from pathlib import Path

# Create a minimal 1-problem benchmark
bench = {'name': 'smoke', 'version': '1.0', 'problems': [
    {'id': 'prime-17', 'domain': 'math', 'problem': 'Prove that 17 is prime.', 'expected_solvable': True}
]}
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    json.dump(bench, f)
    print(f'Benchmark: {f.name}')

# Test that claude -p can receive the prompt without crashing
# If --file doesn't work, fall back to stdin piping
"`

If `--file` fails with `claude -p`, update `run_gate.py` to use stdin fallback:
```python
subprocess.run(
    ["claude", "-p", f"{skill} -p default"],
    input=problem["problem"],
    text=True,
    check=False,
    timeout=1800,
)
```
