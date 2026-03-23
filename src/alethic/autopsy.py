"""Autopsy mode for failed solve/derive loops (feature 1.4).

When the GVR loop exhausts all iterations and returns UNSOLVED, this module
classifies the failure pattern deterministically and uses Claude to synthesize
actionable recommendations. The result is written to worklog/autopsy.md in
the session directory (if available) and returned as a Markdown string.
"""

from __future__ import annotations

from alethic.client_factory import get_client
from alethic.models import AgentResult, EventType
from alethic.subagents import _create_with_retry, _extract_text


def _best_per_iteration(raw_verify_events: list) -> list:
    """Filter VERIFY events to one per iteration (highest confidence).

    In best-of-N mode, multiple VERIFY events are emitted per iteration.
    This keeps only the winning candidate's event so downstream analysis
    reflects the actual trajectory.  Returns the list unchanged when
    best-of-N is not detected.
    """
    iterations_seen = {e.iteration for e in raw_verify_events}
    has_multi = any("candidate" in e.data for e in raw_verify_events)
    if not (has_multi and len(iterations_seen) < len(raw_verify_events)):
        return raw_verify_events

    best = []
    for it in sorted(iterations_seen):
        it_events = [e for e in raw_verify_events if e.iteration == it]
        best.append(max(it_events, key=lambda e: float(e.data.get("confidence", 0.0))))
    return best


def _classify_failure_pattern(result: AgentResult) -> str:
    """Classify the failure pattern from agent events.

    Returns one of:
    - "persistent_flaw": all iterations returned major_flaw
    - "oscillation": verdicts alternate frequently between good and bad
    - "regression": confidence peaked then fell significantly
    - "stall": confidence barely moved throughout
    """
    raw_verify_events = [e for e in result.events if e.type == EventType.VERIFY]
    if not raw_verify_events:
        return "stall"

    verify_events = _best_per_iteration(raw_verify_events)

    verdicts = [e.data.get("verdict", "") for e in verify_events]
    confidences = [float(e.data.get("confidence", 0.0)) for e in verify_events]

    # Persistent flaw: every iteration ended in major_flaw
    if all(v == "major_flaw" for v in verdicts):
        return "persistent_flaw"

    # Oscillation: verdicts alternate in > 60% of transitions (check before regression)
    if len(verdicts) >= 4:
        transitions = sum(1 for i in range(1, len(verdicts)) if verdicts[i] != verdicts[i - 1])
        if transitions / (len(verdicts) - 1) >= 0.6:
            return "oscillation"

    # Regression: confidence peaked early then dropped by > 0.15
    if len(confidences) >= 3:
        peak_idx = confidences.index(max(confidences))
        if peak_idx < len(confidences) - 1 and confidences[-1] < confidences[peak_idx] - 0.15:
            return "regression"

    # Stall: total confidence improvement < 0.05
    return "stall"


def _build_autopsy_context(result: AgentResult, pattern: str) -> str:
    """Build the structured context passed to the LLM for synthesis."""
    raw_verify_events = [e for e in result.events if e.type == EventType.VERIFY]
    verify_events = _best_per_iteration(raw_verify_events)
    stall_count = sum(1 for e in result.events if e.type == EventType.STALL_RESET)

    conf_trajectory = " → ".join(
        f"{e.data.get('confidence', 0.0):.2f}" for e in verify_events
    ) or "(no verifications recorded)"

    approaches = result.failed_approaches[-5:] if result.failed_approaches else []
    approaches_text = "\n".join(f"- {a}" for a in approaches) or "- (none recorded)"

    return (
        f"FAILURE PATTERN: {pattern}\n"
        f"ITERATIONS USED: {result.iterations_used}\n"
        f"CONFIDENCE TRAJECTORY: {conf_trajectory}\n"
        f"STALL RESETS TRIGGERED: {stall_count}\n"
        f"BEST CONFIDENCE REACHED: {result.confidence:.2f}\n"
        f"\nFAILED APPROACHES (last 5):\n{approaches_text}\n"
    )


_AUTOPSY_SYSTEM = """\
You are an expert diagnostician for AI mathematical reasoning systems. Given a \
failed solve loop's statistics, write a concise autopsy report with exactly \
these sections:

## Failure Analysis
One paragraph explaining what went wrong based on the pattern and trajectory.

## Confidence Trajectory Analysis
Interpret the confidence numbers — what does the pattern suggest about \
where the loop got stuck?

## Dominant Error Types
Based on the failed approaches, what categories of errors kept recurring?

## Recommended Next Steps
3-5 concrete, actionable suggestions. Examples:
- Reformulate the problem with additional constraints or hints
- Increase best-of-N (--best-of 3) to diversify candidate solutions
- Use --preset thorough for extended thinking budget
- Break the problem into smaller lemmas and solve each independently
- Provide a partial proof scaffold or known intermediate result in the problem

Keep the total report under 350 words. Be direct and specific.
"""


def generate_autopsy(
    result: AgentResult,
    *,
    api_key: str | None = None,
    model: str = "claude-opus-4-6",
) -> str:
    """Generate a structured autopsy report for a failed (UNSOLVED) solve loop.

    Classifies the failure pattern deterministically, then uses Claude to
    synthesize actionable recommendations.

    Args:
        result: AgentResult (typically with verdict == UNSOLVED).
        api_key: Anthropic API key (default: ANTHROPIC_API_KEY env var).
        model: Model ID for the synthesis call.

    Returns:
        Markdown autopsy report string.
    """
    pattern = _classify_failure_pattern(result)
    context = _build_autopsy_context(result, pattern)

    user = (
        f"Write an autopsy report for this failed solve loop.\n\n"
        f"PROBLEM: {result.problem[:500]}\n\n"
        f"{context}"
    )

    client = get_client(api_key=api_key)
    response = _create_with_retry(
        client,
        {
            "model": model,
            "max_tokens": 1024,
            "system": _AUTOPSY_SYSTEM,
            "messages": [{"role": "user", "content": user}],
        },
    )
    synthesis = _extract_text(response).strip()

    stall_count = sum(1 for e in result.events if e.type == EventType.STALL_RESET)
    lines = [
        "# Autopsy Report",
        "",
        f"**Failure Pattern:** {pattern.replace('_', ' ').title()}",
        f"**Iterations:** {result.iterations_used}",
        f"**Best Confidence:** {result.confidence:.2f}",
        f"**Stall Resets:** {stall_count}",
        "",
        synthesis,
    ]
    return "\n".join(lines)
