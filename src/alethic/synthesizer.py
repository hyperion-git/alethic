"""Consensus synthesis for multi-verifier results.

Two-stage pipeline:
1. Mechanical aggregation (deterministic): majority-vote verdict, mean confidence,
   union of issues with vote counts.
2. LLM critique cleanup (one API call): merges K raw critiques into one coherent text.
   Does NOT override verdict or confidence.
"""

from __future__ import annotations

import logging
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from alethic.models import (
    ConsensusIssue,
    IssueSeverity,
    Verdict,
    VerificationResult,
)

logger = logging.getLogger("alethic")

# Severity ordering: lower = more severe (used for tie-breaking in consensus).
# UNSOLVED (1) > FIXABLE (2): "no solution at all" is worse than "flawed but
# recoverable solution", so ties between the two break toward UNSOLVED.
_VERDICT_SEVERITY = {
    Verdict.MAJOR_FLAW: 0,
    Verdict.UNSOLVED: 1,
    Verdict.FIXABLE: 2,
    Verdict.MINOR_ISSUES: 3,
    Verdict.CORRECT: 4,
}

_ISSUE_SEVERITY = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.MAJOR: 1,
    IssueSeverity.MINOR: 2,
}

_ISSUE_SIMILARITY_THRESHOLD = 0.6

SYNTHESIZER_SYSTEM = """\
You are a technical editor. You receive {k} independent verification reports \
and a mechanical aggregation (verdict, confidence, issues with vote counts).

Your task: produce ONE coherent, well-written critique that represents the \
consensus view.

## Rules

1. You MUST NOT change the verdict or confidence — those are determined \
   mechanically and are final.
2. Weight issues by how many reviewers flagged them.
3. Resolve contradictions explicitly (e.g., "2 of 3 reviewers found X; \
   1 disagreed because Y").
4. Eliminate redundancy across reports.
5. Maintain the severity classifications from the aggregation.
6. Be concise — this is a report, not an essay.

## Output format

Write ONLY the unified critique text. Do not include verdict, confidence, \
or any other metadata — those are handled separately.
"""

SYNTHESIZER_USER = """\
## Mechanical Aggregation

Verdict: {verdict}
Confidence: {confidence:.2f}

Issues:
{issues_text}

## Individual Reports

{reports_text}
"""


def _similar(a: str, b: str) -> bool:
    """Check if two issue texts are similar enough to merge."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _ISSUE_SIMILARITY_THRESHOLD


def _find_similar_index(issue_text: str, merged: list[ConsensusIssue]) -> int | None:
    """Return the index of a similar issue in merged, or None if no match."""
    return next(
        (idx for idx, mi in enumerate(merged) if _similar(issue_text, mi.text)),
        None,
    )


def _most_severe(a: IssueSeverity, b: IssueSeverity) -> IssueSeverity:
    """Return whichever severity is more severe (lower rank)."""
    return min(a, b, key=lambda s: _ISSUE_SEVERITY.get(s, 1))


def aggregate_mechanical(results: list[VerificationResult]) -> dict[str, Any]:
    """Deterministic aggregation of K verification results.

    Returns dict with keys: verdict, confidence, confidence_range, issues.

    Raises:
        ValueError: If results is empty.
    """
    if not results:
        raise ValueError("aggregate_mechanical requires at least one result")

    # Majority-vote verdict (ties broken by severity)
    verdict_counts = Counter(r.verdict for r in results)
    top_count = verdict_counts.most_common(1)[0][1]
    tied = [v for v, c in verdict_counts.items() if c == top_count]
    verdict = min(tied, key=lambda v: _VERDICT_SEVERITY.get(v, 99))

    # Mean confidence
    confidences = [r.confidence for r in results]
    confidence = sum(confidences) / len(confidences)
    confidence_range = (min(confidences), max(confidences))

    # Union of issues with vote counts (deduplicated by similarity)
    merged_issues: list[ConsensusIssue] = []
    for r in results:
        for issue in r.issues:
            match_idx = _find_similar_index(issue.text, merged_issues)
            if match_idx is not None:
                mi = merged_issues[match_idx]
                merged_issues[match_idx] = ConsensusIssue(
                    text=mi.text,
                    severity=_most_severe(mi.severity, issue.severity),
                    flagged_by=mi.flagged_by + 1,
                )
            else:
                merged_issues.append(
                    ConsensusIssue(text=issue.text, severity=issue.severity, flagged_by=1)
                )

    merged_issues.sort(key=lambda i: (-i.flagged_by, _ISSUE_SEVERITY.get(i.severity, 1)))

    return {
        "verdict": verdict,
        "confidence": confidence,
        "confidence_range": confidence_range,
        "issues": merged_issues,
    }


def synthesize_critique(
    client: Any,
    results: list[VerificationResult],
    aggregation: dict[str, Any],
    *,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
) -> str:
    """LLM critique cleanup — merges K raw critiques into one coherent text.

    Does NOT override verdict or confidence.
    """
    issues_text = "\n".join(
        f"- [{issue.severity.value.upper()}] {issue.text} ({issue.flagged_by}/{len(results)})"
        for issue in aggregation["issues"]
    ) or "None"

    reports_text = "\n\n".join(
        f"### Verifier {i}: {r.verdict.value.upper()} ({r.confidence:.2f})\n\n{r.critique}"
        for i, r in enumerate(results, 1)
    )

    system = SYNTHESIZER_SYSTEM.format(k=len(results))
    user_msg = SYNTHESIZER_USER.format(
        verdict=aggregation["verdict"].value.upper(),
        confidence=aggregation["confidence"],
        issues_text=issues_text,
        reports_text=reports_text,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.3,
        )
        parts = [b.text for b in response.content if hasattr(b, "text")]
        return "\n".join(parts) if parts else "[Synthesis failed]"
    except Exception as e:
        logger.warning("Synthesis failed, falling back to concatenation: %s", e)
        return "\n\n".join(
            f"--- Verifier {i} ---\n{r.critique}" for i, r in enumerate(results, 1)
        )
