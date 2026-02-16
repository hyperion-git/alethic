"""Tests for the Alethic math agent.

Tests the architecture components with mocked API calls to avoid
requiring actual Anthropic API access during CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic

from alethic.models import (
    AgentConfig,
    AgentResult,
    Solution,
    Verdict,
    VerificationResult,
)
from alethic.prompts import (
    BALANCED_GENERATOR_ADDENDUM,
    GENERATOR_SYSTEM,
    REVISER_SYSTEM,
    VERIFIER_SYSTEM,
)
from alethic.subagents import _extract_text, _parse_revision, _parse_verification
from alethic.tools import execute_python, extract_code_blocks, process_tool_calls

# ── Data model tests ──────────────────────────────────────────────────


class TestModels:
    def test_verdict_enum(self):
        assert Verdict.CORRECT.value == "correct"
        assert Verdict.MINOR_ISSUES.value == "minor_issues"
        assert Verdict.MAJOR_FLAW.value == "major_flaw"
        assert Verdict.UNSOLVED.value == "unsolved"

    def test_agent_config_defaults(self):
        config = AgentConfig()
        assert config.model == "claude-opus-4-6"
        assert config.max_iterations == 5
        assert config.max_revisions_per_cycle == 3
        assert config.enable_code_execution is True
        assert config.temperature_verifier < config.temperature_generator

    def test_solution_str(self):
        sol = Solution(problem="test", solution_text="answer", iteration=1)
        assert str(sol) == "answer"

    def test_verification_result_methods(self):
        correct = VerificationResult(
            verdict=Verdict.CORRECT, critique="Good", confidence=0.95
        )
        assert correct.is_acceptable()
        assert not correct.needs_revision()

        minor = VerificationResult(
            verdict=Verdict.MINOR_ISSUES, critique="Almost", confidence=0.7
        )
        assert not minor.is_acceptable()
        assert minor.needs_revision()

        major = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="Bad", confidence=0.2
        )
        assert not major.is_acceptable()
        assert major.needs_revision()

        unsolved = VerificationResult(
            verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0
        )
        assert not unsolved.is_acceptable()
        assert not unsolved.needs_revision()

    def test_correct_but_low_confidence_needs_revision(self):
        """CORRECT with confidence < 0.90 should trigger revision, not acceptance."""
        low_conf = VerificationResult(
            verdict=Verdict.CORRECT, critique="Looks right but unsure", confidence=0.75
        )
        assert not low_conf.is_acceptable()
        assert low_conf.needs_revision()

    def test_correct_at_threshold_is_acceptable(self):
        """CORRECT with confidence exactly 0.90 should be acceptable."""
        at_threshold = VerificationResult(
            verdict=Verdict.CORRECT, critique="Verified", confidence=0.90
        )
        assert at_threshold.is_acceptable()
        assert not at_threshold.needs_revision()

    def test_agent_result_solved(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
        )
        assert result.solved

    def test_agent_result_unsolved(self):
        result = AgentResult(
            problem="test",
            solution=None,
            verdict=Verdict.UNSOLVED,
            confidence=0.3,
            iterations_used=5,
            total_revisions=10,
            admitted_failure=True,
        )
        assert not result.solved
        assert "UNSOLVED" in str(result)


# ── Preset and threshold tests ────────────────────────────────────────


class TestPresets:
    def test_preset_from_preset_quick(self):
        config = AgentConfig.from_preset("quick")
        assert config.max_iterations == 2
        assert config.max_revisions_per_cycle == 1
        assert config.confidence_threshold == 0.85
        assert config.extended_thinking is False

    def test_preset_from_preset_thorough(self):
        config = AgentConfig.from_preset("thorough")
        assert config.max_iterations == 8
        assert config.max_revisions_per_cycle == 5
        assert config.confidence_threshold == 0.95
        assert config.extended_thinking is True
        assert config.thinking_budget == 15000
        assert config.max_tokens == 32768

    def test_preset_from_preset_with_overrides(self):
        config = AgentConfig.from_preset("quick", max_iterations=10)
        assert config.max_iterations == 10
        assert config.confidence_threshold == 0.85  # from preset

    def test_preset_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Unknown preset 'nonexistent'"):
            AgentConfig.from_preset("nonexistent")

    def test_config_confidence_threshold_field(self):
        config = AgentConfig()
        assert config.confidence_threshold == 0.90

        config2 = AgentConfig(confidence_threshold=0.80)
        assert config2.confidence_threshold == 0.80

    def test_custom_confidence_threshold(self):
        """is_acceptable and needs_revision respect custom threshold."""
        vr = VerificationResult(
            verdict=Verdict.CORRECT, critique="OK", confidence=0.88
        )
        # Default threshold (0.90): not acceptable
        assert not vr.is_acceptable()
        assert vr.needs_revision()
        # Custom threshold (0.85): acceptable
        assert vr.is_acceptable(0.85)
        assert not vr.needs_revision(0.85)

    def test_cli_preset_flag(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args(["--preset", "quick", "test problem"])
        config = _build_config(args)
        assert config.max_iterations == 2
        assert config.confidence_threshold == 0.85

    def test_cli_preset_with_override(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args(["--preset", "quick", "--iterations", "7", "test"])
        config = _build_config(args)
        assert config.max_iterations == 7  # explicit override
        assert config.confidence_threshold == 0.85  # from preset

    def test_cli_temperature_flags(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args([
            "--temperature-generator", "0.5",
            "--temperature-verifier", "0.1",
            "--temperature-reviser", "0.3",
            "test",
        ])
        config = _build_config(args)
        assert config.temperature_generator == 0.5
        assert config.temperature_verifier == 0.1
        assert config.temperature_reviser == 0.3


# ── Prompt scaffolding tests ──────────────────────────────────────────


class TestPrompts:
    def test_generator_system_has_code_instructions(self):
        assert "<code>" in GENERATOR_SYSTEM
        assert "CONCLUSION:" in GENERATOR_SYSTEM

    def test_verifier_system_is_decoupled(self):
        """Verifier prompt must NOT reference thinking traces or reasoning process."""
        assert "thinking" not in VERIFIER_SYSTEM.lower() or "intermediate thinking" not in VERIFIER_SYSTEM.lower()
        assert "You are independent" in VERIFIER_SYSTEM
        assert "VERDICT:" in VERIFIER_SYSTEM

    def test_verifier_system_has_all_verdicts(self):
        for verdict in ["correct", "minor_issues", "major_flaw", "unsolved"]:
            assert verdict in VERIFIER_SYSTEM

    def test_reviser_system_references_critique(self):
        assert "critique" in REVISER_SYSTEM.lower()
        assert "CHANGES MADE:" in REVISER_SYSTEM
        assert "REVISED SOLUTION:" in REVISER_SYSTEM

    def test_balanced_addendum_explores_counterexamples(self):
        assert "FALSE" in BALANCED_GENERATOR_ADDENDUM
        assert "counterexample" in BALANCED_GENERATOR_ADDENDUM.lower()


# ── Tool execution tests ──────────────────────────────────────────────


class TestTools:
    def test_execute_basic_python(self):
        result = execute_python("print(2 + 2)")
        assert "4" in result

    def test_execute_math(self):
        result = execute_python("import math; print(math.factorial(10))")
        assert "3628800" in result

    def test_execute_restricted_import(self):
        result = execute_python("import os")
        assert "ERROR" in result or "not allowed" in result

    def test_execute_timeout(self):
        result = execute_python("while True: pass", timeout_seconds=2)
        assert "TIMEOUT" in result or "ERROR" in result

    def test_extract_code_blocks_xml(self):
        text = "Here is code: <code>print(42)</code> and more text"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "print(42)" in blocks[0]

    def test_extract_code_blocks_fenced(self):
        text = "Here:\n```python\nprint(42)\n```\nDone"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "print(42)" in blocks[0]

    def test_extract_multiple_blocks(self):
        text = "<code>a = 1</code> and <code>b = 2</code>"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2


# ── Subagent parsing tests ────────────────────────────────────────────


class TestParsing:
    def test_parse_correct_verification(self):
        text = """\
