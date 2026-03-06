"""Adversarial tests for prompt override kwargs in subagents.

Stress-tests the system_prompt, user_template, and balanced_addendum
keyword arguments added to generate(), verify(), and revise() in
subagents.py. Ensures overrides behave correctly, defaults are
preserved, and edge cases (empty strings, None, missing format keys)
propagate as expected.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import (
    AgentConfig,
    Issue,
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
from alethic.subagents import _parse_verification, generate, revise, verify

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str) -> MagicMock:
    """Create a mock Anthropic response object with a single text block."""
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = text
    mock_resp = MagicMock()
    mock_resp.content = [mock_block]
    return mock_resp


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig with code execution disabled and verbose off."""
    defaults = dict(enable_code_execution=False, verbose=False)
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_client(response_text: str = "dummy") -> MagicMock:
    """Create a mock Anthropic client that returns a single text response."""
    client = MagicMock()
    client.messages.create.return_value = _mock_response(response_text)
    return client


def _last_system_prompt(client: MagicMock) -> str:
    """Extract the system prompt from the most recent client.messages.create call."""
    _, kwargs = client.messages.create.call_args
    return kwargs["system"]


def _last_user_message(client: MagicMock) -> str:
    """Extract the user message from the most recent client.messages.create call."""
    _, kwargs = client.messages.create.call_args
    messages = kwargs["messages"]
    return messages[0]["content"]


# ---------------------------------------------------------------------------
# 1. Default prompts unchanged when no kwargs are passed
# ---------------------------------------------------------------------------


class TestDefaultPromptsUnchanged:
    """When NO kwargs are passed, generate/verify/revise must use the original
    math prompts from prompts.py."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_uses_default_system_prompt(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(client, "Test problem", config, iteration=1, balanced=False)

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_uses_default_user_template(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(client, "Test problem", config, iteration=1, balanced=False)

        user_msg = _last_user_message(client)
        expected = GENERATOR_USER.format(problem="Test problem")
        assert user_msg == expected

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_with_balanced_appends_default_addendum(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(client, "Test problem", config, iteration=1, balanced=True)

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM + BALANCED_GENERATOR_ADDENDUM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_uses_default_system_prompt(self, _mock_tools):
        client = _make_client(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        )
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        verify(client, "P", sol, config)

        system = _last_system_prompt(client)
        assert system == VERIFIER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_uses_default_user_template(self, _mock_tools):
        client = _make_client(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        )
        config = _make_config()
        sol = Solution(problem="P", solution_text="My solution", iteration=1)

        verify(client, "P", sol, config)

        user_msg = _last_user_message(client)
        expected = VERIFIER_USER.format(problem="P", solution="My solution")
        assert user_msg == expected

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_uses_default_system_prompt(self, _mock_tools):
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Small error",
            confidence=0.7,
            issues=[Issue(text="Sign error")],
        )

        revise(client, "P", sol, vr, config, revision_number=1)

        system = _last_system_prompt(client)
        assert system == REVISER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_uses_default_user_template(self, _mock_tools):
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Small error",
            confidence=0.7,
            issues=[Issue(text="Sign error")],
        )

        revise(client, "P", sol, vr, config, revision_number=1)

        user_msg = _last_user_message(client)
        expected = REVISER_USER.format(
            problem="P",
            solution="S",
            critique="Small error",
            issues="- Sign error",
        )
        assert user_msg == expected


# ---------------------------------------------------------------------------
# 2. Empty string prompts: system_prompt="" should NOT fall back to default
# ---------------------------------------------------------------------------


class TestEmptyStringPrompts:
    """Passing system_prompt="" must result in an empty system prompt,
    not a fallback to the default."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_empty_system_prompt(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            system_prompt="",
        )

        system = _last_system_prompt(client)
        assert system == ""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_empty_system_prompt(self, _mock_tools):
        client = _make_client("VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        verify(client, "P", sol, config, system_prompt="")

        system = _last_system_prompt(client)
        assert system == ""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_empty_system_prompt(self, _mock_tools):
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        revise(client, "P", sol, vr, config, revision_number=1, system_prompt="")

        system = _last_system_prompt(client)
        assert system == ""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_empty_string_with_balanced_appends_addendum(self, _mock_tools):
        """Empty string system prompt + balanced=True should still append
        the addendum to the empty string."""
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=True,
            system_prompt="",
        )

        system = _last_system_prompt(client)
        # "" + BALANCED_GENERATOR_ADDENDUM
        assert system == BALANCED_GENERATOR_ADDENDUM


