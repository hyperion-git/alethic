"""Pre-flight pitfall surveyor.

Runs ONCE before the GVR loop, given ONLY the problem statement. Produces a
short structured list of known pitfalls, canonical methods, and candidate
sanity checks specific to THIS problem. Output is injected into the
generator (to avoid pitfalls) and the verifier (to seed CHECKS PERFORMED
with problem-specific entries).

Decoupling preserved: the surveyor never sees any solution. It runs before
any generation, so its output cannot be biased toward a particular
candidate's failure modes.

Idea ported from huggingface/physics-intern background-surveyor agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from alethic.subagents import _create_with_retry, _extract_text

logger = logging.getLogger(__name__)


SURVEYOR_SYSTEM = """\
You are a pre-flight surveyor for a mathematical/physics reasoning system. \
You see ONLY the problem statement. You do NOT solve it. Your job is to \
produce a short, problem-specific list of known pitfalls, canonical methods, \
and candidate sanity checks that downstream agents (a generator and a \
verifier) will use as scaffolding.

Be specific to THIS problem, not generic. "Watch sign errors" is useless. \
"For the Lamb shift derivation, the Bethe logarithm must absorb the UV \
divergence — verify the cutoff dependence cancels at order alpha^5" is useful.

## Output format (you MUST follow this exactly)

KNOWN_PITFALLS:
- one specific pitfall for this problem
- ...
(2-6 entries, ranked by likelihood; write NONE if no specific pitfalls apply)

CANONICAL_METHODS:
- one method known to work for this problem family
- ...
(1-4 entries)

SANITY_CHECK_CANDIDATES:
- [constraint|conjecture] testable predicate the final answer should satisfy
- ...
(3-6 entries — these become the verifier's CHECKS PERFORMED seed list)

If the problem is well-posed but you cannot identify any specific pitfalls \
beyond generic ones, write `NONE` under KNOWN_PITFALLS rather than padding. \
Empty is honest; padded is harmful.
"""


@dataclass
class SurveyResult:
    """Structured output of a pre-flight surveyor pass."""

    pitfalls: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    sanity_checks: list[tuple[str, str]] = field(default_factory=list)
    # sanity_checks entries are (type, predicate) tuples; type in {constraint, conjecture}
    raw_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.pitfalls or self.methods or self.sanity_checks)


def survey(
    problem: str,
    client,
    config,
) -> SurveyResult:
    """Run the pre-flight surveyor and return a structured SurveyResult.

    Decoupling: receives only the problem statement, never a solution.
    Failures (parse errors, API errors) degrade gracefully to an empty
    SurveyResult — the loop continues without scaffolding.
    """
    kwargs = {
        "model": config.model,
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": SURVEYOR_SYSTEM,
        "messages": [{"role": "user", "content": f"PROBLEM:\n{problem}"}],
    }
    try:
        response = _create_with_retry(client, kwargs)
        text = _extract_text(response)
    except Exception as e:
        logger.warning("Surveyor call failed (%s); proceeding without scaffolding.", e)
        return SurveyResult()
    return _parse_survey(text)


_SECTION_RE = re.compile(
    r"^\s*(KNOWN_PITFALLS|CANONICAL_METHODS|SANITY_CHECK_CANDIDATES)\s*:\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SANITY_RE = re.compile(r"^\s*\[(constraint|conjecture)\]\s*(.+?)\s*$", re.IGNORECASE)


def _parse_survey(text: str) -> SurveyResult:
    result = SurveyResult(raw_text=text)
    # Split on section headers, keeping their identity.
    sections: dict[str, list[str]] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1).upper()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        sections[name] = [
            line.lstrip("-•* ").strip()
            for line in body.splitlines()
            if line.strip().startswith(("-", "•", "*"))
        ]

    pitfalls = sections.get("KNOWN_PITFALLS", [])
    if len(pitfalls) == 1 and pitfalls[0].upper() == "NONE":
        pitfalls = []
    result.pitfalls = pitfalls
    result.methods = sections.get("CANONICAL_METHODS", [])

    for raw in sections.get("SANITY_CHECK_CANDIDATES", []):
        # Distinct name from the section-header `m` above: that one is a
        # Match[str] from enumerate(), this one is Match[str] | None.
        sanity = _SANITY_RE.match(raw)
        if sanity:
            result.sanity_checks.append((sanity.group(1).lower(), sanity.group(2)))
        elif raw:
            # Tolerate untyped entries — default to constraint.
            result.sanity_checks.append(("constraint", raw))
    return result


def format_survey_block(survey: SurveyResult, role: str) -> str:
    """Format a SurveyResult into a prompt block for generator or verifier.

    `role` is "generator" or "verifier" — selects the appropriate guidance
    suffix from the prompt modules at call sites; this function only emits
    the data block.
    """
    if survey.is_empty:
        return ""
    pitfalls_text = (
        "\n".join(f"- {p}" for p in survey.pitfalls) if survey.pitfalls else "- (none)"
    )
    methods_text = (
        "\n".join(f"- {m}" for m in survey.methods) if survey.methods else "- (none)"
    )
    checks_text = (
        "\n".join(f"- [{t}] {p}" for t, p in survey.sanity_checks)
        if survey.sanity_checks
        else "- (none)"
    )
    return (
        "\n\n## Problem-specific surveyor scaffolding\n"
        "A pre-flight surveyor analyzed this problem (without seeing any "
        "solution) and produced the following scaffolding. Treat it as "
        "advisory, not authoritative.\n\n"
        f"<known-pitfalls>\n{pitfalls_text}\n</known-pitfalls>\n\n"
        f"<canonical-methods>\n{methods_text}\n</canonical-methods>\n\n"
        f"<sanity-check-candidates>\n{checks_text}\n</sanity-check-candidates>\n"
    )