VERDICT: correct
CONFIDENCE: 0.95

CRITIQUE:
The proof is rigorous and complete. All steps follow logically.

ISSUES:
None
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.CORRECT
        assert result.confidence == 0.95
        assert len(result.issues) == 0
        assert "rigorous" in result.critique

    def test_parse_major_flaw_verification(self):
        text = """\
VERDICT: major_flaw
CONFIDENCE: 0.2

CRITIQUE:
Step 3 contains a division by zero error. The denominator x-1 can be zero
when x=1, which was not excluded.

ISSUES:
- Division by zero in step 3 when x=1
- Missing domain restriction
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.MAJOR_FLAW
        assert result.confidence == 0.2
        assert len(result.issues) == 2
        assert "division by zero" in result.issues[0].lower()

    def test_parse_minor_issues_verification(self):
        text = """\
VERDICT: minor_issues
CONFIDENCE: 0.8

CRITIQUE:
The core argument is sound but step 2 skips a justification.

ISSUES:
- Step 2 needs justification for the inequality
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.MINOR_ISSUES
        assert result.confidence == 0.8
        assert len(result.issues) == 1

    def test_parse_unsolved_with_reason(self):
        text = """\
VERDICT: unsolved
CONFIDENCE: 0.1

CRITIQUE:
The problem asks to prove that every continuous function on [0,1] to [0,1]
has no fixed points. However, by Brouwer's fixed point theorem, every such
function must have at least one fixed point. The premise is false.

REASON: The premise is false. Brouwer's fixed point theorem guarantees that every continuous function f: [0,1] -> [0,1] has at least one fixed point.

ISSUES:
- Problem premise is false (contradicts Brouwer's fixed point theorem)
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.UNSOLVED
        assert result.confidence == 0.1
        assert "premise is false" in result.reason.lower()
        assert "Brouwer" in result.reason
        assert len(result.issues) == 1
        assert "premise" in result.issues[0].lower()

    def test_parse_correct_with_reason_na(self):
        """REASON: N/A should result in empty/N/A reason for correct verdicts."""
        text = """\
