#!/usr/bin/env python3
"""Baseline: raw model performance without the Alethic harness.

For each benchmark problem, sends a simple solve prompt to the model and
then a simple judge prompt to the same model.  No revision, no best-of-N,
no stall detection — just one shot per rep.

Multiple reps (-r) give statistical power.  Full solutions are saved for
later batch judging with Claude.

Results are written to a JSONL file.  Supports --resume to skip completed
(problem, rep) pairs.

Usage:
    OPENROUTER_API_KEY=sk-or-... python scripts/baseline_raw.py \
        -m "qwen/qwen3.6-plus:free" -o data/calibration/qwen/baseline.jsonl -r 5

    # Resume after interruption:
    OPENROUTER_API_KEY=sk-or-... python scripts/baseline_raw.py \
        -m "qwen/qwen3.6-plus:free" -o data/calibration/qwen/baseline.jsonl -r 5 --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# Prompts — intentionally simple, no Alethic-specific structure
# ---------------------------------------------------------------------------

SOLVE_SYSTEM = """\
You are a mathematician and physicist. Solve the given problem completely and rigorously.
Show your full working. If the problem asks for a proof, provide a complete proof.
If the problem asks for a derivation, show all steps."""

JUDGE_SYSTEM = """\
You are an expert judge evaluating a mathematical or scientific solution.

Assess the solution for correctness. Output EXACTLY this format at the end:

VERDICT: <CORRECT or INCORRECT>
CONFIDENCE: <0.00 to 1.00>
REASON: <one-line summary>

CORRECT means the solution is substantially correct with at most cosmetic issues.
INCORRECT means the solution has meaningful errors, gaps, or wrong conclusions."""

# ---------------------------------------------------------------------------
# OpenRouter helpers
# ---------------------------------------------------------------------------


def _make_client(api_key: str, model: str):
    """Create an OpenAI-compatible client for OpenRouter."""
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1"), model


def _call(client, model: str, system: str, user: str, *, interval_state: dict) -> str:
    """Send a single chat completion and return the text response.

    Handles free-tier rate limiting via interval_state (shared mutable dict
    with 'last_request' timestamp).
    """
    # Rate limiting for free models
    if ":free" in model:
        now = time.time()
        elapsed = now - interval_state.get("last_request", 0)
        if elapsed < 4.0:
            time.sleep(4.0 - elapsed)
        interval_state["last_request"] = time.time()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=16384,
    )
    interval_state["last_request"] = time.time()
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Judge parsing
# ---------------------------------------------------------------------------


def _parse_judge(text: str) -> tuple[str, float, str]:
    """Extract verdict, confidence, reason from judge response."""
    import re

    verdict = "incorrect"
    confidence = 0.5
    reason = ""

    for line in text.splitlines():
        line_stripped = line.strip()
        if m := re.match(r"VERDICT:\s*(CORRECT|INCORRECT)", line_stripped, re.IGNORECASE):
            verdict = m.group(1).lower()
        elif m := re.match(r"CONFIDENCE:\s*([\d.]+)", line_stripped):
            try:
                confidence = float(m.group(1))
            except ValueError:
                pass
        elif m := re.match(r"REASON:\s*(.+)", line_stripped):
            reason = m.group(1).strip()

    return verdict, confidence, reason


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def _completed_reps(path: str) -> dict[str, set[int]]:
    """Return {problem_id: {completed rep indices}} from existing JSONL."""
    done: dict[str, set[int]] = {}
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
            pid = obj["problem_id"]
            rep = obj.get("rep", 0)
            done.setdefault(pid, set()).add(rep)
    return done


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Baseline: raw model solve + self-judge")
    parser.add_argument("-m", "--model", required=True, help="OpenRouter model ID")
    parser.add_argument("-o", "--output", required=True, help="Output JSONL path")
    parser.add_argument("-r", "--reps", type=int, default=3, help="Repetitions per problem (default 3)")
    parser.add_argument("--resume", action="store_true", help="Skip completed (problem, rep) pairs")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY required", file=sys.stderr)
        sys.exit(1)

    # Load all benchmark problems
    from alethic.eval.harness import load_benchmark

    problems = []
    for bench_path in [
        "data/benchmarks/math-sample.json",
        "data/benchmarks/physics-sample.json",
    ]:
        bench = load_benchmark(bench_path)
        problems.extend(bench["problems"])

    # Build work list: (problem, rep) pairs
    done = _completed_reps(args.output) if args.resume else {}
    work = []
    for prob in problems:
        for rep in range(args.reps):
            if prob["id"] in done and rep in done[prob["id"]]:
                continue
            work.append((prob, rep))

    total_pairs = len(problems) * args.reps
    model_short = args.model.split("/")[-1].replace(":free", "")
    print(f"Baseline: {model_short}")
    print(f"Reps per problem: {args.reps}")
    print(f"Work items: {len(work)}/{total_pairs} remaining")
    if done:
        n_done = sum(len(reps) for reps in done.values())
        print(f"Resuming: {n_done} already completed")
    print()

    client, model = _make_client(api_key, args.model)
    interval_state: dict = {}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    solved = 0
    errors = 0
    start = time.time()

    for i, (prob, rep) in enumerate(work):
        pid = prob["id"]
        domain = prob.get("domain", "math")

        print(f"[{i + 1}/{len(work)}] {pid} rep={rep} ... ", end="", flush=True)
        t0 = time.time()

        try:
            # Step 1: Solve
            solution = _call(client, model, SOLVE_SYSTEM, prob["problem"],
                             interval_state=interval_state)

            # Step 2: Self-judge
            judge_prompt = (
                f"PROBLEM:\n{prob['problem']}\n\n"
                f"SOLUTION:\n{solution}\n\n"
                "Evaluate the solution above for correctness."
            )
            judge_response = _call(client, model, JUDGE_SYSTEM, judge_prompt,
                                   interval_state=interval_state)

            verdict, confidence, reason = _parse_judge(judge_response)
            elapsed = time.time() - t0

            if verdict == "correct":
                solved += 1

            record = {
                "problem_id": pid,
                "rep": rep,
                "domain": domain,
                "model": args.model,
                "verdict": verdict,
                "confidence": confidence,
                "reason": reason,
                "elapsed_seconds": round(elapsed, 1),
                "solution": solution,
                "judge_response": judge_response,
            }
            print(f"{verdict} (conf={confidence:.2f}, {elapsed:.0f}s)")

        except Exception as e:
            elapsed = time.time() - t0
            errors += 1
            record = {
                "problem_id": pid,
                "rep": rep,
                "domain": domain,
                "model": args.model,
                "verdict": "error",
                "confidence": 0.0,
                "reason": str(e)[:200],
                "elapsed_seconds": round(elapsed, 1),
                "solution": "",
                "judge_response": "",
            }
            print(f"ERROR: {e!r}")

        # Append incrementally (crash-safe)
        with open(args.output, "a") as f:
            f.write(json.dumps(record) + "\n")

    wall = time.time() - start
    effective = len(work) - errors
    rate = solved / effective if effective > 0 else 0
    print(f"\n{'='*50}")
    print(f"Model:      {model_short}")
    print(f"Completed:  {len(work) - errors}/{len(work)} ({errors} errors)")
    print(f"Self-judge: {solved}/{effective} correct ({rate:.0%})")
    print(f"Wall time:  {wall:.0f}s")
    print(f"Output:     {args.output}")


if __name__ == "__main__":
    main()
