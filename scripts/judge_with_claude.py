#!/usr/bin/env python3
"""Judge baseline solutions with Claude Opus 4.6 via the Claude Code CLI.

Reads baseline.jsonl files from each model's calibration directory, batches
solutions by (model, problem), and sends each batch to `claude -p` for
ground-truth judging.  Uses ThreadPoolExecutor for parallel dispatch.

Results are written to claude-verdicts.jsonl per model.
Supports --resume to skip already-judged (problem, rep) pairs.

Usage:
    python scripts/judge_with_claude.py
    python scripts/judge_with_claude.py --resume
    python scripts/judge_with_claude.py -w 4 --resume         # 4 parallel judges
    python scripts/judge_with_claude.py --slugs nano qwen     # subset of models
    python scripts/judge_with_claude.py --dry-run             # show work without calling claude
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SLUGS = ["nano", "qwen", "flash", "super"]
CAL_DIR = "data/calibration"
BENCH_PATHS = [
    "data/benchmarks/math-sample.json",
    "data/benchmarks/physics-sample.json",
]

JSON_SCHEMA = json.dumps({
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "rep": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["correct", "incorrect"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["rep", "verdict", "confidence", "reason"],
    },
})

JUDGE_PROMPT_TEMPLATE = """\
You are an expert judge evaluating mathematical and scientific solutions for correctness.

PROBLEM:
{problem}

Below are {n} independent solution attempts (numbered by rep index). For EACH solution, assess whether it is correct:

- CORRECT: the solution is substantially correct — the reasoning is valid, key steps are justified, and the conclusion follows. Minor cosmetic issues (notation, formatting) are fine.
- INCORRECT: the solution has meaningful mathematical/logical errors, unjustified leaps, wrong conclusions, or critical gaps.

Be rigorous but fair. A proof that reaches the right conclusion through valid reasoning is CORRECT even if the exposition is rough.

{solutions_block}

Return a JSON array with one object per solution, in rep order."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_problems() -> dict[str, dict]:
    """Load benchmark problems, keyed by problem ID."""
    from alethic.eval.harness import load_benchmark

    problems = {}
    for path in BENCH_PATHS:
        bench = load_benchmark(path)
        for p in bench["problems"]:
            problems[p["id"]] = p
    return problems


def load_baseline(slug: str) -> list[dict]:
    """Load baseline.jsonl for a model slug."""
    path = os.path.join(CAL_DIR, slug, "baseline.jsonl")
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_completed(slug: str) -> set[tuple[str, int]]:
    """Return {(problem_id, rep)} pairs already judged."""
    path = os.path.join(CAL_DIR, slug, "claude-verdicts.jsonl")
    done: set[tuple[str, int]] = set()
    if not os.path.exists(path):
        return done
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add((obj["problem_id"], obj["rep"]))
    return done


def build_solutions_block(records: list[dict]) -> str:
    """Format numbered solution attempts for the prompt."""
    parts = []
    for rec in records:
        rep = rec["rep"]
        solution = rec.get("solution", "").strip()
        if not solution:
            solution = "(empty response)"
        parts.append(f"--- SOLUTION rep={rep} ---\n{solution}\n--- END SOLUTION rep={rep} ---")
    return "\n\n".join(parts)


def call_claude(prompt: str) -> str:
    """Call claude -p --bare --model opus and return stdout."""
    cmd = [
        "claude", "-p",
        "--bare",
        "--model", "opus",
        "--no-session-persistence",
        "--output-format", "text",
        "--json-schema", JSON_SCHEMA,
    ]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,  # 5 min per batch
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


def parse_verdicts(raw: str) -> list[dict] | None:
    """Parse JSON array of verdicts from Claude's response."""
    text = raw.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        verdicts = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(verdicts, list):
        return None
    return verdicts


# ---------------------------------------------------------------------------
# Work item
# ---------------------------------------------------------------------------


class JudgeTask:
    """One (slug, problem_id) batch to judge."""

    def __init__(self, slug: str, pid: str, prob_text: str, pending: list[dict]):
        self.slug = slug
        self.pid = pid
        self.prob_text = prob_text
        self.pending = pending

    @property
    def n(self) -> int:
        return len(self.pending)

    @property
    def reps(self) -> list[int]:
        return [r.get("rep", 0) for r in self.pending]


# File locks per slug to prevent interleaved JSONL writes
_file_locks: dict[str, threading.Lock] = {}


def _get_lock(slug: str) -> threading.Lock:
    if slug not in _file_locks:
        _file_locks[slug] = threading.Lock()
    return _file_locks[slug]


