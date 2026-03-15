"""The three Alethic subagents: Generator, Verifier, Reviser.

Each subagent wraps a Claude API call with role-specific prompt scaffolding.
The Verifier is deliberately isolated from the Generator's thinking traces —
this is the core architectural insight from DeepMind's Aletheia.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import anthropic

from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
from alethic.models import (
    AgentConfig,
    AtomConfidence,
    Issue,
    IssueSeverity,
    Revision,
    SectionConfidence,
    Solution,
    TokenLedger,
    Verdict,
    VerificationResult,
)
from alethic.prompts import (
    BALANCED_GENERATOR_ADDENDUM,
    GENERATOR_SYSTEM,
    GENERATOR_USER,
    REVISER_SYSTEM,
    REVISER_USER,
    VERIFIER_SYSTEM,
    VERIFIER_USER,
)
from alethic.tools import PYTHON_TOOL, process_tool_calls

logger = logging.getLogger("alethic")


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

_SENTINEL_RE = re.compile(r"^ALETHIC_L\d+_CHECK:.*$", re.MULTILINE)


def _strip_sentinels(text: str) -> str:
    """Remove ALETHIC_LN_CHECK: lines from solution text.

    Sentinel lines are injected by the generator for the verifier's benefit.
    They must be stripped before presenting the corrected solution to the reviser
    to avoid polluting the revision context with verification metadata.
    """
    stripped = _SENTINEL_RE.sub("", text)
    # Collapse any double blank lines left by removed sentinels
    return re.sub(r"\n{3,}", "\n\n", stripped)


def _safe_format(template: str, **kwargs: str) -> str:
    """Format template using single-pass regex to avoid cascade replacement bugs.

    Sequential str.replace() corrupts values that contain other placeholder names
    (e.g., problem text containing literal '{solution}' gets double-replaced).
    Single-pass re.sub() replaces only known keys and never re-processes replacements.
    """
    return _PLACEHOLDER_RE.sub(
        lambda m: kwargs.get(m.group(1), m.group(0)), template
    )


_SEVERITY_MAP: dict[str, IssueSeverity] = {
    "CRITICAL": IssueSeverity.CRITICAL,
    "MAJOR": IssueSeverity.MAJOR,
    "MINOR": IssueSeverity.MINOR,
}


def _extract_text(response) -> str:
    """Extract concatenated text blocks from an Anthropic response."""
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts) if parts else "[No response generated]"


_MAX_RETRIES = 3


def _create_with_retry(client, kwargs: dict):
    """Call client.messages.create with exponential backoff on rate limits."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt < _MAX_RETRIES:
                delay = 2**attempt  # 1s, 2s, 4s
                logger.warning(
                    "Rate limited (attempt %d/%d) — retrying in %ds",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error("Rate limited — exhausted %d retries", _MAX_RETRIES)
                raise
    # Unreachable, but keeps the type checker happy
    raise RuntimeError("Unreachable")


def _call_model(
    client,
    *,
    system: str,
    user_message: str,
    config: AgentConfig,
    temperature: float,
    tools: list[dict] | None = None,
    ledger: TokenLedger | None = None,
    context_limit: int = 200_000,
    context_threshold: float = 0.8,
) -> str:
    """Make an API call to Claude, handling tool use loops.

    Returns the final text response after all tool calls have been resolved.

    Args:
        ledger: If provided, records token usage from each API call.
        context_limit: Model context window size in tokens.
        context_threshold: Fraction of context_limit that triggers safety abort.

    Raises:
        ContextExhaustedError: If estimated input tokens exceed the threshold.
        TruncatedResponseError: If the API response was truncated (stop_reason=max_tokens).
    """
    # Pre-flight estimate: chars/4 heuristic for token count
    estimated_input = len(system + user_message) // 4
    if estimated_input > context_threshold * context_limit:
        raise ContextExhaustedError(
            f"Pre-flight estimate: ~{estimated_input} tokens estimated "
            f"(threshold: {int(context_threshold * context_limit)} of {context_limit})"
        )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    kwargs = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": system,
        "messages": messages,
    }
    if config.extended_thinking:
        # Extended thinking requires temperature=1 and uses a budget_tokens param
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": config.thinking_budget,
        }
        kwargs["temperature"] = 1  # Required by the API when thinking is enabled
        if temperature != 1:
            logger.debug(
                "Extended thinking enabled; ignoring temperature=%.1f (API requires 1)",
                temperature,
            )
    else:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = tools

    # Tool-use loop: keep calling until we get a final text response
    max_tool_rounds = 5
    for _ in range(max_tool_rounds):
        response = _create_with_retry(client, kwargs)

        if ledger is not None:
            ledger.record(response.usage)

        # Check for tool use
        tool_results = process_tool_calls(response) if tools else []

        if not tool_results:
            # Check for truncation before returning
            if getattr(response, "stop_reason", None) == "max_tokens":
                raise TruncatedResponseError(
                    f"Response truncated (stop_reason=max_tokens) after "
                    f"{ledger.api_calls if ledger else '?'} calls"
                )
            # No tool calls — extract final text
            return _extract_text(response)

        # Build tool result messages and continue the loop
        # First, add the assistant's response (with tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        # Then add tool results
        tool_result_content = [
            {
                "type": "tool_result",
                "tool_use_id": tr["tool_use_id"],
                "content": tr["result"],
            }
            for tr in tool_results
        ]
        messages.append({"role": "user", "content": tool_result_content})
        kwargs["messages"] = messages

        # Re-estimate context after tool round
        total_chars = sum(len(str(m.get("content", ""))) for m in messages) + len(system)
        re_estimated = total_chars // 4
        if re_estimated > context_threshold * context_limit:
            raise ContextExhaustedError(
                f"Tool-use loop estimate: ~{re_estimated} tokens "
                f"(threshold: {int(context_threshold * context_limit)} of {context_limit})"
            )

    # Exhausted tool rounds — return whatever text we have
    return _extract_text(response)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate(
    client,
    problem: str,
    config: AgentConfig,
    iteration: int,
    balanced: bool = True,
    *,
    failed_approaches: tuple[str, ...] = (),
    reset_context: str | None = None,
    system_prompt: str | None = None,
    user_template: str | None = None,
    balanced_addendum: str | None = None,
    ledger: TokenLedger | None = None,
    context_limit: int = 200_000,
    context_threshold: float = 0.8,
    partial_solution: str | None = None,
) -> Solution:
    """Generate a candidate solution.

    Args:
        client: Anthropic client instance.
        problem: The problem statement.
        config: Agent configuration.
        iteration: Current iteration number (for logging).
        balanced: If True, append balanced prompting addendum to encourage
                  exploring counterexamples before proving.
        system_prompt: Override the default generator system prompt.
        user_template: Override the default generator user template.
        balanced_addendum: Override the default balanced prompting addendum.

    Returns:
        A Solution object containing the candidate.
    """
    system = system_prompt if system_prompt is not None else GENERATOR_SYSTEM
    if balanced:
        addendum = (
            balanced_addendum if balanced_addendum is not None else BALANCED_GENERATOR_ADDENDUM
        )
        system += addendum

    template = user_template if user_template is not None else GENERATOR_USER
    user_msg = _safe_format(template, problem=problem)

    if reset_context is not None:
        user_msg += f"\n\n{reset_context}"
    elif failed_approaches:
        approaches_text = "\n".join(f"- {a}" for a in failed_approaches)
        user_msg += (
            f"\n\n## Previously attempted strategies that did NOT work:\n"
            f"{approaches_text}\n"
            f"Avoid repeating these approaches. Try a fundamentally different strategy."
        )

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    logger.info("Generator: iteration %d — generating solution", iteration)

    text = _call_model(
        client,
        system=system,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_generator,
        tools=tools,
        ledger=ledger,
        context_limit=context_limit,
        context_threshold=context_threshold,
    )

    return Solution(
        problem=problem,
        solution_text=text,
        iteration=iteration,
    )


