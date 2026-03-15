"""Adversarial breaker module — probes CORRECT solutions for flaws.

The breaker is called *after* the verifier returns a CORRECT verdict. It
attempts to falsify the solution by finding a concrete counterexample,
constructing a boundary-case failure, or identifying a logical gap.  It is
one-directional: it can only decrease effective confidence (by triggering
re-verification after injection of the critique), never increase it.

Skipped automatically when the solution has no atom annotations (monolithic
fallback), since it needs structural hooks to target specific claims.
"""

from __future__ import annotations

import dataclasses
import logging
import re

from alethic.exceptions import ContextExhaustedError
from alethic.models import AgentConfig, BreakerVerdict, TokenLedger
from alethic.subagents import _call_model, _safe_format

logger = logging.getLogger("alethic")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

MATH_BREAKER_SYSTEM = """\
You are an adversarial mathematical proof-checker. Your sole goal is to find
a concrete flaw in the solution presented to you.

## Attack strategy (in order)

1. **Base-case check**: plug n=0, n=1, x=0, x=1 into every claimed formula.
   Confirm the claim holds. A single failing evaluation is a FLAW_FOUND.
2. **Boundary / edge-case**: try negative numbers, empty sets, singular
   matrices, limits as variables approach 0 or ∞.
3. **Logical-gap hunt**: read every "therefore" and "it follows that". Ask:
   does this *actually* follow? Identify any step where the inference is not
   rigorously justified.
4. **Counterexample search**: if the claim is universal (∀ x ...), try to
   construct a specific x that violates it.
5. **Citation check**: every invoked theorem must be named. "It is well known"
   with no theorem name is a SUSPECTED_FLAW.

## Output format (REQUIRED — output ONLY this, no other text)

BREAKER_VERDICT: FLAW_FOUND | SUSPECTED_FLAW | NO_FLAW_FOUND
TARGET_ATOM: <integer atom id, or 0 if targeting the overall solution>
FLAW_TYPE: counterexample | logical_gap | base_case | boundary | citation | none
EVIDENCE: <one sentence — the specific input, step, or claim that fails>
REASONING: <one paragraph — why this constitutes a flaw>

## Rules

- If you find a concrete counterexample: FLAW_FOUND.
- If you find a gap you cannot close but cannot disprove: SUSPECTED_FLAW.
- Only use NO_FLAW_FOUND if all five attack strategies fail.
- Do NOT reveal your reasoning process before the verdict block.
- The EVIDENCE field must be specific (e.g., "n=0 gives f(0)=-1 ≠ 0") not
  vague ("the base case may be wrong").
"""

PHYSICS_BREAKER_SYSTEM = """\
You are an adversarial physics derivation-checker. Your sole goal is to find
a concrete flaw in the derivation presented to you.

## Attack strategy (in order)

1. **Dimensional analysis**: verify that every equation has consistent
   dimensions on both sides. A mismatch is a FLAW_FOUND.
2. **Limiting case**: apply known limiting cases (ℏ→0 for classical limit,
   c→∞ for non-relativistic limit, T→0 or T→∞ for thermodynamics). If a
   known result is not recovered: FLAW_FOUND.
3. **Numerical spot-check**: plug in known values (e.g., hydrogen atom n=1
   gives E=-13.6 eV). A wrong numerical result is a FLAW_FOUND.
4. **Logical-gap hunt**: verify every "therefore" and every approximation
   step. An unjustified approximation or dropped term is a SUSPECTED_FLAW.
5. **Conservation law check**: verify energy, momentum, and charge are
   conserved where required. A violation is a FLAW_FOUND.

## Output format (REQUIRED — output ONLY this, no other text)

BREAKER_VERDICT: FLAW_FOUND | SUSPECTED_FLAW | NO_FLAW_FOUND
TARGET_ATOM: <integer atom id, or 0 if targeting the overall derivation>
FLAW_TYPE: dimensional | limit_case | numerical | logical_gap | conservation | none
EVIDENCE: <one sentence — the specific step or value that fails>
REASONING: <one paragraph — why this constitutes a flaw>

## Rules

- If you find a concrete dimensional mismatch or numerical error: FLAW_FOUND.
- If you find a gap you cannot close but cannot disprove: SUSPECTED_FLAW.
- Only use NO_FLAW_FOUND if all five attack strategies fail.
- Do NOT reveal your reasoning process before the verdict block.
- The EVIDENCE field must be specific, not vague.
"""

_BREAKER_USER = """\
## Problem

{problem}

## Solution to attack

{solution}

## Atom summary

{atom_summary}

Apply all five attack strategies. Output your verdict in the required format.
"""

