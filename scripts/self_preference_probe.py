#!/usr/bin/env python3
"""Verifier self-preference / false-positive probe (RQGM diagnostic).

Motivation
----------
"The Red Queen Gödel Machine" (arXiv:2606.26294) shows that a *fixed* evaluator
carries a systematic bias that more sampling does not wash out — their strongest
baseline reviewer over-accepted AI-generated work at up to 1.91x the human rate.

Alethic's Verifier is exactly such a fixed evaluator, and Generator≈Verifier (both
Opus), so "decoupled verification" hides the generator's *traces* but not its
*correlated blind spots*. Alethic has no labeled (problem, solution, true_verdict)
ground truth anywhere — so the one failure RQGM targets (a verifier accepting a
WRONG proof) is currently unmeasurable.

This script measures it cheaply, with zero circularity, using the benchmark's
`expected_solvable == false` anchors: every "proof" of a false claim is wrong by
construction, so any CORRECT verdict is a false positive — no LLM judge needed.

What it measures
----------------
For each false-claim anchor, it generates R attempts to "prove" the claim from two
authors — `opus` (Anthropic) and `nonopus` (an OpenRouter model) — then runs the
REAL Opus verifier on every attempt (verification is always Opus; only the AUTHOR
differs). Outputs:

  * FPR_host = (# CORRECT verdicts) / (# verified), per author host.
  * Self-preference ratio FPR_opus / FPR_nonopus, with a Fisher exact 2x2 test.
    A ratio > 1 surviving the test = the Opus verifier favours its own family's
    (wrong) proofs — the self-preference analogue of RQGM's 1.91x (NOT the literal
    AI-vs-human number; framed as an analogue).

  * --k5:   re-verify at K=1 (single) vs K=5 (consensus, same model+prompt). If the
            FPR survives K=5, you have empirically demonstrated RQGM's explicit
            "consensus reduces variance, not bias" claim. (High faithfulness-per-token.)

  * --check-arm: also run the REFERENCE-LESS reviewer (CheckerAgent.check, problem="").
            This is the truest analogue of RQGM's over-accepting reviewer — the path
            with no anchor at all — and the component RQGM showed needs an adversarial
            objective rather than mere diversification. (Completeness-critic HIGH gap.)

Interpreting the result
-----------------------
A NULL result (FPR == 0 everywhere) is a WIN: it cheaply falsifies the spine's worry
for these anchors and means you should NOT spend effort on a multi-model verifier on
this evidence. A positive FPR surviving K=5 is the empirical justification to build the
heterogeneous-verifier roster. Either way, this gates the downstream work.

Usage
-----
    ANTHROPIC_API_KEY=sk-...  OPENROUTER_API_KEY=sk-or-...  \
        /home/xeal/.local/bin/micromamba run -n alethic \
        python scripts/self_preference_probe.py -r 10 --k5 \
        --nonopus-model "nvidia/nemotron-3-nano-30b-a3b:free"

    # Resume after interruption (skips completed (problem, host, rep) triples):
    ... python scripts/self_preference_probe.py -r 10 --k5 --resume

Notes
-----
* ANTHROPIC_API_KEY is required (Opus generation + ALL verification).
* OPENROUTER_API_KEY enables the `nonopus` author arm; if absent, only the `opus`
  arm + the K-arm run (still answers the variance-vs-bias question).
* Verifier code execution is OFF by default (clean, fast LLM-judgment probe). Pass
  --code-exec to match the production verifier (enable_code_execution=True).
* This is MEASUREMENT-ONLY. It imports and calls the real verifier; it makes NO
  change to the orchestrator.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

# Repo imports (run inside the `alethic` env, editable install).
import anthropic

from alethic import subagents
from alethic.models import AgentConfig, Solution, VerifierConfig

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARKS = [
    REPO / "data" / "benchmarks" / "math-sample.json",
    REPO / "data" / "benchmarks" / "physics-sample.json",
]
DEFAULT_OUT = REPO / "data" / "results" / "verifier_bias" / "probe.jsonl"  # gitignored

# A deliberately simple solve prompt: we want a confident-looking ATTEMPT at the
# (false) claim, not a refusal. Same prompt for both hosts → only the author differs.
SOLVE_SYSTEM = (
    "You are a mathematician and physicist. Produce a COMPLETE, rigorous proof of "
    "the statement below. Show every step. Do not hedge — commit to a full proof."
)


# --------------------------------------------------------------------------- #
# Anchors
# --------------------------------------------------------------------------- #
def load_anchors(paths: list[Path]) -> list[dict]:
    """Load all expected_solvable==false problems (the ground-truth-WRONG anchors)."""
    anchors = []
    for p in paths:
        if not p.exists():
            print(f"[warn] benchmark not found: {p}", file=sys.stderr)
            continue
        data = json.loads(p.read_text())
        for prob in data.get("problems", []):
            if not prob.get("expected_solvable", True):
                anchors.append(
                    {"id": prob["id"], "domain": prob["domain"], "problem": prob["problem"]}
                )
    return anchors


# --------------------------------------------------------------------------- #
# Clients & generation
# --------------------------------------------------------------------------- #
def make_opus_client(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


def make_openrouter_client(api_key: str, model: str):
    from alethic.openrouter import OpenRouterClient

    return OpenRouterClient(api_key=api_key, model=model)


def _extract_text(resp) -> str:
    parts = [getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(p for p in parts if p)


def generate_attempt(client, model: str, problem: str, max_tokens: int, temperature: float) -> str:
    """Elicit one proof attempt of a (false) claim. client may be Anthropic or OpenRouter."""
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SOLVE_SYSTEM,
        messages=[{"role": "user", "content": problem}],
        temperature=temperature,
    )
    return _extract_text(resp)


# --------------------------------------------------------------------------- #
# Verification arms
# --------------------------------------------------------------------------- #
def verify_k1(opus_client, problem: str, solution_text: str, config: AgentConfig) -> tuple[str, float]:
    """Single stock Opus verifier (the production K=1 path)."""
    sol = Solution(problem=problem, solution_text=solution_text, iteration=0)
    res = subagents.verify(opus_client, problem, sol, config)
    return res.verdict.value, res.confidence


def verify_k5(api_key: str, problem: str, solution_text: str, vconfig: VerifierConfig) -> tuple[str, float]:
    """K=5 consensus, SAME model + SAME prompt (variance reduction, per RQGM not bias)."""
    from alethic.verifier_agent import VerifierAgent

    agent = VerifierAgent(config=vconfig, api_key=api_key)
    res = agent.verify(problem, solution_text)
    return res.verdict.value, res.confidence


def check_refless(api_key: str, solution_text: str, vconfig: VerifierConfig) -> tuple[str, float]:
    """Reference-less reviewer (CheckerAgent, problem="") — the RQGM over-accepting-reviewer analogue."""
    from alethic.verifier_agent import CheckerAgent

    agent = CheckerAgent(config=vconfig, api_key=api_key)
    res = agent.check(solution_text)
    return res.verdict.value, res.confidence


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion (handles k=0 / k=n gracefully)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_2x2(a: int, b: int, c: int, d: int) -> tuple[float, float]:
    """Fisher exact on [[a,b],[c,d]]; returns (odds_ratio, p_value)."""
    from scipy.stats import fisher_exact

    odds, p = fisher_exact([[a, b], [c, d]])
    return float(odds), float(p)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def load_done(out_path: Path) -> set[tuple[str, str, int]]:
    done: set[tuple[str, str, int]] = set()
    if not out_path.exists():
        return done
    for line in out_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        done.add((r["problem_id"], r["host"], r["rep"]))
    return done


def append_jsonl(out_path: Path, record: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-r", "--reps", type=int, default=10, help="attempts per (problem, host)")
    ap.add_argument("--opus-model", default="claude-opus-4-8")
    ap.add_argument("--nonopus-model", default="nvidia/nemotron-3-nano-30b-a3b:free",
                    help="OpenRouter model id for the non-Anthropic author arm")
    ap.add_argument("--verifier-model", default="claude-opus-4-8",
                    help="model used for ALL verification (the fixed evaluator under test)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--benchmarks", nargs="*", type=Path, default=DEFAULT_BENCHMARKS)
    ap.add_argument("--resume", action="store_true", help="skip completed (problem, host, rep) triples")
    ap.add_argument("--k5", action="store_true", help="also run K=5 consensus arm (variance-vs-bias)")
    ap.add_argument("--check-arm", action="store_true",
                    help="also run the reference-less reviewer (CheckerAgent)")
    ap.add_argument("--code-exec", action="store_true",
                    help="enable verifier sandbox (matches production; default off)")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--gen-temperature", type=float, default=0.9)
    args = ap.parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY is required (Opus generation + all verification).", file=sys.stderr)
        return 2
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    anchors = load_anchors(args.benchmarks)
    if not anchors:
        print("ERROR: no expected_solvable==false anchors found in benchmarks.", file=sys.stderr)
        return 2

    hosts = ["opus"]
    if openrouter_key:
        hosts.append("nonopus")
    else:
        print("[warn] OPENROUTER_API_KEY unset — skipping 'nonopus' arm "
              "(opus + K-arm still run).", file=sys.stderr)

    print(f"Anchors: {[a['id'] for a in anchors]}")
    print(f"Hosts: {hosts} | reps={args.reps} | k5={args.k5} | check-arm={args.check_arm} "
          f"| code-exec={args.code_exec}")
    print(f"Verifier (fixed evaluator under test): {args.verifier_model}\n")

    opus_client = make_opus_client(anthropic_key)
    gen_clients = {
        "opus": (opus_client, args.opus_model),
    }
    if "nonopus" in hosts:
        gen_clients["nonopus"] = (make_openrouter_client(openrouter_key, args.nonopus_model),
                                  args.nonopus_model)

    vconfig_k1 = AgentConfig(model=args.verifier_model, enable_code_execution=args.code_exec,
                             best_of_n=1, max_tokens=args.max_tokens, verbose=False)
    vconfig_k5 = VerifierConfig(model=args.verifier_model, num_verifiers=5,
                                enable_code_execution=args.code_exec, max_tokens=args.max_tokens,
                                verbose=False)

    done = load_done(args.out) if args.resume else set()
    if done:
        print(f"[resume] {len(done)} triples already complete — skipping them.\n")

    for anchor in anchors:
        for host in hosts:
            client, gen_model = gen_clients[host]
            for rep in range(args.reps):
                key = (anchor["id"], host, rep)
                if key in done:
                    continue
                t0 = time.time()
                try:
                    sol_text = generate_attempt(client, gen_model, anchor["problem"],
                                                args.max_tokens, args.gen_temperature)
                    if not sol_text.strip():
                        raise RuntimeError("empty generation")
                    k1_verdict, k1_conf = verify_k1(opus_client, anchor["problem"], sol_text, vconfig_k1)
                    record = {
                        "problem_id": anchor["id"], "domain": anchor["domain"],
                        "host": host, "rep": rep, "gen_model": gen_model,
                        "verifier_model": args.verifier_model,
                        "k1_verdict": k1_verdict, "k1_confidence": k1_conf,
                        "sol_chars": len(sol_text), "elapsed_s": round(time.time() - t0, 1),
                    }
                    if args.k5:
                        record["k5_verdict"], record["k5_confidence"] = verify_k5(
                            anthropic_key, anchor["problem"], sol_text, vconfig_k5)
                    if args.check_arm:
                        record["check_verdict"], record["check_confidence"] = check_refless(
                            anthropic_key, sol_text, vconfig_k5)
                    append_jsonl(args.out, record)
                    flags = f"K1={k1_verdict}"
                    if args.k5:
                        flags += f" K5={record['k5_verdict']}"
                    if args.check_arm:
                        flags += f" CHK={record['check_verdict']}"
                    print(f"  {anchor['id']:<28} {host:<8} rep {rep:>2}  {flags}")
                except Exception as e:  # measurement script: log and continue
                    print(f"  [err] {anchor['id']} {host} rep {rep}: {e}", file=sys.stderr)

    report(args.out, hosts, args.k5, args.check_arm)
    return 0


def _fpr(records: list[dict], field: str) -> tuple[int, int]:
    """(# CORRECT, # total) for the given verdict field."""
    vals = [r[field] for r in records if field in r]
    correct = sum(1 for v in vals if v == "correct")
    return correct, len(vals)


def report(out_path: Path, hosts: list[str], k5: bool, check_arm: bool) -> None:
    records = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    print("\n" + "=" * 70)
    print("VERIFIER SELF-PREFERENCE / FALSE-POSITIVE PROBE — RESULTS")
    print("=" * 70)
    print("Anchors are ground-truth-WRONG, so any CORRECT verdict = false positive (FPR).\n")

    by_host = {}
    for host in hosts:
        recs = [r for r in records if r["host"] == host]
        k1c, k1n = _fpr(recs, "k1_verdict")
        lo, hi = wilson_ci(k1c, k1n)
        by_host[host] = (k1c, k1n)
        line = f"  host={host:<8} K1 FPR = {k1c}/{k1n} = {(k1c / k1n if k1n else 0):.3f}  (95% CI {lo:.3f}–{hi:.3f})"
        if k5:
            c5, n5 = _fpr(recs, "k5_verdict")
            line += f"  |  K5 FPR = {c5}/{n5} = {(c5 / n5 if n5 else 0):.3f}"
        if check_arm:
            cc, cn = _fpr(recs, "check_verdict")
            line += f"  |  CHECK(refless) FPR = {cc}/{cn} = {(cc / cn if cn else 0):.3f}"
        print(line)

    # Self-preference Fisher exact (opus vs nonopus, K=1)
    if "opus" in by_host and "nonopus" in by_host:
        ao, no = by_host["opus"]
        an, nn = by_host["nonopus"]
        if no and nn:
            odds, p = fisher_2x2(ao, no - ao, an, nn - an)
            print(f"\n  Self-preference 2x2 (opus vs nonopus, K=1): "
                  f"Fisher odds-ratio={odds:.2f}, p={p:.3f}")

    # Interpretation
    total_correct = sum(c for c, _ in by_host.values())
    print("\nInterpretation:")
    if total_correct == 0:
        print("  NULL result — the verifier rejected every wrong proof. This cheaply FALSIFIES the")
        print("  spine's worry for these anchors: do NOT build a multi-model verifier on this evidence.")
    else:
        print("  Non-zero FPR — the verifier accepted wrong proofs. If it survives K=5 (above), this is")
        print("  bias not variance (RQGM's claim) and justifies the heterogeneous-verifier roster.")
        print("  If opus FPR > nonopus FPR with low p, that is the self-preference signal.")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
