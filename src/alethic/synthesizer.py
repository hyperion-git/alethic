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

# Severity ordering for tie-breaking (most severe first)
_SEVERITY_ORDER = {
    Verdict.MAJOR_FLAW: 0,
    Verdict.UNSOLVED: 1,
    Verdict.MINOR_ISSUES: 2,
    Verdict.CORRECT: 3,
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


def aggregate_mechanical(results: list[VerificationResult]) -> dict[str, Any]:
    """Deterministic aggregation of K verification results.

    Returns dict with keys: verdict, confidence, confidence_range, issues.

    Raises:
        ValueError: If results is empty.
    """
    if not results:
        raise ValueError("aggregate_mechanical requires at least one result")

    # Majority-vote verdict
    verdict_counts = Counter(r.verdict for r in results)
    most_common = verdict_counts.most_common()
    if len(most_common) == 1 or most_common[0][1] > most_common[1][1]:
        verdict = most_common[0][0]
    else:
        # Tie — pick most severe
        tied = [v for v, c in most_common if c == most_common[0][1]]
        verdict = min(tied, key=lambda v: _SEVERITY_ORDER.get(v, 99))

    # Mean confidence
    confidences = [r.confidence for r in results]
    confidence = sum(confidences) / len(confidences)
    confidence_range = (min(confidences), max(confidences))

    # Union of issues with vote counts (deduplicated by similarity)
    _sev_rank = {"critical": 0, "major": 1, "minor": 2}
    merged_issues: list[ConsensusIssue] = []
    for r in results:
        for issue in r.issues:
            found = False
            for idx, mi in enumerate(merged_issues):
                if _similar(issue.text, mi.text):
                    higher_sev = min(
                        mi.severity,
                        issue.severity,
                        key=lambda s: _sev_rank.get(s.value, 1),
                    )
                    merged_issues[idx] = ConsensusIssue(
                        text=mi.text,
                        severity=higher_sev,
                        flagged_by=mi.flagged_by + 1,
                    )
                    found = True
                    break
            if not found:
                merged_issues.append(
                    ConsensusIssue(text=issue.text, severity=issue.severity, flagged_by=1)
                )

    # Sort by flagged_by descending, then severity (most severe first)
    sev_order = {IssueSeverity.CRITICAL: 0, IssueSeverity.MAJOR: 1, IssueSeverity.MINOR: 2}
    merged_issues.sort(key=lambda i: (-i.flagged_by, sev_order.get(i.severity, 1)))

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
    issues_lines = []
    for issue in aggregation["issues"]:
        issues_lines.append(
            f"- [{issue.severity.value.upper()}] {issue.text} ({issue.flagged_by}/{len(results)})"
        )
    issues_text = "\n".join(issues_lines) if issues_lines else "None"

    reports = []
    for i, r in enumerate(results, 1):
        reports.append(
            f"### Verifier {i}: {r.verdict.value.upper()} ({r.confidence:.2f})\n\n{r.critique}"
        )
    reports_text = "\n\n".join(reports)

    system = SYNTHESIZER_SYSTEM.format(k=len(results))
    user_msg = SYNTHESIZER_USER.format(
        verdict=aggregation["verdict"].value.upper(),
        confidence=aggregation["confidence"],
        issues_text=issues_text,
        reports_text=reports_text,
    )

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.3,
    )

    parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(parts) if parts else "[Synthesis failed]"