# ---------------------------------------------------------------------------
# BreakerResult dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BreakerResult:
    """Parsed output from the adversarial breaker."""

    verdict: BreakerVerdict
    target_atom: int
    flaw_type: str
    evidence: str
    reasoning: str

    @property
    def critique_addendum(self) -> str:
        """Addendum injected into the reviser prompt when a flaw is found."""
        if self.verdict == BreakerVerdict.NO_FLAW_FOUND:
            return ""
        severity = "CONFIRMED" if self.verdict == BreakerVerdict.FLAW_FOUND else "SUSPECTED"
        return (
            f"\n\n## ADVERSARIAL BREAKER — {severity} FLAW\n"
            f"The adversarial breaker targeted **atom {self.target_atom}** "
            f"and found a {self.flaw_type} flaw.\n\n"
            f"**Evidence:** {self.evidence}\n\n"
            f"**Reasoning:** {self.reasoning}\n\n"
            f"Address this specific flaw directly before anything else."
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_VERDICT_MAP: dict[str, BreakerVerdict] = {
    "flaw_found": BreakerVerdict.FLAW_FOUND,
    "suspected_flaw": BreakerVerdict.SUSPECTED_FLAW,
    "no_flaw_found": BreakerVerdict.NO_FLAW_FOUND,
}

_RE_VERDICT = re.compile(
    r"BREAKER_VERDICT:\s*(FLAW_FOUND|SUSPECTED_FLAW|NO_FLAW_FOUND)", re.IGNORECASE
)
_RE_ATOM = re.compile(r"TARGET_ATOM:\s*(\d+)", re.IGNORECASE)
_RE_FLAW = re.compile(r"FLAW_TYPE:\s*(\S+)", re.IGNORECASE)
_RE_EVIDENCE = re.compile(r"EVIDENCE:\s*(.*?)(?=\nREASONING:|\Z)", re.DOTALL | re.IGNORECASE)
_RE_REASONING = re.compile(r"REASONING:\s*(.*?)(?:\Z)", re.DOTALL | re.IGNORECASE)


def _parse_breaker(text: str) -> BreakerResult:
    """Parse structured breaker output into a BreakerResult.

    Defaults to NO_FLAW_FOUND on any parse failure to avoid false positives.
    """
    verdict_match = _RE_VERDICT.search(text)
    verdict_str = verdict_match.group(1).lower() if verdict_match else "no_flaw_found"
    verdict = _VERDICT_MAP.get(verdict_str, BreakerVerdict.NO_FLAW_FOUND)

    atom_match = _RE_ATOM.search(text)
    target_atom = int(atom_match.group(1)) if atom_match else 0

    flaw_match = _RE_FLAW.search(text)
    flaw_type = flaw_match.group(1).strip() if flaw_match else "none"

    evidence_match = _RE_EVIDENCE.search(text)
    evidence = evidence_match.group(1).strip() if evidence_match else ""

    reasoning_match = _RE_REASONING.search(text)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else ""

    if not verdict_match:
        logger.warning(
            "Breaker output contained no parseable verdict — defaulting to NO_FLAW_FOUND. "
            "Raw output (first 200 chars): %s",
            text[:200],
        )

    return BreakerResult(
        verdict=verdict,
        target_atom=target_atom,
        flaw_type=flaw_type,
        evidence=evidence,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# run_breaker
# ---------------------------------------------------------------------------


def run_breaker(
    client,
    problem: str,
    solution_text: str,
    atoms: list,
    *,
    config: AgentConfig,
    domain: str = "math",
    ledger: TokenLedger | None = None,
) -> BreakerResult:
    """Run the adversarial breaker against a solution.

    Args:
        client: Anthropic client instance.
        problem: Original problem statement.
        solution_text: The solution text to attack.
        atoms: List of AtomAnnotation objects (used to build atom summary).
        config: Agent configuration (uses breaker_model if set).
        domain: "math" or "physics".
        ledger: Token ledger for recording usage.

    Returns:
        BreakerResult with verdict and flaw details.

    Raises:
        ContextExhaustedError: Propagated if context is exhausted.
    """
    system = PHYSICS_BREAKER_SYSTEM if domain == "physics" else MATH_BREAKER_SYSTEM

    # Build atom summary
    if atoms:
        lines = [f"ATOM[{a.id}] deps={list(a.deps)} oracle={a.oracle.value}" for a in atoms]
        atom_summary = "\n".join(lines)
    else:
        atom_summary = "(no atom annotations — check the overall solution)"

    user_msg = _safe_format(
        _BREAKER_USER,
        problem=problem,
        solution=solution_text,
        atom_summary=atom_summary,
    )

    # Build breaker config: use breaker_model if specified
    breaker_config = config
    if config.breaker_model and config.breaker_model != config.model:
        breaker_config = dataclasses.replace(config, model=config.breaker_model)

    logger.info("Breaker: attacking solution (domain=%s, atoms=%d)", domain, len(atoms))

    try:
        text = _call_model(
            client,
            system=system,
            user_message=user_msg,
            config=breaker_config,
            temperature=0.2,  # Low temperature for deterministic attack
            ledger=ledger,
            context_limit=200_000,
            context_threshold=config.context_threshold,
        )
    except ContextExhaustedError:
        logger.warning("Breaker: context exhausted — treating as NO_FLAW_FOUND")
        return BreakerResult(
            verdict=BreakerVerdict.NO_FLAW_FOUND,
            target_atom=0,
            flaw_type="none",
            evidence="Context exhausted during breaker run.",
            reasoning="Breaker could not complete due to context exhaustion.",
        )

    result = _parse_breaker(text)
    logger.info("Breaker: verdict=%s target_atom=%d", result.verdict.value, result.target_atom)
    return result