VERDICT: correct
CONFIDENCE: 0.95

CRITIQUE:
All steps verified.

REASON: N/A

ISSUES:
None
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.CORRECT
        assert result.confidence == 0.95
        assert result.reason == "N/A"
        assert len(result.issues) == 0

    def test_confidence_clamping(self):
        """Confidence values above 1.0 should be clamped to 1.0."""
        text = "VERDICT: correct\nCONFIDENCE: 1.5\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 1.0

    def test_confidence_missing_defaults(self):
        """Missing confidence should default to 0.5."""
        text = "VERDICT: correct\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 0.5

    def test_parse_revision(self):
        text = """\
CHANGES MADE:
Fixed the division by zero error by adding a domain restriction.

REVISED SOLUTION:
Let x be a real number with x != 1. Then...

CONCLUSION: The theorem holds for all x != 1.
"""
        revision = _parse_revision(text, revision_number=1, critique="div by zero")
        assert "x != 1" in revision.revised_solution
        assert "division by zero" in revision.changes_made.lower() or "domain" in revision.changes_made.lower()
        assert revision.revision_number == 1


# ── CLI tests ─────────────────────────────────────────────────────────


class TestCLI:
    def test_cli_parser_help(self):
        from alethic.cli import build_parser
        parser = build_parser()
        # Just verify it builds without error
        assert parser.prog == "alethic"

    def test_cli_parser_args(self):
        from alethic.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--iterations", "3", "--no-code", "test problem"])
        assert args.iterations == 3
        assert args.no_code is True
        assert args.problem == "test problem"

    def test_cli_json_flag(self):
        from alethic.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--json", "test"])
        assert args.json_output is True


# ── Integration test with mocked API ──────────────────────────────────


