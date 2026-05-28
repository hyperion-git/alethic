"""The three Alethic subagents: Generator, Verifier, Reviser.

Each subagent wraps a Claude API call with role-specific prompt scaffolding.
The Verifier is deliberately isolated from the Generator's thinking traces —
this is the core architectural insight from DeepMind's Aletheia.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from typing import Any

import anthropic

_RATE_LIMIT_ERRORS: tuple[type, ...] = (anthropic.RateLimitError,)
try:
    import openai
    _RATE_LIMIT_ERRORS = (anthropic.RateLimitError, openai.RateLimitError)
except ImportError:
    pass

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
_COLLAPSE_BLANK_RE = re.compile(r"\n{3,}")

# --- Pre-compiled regexes for _parse_verification and friends ---
# Labels may be wrapped in markdown bold (**LABEL:**) by non-Claude models.
# _B matches 0-2 asterisks before/after the label. The colon is always required
# to avoid matching substrings (e.g. "minor_issues" contains "ISSUES").
_B = r"\*{0,2}"  # matches 0, 1, or 2 asterisks (plain, italic, or bold)
_ISSUES_BLOCK_RE = re.compile(
    rf"{_B}ISSUES:{_B}\s*\n(.*?)(?=\n{_B}REASON:{_B}|\n{_B}ATOM CONFIDENCES:{_B}|\n{_B}SECTION CONFIDENCES:{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_SEVERITY_TAG_RE = re.compile(r"\[(\w+)\]\s*(.*)")
_SECTION_CONF_BLOCK_RE = re.compile(
    rf"{_B}SECTION CONFIDENCES:{_B}\s*\n(.*?)(?=\n{_B}REASON:{_B}|\n{_B}ISSUES:{_B}|\n{_B}ATOM CONFIDENCES:{_B}|\n{_B}CORRECTED SOLUTION:{_B}|\n{_B}END CORRECTED SOLUTION{_B}|\n{_B}VERDICT:{_B}|\Z)",
    re.DOTALL,
)
_ATOM_GUARD_RE = re.compile(r"ATOM\[\d+\]")
_SECTION_CONF_LINE_RE = re.compile(r"(.+?):\s*([\d.]+)\s*(.*)")
_ATOM_CONF_BLOCK_RE = re.compile(
    rf"{_B}ATOM CONFIDENCES:{_B}\s*\n(.*?)(?=\n{_B}SECTION CONFIDENCES:{_B}|\n{_B}CORRECTED SOLUTION:{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_ATOM_CONF_LINE_RE = re.compile(r"^ATOM\[(\d+)\]:\s+([\d.]+)(?:\s+(.+))?$")
_VERDICT_RE = re.compile(
    rf"{_B}VERDICT:{_B}\s*(correct|minor_issues|fixable|major_flaw|unsolved)[\s.,;]*$",
    re.IGNORECASE | re.MULTILINE,
)
# Fallback: looser match for non-Claude models that deviate from format
_VERDICT_FUZZY_RE = re.compile(
    rf"{_B}VERDICT:{_B}\s*(.+?)$", re.IGNORECASE | re.MULTILINE
)
_CONFIDENCE_RE = re.compile(rf"{_B}CONFIDENCE:{_B}\s*([\d.]+)", re.IGNORECASE)
_CRITIQUE_RE = re.compile(
    rf"{_B}CRITIQUE:{_B}\s*\n(.*?)(?=\n{_B}REASON:{_B}|\n{_B}ISSUES:{_B}|\n{_B}SECTION CONFIDENCES:{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_REASON_RE = re.compile(rf"{_B}REASON:{_B}\s*(.*?)(?=\n{_B}ISSUES:{_B}|\Z)", re.DOTALL | re.IGNORECASE)
_CORRECTED_RE = re.compile(
    rf"{_B}CORRECTED SOLUTION:{_B}\s*\n(.*?)(?:\n{_B}END CORRECTED SOLUTION{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_CHANGES_RE = re.compile(rf"{_B}CHANGES MADE:{_B}\s*\n(.*?)(?=\n{_B}REVISED SOLUTION:{_B}|\Z)", re.DOTALL)
_REVISED_RE = re.compile(rf"{_B}REVISED SOLUTION:{_B}\s*\n(.*)", re.DOTALL)
# CHECKS PERFORMED block (patch #1 from PR #9): terminates on the next section
# heading. Order in prompt: ...REASON → CHECKS PERFORMED → ISSUES → ATOM/SECTION/CORRECTED.
_CHECKS_BLOCK_RE = re.compile(
    rf"{_B}CHECKS PERFORMED:{_B}\s*\n(.*?)(?=\n{_B}ISSUES:{_B}|\n{_B}ATOM CONFIDENCES:{_B}|\n{_B}SECTION CONFIDENCES:{_B}|\n{_B}CORRECTED SOLUTION:{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# Entry format: - [name | type=constraint|conjecture | outcome=PASS|FAIL|N/A] description
_CHECKS_ENTRY_RE = re.compile(
    r"\[\s*[^|\]]+?\s*\|\s*type\s*=\s*(constraint|conjecture)\s*\|\s*outcome\s*=\s*(PASS|FAIL|N/A)\s*\]",
    re.IGNORECASE,
)
_CHECKS_FLOOR_CONFIDENCE = 0.30
_CHECKS_FLOOR_MIN_CONSTRAINT_PASS = 3
# ISSUE TRIAGE block (patch #2 from PR #9): terminates on the next section
# heading. Order in reviser prompt: ISSUE TRIAGE -> CHANGES MADE -> REVISED SOLUTION.
_TRIAGE_BLOCK_RE = re.compile(
    rf"{_B}ISSUE TRIAGE:?{_B}\s*\n(.*?)(?=\n{_B}CHANGES MADE:{_B}|\n{_B}REVISED SOLUTION:{_B}|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# Entry verdict pattern: - [<issue text> | verdict=accept|decline|dismiss] reason
_TRIAGE_VERDICT_RE = re.compile(
    r"\|\s*verdict\s*=\s*(accept|decline|dismiss)\s*\]", re.IGNORECASE
)


def _parse_checks_performed(text: str) -> tuple[int, int, int]:
    """Parse the verifier's CHECKS PERFORMED block (patch #1 from PR #9).

    Returns (n_constraint_pass, n_constraint_fail, n_total_entries). Returns
    (0, 0, 0) if no block is present — which is the signal that the verifier
    either ignored the directive or had nothing to report. Per the prompt rule,
    that case must cap CONFIDENCE below 0.30; see _apply_checks_floor.
    """
    block_match = _CHECKS_BLOCK_RE.search(text)
    if not block_match:
        return (0, 0, 0)
    block = block_match.group(1)
    n_pass = n_fail = n_total = 0
    for m in _CHECKS_ENTRY_RE.finditer(block):
        n_total += 1
        type_ = m.group(1).lower()
        outcome = m.group(2).upper()
        if type_ == "constraint":
            if outcome == "PASS":
                n_pass += 1
            elif outcome == "FAIL":
                n_fail += 1
    return (n_pass, n_fail, n_total)


def _parse_triage_verdicts(text: str) -> dict[str, int]:
    """Parse the reviser's ISSUE TRIAGE block (patch #2 from PR #9).

    Returns counts keyed by verdict label: {"accept": N, "decline": N, "dismiss": N}.
    Returns {} if no ISSUE TRIAGE block is present. The caller can detect the
    all-declined pattern as: counts.get("accept", 0) == 0 and sum(counts.values()) > 0.
    """
    block_match = _TRIAGE_BLOCK_RE.search(text)
    if not block_match:
        return {}
    block = block_match.group(1)
    counts: dict[str, int] = {}
    for m in _TRIAGE_VERDICT_RE.finditer(block):
        verdict = m.group(1).lower()
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def _apply_checks_floor(result: VerificationResult, text: str) -> VerificationResult:
    """Enforce patch #1's CHECKS PERFORMED confidence floor.

    Rule from the verifier prompt: an empty or insufficient CHECKS PERFORMED
    block (fewer than 3 `type=constraint outcome=PASS` entries) means the
    verifier "checked nothing" and confidence MUST be below 0.30. We enforce
    this parser-side because non-Claude models observed silently violating the
    format (e.g., Kimi K2.6 emits prose Markdown headers instead of structured
    rows). When the block is absent or under-populated, this is the only
    backstop against a hallucinated high-confidence verdict.

    Returns either the original result (if floor not triggered) or a copy with
    confidence reduced to _CHECKS_FLOOR_CONFIDENCE.
    """
    n_pass, _n_fail, n_total = _parse_checks_performed(text)
    if n_pass < _CHECKS_FLOOR_MIN_CONSTRAINT_PASS and result.confidence > _CHECKS_FLOOR_CONFIDENCE:
        logger.warning(
            "CHECKS PERFORMED block has %d constraint PASS (< %d required, %d total entries) "
            "— flooring confidence at %.2f (was %.2f). The verifier prompt rule: "
            "an empty/insufficient block requires CONFIDENCE below %.2f.",
            n_pass, _CHECKS_FLOOR_MIN_CONSTRAINT_PASS, n_total,
            _CHECKS_FLOOR_CONFIDENCE, result.confidence, _CHECKS_FLOOR_CONFIDENCE,
        )
        return replace(result, confidence=_CHECKS_FLOOR_CONFIDENCE)
    return result


def _strip_sentinels(text: str) -> str:
    """Remove ALETHIC_LN_CHECK: lines from solution text.

    Sentinel lines are injected by the generator for the verifier's benefit.
    They must be stripped before presenting the corrected solution to the reviser
    to avoid polluting the revision context with verification metadata.
    """
    stripped = _SENTINEL_RE.sub("", text)
    # Collapse any double blank lines left by removed sentinels
    return _COLLAPSE_BLANK_RE.sub("\n\n", stripped)


def _safe_format(template: str, **kwargs: str) -> str:
    """Format template using single-pass regex to avoid cascade replacement bugs.

    Sequential str.replace() corrupts values that contain other placeholder names
    (e.g., problem text containing literal '{solution}' gets double-replaced).
    Single-pass re.sub() replaces only known keys and never re-processes replacements.
    """
    return _PLACEHOLDER_RE.sub(
        lambda m: kwargs.get(m.group(1), m.group(0)), template
    )


_SEVERITY_MAP: dict[str, IssueSeverity] = {s.name: s for s in IssueSeverity}


def _extract_text(response) -> str:
    """Extract concatenated text blocks from an Anthropic response."""
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    if not parts:
        block_types = [getattr(b, "type", "unknown") for b in response.content]
        logger.warning(
            "No text blocks in response (stop_reason=%s, blocks=%s, content_len=%d)",
            getattr(response, "stop_reason", "?"),
            block_types,
            len(response.content),
        )
    return "\n".join(parts) if parts else "[No response generated]"


_MAX_RETRIES = 5
_BASE_RETRY_DELAY = 5  # seconds; free-tier rate limits need longer backoff


def _do_create(client, kwargs: dict):
    """Single API call with automatic streaming fallback.

    SDK 0.79+ raises ValueError for non-streaming calls estimated to exceed
    10 minutes (based on max_tokens). Fall back to streaming transparently.
    """
    try:
        return client.messages.create(**kwargs)
    except ValueError as e:
        if "Streaming is required" not in str(e):
            raise
        # Long-running request: use streaming, collect full response
        logger.debug("Streaming fallback triggered for model=%s max_tokens=%s",
                      kwargs.get("model"), kwargs.get("max_tokens"))
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()


def _create_with_retry(client, kwargs: dict):
    """Call client.messages with exponential backoff on rate limits."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _do_create(client, kwargs)
        except _RATE_LIMIT_ERRORS:
            if attempt < _MAX_RETRIES:
                # Exponential backoff with jitter; free-tier needs longer waits
                import random
                delay = _BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 2)
                logger.warning(
                    "Rate limited (attempt %d/%d) — retrying in %.0fs",
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
        # Extended thinking requires temperature=1 and uses a budget_tokens param.
        # SDK 0.79+ emits a deprecation warning suggesting "adaptive" type, but
        # adaptive doesn't accept budget_tokens — we need explicit budget control.
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

    # Tool-use loop: keep calling until we get a final text response.
    # Accumulate text across ALL rounds — each round may produce text + tool_use
    # blocks, and we need to collect text from every round (not just the last).
    max_tool_rounds = 15
    accumulated_text: list[str] = []
    for _ in range(max_tool_rounds):
        response = _create_with_retry(client, kwargs)

        if ledger is not None:
            ledger.record(response.usage)

        # Collect text blocks from this round
        round_text = [
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ]
        accumulated_text.extend(round_text)

        # Check for tool use
        tool_results = process_tool_calls(response) if tools else []

        if not tool_results:
            # Check for truncation before returning
            if getattr(response, "stop_reason", None) == "max_tokens":
                raise TruncatedResponseError(
                    f"Response truncated (stop_reason=max_tokens) after "
                    f"{ledger.api_calls if ledger else '?'} calls"
                )
            # No tool calls — return all accumulated text
            return "\n".join(accumulated_text) if accumulated_text else "[No response generated]"

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

    # Exhausted tool rounds — return whatever text we accumulated
    if not accumulated_text:
        logger.warning("Exhausted %d tool rounds with no text output", max_tool_rounds)
    return "\n".join(accumulated_text) if accumulated_text else "[No response generated]"


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


_VERDICT_MAP: dict[str, Verdict] = {v.value: v for v in Verdict}


def _fuzzy_match_verdict(raw: str) -> str:
    """Fuzzy-match a non-standard verdict string to a known verdict value.

    Handles: trailing punctuation, partial matches, common synonyms.
    Returns a lowercase verdict string suitable for _VERDICT_MAP lookup.
    Conservative fallback: "major_flaw" (forces revision rather than false acceptance).
    """
    cleaned = raw.strip().strip(".,;:()").lower().replace(" ", "_")

    # Direct match after cleanup
    if cleaned in _VERDICT_MAP:
        return cleaned

    # Substring containment: check if any known verdict is contained in the raw string
    # Check longest first to avoid "correct" matching inside "minor_issues" context
    for candidate in ["minor_issues", "major_flaw", "unsolved", "fixable", "correct"]:
        if candidate in cleaned:
            return candidate

    # Common synonyms — conservative: only map to CORRECT for exact synonyms,
    # map ambiguous terms to MINOR_ISSUES or MAJOR_FLAW
    _SYNONYMS = {
        "mostly_correct": "minor_issues",
        "partially_correct": "minor_issues",
        "almost_correct": "minor_issues",
        "wrong": "major_flaw",
        "incorrect": "major_flaw",
        "invalid": "major_flaw",
        "fail": "major_flaw",
    }
    for pattern, mapped in _SYNONYMS.items():
        if pattern in cleaned:
            return mapped

    logger.warning("Fuzzy verdict match failed for %r — defaulting to major_flaw", raw)
    return "major_flaw"


def _parse_issues(text: str) -> list[Issue]:
    """Parse the ISSUES block from verifier output into Issue objects."""
    issues_match = _ISSUES_BLOCK_RE.search(text)
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
        severity_tag_match = _SEVERITY_TAG_RE.match(cleaned)
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
    match = _SECTION_CONF_BLOCK_RE.search(text)
    if not match:
        return []

    results = []
    for line in match.group(1).strip().split("\n"):
        cleaned = line.strip().lstrip("- ").strip()
        if not cleaned:
            continue
        # Skip atom confidence lines that bled past ATOM CONFIDENCES block
        if _ATOM_GUARD_RE.match(cleaned):
            continue
        sc_match = _SECTION_CONF_LINE_RE.match(cleaned)
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


def _parse_atom_confidences(text: str) -> list[AtomConfidence]:
    """Parse ATOM CONFIDENCES block from verifier output."""
    block_match = _ATOM_CONF_BLOCK_RE.search(text)
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
    verdict_match = _VERDICT_RE.search(text)
    if verdict_match:
        verdict_str = verdict_match.group(1).lower()
    else:
        # Fuzzy fallback: try looser regex then substring matching
        fuzzy_match = _VERDICT_FUZZY_RE.search(text)
        if fuzzy_match:
            verdict_str = _fuzzy_match_verdict(fuzzy_match.group(1))
            logger.info("Fuzzy verdict match: %r → %s", fuzzy_match.group(1).strip(), verdict_str)
        else:
            logger.warning("No VERDICT: line found — defaulting to major_flaw")
            verdict_str = "major_flaw"

    verdict = _VERDICT_MAP.get(verdict_str, Verdict.MAJOR_FLAW)

    conf_match = _CONFIDENCE_RE.search(text)
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

    critique_match = _CRITIQUE_RE.search(text)
    critique = critique_match.group(1).strip() if critique_match else text

    reason_match = _REASON_RE.search(text)
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

    corrected_match = _CORRECTED_RE.search(text)
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

    # Patch #1 (PR #9) parser-side enforcement: floor confidence at 0.30 when
    # the CHECKS PERFORMED block is absent or has <3 constraint PASS entries.
    # Opt-in via AgentConfig; default off on bare AgentConfig() for back-compat,
    # default on in all presets where modern prompts are used.
    if getattr(config, "enforce_checks_floor", False):
        result = _apply_checks_floor(result, text)

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
    changes_match = _CHANGES_RE.search(text)
    changes = changes_match.group(1).strip() if changes_match else "See revised solution"

    revised_match = _REVISED_RE.search(text)
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
    triage_counts = _parse_triage_verdicts(text)
    if triage_counts and triage_counts.get("accept", 0) == 0 and sum(triage_counts.values()) > 0:
        logger.info(
            "Reviser: all_declined revision (verdicts=%s) — solution returned likely unchanged",
            triage_counts,
        )

    # Return as a new Solution
    return Solution(
        problem=problem,
        solution_text=revision.revised_solution,
        iteration=solution.iteration,
        triage_summary=triage_counts or None,
    )
