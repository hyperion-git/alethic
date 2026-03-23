"""Error taxonomy for adaptive revision (feature 1.2).

Classifies verifier critique text into error categories via keyword heuristics
(no additional LLM call). Returns a category string and a per-category revision
addendum that guides the reviser toward the most effective repair strategy.

Categories are organized into a 5-level hierarchy (most → least severe):

    Level 0  PROBLEM        false_premise, counterexample
    Level 1  APPROACH       wrong_method
    Level 2  STRUCTURAL     missing_case, logic
    Level 3  MECHANICAL     algebra, units
    Level 4  PRESENTATION   interpretation, citation

Classification traverses top-down.  The first LEVEL with any keyword hits
determines the firing level and primary category.  Within a level, the
category with the most hits wins (ties broken by list position — first =
higher severity).  All levels are scanned to populate ``all_matches``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from alethic.models import OracleType

# ---------------------------------------------------------------------------
# Hierarchical category tree
# ---------------------------------------------------------------------------

_TREE: list[tuple[str, list[str]]] = [
    ("problem",      ["false_premise", "counterexample"]),
    ("approach",     ["wrong_method"]),
    ("structural",   ["missing_case", "logic"]),
    ("mechanical",   ["algebra", "units"]),
    ("presentation", ["interpretation", "citation"]),
]

# ---------------------------------------------------------------------------
# Keywords per category (exact same lists as before for 8 existing cats)
# ---------------------------------------------------------------------------

_KEYWORDS: dict[str, list[str]] = {
    "false_premise": [
        "false premise", "false claim", "claim is false", "statement is false",
        "does not hold", "no valid solution", "no solution exists",
        "unsolvable", "cannot be proved", "impossible to prove",
        "contradicts known", "violates known",
    ],
    "counterexample": [
        "counterexample", "flaw found", "breaker found",
        "regime failure", "falsif",
    ],
    "wrong_method": [
        "wrong approach", "different method", "different approach",
        "not suitable", "inapplicable", "should use", "consider using",
        "try instead", "does not apply here", "not the right",
    ],
    "missing_case": [
        "missing case", "edge case", "special case",
        "boundary case", "boundary condition", "not handled", "case analysis",
        "degenerate", "not considered", "overlooked",
    ],
    "logic": [
        "does not follow", "non sequitur", "circular", "circular argument",
        "implication", "gap in", "logical gap", "invalid inference",
        "unjustified", "without justification", "not proven", "assumption not established",
    ],
    "algebra": [
        "sign error", "wrong sign", "arithmetic", "calculation error",
        "simplif", "expand", "factor", "distribut", "algebraic error",
        "incorrect step", "wrong value", "computation error",
    ],
    "units": [
        "dimension", "dimensional", "si unit", "inconsistent units",
        "dimensionless", "dimensional mismatch",
    ],
    "interpretation": [
        "misinterpret", "misread", "wrong problem", "reinterpret",
        "different question", "weaker problem", "specification gaming",
    ],
    "citation": [
        "citation", "cite", "well known", "standard result", "it can be shown",
        "it is known", "no source", "no reference", "no proof given", "vague appeal",
        "theorem name", "by a known", "appeal to",
    ],
}

# Backward-compatible alias — some callers import _TAXONOMY_KEYWORDS directly.
# Includes all categories in tree-traversal (severity) order.
_TAXONOMY_KEYWORDS: dict[str, list[str]] = {
    cat: _KEYWORDS[cat]
    for _level_name, _cats in _TREE
    for cat in _cats
}

# Pre-compiled regex per category: single alternation of all keywords (escaped).
_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    cat: re.compile("|".join(re.escape(kw) for kw in kws))
    for cat, kws in _KEYWORDS.items()
}

# Legacy alias (list-of-tuples, severity order without wrong_method).
_TAXONOMY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (cat, _KEYWORD_PATTERNS[cat])
    for cat in _TAXONOMY_KEYWORDS
]

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_keyword(categories: list[str], lower: str) -> dict[str, int]:
    """Count keyword hits for each *category* in *lower*-cased critique text."""
    scores: dict[str, int] = {}
    for cat in categories:
        hits = len(_KEYWORD_PATTERNS[cat].findall(lower))
        if hits > 0:
            scores[cat] = hits
    return scores


_score_fn = _score_keyword

# ---------------------------------------------------------------------------
# InconsistencyResult dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InconsistencyResult:
    """Full classification result with level, primary category, and all matches.

    Attributes:
        level: The name of the firing level (e.g. "problem", "mechanical"),
               or "none" when nothing matched.
        primary: The winning category at the firing level (e.g. "false_premise"),
                 or "general" when nothing matched.
        all_matches: ``{category: hit_count}`` across ALL levels (not just the
                     firing level).  Empty dict when nothing matched.
    """

    level: str
    primary: str
    all_matches: dict[str, int]

# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_inconsistency(critique: str) -> InconsistencyResult:
    """Classify a verifier critique into the hierarchical error taxonomy.

    Traverses the ``_TREE`` top-down.  The first level with any keyword hits
    determines the firing level; within that level the category with the most
    hits wins (ties broken by list position — first = higher severity).
    All levels are scanned so ``all_matches`` is always complete.

    Returns an :class:`InconsistencyResult` with ``level``, ``primary``, and
    ``all_matches``.  When no keywords match, returns
    ``InconsistencyResult("none", "general", {})``.
    """
    lower = critique.lower()

    firing_level: str | None = None
    primary: str | None = None
    all_matches: dict[str, int] = {}

    for level_name, categories in _TREE:
        scores = _score_fn(categories, lower)
        all_matches.update(scores)

        if scores and firing_level is None:
            # First level with hits — determine primary by max score,
            # ties broken by list position (first = higher severity).
            best_cat = max(
                categories,
                key=lambda c: scores.get(c, 0),
            )
            # Only set if the best actually has hits (max among 0s is spurious).
            if scores.get(best_cat, 0) > 0:
                firing_level = level_name
                primary = best_cat

    if firing_level is None:
        return InconsistencyResult("none", "general", {})
    assert primary is not None  # guaranteed by the logic above
    return InconsistencyResult(firing_level, primary, dict(all_matches))

# ---------------------------------------------------------------------------
# Backward-compatible wrappers
# ---------------------------------------------------------------------------

def classify_errors(critique: str) -> str:
    """Classify a verifier critique into an error category via keyword heuristics.

    Thin wrapper around :func:`classify_inconsistency` — returns only the
    primary category string.

    Returns "general" if no category matches.

    Args:
        critique: The verifier's critique text.

    Returns:
        One of: "algebra", "logic", "citation", "false_premise",
                "interpretation", "units", "counterexample",
                "missing_case", "wrong_method", "general".
    """
    return classify_inconsistency(critique).primary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_all_categories() -> list[str]:
    """Return all category names in severity order (most → least severe).

    Includes ``"general"`` as the final entry.
    """
    cats = [cat for _, categories in _TREE for cat in categories]
    cats.append("general")
    return cats


def get_revision_addendum(category: str) -> str:
    """Return the revision strategy addendum for the given error category.

    Returns "" for unknown categories and "general".
    """
    return REVISION_ADDENDA.get(category, "")

# ---------------------------------------------------------------------------
# Revision addenda
# ---------------------------------------------------------------------------

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
    "false_premise": (
        "\n\n## Revision focus: questioning the premise\n"
        "Accumulated evidence suggests the problem's claim may be FALSE. "
        "Before attempting another proof, systematically search for counterexamples. "
        "Test small cases exhaustively (n=0,1,2,3). For real-valued claims, try "
        "rationals, irrationals, negative numbers, zero, and boundary values. "
        "Use Python/SymPy to automate the search. If you find a counterexample, "
        "present it clearly with computational verification."
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
    "counterexample": (
        "\n\n## Revision focus: addressing identified flaw\n"
        "A specific counterexample or logical flaw was found by the adversarial breaker. "
        "Re-examine the targeted claim at the identified input or logical step. Either "
        "refute the flaw (show why it does not actually violate your claim) or repair "
        "the proof step that fails for this input."
    ),
    "missing_case": (
        "\n\n## Revision focus: case completeness\n"
        "The dominant error is missing cases. Begin by enumerating all possible cases "
        "or branches explicitly. For each case, provide a complete argument. "
        "Pay special attention to: n=0 or n=1 base cases, empty sets, zero vectors, "
        "singular matrices, boundary conditions, and degenerate configurations. "
        "Verify each case numerically where possible."
    ),
    "wrong_method": (
        "\n\n## Revision focus: change of approach\n"
        "The current method appears fundamentally unsuitable for this problem. "
        "Do NOT revise within the current approach — choose a categorically "
        "different method. Consider what mathematical/physical structure the "
        "problem has (symmetry, recursion, conservation law, etc.) and pick "
        "a technique that exploits that structure directly."
    ),
    "general": "",
}

# ---------------------------------------------------------------------------
# Oracle routing
# ---------------------------------------------------------------------------

# Oracle routing table: error_category -> (OracleType, force_adversarial)
_ORACLE_ROUTING: dict[str, tuple[OracleType, bool]] = {
    "algebra": (OracleType.LAYER2_CONSISTENCY, False),
    "logic": (OracleType.LAYER3_LLM_ADVERSARIAL, True),
    "citation": (OracleType.LAYER3_LLM, False),
    "false_premise": (OracleType.LAYER3_LLM_ADVERSARIAL, True),
    "interpretation": (OracleType.LAYER3_LLM, False),
    "units": (OracleType.LAYER0_STRUCTURAL, False),
    "counterexample": (OracleType.LAYER1_BEHAVIORAL, False),
    "missing_case": (OracleType.LAYER1_BEHAVIORAL, False),
    "wrong_method": (OracleType.LAYER3_LLM_ADVERSARIAL, True),
    "general": (OracleType.LAYER3_LLM, False),
}


def classify_errors_routed(critique: str) -> tuple[str, OracleType, bool]:
    """Classify critique and return (category, next_oracle, force_adversarial).

    Extends classify_errors() with routing information for the Verification Ladder.
    The agent.py orchestrator reads next_oracle to decide verifier configuration
    for the next iteration.
    """
    result = classify_inconsistency(critique)
    oracle, force_adv = _ORACLE_ROUTING[result.primary]
    return result.primary, oracle, force_adv
