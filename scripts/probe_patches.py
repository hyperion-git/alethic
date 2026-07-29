#!/usr/bin/env python3
"""Targeted probe: does the model emit CHECKS PERFORMED and ISSUE TRIAGE blocks?

Skips the GVR loop entirely. Makes one direct call to verify() and one to
revise() with hand-crafted inputs. Grep the raw response for the expected
block markers. Per-call timeout (75s) keeps wall time bounded.

Usage:
    NVIDIA_API_KEY=nvapi-... python scripts/probe_patches.py <model>
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
CALL_TIMEOUT_S = 75

PROBLEM = (
    "Derive the relativistic energy of a particle at rest from the energy-momentum "
    "relation E^2 = p^2 c^2 + m^2 c^4, showing E = m c^2."
)

# Intentionally flawed solution — has dimensional/algebra issues that give the
# verifier something to flag, exercising patch #1 (CHECKS PERFORMED).
FLAWED_SOLUTION = """
For a particle at rest the momentum is zero, p = 0.
Substituting into the energy-momentum relation:
  E^2 = 0 + m^2 c^4
Therefore E = m^2 c^2 (taking the positive root).
This is Einstein's famous mass-energy equivalence.
"""

# Hand-crafted verification result with FIXABLE verdict so reviser engages
# fully. Provides three issues so patch #2 (ISSUE TRIAGE) has substance to triage.
HAND_CRITIQUE = """The derivation reaches the correct conceptual result but contains an algebra error and is missing key justification steps."""

HAND_ISSUES_TEXT = [
    "[MAJOR] Algebra error: sqrt(m^2 c^4) = m c^2, not m^2 c^2 — the solution incorrectly took the square root.",
    "[MINOR] Did not state the assumption that 'at rest' means the rest frame of the particle (frame-dependence not addressed).",
    "[MINOR] Did not verify dimensionally that m c^2 has units of energy.",
]


def main() -> int:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("ERROR: NVIDIA_API_KEY required", file=sys.stderr)
        return 1

    if len(sys.argv) < 2:
        print("ERROR: model name required as first argument", file=sys.stderr)
        return 1
    model = sys.argv[1]

    from alethic.openrouter import OpenRouterClient
    from alethic.models import AgentConfig, Solution, VerificationResult, Verdict, Issue, IssueSeverity
    from alethic.subagents import verify, revise
    from alethic import physics_prompts

    # Build client with response-capture wrapper
    dump_path = Path("/tmp") / f"probe-patches-{model.replace('/', '_')}.txt"
    dump_path.write_text("")
    print(f"Dump: {dump_path}")

    client = OpenRouterClient(api_key=api_key, model=model, base_url=NVIDIA_BASE, request_interval=0.0)
    orig = client.messages.create

    def wrapped(**kwargs):
        resp = orig(**kwargs)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        with dump_path.open("a") as fh:
            fh.write(f"\n{'='*70}\nRESPONSE ({len(text)} chars):\n{text}\n")
        return resp

    client.messages.create = wrapped

    config = AgentConfig.from_preset(
        "default",
        model=model,
        adversarial_breaker=False,
        extended_thinking=False,
    )

    sol = Solution(problem=PROBLEM, solution_text=FLAWED_SOLUTION, iteration=1)

    # ─── PROBE 1: VERIFY ──────────────────────────────────────────────────
    print(f"\n=== {model}: probe verify() — looking for CHECKS PERFORMED ===")
    try:
        with timeout(CALL_TIMEOUT_S):
            vr = verify(
                client, PROBLEM, sol, config,
                system_prompt=physics_prompts.PHYSICS_VERIFIER_SYSTEM,
                user_template=physics_prompts.PHYSICS_VERIFIER_USER,
            )
        print(f"  verdict={vr.verdict.value}  conf={vr.confidence:.2f}  issues={len(vr.issues)}")
    except TimeoutError:
        print(f"  TIMEOUT after {CALL_TIMEOUT_S}s")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")

    # ─── PROBE 2: REVISE ──────────────────────────────────────────────────
    print(f"\n=== {model}: probe revise() — looking for ISSUE TRIAGE ===")
    hand_vr = VerificationResult(
        verdict=Verdict.FIXABLE,
        critique=HAND_CRITIQUE,
        confidence=0.6,
        issues=[Issue(text=t, severity=IssueSeverity.MAJOR if "MAJOR" in t else IssueSeverity.MINOR)
                for t in HAND_ISSUES_TEXT],
    )
    try:
        with timeout(CALL_TIMEOUT_S):
            rev = revise(
                client, PROBLEM, sol, hand_vr, config, revision_number=1,
                system_prompt=physics_prompts.PHYSICS_REVISER_SYSTEM,
                user_template=physics_prompts.PHYSICS_REVISER_USER,
            )
        print(f"  produced {len(rev.solution_text)} chars")
    except TimeoutError:
        print(f"  TIMEOUT after {CALL_TIMEOUT_S}s")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")

    # ─── SCAN ─────────────────────────────────────────────────────────────
    print(f"\n=== Marker hits in {dump_path} ===")
    text = dump_path.read_text()
    for marker in ["CHECKS PERFORMED", "ISSUE TRIAGE", "VERDICT:", "CONFIDENCE:"]:
        print(f"  {text.count(marker):3d}  {marker}")

    return 0


class timeout:
    """SIGALRM-based per-block timeout."""
    def __init__(self, seconds: int):
        self.seconds = seconds
    def __enter__(self):
        signal.signal(signal.SIGALRM, self._handler)
        signal.alarm(self.seconds)
    def __exit__(self, *args):
        signal.alarm(0)
    def _handler(self, signum, frame):
        raise TimeoutError(f"call exceeded {self.seconds}s")


if __name__ == "__main__":
    sys.exit(main())