def execute_task(task: JudgeTask) -> dict:
    """Judge one batch and write results. Returns summary dict."""
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        problem=task.prob_text,
        n=task.n,
        solutions_block=build_solutions_block(task.pending),
    )

    t0 = time.time()
    try:
        raw = call_claude(prompt)
        elapsed = time.time() - t0
    except Exception as e:
        return {
            "slug": task.slug, "pid": task.pid,
            "status": "error", "error": str(e),
            "judged": 0, "correct": 0, "elapsed": time.time() - t0,
        }

    verdicts = parse_verdicts(raw)
    if verdicts is None:
        err_path = os.path.join(CAL_DIR, task.slug, f"claude-raw-{task.pid}.txt")
        with open(err_path, "w") as f:
            f.write(raw)
        return {
            "slug": task.slug, "pid": task.pid,
            "status": "parse_error", "error": "Could not parse JSON",
            "judged": 0, "correct": 0, "elapsed": elapsed,
        }

    # Match verdicts to pending records and write atomically
    output_path = os.path.join(CAL_DIR, task.slug, "claude-verdicts.jsonl")
    judged = 0
    correct = 0
    lines = []

    for v in verdicts:
        rep_idx = v.get("rep")
        matching = [r for r in task.pending if r.get("rep", 0) == rep_idx]
        if not matching:
            continue

        rec = matching[0]
        out = {
            "problem_id": task.pid,
            "rep": rep_idx,
            "domain": rec.get("domain", "math"),
            "model": rec.get("model", ""),
            "claude_verdict": v.get("verdict", "").lower(),
            "claude_confidence": v.get("confidence", 0.0),
            "claude_reason": v.get("reason", ""),
            "self_verdict": rec.get("verdict", ""),
            "self_confidence": rec.get("confidence", 0.0),
        }
        lines.append(json.dumps(out))
        judged += 1
        if v.get("verdict", "").lower() == "correct":
            correct += 1

    # Atomic write under lock
    with _get_lock(task.slug):
        with open(output_path, "a") as f:
            f.write("\n".join(lines) + "\n")

    return {
        "slug": task.slug, "pid": task.pid,
        "status": "ok",
        "judged": judged, "correct": correct, "elapsed": elapsed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()


def main():
    parser = argparse.ArgumentParser(description="Judge baseline solutions with Claude Opus")
    parser.add_argument("--resume", action="store_true", help="Skip already-judged batches")
    parser.add_argument("--slugs", nargs="+", default=SLUGS, help="Model slugs to judge")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Parallel claude -p workers (default 4)")
    parser.add_argument("--dry-run", action="store_true", help="Show work plan without calling Claude")
    args = parser.parse_args()

    problems = load_problems()

    # Build all tasks
    tasks: list[JudgeTask] = []
    total_skipped = 0

    for slug in args.slugs:
        records = load_baseline(slug)
        if not records:
            with _print_lock:
                print(f"  {slug}: no baseline.jsonl — skipping")
            continue

        completed = load_completed(slug) if args.resume else set()

        by_problem: dict[str, list[dict]] = {}
        for rec in records:
            pid = rec["problem_id"]
            by_problem.setdefault(pid, []).append(rec)

        slug_pending = 0
        slug_skipped = 0
        for pid in sorted(by_problem.keys()):
            recs = sorted(by_problem[pid], key=lambda r: r.get("rep", 0))
            pending = [r for r in recs if (r["problem_id"], r.get("rep", 0)) not in completed]
            if not pending:
                slug_skipped += len(recs)
                continue
            prob_text = problems.get(pid, {}).get("problem", f"(problem {pid} not found)")
            tasks.append(JudgeTask(slug, pid, prob_text, pending))
            slug_pending += len(pending)

        total_skipped += slug_skipped
        print(f"  {slug}: {slug_pending} solutions in {sum(1 for t in tasks if t.slug == slug)} batches"
              + (f" ({slug_skipped} skipped)" if slug_skipped else ""))

    total_solutions = sum(t.n for t in tasks)
    print(f"\nTotal: {len(tasks)} batches, {total_solutions} solutions to judge, {args.workers} workers")

    if args.dry_run or not tasks:
        return

    # Execute in parallel
    wall_start = time.time()
    completed_count = 0
    total_judged = 0
    total_correct = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(execute_task, task): task for task in tasks}

        for future in as_completed(futures):
            task = futures[future]
            completed_count += 1
            try:
                result = future.result()
            except Exception as e:
                with _print_lock:
                    print(f"  [{completed_count}/{len(tasks)}] {task.slug}/{task.pid}: EXCEPTION {e}")
                errors += 1
                continue

            total_judged += result["judged"]
            total_correct += result["correct"]

            with _print_lock:
                if result["status"] == "ok":
                    print(f"  [{completed_count}/{len(tasks)}] {task.slug}/{task.pid}: "
                          f"{result['correct']}/{result['judged']} correct ({result['elapsed']:.0f}s)")
                else:
                    print(f"  [{completed_count}/{len(tasks)}] {task.slug}/{task.pid}: "
                          f"{result['status']} — {result.get('error', '')[:100]}")
                    errors += 1

    # Summary
    wall = time.time() - wall_start
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Batches:          {len(tasks)} ({errors} errors)")
    print(f"  Solutions judged: {total_judged}")
    print(f"  Claude correct:   {total_correct}/{total_judged} ({100*total_correct/total_judged:.0f}%)" if total_judged else "")
    if total_skipped:
        print(f"  Skipped (resume): {total_skipped}")
    print(f"  Workers:          {args.workers}")
    print(f"  Wall time:        {wall:.0f}s")

    # Per-model comparison
    print(f"\n  Per-model results:")
    for slug in args.slugs:
        path = os.path.join(CAL_DIR, slug, "claude-verdicts.jsonl")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        if not lines:
            continue

        n = len(lines)
        claude_correct = sum(1 for l in lines if l["claude_verdict"] == "correct")
        self_correct = sum(1 for l in lines if l["self_verdict"] == "correct")
        agree = sum(1 for l in lines if l["claude_verdict"] == l["self_verdict"])

        # Per-problem breakdown
        by_pid: dict[str, list[dict]] = {}
        for l in lines:
            by_pid.setdefault(l["problem_id"], []).append(l)

        print(f"\n    {slug}: Claude {claude_correct}/{n} ({100*claude_correct/n:.0f}%) | "
              f"Self {self_correct}/{n} ({100*self_correct/n:.0f}%) | "
              f"Agree {agree}/{n} ({100*agree/n:.0f}%)")

        for pid in sorted(by_pid.keys()):
            recs = by_pid[pid]
            c = sum(1 for r in recs if r["claude_verdict"] == "correct")
            s = sum(1 for r in recs if r["self_verdict"] == "correct")
            print(f"      {pid:30s}  claude={c}/{len(recs)}  self={s}/{len(recs)}")


if __name__ == "__main__":
    main()