# ---------------------------------------------------------------------------
# Verifier — the decoupled component
# ---------------------------------------------------------------------------


_VERDICT_MAP: dict[str, Verdict] = {
    "correct": Verdict.CORRECT,
    "minor_issues": Verdict.MINOR_ISSUES,
    "fixable": Verdict.FIXABLE,
    "major_flaw": Verdict.MAJOR_FLAW,
    "unsolved": Verdict.UNSOLVED,
}


def _parse_issues(text: str) -> list[Issue]:
    """Parse the ISSUES block from verifier output into Issue objects."""
    issues_match = re.search(
        r"ISSUES:\s*\n(.*?)(?=\nREASON:|\nATOM CONFIDENCES:|\nSECTION CONFIDENCES:|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if not issues_match:
        return []

    raw_issues = issues_match.group(1).strip()
    if raw_issues.lower() == "none":
        return []

    issues: list[Issue] = []
    for line in raw_issues.split("\n"):
        cleaned = line.strip().lstrip("- ").strip()
        if not cleaned:
            continue
        # Try to parse severity tag: [CRITICAL], [MAJOR], [MINOR]
        severity_tag_match = re.match(r"\[(\w+)\]\s*(.*)", cleaned)
        if severity_tag_match:
            tag = severity_tag_match.group(1).upper()
            issue_text = severity_tag_match.group(2).strip()
            severity = _SEVERITY_MAP.get(tag, IssueSeverity.MAJOR)
        else:
            issue_text = cleaned
            severity = IssueSeverity.MAJOR
        if issue_text:
            issues.append(Issue(text=issue_text, severity=severity))
    return issues


def _parse_section_confidences(text: str) -> list[SectionConfidence]:
    """Parse SECTION CONFIDENCES block from verifier output."""
    match = re.search(
        r"SECTION CONFIDENCES:\s*\n(.*?)(?=\nREASON:|\nISSUES:|\nATOM CONFIDENCES:|\nCORRECTED SOLUTION:|\nEND CORRECTED SOLUTION:|\nVERDICT:|\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return []

    results = []
    for line in match.group(1).strip().split("\n"):
        cleaned = line.strip().lstrip("- ").strip()
        if not cleaned:
            continue
        # Skip atom confidence lines that bled past ATOM CONFIDENCES block
        if re.match(r"ATOM\[\d+\]", cleaned):
            continue
        # Pattern: "section name: 0.85 optional note"
        sc_match = re.match(r"(.+?):\s*([\d.]+)\s*(.*)", cleaned)
        if sc_match:
            section = sc_match.group(1).strip()
            try:
                conf = float(sc_match.group(2))
                conf = max(0.0, min(1.0, conf))
            except ValueError:
                continue
            note = sc_match.group(3).strip()
            results.append(SectionConfidence(section=section, confidence=conf, note=note))
    return results


_ATOM_CONF_LINE_RE = re.compile(r"^ATOM\[(\d+)\]:\s+([\d.]+)(?:\s+(.+))?$")


def _parse_atom_confidences(text: str) -> list[AtomConfidence]:
    """Parse ATOM CONFIDENCES block from verifier output."""
    block_match = re.search(
        r"ATOM CONFIDENCES:\s*\n(.*?)(?=\nSECTION CONFIDENCES:|\nCORRECTED SOLUTION:|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not block_match:
        return []
    results = []
    for line in block_match.group(1).strip().splitlines():
        m = _ATOM_CONF_LINE_RE.match(line.strip())
        if m:
            atom_id = int(m.group(1))
            confidence = max(0.0, min(1.0, float(m.group(2))))
            note_raw = m.group(3)
            note = note_raw.strip() if note_raw and note_raw.strip() else None
            results.append(AtomConfidence(id=atom_id, confidence=confidence, note=note))
    return results


def _parse_verification(text: str) -> VerificationResult:
    """Parse structured verifier output into a VerificationResult."""
    # Extract verdict
    verdict_match = re.search(
        r"VERDICT:\s*(correct|minor_issues|fixable|major_flaw|unsolved)",
        text,
        re.IGNORECASE,
    )
    if verdict_match:
        verdict_str = verdict_match.group(1).lower()
    else:
        logger.warning("Verdict regex failed to match — defaulting to major_flaw")
        verdict_str = "major_flaw"

    verdict = _VERDICT_MAP.get(verdict_str, Verdict.MAJOR_FLAW)

    # Extract confidence
    conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", text, re.IGNORECASE)
    if conf_match:
        try:
            raw = float(conf_match.group(1))
        except ValueError:
            logger.warning(
                "Confidence value %r is malformed — defaulting to 0.5", conf_match.group(1)
            )
            raw = 0.5
        # Normalize percentage values (e.g., 95 → 0.95); small overshoots
        # like 1.5 are just clamped rather than treated as percentages.
        if raw >= 2.0:
            raw /= 100.0
        confidence = max(0.0, min(1.0, raw))
    else:
        logger.warning("Confidence regex failed to match — defaulting to 0.5")
        confidence = 0.5

    # Extract critique (stops at REASON: or ISSUES: or SECTION CONFIDENCES: whichever comes first)
    critique_match = re.search(
        r"CRITIQUE:\s*\n(.*?)(?=\nREASON:|\nISSUES:|\nSECTION CONFIDENCES:|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    critique = critique_match.group(1).strip() if critique_match else text

    # Extract reason (for false-premise detection)
    reason_match = re.search(r"REASON:\s*(.*?)(?=\nISSUES:|\Z)", text, re.DOTALL | re.IGNORECASE)
    reason = reason_match.group(1).strip() if reason_match else ""

    issues = _parse_issues(text)

    if not verdict_match and not conf_match and not critique_match:
        logger.warning(
            "Verifier output contained no parseable fields — "
            "all values are defaults. Raw output (first 200 chars): %s",
            text[:200],
        )

    section_confidences = _parse_section_confidences(text)
    atom_confidences = _parse_atom_confidences(text)

    # Extract corrected solution (for FIXABLE verdicts)
    corrected_match = re.search(
        r"CORRECTED SOLUTION:\s*\n(.*?)(?:\nEND CORRECTED SOLUTION|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    corrected_solution = corrected_match.group(1).strip() if corrected_match else None

    return VerificationResult(
        verdict=verdict,
        critique=critique,
        confidence=confidence,
        issues=issues,
        reason=reason,
        section_confidences=section_confidences,
        atom_confidences=atom_confidences,
        corrected_solution=corrected_solution,
    )


def verify(
    client,
    problem: str,
    solution: Solution,
    config: AgentConfig,
    *,
    system_prompt: str | None = None,
    user_template: str | None = None,
    extra_system: str | None = None,
    ledger: TokenLedger | None = None,
    context_limit: int = 200_000,
    context_threshold: float = 0.8,
) -> VerificationResult:
    """Independently verify a candidate solution.

    CRITICAL: The verifier receives ONLY the final solution text — never the
    generator's intermediate reasoning or thinking traces. This decoupling
    is the key architectural insight from DeepMind's Aletheia.

    Args:
        client: Anthropic client instance.
        problem: Original problem statement.
        solution: The candidate solution to verify.
        config: Agent configuration.
        system_prompt: Override the default verifier system prompt.
        user_template: Override the default verifier user template.

    Returns:
        A VerificationResult with verdict, critique, and issues.
    """
    template = user_template if user_template is not None else VERIFIER_USER
    user_msg = _safe_format(
        template,
        problem=problem,
        solution=solution.solution_text,
    )

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    system = system_prompt if system_prompt is not None else VERIFIER_SYSTEM
    if extra_system is not None:
        system = system + extra_system

    logger.info("Verifier: evaluating solution from iteration %d", solution.iteration)

    text = _call_model(
        client,
        system=system,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_verifier,
        tools=tools,
        ledger=ledger,
        context_limit=context_limit,
        context_threshold=context_threshold,
    )

    result = _parse_verification(text)

    logger.info(
        "Verifier: verdict=%s confidence=%.0f%% issues=%d",
        result.verdict.value,
        result.confidence * 100,
        len(result.issues),
    )

    return result


# ---------------------------------------------------------------------------
# Reviser
# ---------------------------------------------------------------------------


def _parse_revision(text: str, revision_number: int, critique: str) -> Revision:
    """Parse reviser output into a Revision object."""
    # Extract changes summary
    changes_match = re.search(r"CHANGES MADE:\s*\n(.*?)(?=\nREVISED SOLUTION:|\Z)", text, re.DOTALL)
    changes = changes_match.group(1).strip() if changes_match else "See revised solution"

    # Extract revised solution
    revised_match = re.search(r"REVISED SOLUTION:\s*\n(.*)", text, re.DOTALL)
    revised = revised_match.group(1).strip() if revised_match else text

    return Revision(
        revised_solution=revised,
        changes_made=changes,
        revision_number=revision_number,
        based_on_critique=critique,
    )


def revise(
    client,
    problem: str,
    solution: Solution,
    verification: VerificationResult,
    config: AgentConfig,
    revision_number: int,
    *,
    system_prompt: str | None = None,
    user_template: str | None = None,
    critique_addendum: str | None = None,
    atom_context: str | None = None,
    ledger: TokenLedger | None = None,
    context_limit: int = 200_000,
    context_threshold: float = 0.8,
) -> Solution:
    """Revise a solution based on verifier feedback.

    Args:
        client: Anthropic client instance.
        problem: Original problem statement.
        solution: The solution to revise.
        verification: The verifier's critique and issues.
        config: Agent configuration.
        revision_number: Which revision attempt this is.
        system_prompt: Override the default reviser system prompt.
        user_template: Override the default reviser user template.
        critique_addendum: Optional targeted revision strategy text (from error
            taxonomy), appended to the critique in the user message.
        atom_context: Optional atom stability advisory text (from
            _build_atom_context), appended after critique_addendum.

    Returns:
        A new Solution containing the revised answer.
    """
    issues_text = "\n".join(f"- {issue}" for issue in verification.issues) or "See critique above."

    critique_text = verification.critique + (critique_addendum or "")

    template = user_template if user_template is not None else REVISER_USER
    user_msg = _safe_format(
        template,
        problem=problem,
        solution=solution.solution_text,
        critique=critique_text,
        issues=issues_text,
    )

    # Add low-confidence section targeting if available
    low_conf_sections = [sc for sc in verification.section_confidences if sc.confidence < 0.70]
    if low_conf_sections:
        sections_text = "\n".join(
            f"- {sc.section}: {sc.confidence:.2f}" + (f" ({sc.note})" if sc.note else "")
            for sc in low_conf_sections
        )
        user_msg += f"\n\n## Low-confidence sections (focus revision here):\n{sections_text}"

    if atom_context:
        user_msg += f"\n\n{atom_context}"

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    system = system_prompt if system_prompt is not None else REVISER_SYSTEM

    logger.info(
        "Reviser: revision %d based on %s verdict",
        revision_number,
        verification.verdict.value,
    )

    text = _call_model(
        client,
        system=system,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_reviser,
        tools=tools,
        ledger=ledger,
        context_limit=context_limit,
        context_threshold=context_threshold,
    )

    revision = _parse_revision(text, revision_number, verification.critique)

    # Return as a new Solution
    return Solution(
        problem=problem,
        solution_text=revision.revised_solution,
        iteration=solution.iteration,
    )