class TestAgentIntegration:
    """Test the full orchestrator loop with mocked Claude API calls."""

    def _mock_response(self, text: str):
        """Create a mock Anthropic response object."""
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = text
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        return mock_resp

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_solve_correct_on_first_try(self, mock_tools):
        """Agent should return immediately if verifier says CORRECT."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            enable_code_execution=False,
            verbose=False,
        )

        solution_response = self._mock_response("The answer is 42.\n\nCONCLUSION: 42")
        verification_response = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nPerfect.\n\nISSUES:\nNone"
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            solution_response,
            verification_response,
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("What is 6 * 7?")

        assert result.solved
        assert result.verdict == Verdict.CORRECT
        assert result.iterations_used == 1
        assert result.total_revisions == 0
        assert result.confidence == 0.95

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_solve_with_revision(self, mock_tools):
        """Agent should revise when verifier finds minor issues."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            max_revisions_per_cycle=2,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iteration 1: generate
            self._mock_response("First attempt with a bug"),
            # Iteration 1: verify → minor issues
            self._mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.6\n\n"
                "CRITIQUE:\nSmall error in step 2.\n\nISSUES:\n- Sign error"
            ),
            # Iteration 1: revise
            self._mock_response(
                "CHANGES MADE:\nFixed sign.\n\nREVISED SOLUTION:\nCorrected version"
            ),
            # Iteration 1: re-verify → correct
            self._mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.9\n\n"
                "CRITIQUE:\nNow correct.\n\nISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.total_revisions == 1
        assert result.iterations_used == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_admit_failure(self, mock_tools):
        """Agent should admit failure after exhausting iterations."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
        )

        bad_verify = self._mock_response(
            "VERDICT: major_flaw\nCONFIDENCE: 0.1\n\n"
            "CRITIQUE:\nFundamentally wrong.\n\nISSUES:\n- Logic error"
        )
        bad_revision_verify = self._mock_response(
            "VERDICT: major_flaw\nCONFIDENCE: 0.15\n\n"
            "CRITIQUE:\nStill wrong.\n\nISSUES:\n- Same logic error"
        )

        responses = [
            # Iteration 1: generate
            self._mock_response("Wrong attempt 1"),
            bad_verify,
            # Iteration 1: revise
            self._mock_response("CHANGES MADE:\nTried fix\n\nREVISED SOLUTION:\nStill wrong"),
            bad_revision_verify,
            # Iteration 2: generate (fresh)
            self._mock_response("Wrong attempt 2"),
            bad_verify,
            # Iteration 2: revise
            self._mock_response("CHANGES MADE:\nTried again\n\nREVISED SOLUTION:\nStill wrong"),
            bad_revision_verify,
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("impossible problem")

        assert not result.solved
        assert result.admitted_failure
        assert result.verdict == Verdict.UNSOLVED
        assert result.iterations_used == 2

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_solve_api_error_resilience(self, mock_tools):
        """Agent should survive an API error on one iteration and continue."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            enable_code_execution=False,
            verbose=False,
        )

        # Iteration 1: API error during generate
        # Iteration 2: succeeds normally
        solution_response = self._mock_response("The answer.\n\nCONCLUSION: answer")
        verification_response = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            anthropic.APIError(
                message="rate limit",
                request=MagicMock(),
                body=None,
            ),
            solution_response,
            verification_response,
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.iterations_used == 2
        # History should contain an error entry from iteration 1
        error_entries = [h for h in result.history if h.get("phase") == "error"]
        assert len(error_entries) == 1
        assert error_entries[0]["iteration"] == 1


# ── Confidence parsing edge-case tests ───────────────────────────────


class TestConfidenceParsing:
    def test_parse_verification_malformed_confidence(self):
        """Malformed confidence like '1.2.3' should not crash, defaults to 0.5."""
        text = "VERDICT: correct\nCONFIDENCE: 1.2.3\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 0.5
        assert result.verdict == Verdict.CORRECT

    def test_parse_verification_percentage_confidence(self):
        """Percentage value like '95' should be normalized to 0.95."""
        text = "VERDICT: correct\nCONFIDENCE: 95\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 0.95

    def test_parse_verification_normal_confidence(self):
        """Normal value like '0.92' should stay as-is."""
        text = "VERDICT: correct\nCONFIDENCE: 0.92\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 0.92

    def test_parse_verification_percentage_100(self):
        """Value '100' should normalize to 1.0."""
        text = "VERDICT: correct\nCONFIDENCE: 100\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert result.confidence == 1.0

    def test_parse_verification_zero_confidence(self):
        """Value '0' should stay as 0.0."""
        text = "VERDICT: major_flaw\nCONFIDENCE: 0\n\nCRITIQUE:\nBad\n\nISSUES:\n- Wrong"
        result = _parse_verification(text)
        assert result.confidence == 0.0


# ── M1: _extract_text helper tests ──────────────────────────────────


