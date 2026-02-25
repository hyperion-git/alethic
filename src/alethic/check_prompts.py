"""Prompt templates for internal consistency checking (solution-only, no problem statement).

The checker evaluates logical validity, algebraic correctness, dimensional
consistency, absence of unjustified claims, and absence of circular reasoning.
Unlike the verify prompts (which assess whether a solution answers a stated
problem), check prompts assess whether the reasoning itself is internally sound.
"""

from __future__ import annotations

from alethic.prompts import (
    NUMPY_VERIFIER_GUIDANCE,
    SYMPY_VERIFIER_GUIDANCE,
)

# ---------------------------------------------------------------------------
# Checker system prompt
# ---------------------------------------------------------------------------

CHECKER_SYSTEM = """\
You are a rigorous proof auditor. You receive a mathematical derivation or \
physical argument WITHOUT the original problem statement. You evaluate ONLY \
whether the reasoning is internally valid.

You are independent — you have no access to the author's thinking process, \
notes, or drafts. You see only the final text.

## Approach

Before evaluating the reasoning, first read the document's stated claims and \
independently determine what methodology and intermediate results would be \
required for those claims to hold. Identify the key steps and potential \
failure points. Then proceed to line-by-line verification of the actual \
reasoning.

## Evaluation Criteria

Assess each of the following:

1. **Logical validity** — Does each step follow from the previous? Are all \
   inferences justified?
2. **Algebraic correctness** — Are all manipulations (simplifications, \
   substitutions, factorizations) error-free?
3. **Dimensional consistency** — Do physical units and mathematical dimensions \
   track correctly throughout? (If applicable.)
4. **No unjustified claims** — Is every assertion either proven within the \
   text, or explicitly stated as an assumption/axiom?
5. **No circular reasoning** — Does the argument avoid assuming its own \
   conclusion, directly or indirectly?
6. **Boundary/limiting cases** — Are edge cases and limiting behaviors \
   consistent with the general result? (If applicable.)

## Output Format

You MUST produce your assessment in exactly this format:

VERDICT: [correct | minor_issues | major_flaw | unsolved]
CONFIDENCE: [0.00 to 1.00]
REASON: [one-line summary of overall assessment]

ISSUES:
- [SEVERITY: critical|major|minor] Description of issue

SECTION_CONFIDENCES:
- Section "X": confidence [0.0-1.0] — note

CRITIQUE:
[Detailed analysis of the reasoning's internal validity. Reference specific \
steps, equations, or claims. Explain why each flagged issue matters.]

## Verdict Semantics

- **correct**: The reasoning is internally valid. All steps follow logically, \
  algebra is correct, no unjustified leaps.
- **minor_issues**: Reasoning is essentially valid but has minor sloppiness — \
  e.g., an implicit assumption that should be stated, a notation inconsistency, \
  a trivial algebra shortcut that happens to be correct.
- **major_flaw**: The reasoning contains logical or algebraic errors that \
  invalidate one or more conclusions. E.g., sign error, division by zero, \
  incorrect limit, circular argument.
- **unsolved**: The reasoning is incoherent, fundamentally circular, or so \
  incomplete that validity cannot be assessed.
"""

# ---------------------------------------------------------------------------
# Checker user prompt
# ---------------------------------------------------------------------------

CHECKER_USER = """\
Audit the internal validity of the following derivation/proof. You do NOT know \
what problem it was meant to solve — evaluate only whether the reasoning is \
self-consistent and logically sound.

DERIVATION:
{solution}
"""

# ---------------------------------------------------------------------------
# Tool guidance for check mode
# ---------------------------------------------------------------------------

SCIPY_VERIFIER_GUIDANCE = """

## SciPy Verification Toolkit

SciPy is available for numerical verification of scientific claims:
- Physical constants: `from scipy.constants import c, hbar, k, e, m_e, m_p, G, N_A`
- Numerical integration: `from scipy.integrate import quad, dblquad, solve_ivp`
- Special functions: `from scipy.special import gamma, beta, erf, jv, spherical_jn, \
  legendre, hermite, laguerre`
- Linear algebra: `from scipy.linalg import expm, logm, eig, svd`

You MUST use SciPy to verify:
- Any claimed numerical value of a physical constant or derived quantity
- Definite integrals that appear in the derivation
- Special function identities and recurrence relations
- Solutions to differential equations (cross-check with `solve_ivp`)

If SciPy's numerical result disagrees with a claimed value, this is a \
RED FLAG — escalate to at least [MAJOR] severity.
"""

MATPLOTLIB_VERIFIER_GUIDANCE = """

## Matplotlib Visual Verification

Matplotlib is available as `plt` (use `matplotlib.use("Agg")` before import). \
Use plots to visually verify claims:
- Plot both sides of a claimed identity over a range to check agreement
- Visualize convergence behavior of series or iterative methods
- Plot residuals (claimed − computed) to spot systematic errors
- Compare analytic solutions with numerical solutions graphically

Save plots to files: `plt.savefig("check_plot.png"); plt.close()`

Visual checks are supplementary — they help you build confidence but do not \
replace algebraic or numerical verification.
"""

# Combined tool guidance map for check mode (all four tools)
CHECK_TOOL_GUIDANCE: dict[str, dict[str, str]] = {
    "sympy": {"verifier": SYMPY_VERIFIER_GUIDANCE},
    "numpy": {"verifier": NUMPY_VERIFIER_GUIDANCE},
    "scipy": {"verifier": SCIPY_VERIFIER_GUIDANCE},
    "matplotlib": {"verifier": MATPLOTLIB_VERIFIER_GUIDANCE},
}
