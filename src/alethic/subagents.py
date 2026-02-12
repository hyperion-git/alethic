"""The three Alethic subagents: Generator, Verifier, Reviser.

Each subagent wraps a Claude API call with role-specific prompt scaffolding.
The Verifier is deliberately isolated from the Generator's thinking traces —
this is the core architectural insight from DeepMind's Aletheia.
"""

from __future__ import annotations

import logging
import re

from alethic.models import (
    AgentConfig,
    Revision,
    Solution,
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


def _call_model(
    client,
    *,
    system: str,
    user_message: str,
    config: AgentConfig,
    temperature: float,
    tools: list[dict] | None = None,
) -> str:
    """Make an API call to Claude, handling tool use loops.

    Returns the final text response after all tool calls have been resolved.
    """
    messages = [{"role": "user", "content": user_message}]
    kwargs = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    # Tool-use loop: keep calling until we get a final text response
    max_tool_rounds = 5
    for _ in range(max_tool_rounds):
        response = client.messages.create(**kwargs)

        # Check for tool use
        tool_results = process_tool_calls(response) if tools else []

        if not tool_results:
            # No tool calls — extract final text
            text_parts = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(text_parts)

        # Build tool result messages and continue the loop
        # First, add the assistant's response (with tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        # Then add tool results
        tool_result_content = []
        for tr in tool_results:
            tool_result_content.append({
                "type": "tool_result",
                "tool_use_id": tr["tool_use_id"],
                "content": tr["result"],
            })
        messages.append({"role": "user", "content": tool_result_content})
        kwargs["messages"] = messages

    # Exhausted tool rounds — return whatever text we have
    text_parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(text_parts) if text_parts else "[No response generated]"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate(
    client,
    problem: str,
    config: AgentConfig,
    iteration: int,
    balanced: bool = True,
) -> Solution:
    """Generate a candidate mathematical solution.

    Args:
        client: Anthropic client instance.
        problem: The mathematical problem statement.
        config: Agent configuration.
        iteration: Current iteration number (for logging).
        balanced: If True, append balanced prompting addendum to encourage
                  exploring counterexamples before proving.

    Returns:
        A Solution object containing the candidate.
    """
    system = GENERATOR_SYSTEM
    if balanced:
        system += BALANCED_GENERATOR_ADDENDUM

    user_msg = GENERATOR_USER.format(problem=problem)

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    logger.info("Generator: iteration %d — generating solution", iteration)

    text = _call_model(
        client,
        system=system,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_generator,
        tools=tools,
    )

    return Solution(
        problem=problem,
        solution_text=text,
        iteration=iteration,
    )


# ---------------------------------------------------------------------------
# Verifier — the decoupled component
# ---------------------------------------------------------------------------


def _parse_verification(text: str) -> VerificationResult:
    """Parse structured verifier output into a VerificationResult."""
    # Extract verdict
    verdict_match = re.search(
        r"VERDICT:\s*(correct|minor_issues|major_flaw|unsolved)",
        text,
        re.IGNORECASE,
    )
    verdict_str = verdict_match.group(1).lower() if verdict_match else "major_flaw"

    verdict_map = {
        "correct": Verdict.CORRECT,
        "minor_issues": Verdict.MINOR_ISSUES,
        "major_flaw": Verdict.MAJOR_FLAW,
        "unsolved": Verdict.UNSOLVED,
    }
    verdict = verdict_map.get(verdict_str, Verdict.MAJOR_FLAW)

    # Extract confidence
    conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", text)
    confidence = float(conf_match.group(1)) if conf_match else 0.5

    # Extract critique
    critique_match = re.search(
        r"CRITIQUE:\s*\n(.*?)(?=\nISSUES:|\Z)", text, re.DOTALL
    )
    critique = critique_match.group(1).strip() if critique_match else text

    # Extract issues
    issues_match = re.search(r"ISSUES:\s*\n(.*)", text, re.DOTALL)
    issues = []
    if issues_match:
        issues_text = issues_match.group(1).strip()
        if issues_text.lower() != "none":
            for line in issues_text.split("\n"):
                line = line.strip().lstrip("- ").strip()
                if line:
                    issues.append(line)

    return VerificationResult(
        verdict=verdict,
        critique=critique,
        confidence=confidence,
        issues=issues,
    )


def verify(
    client,
    problem: str,
    solution: Solution,
    config: AgentConfig,
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

    Returns:
        A VerificationResult with verdict, critique, and issues.
    """
    user_msg = VERIFIER_USER.format(
        problem=problem,
        solution=solution.solution_text,
    )

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    logger.info("Verifier: evaluating solution from iteration %d", solution.iteration)

    text = _call_model(
        client,
        system=VERIFIER_SYSTEM,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_verifier,
        tools=tools,
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
    changes_match = re.search(
        r"CHANGES MADE:\s*\n(.*?)(?=\nREVISED SOLUTION:|\Z)", text, re.DOTALL
    )
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
) -> Solution:
    """Revise a solution based on verifier feedback.

    Args:
        client: Anthropic client instance.
        problem: Original problem statement.
        solution: The solution to revise.
        verification: The verifier's critique and issues.
        config: Agent configuration.
        revision_number: Which revision attempt this is.

    Returns:
        A new Solution containing the revised answer.
    """
    issues_text = "\n".join(f"- {issue}" for issue in verification.issues)
    if not issues_text:
        issues_text = "See critique above."

    user_msg = REVISER_USER.format(
        problem=problem,
        solution=solution.solution_text,
        critique=verification.critique,
        issues=issues_text,
    )

    tools = [PYTHON_TOOL] if config.enable_code_execution else None

    logger.info(
        "Reviser: revision %d based on %s verdict",
        revision_number,
        verification.verdict.value,
    )

    text = _call_model(
        client,
        system=REVISER_SYSTEM,
        user_message=user_msg,
        config=config,
        temperature=config.temperature_reviser,
        tools=tools,
    )

    revision = _parse_revision(text, revision_number, verification.critique)

    # Return as a new Solution
    return Solution(
        problem=problem,
        solution_text=revision.revised_solution,
        iteration=solution.iteration,
    )