class TestExtractText:
    def test_extract_single_text_block(self):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello world"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        assert _extract_text(mock_resp) == "Hello world"

    def test_extract_multiple_text_blocks(self):
        blocks = []
        for text in ["Part 1", "Part 2"]:
            b = MagicMock()
            b.type = "text"
            b.text = text
            blocks.append(b)
        mock_resp = MagicMock()
        mock_resp.content = blocks
        assert _extract_text(mock_resp) == "Part 1\nPart 2"

    def test_extract_no_text_blocks(self):
        """Empty response should return '[No response generated]'."""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.text = None  # no text attr in practice, but hasattr guard covers
        del tool_block.text  # remove text attribute so hasattr returns False
        mock_resp = MagicMock()
        mock_resp.content = [tool_block]
        assert _extract_text(mock_resp) == "[No response generated]"

    def test_extract_empty_content(self):
        """Response with empty content list should return fallback."""
        mock_resp = MagicMock()
        mock_resp.content = []
        assert _extract_text(mock_resp) == "[No response generated]"

    def test_extract_skips_thinking_blocks(self):
        """Only 'text' type blocks should be extracted."""
        thinking = MagicMock()
        thinking.type = "thinking"
        thinking.text = "internal reasoning"
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Final answer"
        mock_resp = MagicMock()
        mock_resp.content = [thinking, text_block]
        assert _extract_text(mock_resp) == "Final answer"


# ── M2: Temperature override logging tests ──────────────────────────


class TestTemperatureOverrideLogging:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_logs_debug_when_temperature_overridden(self, mock_tools):
        """Extended thinking should log debug when overriding non-1 temperature."""
        from alethic.subagents import _call_model

        config = AgentConfig(
            extended_thinking=True,
            thinking_budget=10000,
            max_tokens=20000,
            enable_code_execution=False,
        )

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "result"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("alethic.subagents.logger") as mock_logger:
            _call_model(
                mock_client,
                system="test",
                user_message="test",
                config=config,
                temperature=0.5,
            )
            mock_logger.debug.assert_called_once()
            assert "temperature=0.5" in mock_logger.debug.call_args[0][0] % mock_logger.debug.call_args[0][1:]

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_no_log_when_temperature_is_one(self, mock_tools):
        """Extended thinking with temperature=1 should not log override."""
        from alethic.subagents import _call_model

        config = AgentConfig(
            extended_thinking=True,
            thinking_budget=10000,
            max_tokens=20000,
            enable_code_execution=False,
        )

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "result"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("alethic.subagents.logger") as mock_logger:
            _call_model(
                mock_client,
                system="test",
                user_message="test",
                config=config,
                temperature=1,
            )
            mock_logger.debug.assert_not_called()


# ── M5: Empty code tool call tests ──────────────────────────────────


class TestEmptyCodeToolCall:
    def test_empty_code_returns_error(self):
        """Tool call with empty code should return error, not success."""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "execute_python"
        mock_block.input = {"code": ""}
        mock_block.id = "tool_123"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        results = process_tool_calls(mock_resp)
        assert len(results) == 1
        assert "ERROR" in results[0]["result"]
        assert "Empty code" in results[0]["result"]

    def test_whitespace_code_returns_error(self):
        """Tool call with whitespace-only code should return error."""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "execute_python"
        mock_block.input = {"code": "   \n  "}
        mock_block.id = "tool_456"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        results = process_tool_calls(mock_resp)
        assert len(results) == 1
        assert "ERROR" in results[0]["result"]

    def test_valid_code_still_works(self):
        """Non-empty code should still execute normally."""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "execute_python"
        mock_block.input = {"code": "print(42)"}
        mock_block.id = "tool_789"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        results = process_tool_calls(mock_resp)
        assert len(results) == 1
        assert "42" in results[0]["result"]


# ── M6: Auto-bump max_tokens for thinking tests ────────────────────


class TestThinkingTokenBump:
    def test_quick_preset_with_thinking_bumps_tokens(self):
        """--preset quick --thinking should auto-bump max_tokens."""
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "quick", "--thinking", "test"])
        config = _build_config(args)
        assert config.extended_thinking is True
        # quick preset has max_tokens=16384, thinking_budget=10000
        # min_tokens = 10000 + 8192 = 18192
        assert config.max_tokens >= config.thinking_budget + 8192

    def test_explicit_max_tokens_not_overridden(self):
        """Explicit --max-tokens should not be auto-bumped."""
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "quick", "--thinking", "--max-tokens", "12000", "test"])
        config = _build_config(args)
        assert config.max_tokens == 12000  # user's explicit value preserved

    def test_thorough_preset_already_adequate(self):
        """--preset thorough already has enough tokens; no bump needed."""
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "thorough", "test"])
        config = _build_config(args)
        # thorough: max_tokens=32768, thinking_budget=15000 → min=23192
        assert config.max_tokens == 32768  # unchanged
