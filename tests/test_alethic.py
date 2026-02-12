"""Tests for the Alethic math agent.

Tests the architecture components with mocked API calls to avoid
requiring actual Anthropic API access during CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
from alethic.subagents import _parse_revision, _parse_verification
from alethic.tools import execute_python, extract_code_blocks

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

    def test_verification_result_properties(self):
        correct = VerificationResult(
            verdict=Verdict.CORRECT, critique="Good", confidence=0.95
        )
        assert correct.is_acceptable
        assert not correct.needs_revision

        minor = VerificationResult(
            verdict=Verdict.MINOR_ISSUES, critique="Almost", confidence=0.7
        )
        assert not minor.is_acceptable
        assert minor.needs_revision

        major = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="Bad", confidence=0.2
        )
        assert not major.is_acceptable
        assert major.needs_revision

        unsolved = VerificationResult(
            verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0
        )
        assert not unsolved.is_acceptable
        assert not unsolved.needs_revision

    def test_correct_but_low_confidence_needs_revision(self):
        """CORRECT with confidence < 0.90 should trigger revision, not acceptance."""
        low_conf = VerificationResult(
            verdict=Verdict.CORRECT, critique="Looks right but unsure", confidence=0.75
        )
        assert not low_conf.is_acceptable
        assert low_conf.needs_revision

    def test_correct_at_threshold_is_acceptable(self):
        """CORRECT with confidence exactly 0.90 should be acceptable."""
        at_threshold = VerificationResult(
            verdict=Verdict.CORRECT, critique="Verified", confidence=0.90
        )
        assert at_threshold.is_acceptable
        assert not at_threshold.needs_revision

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
