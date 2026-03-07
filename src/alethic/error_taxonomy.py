"""Error taxonomy for adaptive revision (feature 1.2).

Classifies verifier critique text into error categories via keyword heuristics
(no additional LLM call). Returns a category string and a per-category revision
addendum that guides the reviser toward the most effective repair strategy.
"""

from __future__ import annotations

from alethic.models import OracleType

# Keyword -> category mapping. Checked in priority order; first match wins.
# Keys are category names; values are lists of lowercase substrings to search for.
_TAXONOMY_KEYWORDS: dict[str, list[str]] = {
    "algebra": [
        "sign error", "wrong sign", "arithmetic", "calculation error",
        "simplif", "expand", "factor", "distribut", "algebraic error",
        "incorrect step", "wrong value", "computation error",
    ],
    "logic": [
        "does not follow", "non sequitur", "circular", "circular argument",
        "implication", "gap in", "logical gap", "invalid inference",
        "unjustified", "without justification", "not proven", "assumption not established",
    ],
    "citation": [
        "citation", "cite", "well known", "standard result", "it can be shown",
        "it is known", "no source", "no reference", "no proof given", "vague appeal",
        "theorem name", "by a known", "appeal to",
    ],
    "interpretation": [
        "misinterpret", "misread", "premise", "wrong problem", "reinterpret",
        "different question", "weaker problem", "specification", "scope",
    ],
    "units": [
        "unit", "dimension", "dimensional", "si unit", "conversion",
        "magnitude", "does not balance", "inconsistent units",
    ],
    "missing_case": [
        "missing case", "edge case", "counterexample", "special case",
        "boundary case", "boundary condition", "not handled", "case analysis",
        "degenerate", "not considered", "overlooked",
    ],
}

REVISION_ADDENDA: dict[str, str] = {
    "algebra": (
        "\n\n## Revision focus: algebraic correctness\n"
        "The dominant error is algebraic. Re-derive each arithmetic or algebraic step "
        "from scratch — do not copy expressions from your previous attempt. At each "
        "step, verify the result numerically using SymPy or NumPy before proceeding. "
        "Be especially careful with signs, exponents, and distribution."
    ),
    "logic": (
        "\n\n## Revision focus: logical rigor\n"
        "The dominant error is logical. For every inference in your proof, write an "
        "explicit justification: 'This follows because...'. Do not skip steps. "
        "If you cannot rigorously justify an inference, treat it as an open sub-problem "
        "and solve it separately before proceeding."
    ),
    "citation": (
        "\n\n## Revision focus: citation accuracy\n"
        "The dominant error is citation vagueness. For every theorem, lemma, or known "
        "result you invoke: either (a) prove it inline within your solution, or "
        "(b) cite it by its exact conventional name (e.g., 'by the Cauchy-Schwarz "
        "inequality', 'by Fermat's Little Theorem'). Remove all 'it is well known' "
        "and 'by a standard result' phrasing."
    ),
    "interpretation": (
        "\n\n## Revision focus: problem interpretation\n"
        "The dominant error is misinterpretation. Re-read the problem statement "
        "carefully before writing a single line. Restate the problem in your own words "
        "at the top of your solution to confirm you understand it. Verify that your "
        "conclusion directly answers the question asked — not a weaker or related question."
    ),
    "units": (
        "\n\n## Revision focus: dimensional consistency\n"
        "The dominant error is dimensional. At every step, track units explicitly. "
        "Write the units of each quantity next to it (e.g., [J], [m/s\u00b2], [kg\u22c5m\u00b2/s\u00b2]). "
        "Before submitting your answer, verify that both sides of every equation have "
        "identical dimensions. Use sympy.physics.units or scipy.constants for reference values."
    ),
    "missing_case": (
        "\n\n## Revision focus: case completeness\n"
        "The dominant error is missing cases. Begin by enumerating all possible cases "
        "or branches explicitly. For each case, provide a complete argument. "
        "Pay special attention to: n=0 or n=1 base cases, empty sets, zero vectors, "
        "singular matrices, boundary conditions, and degenerate configurations. "
        "Verify each case numerically where possible."
    ),
    "general": "",
}


def classify_errors(critique: str) -> str:
    """Classify a verifier critique into an error category via keyword heuristics.

    Checks categories in priority order; returns the first match.
    Returns "general" if no category matches.

    Args:
        critique: The verifier's critique text.

    Returns:
        One of: "algebra", "logic", "citation", "interpretation",
                "units", "missing_case", "general".
    """
    lower = critique.lower()
    for category, keywords in _TAXONOMY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "general"


def get_revision_addendum(category: str) -> str:
    """Return the revision strategy addendum for the given error category.

    Returns "" for unknown categories and "general".
    """
    return REVISION_ADDENDA.get(category, "")


# Oracle routing table: error_category -> (OracleType, force_adversarial)
_ORACLE_ROUTING: dict[str, tuple[OracleType, bool]] = {
    "algebra": (OracleType.LAYER2_CONSISTENCY, False),
    "logic": (OracleType.LAYER3_LLM_ADVERSARIAL, True),
    "citation": (OracleType.LAYER3_LLM, False),
    "interpretation": (OracleType.LAYER3_LLM, False),
    "units": (OracleType.LAYER0_STRUCTURAL, False),
    "missing_case": (OracleType.LAYER1_BEHAVIORAL, False),
    "general": (OracleType.LAYER3_LLM, False),
}


def classify_errors_routed(critique: str) -> tuple[str, OracleType, bool]:
    """Classify critique and return (category, next_oracle, force_adversarial).

    Extends classify_errors() with routing information for the Verification Ladder.
    The agent.py orchestrator reads next_oracle to decide verifier configuration
    for the next iteration.
    """
    category = classify_errors(critique)
    oracle, force_adv = _ORACLE_ROUTING[category]
    return category, oracle, force_adv