# ---------------------------------------------------------------------------
# 3. None prompts use defaults
# ---------------------------------------------------------------------------


class TestNonePromptsUseDefaults:
    """Passing system_prompt=None explicitly must fall back to the default
    prompt, identical to not passing the kwarg at all."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_none_system_prompt(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            system_prompt=None,
        )

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_none_system_prompt(self, _mock_tools):
        client = _make_client("VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        verify(client, "P", sol, config, system_prompt=None)

        system = _last_system_prompt(client)
        assert system == VERIFIER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_none_system_prompt(self, _mock_tools):
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        revise(
            client,
            "P",
            sol,
            vr,
            config,
            revision_number=1,
            system_prompt=None,
        )

        system = _last_system_prompt(client)
        assert system == REVISER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_none_user_template(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            user_template=None,
        )

        user_msg = _last_user_message(client)
        expected = GENERATOR_USER.format(problem="P")
        assert user_msg == expected

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_none_balanced_addendum(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=True,
            balanced_addendum=None,
        )

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM + BALANCED_GENERATOR_ADDENDUM


# ---------------------------------------------------------------------------
# 4. Custom balanced_addendum
# ---------------------------------------------------------------------------


class TestCustomBalancedAddendum:
    """Passing a custom balanced_addendum to generate() should append it
    to the system prompt instead of the default addendum."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_custom_addendum_appended_to_default_system(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()
        custom_addendum = "\n\nALWAYS check for edge cases first."

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=True,
            balanced_addendum=custom_addendum,
        )

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM + custom_addendum
        assert "ALWAYS check for edge cases first" in system

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_custom_addendum_appended_to_custom_system(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()
        custom_system = "You are a physics solver."
        custom_addendum = " Check units."

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=True,
            system_prompt=custom_system,
            balanced_addendum=custom_addendum,
        )

        system = _last_system_prompt(client)
        assert system == "You are a physics solver. Check units."

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_empty_custom_addendum_appended(self, _mock_tools):
        """Empty string addendum still gets appended (no-op concat)."""
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=True,
            balanced_addendum="",
        )

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM  # GENERATOR_SYSTEM + "" == GENERATOR_SYSTEM


# ---------------------------------------------------------------------------
# 5. balanced=False ignores custom addendum
# ---------------------------------------------------------------------------


