"""Output formatting for ConsensusResult (verify/check commands)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alethic.models import ConsensusResult


def format_consensus(
    result: ConsensusResult,
    *,
    mode: str = "text",
    command: str = "verify",
    session_dir: str | None = None,
) -> str:
    """Format a ConsensusResult for display.

    Args:
        result: The consensus result to format.
        mode: "text" (box-drawing), "json", or "quiet" (single line).
        command: "verify" or "check" — used in the header label.
        session_dir: Optional session directory path for the footer.

    Returns:
        Formatted string.
    """
    if mode == "json":
        return _format_json(result)
    if mode == "quiet":
        return _format_quiet(result)
    return _format_text(result, command=command, session_dir=session_dir)


def _format_text(
    result: ConsensusResult,
    *,
    command: str = "verify",
    session_dir: str | None = None,
) -> str:
    label = command.upper()
    w = 48
    bar = "\u2501" * w
    thin = "\u2500" * w

    lines = [
        bar,
        f"  ALETHIC {label}  \u2502  Domain: {result.domain_detected}  \u2502  K={result.num_verifiers}",
        bar,
        "",
        f"  Verdict:     {result.verdict.value.upper()}",
        f"  Confidence:  {result.confidence:.2f}  (range: {result.confidence_range[0]:.2f}\u2013{result.confidence_range[1]:.2f})",
        f"  Consensus:   {result.consensus_ratio} agree",
        "",
    ]

    # Critique
    lines.append(thin)
    lines.append("  CRITIQUE")
    lines.append(thin)
    lines.append("")
    for crit_line in result.critique.splitlines():
        lines.append(f"  {crit_line}")
    lines.append("")

    # Issues
    if result.issues:
        lines.append(thin)
        lines.append(f"  ISSUES ({len(result.issues)})")
        lines.append(thin)
        lines.append("")
        for issue in result.issues:
            sev = issue.severity.value.upper()
            lines.append(f"  [{sev}] {issue.text}  ({issue.flagged_by}/{result.num_verifiers})")
        lines.append("")

    # Footer
    lines.append(bar)
    if session_dir:
        lines.append(f"  Session: {session_dir}")
        lines.append(bar)

    return "\n".join(lines)


def _format_json(result: ConsensusResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def _format_quiet(result: ConsensusResult) -> str:
    return (
        f"{result.verdict.value.upper()}  "
        f"{result.confidence:.2f}  "
        f"{result.consensus_ratio}  "
        f"{result.domain_detected}"
    )