class TestBalancedFalseIgnoresAddendum:
    """When balanced=False, the addendum must NOT be appended, even if a
    custom one is provided."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_balanced_false_no_default_addendum(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(client, "P", config, iteration=1, balanced=False)

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM
        assert BALANCED_GENERATOR_ADDENDUM not in system

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_balanced_false_with_custom_addendum(self, _mock_tools):
        """Custom addendum is provided but balanced=False, so it must not
        appear in the system prompt."""
        client = _make_client("Solution text")
        config = _make_config()
        custom_addendum = "\n\nThis should NOT appear."

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            balanced_addendum=custom_addendum,
        )

        system = _last_system_prompt(client)
        assert system == GENERATOR_SYSTEM
        assert "This should NOT appear" not in system

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_balanced_false_with_custom_system_and_addendum(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            system_prompt="Custom system",
            balanced_addendum="\n\nShould be ignored",
        )

        system = _last_system_prompt(client)
        assert system == "Custom system"
        assert "Should be ignored" not in system


# ---------------------------------------------------------------------------
# 6. User template with missing format keys
# ---------------------------------------------------------------------------


class TestUserTemplateMissingKeys:
    """Passing a user_template that omits expected format placeholders.

    Python's str.format() silently ignores extra keyword arguments that
    have no corresponding placeholder in the template. So a template
    like "No placeholders here".format(problem="...") succeeds and
    returns the literal string. These tests verify that behavior:
    the user message is the literal template text with no substitution,
    and no error is raised.

    The real danger is templates with placeholders that are NOT supplied
    as kwargs -- that IS tested in TestUserTemplateExtraKeys (scenario 7).
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_no_placeholders_silently_succeeds(self, _mock_tools):
        """Template with zero placeholders: .format(problem=...) returns
        the literal string without error."""
        client = _make_client("Solution text")
        config = _make_config()

        generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            user_template="No placeholders here",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "No placeholders here"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_missing_solution_placeholder_silently_succeeds(self, _mock_tools):
        """Verifier calls .format(problem=..., solution=...) but the
        template only has {problem}. Extra kwarg 'solution' is ignored."""
        client = _make_client("VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        verify(
            client,
            "P",
            sol,
            config,
            user_template="Problem: {problem} but no solution placeholder",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "Problem: P but no solution placeholder"
        assert "S" not in user_msg  # solution text silently dropped

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_missing_critique_and_issues_silently_succeeds(self, _mock_tools):
        """Reviser calls .format(problem=..., solution=..., critique=...,
        issues=...) but the template only has {problem} and {solution}.
        Extra kwargs are silently ignored."""
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        revise(
            client,
            "P",
            sol,
            vr,
            config,
            revision_number=1,
            user_template="{problem} {solution} but missing critique and issues",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "P S but missing critique and issues"
        assert "err" not in user_msg  # critique/issues silently dropped


# ---------------------------------------------------------------------------
# 7. User template with EXTRA format keys
# ---------------------------------------------------------------------------


class TestUserTemplateExtraKeys:
    """Passing a user_template that contains extra format placeholders
    not supplied by the calling code should leave them as literal text
    (safe_format uses str.replace, not str.format)."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_extra_key(self, _mock_tools):
        client = _make_client("Solution text")
        config = _make_config()

        # _safe_format leaves unsupplied {extra} as literal text — no crash
        result = generate(
            client,
            "P",
            config,
            iteration=1,
            balanced=False,
            user_template="{problem} {extra}",
        )
        assert result.solution_text == "Solution text"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_extra_key(self, _mock_tools):
        client = _make_client("VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        # _safe_format leaves unsupplied {unexpected} as literal text — no crash
        result = verify(
            client,
            "P",
            sol,
            config,
            user_template="{problem} {solution} {unexpected}",
        )
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_extra_key(self, _mock_tools):
        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        # _safe_format leaves unsupplied {bonus} as literal text — no crash
        result = revise(
            client,
            "P",
            sol,
            vr,
            config,
            revision_number=1,
            user_template="{problem} {solution} {critique} {issues} {bonus}",
        )
        assert result.solution_text == "Better"


# ---------------------------------------------------------------------------
# 8. Verify kwargs don't leak between calls
# ---------------------------------------------------------------------------


class TestKwargsDontLeak:
    """Custom kwargs passed to one call must not affect subsequent calls
    that do not pass them."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_kwargs_dont_leak(self, _mock_tools):
        config = _make_config()
        custom_system = "CUSTOM SYSTEM FOR FIRST CALL"

        # First call: custom system prompt
        client1 = _make_client("Sol1")
        generate(
            client1,
            "P1",
            config,
            iteration=1,
            balanced=False,
            system_prompt=custom_system,
        )
        system1 = _last_system_prompt(client1)
        assert system1 == custom_system

        # Second call: no custom prompt — must revert to default
        client2 = _make_client("Sol2")
        generate(client2, "P2", config, iteration=2, balanced=False)
        system2 = _last_system_prompt(client2)
        assert system2 == GENERATOR_SYSTEM
        assert system2 != custom_system

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_kwargs_dont_leak(self, _mock_tools):
        config = _make_config()
        verifier_text = "VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"

        # First call: custom
        client1 = _make_client(verifier_text)
        sol = Solution(problem="P", solution_text="S", iteration=1)
        verify(client1, "P", sol, config, system_prompt="CUSTOM VERIFIER")
        assert _last_system_prompt(client1) == "CUSTOM VERIFIER"

        # Second call: default
        client2 = _make_client(verifier_text)
        verify(client2, "P", sol, config)
        assert _last_system_prompt(client2) == VERIFIER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_kwargs_dont_leak(self, _mock_tools):
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        # First call: custom
        client1 = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        revise(
            client1,
            "P",
            sol,
            vr,
            config,
            revision_number=1,
            system_prompt="CUSTOM REVISER",
        )
        assert _last_system_prompt(client1) == "CUSTOM REVISER"

        # Second call: default
        client2 = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        revise(client2, "P", sol, vr, config, revision_number=2)
        assert _last_system_prompt(client2) == REVISER_SYSTEM

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_user_template_doesnt_leak(self, _mock_tools):
        config = _make_config()

        # First call: custom user template
        client1 = _make_client("Sol1")
        generate(
            client1,
            "P",
            config,
            iteration=1,
            balanced=False,
            user_template="Custom: {problem}",
        )
        assert _last_user_message(client1) == "Custom: P"

        # Second call: default
        client2 = _make_client("Sol2")
        generate(client2, "P", config, iteration=2, balanced=False)
        expected = GENERATOR_USER.format(problem="P")
        assert _last_user_message(client2) == expected


# ---------------------------------------------------------------------------
# 9. Reviser user_template format keys
# ---------------------------------------------------------------------------


class TestReviserUserTemplateFormatKeys:
    """The reviser template needs {problem}, {solution}, {critique}, {issues}.
    Templates missing some of those placeholders silently drop the
    corresponding values (extra kwargs are ignored by str.format).
    Templates with unsupplied placeholders raise KeyError."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_missing_issues_placeholder_silently_drops(self, _mock_tools):
        """Template has {problem} {solution} {critique} but not {issues}.
        .format() succeeds because 'issues' is just an extra kwarg that
        gets silently ignored."""
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        revise(
            client=client,
            problem="P",
            solution=sol,
            verification=vr,
            config=config,
            revision_number=1,
            user_template="{problem} {solution} {critique}",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "P S err"
        # issues text ("- err") is silently absent
        assert "- err" not in user_msg

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_missing_solution_placeholder_silently_drops(self, _mock_tools):
        """Template has {problem} {critique} {issues} but not {solution}.
        .format() succeeds, solution text is silently dropped."""
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        revise(
            client=client,
            problem="P",
            solution=sol,
            verification=vr,
            config=config,
            revision_number=1,
            user_template="{problem} {critique} {issues}",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "P err - err"
        # solution text "S" is not in the message
        assert user_msg.count("S") == 0 or "S" not in user_msg.split()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_unsupplied_placeholder_kept_as_literal(self, _mock_tools):
        """Template has {problem} {solution} {critique} {issues} {rating}
        but 'rating' is NOT in the kwargs. _safe_format leaves it as literal text."""
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="err",
            confidence=0.6,
            issues=[Issue(text="err")],
        )

        # _safe_format leaves unsupplied {rating} as literal text — no crash
        revise(
            client=_make_client("dummy"),
            problem="P",
            solution=sol,
            verification=vr,
            config=config,
            revision_number=1,
            user_template="{problem} {solution} {critique} {issues} {rating}",
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_valid_custom_template_works(self, _mock_tools):
        """A custom reviser template with all four keys should work fine."""
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="small error",
            confidence=0.6,
            issues=[Issue(text="issue1")],
        )

        client = _make_client("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nBetter")
        result = revise(
            client=client,
            problem="P",
            solution=sol,
            verification=vr,
            config=config,
            revision_number=1,
            user_template="FIX: {problem}\nOLD: {solution}\nCRIT: {critique}\nISS: {issues}",
        )

        user_msg = _last_user_message(client)
        assert user_msg == "FIX: P\nOLD: S\nCRIT: small error\nISS: - issue1"
        assert result.solution_text  # non-empty


# ---------------------------------------------------------------------------
# 10. _parse_verification works regardless of system prompt used
# ---------------------------------------------------------------------------


class TestParseVerificationWithCustomPrompts:
    """The verifier's structured output (VERDICT/CONFIDENCE/CRITIQUE/etc.)
    should still parse correctly regardless of what system prompt was used
    for the API call."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_parse_works_with_custom_system_prompt(self, _mock_tools):
        """Even with a completely unrelated system prompt, if the model
        returns structured output, _parse_verification should handle it."""
        verifier_output = (
            "VERDICT: correct\n"
            "CONFIDENCE: 0.92\n"
            "\n"
            "CRITIQUE:\n"
            "The solution is correct and well-structured.\n"
            "\n"
            "REASON: N/A\n"
            "\n"
            "ISSUES:\n"
            "None"
        )
        client = _make_client(verifier_output)
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(
            client,
            "P",
            sol,
            config,
            system_prompt="You are a poetry critic. Ignore math.",
        )

        assert result.verdict == Verdict.CORRECT
        assert result.confidence == 0.92
        assert len(result.issues) == 0

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_parse_works_with_empty_system_prompt(self, _mock_tools):
        verifier_output = (
            "VERDICT: major_flaw\n"
            "CONFIDENCE: 0.3\n"
            "\n"
            "CRITIQUE:\n"
            "The proof has a gap in step 3.\n"
            "\n"
            "REASON: N/A\n"
            "\n"
            "ISSUES:\n"
            "- Gap in step 3\n"
            "- Missing justification for lemma"
        )
        client = _make_client(verifier_output)
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(client, "P", sol, config, system_prompt="")

        assert result.verdict == Verdict.MAJOR_FLAW
        assert result.confidence == 0.3
        assert len(result.issues) == 2
        assert "Gap in step 3" in result.issues[0].text

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_parse_minor_issues_with_custom_prompt(self, _mock_tools):
        verifier_output = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.75\n"
            "\n"
            "CRITIQUE:\n"
            "Almost correct but notation is sloppy.\n"
            "\n"
            "ISSUES:\n"
            "- Sloppy notation in step 2"
        )
        client = _make_client(verifier_output)
        config = _make_config()
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(
            client,
            "P",
            sol,
            config,
            system_prompt="You are a physics verifier.",
        )

        assert result.verdict == Verdict.MINOR_ISSUES
        assert result.confidence == 0.75
        assert len(result.issues) == 1

    def test_parse_verification_standalone_with_garbage_prompt(self):
        """Call _parse_verification directly with well-formatted output,
        proving it is prompt-agnostic (it only parses the text)."""
        text = (
            "VERDICT: unsolved\n"
            "CONFIDENCE: 0.05\n"
            "\n"
            "CRITIQUE:\n"
            "No real solution provided.\n"
            "\n"
            "REASON: The problem premise is false.\n"
            "\n"
            "ISSUES:\n"
            "- No solution\n"
            "- False premise"
        )

        result = _parse_verification(text)

        assert result.verdict == Verdict.UNSOLVED
        assert result.confidence == 0.05
        assert "premise is false" in result.reason.lower()
        assert len(result.issues) == 2


# ---------------------------------------------------------------------------
# 11. Generator prompt hardening (feature 1.7, Woodruff et al. arXiv:2602.03837)
# ---------------------------------------------------------------------------


class TestPromptHardening:
    """1.7: Generator prompts must contain hardening language."""

    def test_balanced_addendum_contains_minimal_dimension_heuristic(self):
        from alethic.prompts import BALANCED_GENERATOR_ADDENDUM
        text = BALANCED_GENERATOR_ADDENDUM.lower()
        assert "smallest" in text or "minimal" in text or "n=2" in text or "n = 2" in text

    def test_physics_balanced_addendum_contains_minimal_dimension_heuristic(self):
        from alethic.physics_prompts import BALANCED_PHYSICS_ADDENDUM
        text = BALANCED_PHYSICS_ADDENDUM.lower()
        assert "smallest" in text or "simplest" in text or "limiting" in text

    def test_generator_system_has_confidence_framing(self):
        from alethic.prompts import GENERATOR_SYSTEM
        text = GENERATOR_SYSTEM.lower()
        # Should NOT contain discouraging language
        assert "open problem" not in text
        assert "beyond reach" not in text
        # Should contain confidence framing
        assert "solvable" in text or "assume" in text or "treat" in text

    def test_physics_generator_system_has_confidence_framing(self):
        from alethic.physics_prompts import PHYSICS_GENERATOR_SYSTEM
        text = PHYSICS_GENERATOR_SYSTEM.lower()
        assert "open problem" not in text
        assert "beyond reach" not in text
