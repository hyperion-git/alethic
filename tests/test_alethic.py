"""Tests for the Alethic math agent.

Tests the architecture components with mocked API calls to avoid
requiring actual Anthropic API access during CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

from alethic.models import (
    AgentConfig,
    AgentResult,
    ConsensusIssue,
    ConsensusResult,
    EventType,
    IssueSeverity,
    Solution,
    Verdict,
    VerificationResult,
    VerifierConfig,
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
        assert Verdict.FIXABLE.value == "fixable"
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
        correct = VerificationResult(verdict=Verdict.CORRECT, critique="Good", confidence=0.95)
        assert correct.is_acceptable()
        assert not correct.needs_revision()

        minor = VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="Almost", confidence=0.7)
        assert not minor.is_acceptable()
        assert minor.needs_revision()

        major = VerificationResult(verdict=Verdict.MAJOR_FLAW, critique="Bad", confidence=0.2)
        assert not major.is_acceptable()
        assert major.needs_revision()

        unsolved = VerificationResult(verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0)
        assert not unsolved.is_acceptable()
        assert not unsolved.needs_revision()

    def test_fixable_verdict_needs_revision(self):
        fixable = VerificationResult(verdict=Verdict.FIXABLE, critique="Sign error", confidence=0.7)
        assert not fixable.is_acceptable()
        assert fixable.needs_revision()

    def test_fixable_has_correction_true(self):
        fixable = VerificationResult(
            verdict=Verdict.FIXABLE,
            critique="Sign error",
            confidence=0.7,
            corrected_solution="Fixed version here",
        )
        assert fixable.has_correction

    def test_fixable_has_correction_false_no_text(self):
        fixable = VerificationResult(
            verdict=Verdict.FIXABLE,
            critique="Sign error",
            confidence=0.7,
        )
        assert not fixable.has_correction

    def test_has_correction_false_for_non_fixable(self):
        minor = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Gap",
            confidence=0.8,
            corrected_solution="Some text",
        )
        assert not minor.has_correction

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


# ── VerifierConfig and ConsensusResult model tests ───────────────────


class TestVerifierModels:
    def test_verifier_config_defaults(self):
        config = VerifierConfig()
        assert config.model == "claude-opus-4-6"
        assert config.num_verifiers == 3
        assert config.tool_guidance == frozenset({"sympy", "numpy", "scipy", "matplotlib"})
        assert config.domain is None

    def test_verifier_config_presets(self):
        quick = VerifierConfig.from_preset("quick")
        assert quick.num_verifiers == 2
        thorough = VerifierConfig.from_preset("thorough")
        assert thorough.num_verifiers == 5
        assert thorough.extended_thinking is True
        extreme = VerifierConfig.from_preset("extreme")
        assert extreme.num_verifiers == 7

    def test_verifier_config_preset_override(self):
        config = VerifierConfig.from_preset("quick", num_verifiers=4)
        assert config.num_verifiers == 4

    def test_verifier_config_unknown_preset(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            VerifierConfig.from_preset("nonexistent")

    def test_verifier_config_validation(self):
        with pytest.raises(ValueError, match="num_verifiers must be >= 1"):
            VerifierConfig(num_verifiers=0)

    def test_consensus_result_basics(self):
        result = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.91,
            confidence_range=(0.85, 0.95),
            critique="Looks good",
            issues=[],
            individual_results=[],
            domain_detected="math",
            num_verifiers=3,
            elapsed_seconds=12.5,
        )
        assert result.consensus_ratio == "0/0"  # no individual results
        assert result.verdict == Verdict.CORRECT

    def test_consensus_result_with_individuals(self):
        vr1 = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.95)
        vr2 = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90)
        vr3 = VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.85)
        result = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.90,
            confidence_range=(0.85, 0.95),
            critique="Synthesized",
            issues=[ConsensusIssue(text="Minor gap", severity=IssueSeverity.MINOR, flagged_by=1)],
            individual_results=[vr1, vr2, vr3],
            domain_detected="physics",
            num_verifiers=3,
            elapsed_seconds=30.0,
        )
        assert result.consensus_ratio == "2/3"

    def test_consensus_issue(self):
        issue = ConsensusIssue(
            text="Sign error in step 3", severity=IssueSeverity.MAJOR, flagged_by=2
        )
        assert issue.flagged_by == 2
        assert issue.severity == IssueSeverity.MAJOR


# ── Preset and threshold tests ────────────────────────────────────────


class TestPresets:
    def test_preset_from_preset_quick(self):
        config = AgentConfig.from_preset("quick")
        assert config.max_iterations == 2
        assert config.max_revisions_per_cycle == 1
        assert config.confidence_threshold == 0.85
        assert config.extended_thinking is False
        assert config.best_of_n == 1

    def test_preset_from_preset_thorough(self):
        config = AgentConfig.from_preset("thorough")
        assert config.max_iterations == 8
        assert config.max_revisions_per_cycle == 5
        assert config.confidence_threshold == 0.95
        assert config.extended_thinking is True
        assert config.thinking_budget == 15000
        assert config.max_tokens == 32768
        assert config.best_of_n == 3

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
        vr = VerificationResult(verdict=Verdict.CORRECT, critique="OK", confidence=0.88)
        # Default threshold (0.90): not acceptable
        assert not vr.is_acceptable()
        assert vr.needs_revision()
        # Custom threshold (0.85): acceptable
        assert vr.is_acceptable(0.85)
        assert not vr.needs_revision(0.85)

    def test_preset_stall_reset_values(self):
        quick = AgentConfig.from_preset("quick")
        assert quick.stall_reset is False
        assert quick.reset_n_boost == 0

        default = AgentConfig.from_preset("default")
        assert default.stall_reset is True
        assert default.stall_window == 2
        assert default.stall_epsilon == 0.03
        assert default.reset_n_boost == 1

        thorough = AgentConfig.from_preset("thorough")
        assert thorough.stall_window == 3
        assert thorough.stall_epsilon == 0.02
        assert thorough.reset_n_boost == 1

        extreme = AgentConfig.from_preset("extreme")
        assert extreme.stall_window == 3
        assert extreme.stall_epsilon == 0.02
        assert extreme.reset_n_boost == 2

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
        args = parser.parse_args(
            [
                "--temperature-generator",
                "0.5",
                "--temperature-verifier",
                "0.1",
                "--temperature-reviser",
                "0.3",
                "test",
            ]
        )
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
        assert (
            "thinking" not in VERIFIER_SYSTEM.lower()
            and "intermediate thinking" not in VERIFIER_SYSTEM.lower()
        )
        assert "You are independent" in VERIFIER_SYSTEM
        assert "VERDICT:" in VERIFIER_SYSTEM

    def test_verifier_system_has_all_verdicts(self):
        for verdict in ["correct", "minor_issues", "fixable", "major_flaw", "unsolved"]:
            assert verdict in VERIFIER_SYSTEM

    def test_reviser_system_references_critique(self):
        assert "critique" in REVISER_SYSTEM.lower()
        assert "CHANGES MADE:" in REVISER_SYSTEM
        assert "REVISED SOLUTION:" in REVISER_SYSTEM

    def test_balanced_addendum_explores_counterexamples(self):
        assert "FALSE" in BALANCED_GENERATOR_ADDENDUM
        assert "counterexample" in BALANCED_GENERATOR_ADDENDUM.lower()


class TestCheckPrompts:
    def test_check_prompts_exist(self):
        from alethic.check_prompts import CHECKER_SYSTEM, CHECKER_USER

        assert (
            "internally valid" in CHECKER_SYSTEM.lower()
            or "proof auditor" in CHECKER_SYSTEM.lower()
        )
        assert "{solution}" in CHECKER_USER

    def test_check_tool_guidance_has_all_four(self):
        from alethic.check_prompts import CHECK_TOOL_GUIDANCE

        assert "sympy" in CHECK_TOOL_GUIDANCE
        assert "numpy" in CHECK_TOOL_GUIDANCE
        assert "scipy" in CHECK_TOOL_GUIDANCE
        assert "matplotlib" in CHECK_TOOL_GUIDANCE

    def test_check_tool_guidance_has_verifier_keys(self):
        from alethic.check_prompts import CHECK_TOOL_GUIDANCE

        for tool_name, entries in CHECK_TOOL_GUIDANCE.items():
            assert "verifier" in entries, f"{tool_name} missing 'verifier' key"

    def test_checker_section_confidences_matches_parser(self):
        """CHECKER_SYSTEM must use 'SECTION CONFIDENCES:' (space) to match the parser regex."""
        from alethic.check_prompts import CHECKER_SYSTEM

        assert "SECTION CONFIDENCES:" in CHECKER_SYSTEM
        assert "SECTION_CONFIDENCES:" not in CHECKER_SYSTEM


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

    def test_matplotlib_allowed_in_sandbox(self):
        """matplotlib should be importable in the sandbox."""
        result = execute_python("import matplotlib; print(matplotlib.__name__)")
        assert "matplotlib" in result
        assert "not allowed" not in result

    def test_matplotlib_agg_backend(self):
        """matplotlib.use('Agg') should work in sandbox."""
        result = execute_python(
            "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nprint('ok')"
        )
        assert "ok" in result

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
        assert "division by zero" in result.issues[0].text.lower()

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
        assert "premise" in result.issues[0].text.lower()

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

    def test_parse_fixable_with_correction(self):
        text = """\
VERDICT: fixable
CONFIDENCE: 0.75

CRITIQUE:
Sign error in step 3, otherwise sound.

ISSUES:
- [MAJOR] Sign error in step 3

CORRECTED SOLUTION:
The corrected proof goes like this...
Step 1: ...
Step 2: ...
Step 3 (fixed): correct sign
END CORRECTED SOLUTION
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.FIXABLE
        assert result.confidence == 0.75
        assert result.corrected_solution is not None
        assert "correct sign" in result.corrected_solution

    def test_parse_fixable_without_correction(self):
        text = """\
VERDICT: fixable
CONFIDENCE: 0.70

CRITIQUE:
Has fixable errors but no correction provided.

ISSUES:
- [MAJOR] Algebra error
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.FIXABLE
        assert result.corrected_solution is None

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
        assert (
            "division by zero" in revision.changes_made.lower()
            or "domain" in revision.changes_made.lower()
        )
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

    def test_cli_no_stall_reset_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--no-stall-reset", "test"])
        config = _build_config(args)
        assert config.stall_reset is False

    def test_cli_stall_window_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--stall-window", "4", "test"])
        config = _build_config(args)
        assert config.stall_window == 4

    def test_cli_stall_epsilon_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--stall-epsilon", "0.05", "test"])
        config = _build_config(args)
        assert config.stall_epsilon == 0.05


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
                "VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nNow correct.\n\nISSUES:\nNone"
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
    def test_fixable_correction_accepted(self, mock_tools):
        """FIXABLE with correction that passes re-verification should skip reviser."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Generate
            self._mock_response("Attempt with sign error"),
            # Verify → fixable with correction
            self._mock_response(
                "VERDICT: fixable\nCONFIDENCE: 0.75\n\n"
                "CRITIQUE:\nSign error in step 3.\n\nISSUES:\n- [MAJOR] Sign error\n\n"
                "CORRECTED SOLUTION:\nFixed solution text\nEND CORRECTED SOLUTION"
            ),
            # Re-verify correction → correct
            self._mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nNow correct.\n\nISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.solution == "Fixed solution text"
        assert result.total_revisions == 0  # reviser was skipped

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_fixable_correction_fails_reverification(self, mock_tools):
        """FIXABLE correction that fails re-verification should fall through to reviser."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Generate
            self._mock_response("Attempt with errors"),
            # Verify → fixable with correction
            self._mock_response(
                "VERDICT: fixable\nCONFIDENCE: 0.70\n\n"
                "CRITIQUE:\nErrors found.\n\nISSUES:\n- [MAJOR] Error\n\n"
                "CORRECTED SOLUTION:\nStill wrong fix\nEND CORRECTED SOLUTION"
            ),
            # Re-verify correction → still has issues
            self._mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.80\n\n"
                "CRITIQUE:\nStill has issues.\n\nISSUES:\n- [MINOR] Gap"
            ),
            # Reviser (fallback)
            self._mock_response(
                "CHANGES MADE:\nFixed gap.\n\nREVISED SOLUTION:\nFinal correct version"
            ),
            # Re-verify revision → correct
            self._mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.92\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.total_revisions == 1  # reviser was used as fallback

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
        # Events should contain an error entry from iteration 1
        error_entries = [e for e in result.events if e.type == EventType.ERROR]
        assert len(error_entries) == 1
        assert error_entries[0].iteration == 1


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
            assert (
                "temperature=0.5"
                in mock_logger.debug.call_args[0][0] % mock_logger.debug.call_args[0][1:]
            )

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
        args = parser.parse_args(
            ["--preset", "quick", "--thinking", "--max-tokens", "12000", "test"]
        )
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


# ── Variant B A/B generation tests ───────────────────────────────────


class TestVariantB:
    def test_config_variant_b_default_none(self):
        """Default AgentConfig has variant_b=None."""
        config = AgentConfig()
        assert config.variant_b is None

    def test_config_variant_b_preset_thorough(self):
        """Thorough preset has variant_b with model."""
        config = AgentConfig.from_preset("thorough")
        assert config.variant_b is not None
        assert config.variant_b["model"] == "claude-sonnet-4-6"

    def test_build_variant_b_config(self):
        """build_variant_b_config produces correct merged config."""
        config = AgentConfig(
            model="claude-opus-4-6",
            max_iterations=5,
            variant_b={"model": "claude-sonnet-4-6", "max_iterations": 3},
        )
        vb = config.build_variant_b_config()
        assert vb.model == "claude-sonnet-4-6"
        assert vb.max_iterations == 3
        assert vb.variant_b is None  # no recursion
        # Unoverridden fields inherited
        assert vb.max_revisions_per_cycle == config.max_revisions_per_cycle

    def test_variant_b_invalid_keys(self):
        """Unknown keys in variant_b raise ValueError."""
        with pytest.raises(ValueError, match="variant_b contains unknown keys"):
            AgentConfig(variant_b={"nonexistent_field": 42})

    def test_no_variant_b_flag(self):
        """CLI --no-variant-b overrides preset variant_b."""
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "thorough", "--no-variant-b", "test"])
        config = _build_config(args)
        assert config.variant_b is None

    def test_variant_b_model_flag(self):
        """CLI --variant-b-model sets variant_b."""
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--variant-b-model", "claude-sonnet-4-6", "test"])
        config = _build_config(args)
        assert config.variant_b == {"model": "claude-sonnet-4-6"}

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_n1_ignores_variant_b(self, mock_tools):
        """With n=1, variant_b is ignored (only primary config used)."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Solution text.\n\nCONCLUSION: answer"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        verify_resp_block = MagicMock()
        verify_resp_block.type = "text"
        verify_resp_block.text = (
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
        )
        verify_resp = MagicMock()
        verify_resp.content = [verify_resp_block]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [mock_resp, verify_resp]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")
        assert result.solved
        # With n=1, only 2 API calls: generate + verify (no variant B client created)
        assert mock_client.messages.create.call_count == 2


# ── Audit fix tests ──────────────────────────────────────────────────


class TestAuditFixes:
    """Tests for bugs found in the 6-agent parallel audit and their fixes."""

    def _mock_response(self, text: str):
        """Create a mock Anthropic response object."""
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = text
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        return mock_resp

    # ── T5: .format() safety (curly braces in problem text) ──────────

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_with_curly_braces_in_problem(self, mock_tools):
        """generate() must not crash when problem text contains set notation {x | x > 0}."""
        from alethic.subagents import generate

        config = AgentConfig(
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "The set {x | x > 0} is the positive reals.\n\nCONCLUSION: answer"
        )

        problem = "Let S = {x | x > 0}. Prove that S is uncountable."
        sol = generate(mock_client, problem, config, iteration=1)
        assert sol.solution_text is not None
        # The problem text should appear verbatim in the API call
        call_args = mock_client.messages.create.call_args
        user_msg = call_args.kwargs.get("messages", call_args[1].get("messages", [{}]))[0][
            "content"
        ]
        assert "{x | x > 0}" in user_msg

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_with_curly_braces_in_problem(self, mock_tools):
        """verify() must not crash when problem/solution contain curly braces."""
        from alethic.subagents import verify

        config = AgentConfig(
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
        )

        problem = "Prove {x ∈ R | x^2 < 2} is open."
        sol = Solution(
            problem=problem, solution_text="Proof using {x | ...} notation.", iteration=1
        )
        result = verify(mock_client, problem, sol, config)
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_with_curly_braces_in_problem(self, mock_tools):
        """revise() must not crash when all inputs contain curly braces."""
        from alethic.subagents import revise

        config = AgentConfig(
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "CHANGES MADE:\nFixed.\n\nREVISED SOLUTION:\nUsing {x | x > 0} correctly."
        )

        problem = "Let A = {a, b, {c, d}}. Find |A|."
        sol = Solution(problem=problem, solution_text="Previous attempt with {a, b}.", iteration=1)
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="The set {c, d} was miscounted.",
            confidence=0.7,
        )
        revised = revise(mock_client, problem, sol, vr, config, revision_number=1)
        assert revised.solution_text is not None

    # ── T6: CORRECTED SOLUTION regex robustness ──────────────────────

    def test_corrected_solution_with_two_word_caps_label(self):
        """CORRECTED SOLUTION containing two-word ALL-CAPS labels (e.g. STEP ONE:)
        should NOT be truncated at those labels."""
        text = """\
VERDICT: fixable
CONFIDENCE: 0.75

CRITIQUE:
Sign error.

ISSUES:
- [MAJOR] Sign error

CORRECTED SOLUTION:
STEP ONE: Set up the integral.
STEP TWO: Evaluate boundary terms.
The final answer is 42.
END CORRECTED SOLUTION
"""
        result = _parse_verification(text)
        assert result.verdict == Verdict.FIXABLE
        assert result.corrected_solution is not None
        assert "STEP ONE:" in result.corrected_solution
        assert "STEP TWO:" in result.corrected_solution
        assert "final answer is 42" in result.corrected_solution

    def test_corrected_solution_end_marker_terminates(self):
        """END CORRECTED SOLUTION should terminate the corrected solution block."""
        text = """\
VERDICT: fixable
CONFIDENCE: 0.70

CRITIQUE:
Error found.

ISSUES:
- [MAJOR] Error

CORRECTED SOLUTION:
Fixed content here.
END CORRECTED SOLUTION

REASON: N/A
"""
        result = _parse_verification(text)
        assert result.corrected_solution is not None
        assert "Fixed content here." in result.corrected_solution
        # The text after END CORRECTED SOLUTION should NOT be in the correction
        assert "REASON:" not in result.corrected_solution

    # ── T7: Verifier exception resilience ────────────────────────────

    def test_consensus_survives_one_verifier_exception(self):
        """When one of K verifier futures raises, consensus should still complete with K-1."""
        from alethic.verifier_agent import VerifierAgent

        config = VerifierConfig(
            num_verifiers=3,
            enable_code_execution=False,
            verbose=False,
        )

        # Synthesizer uses client.messages.create directly
        synth_response = self._mock_response("Unified critique: all good.")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = synth_response

        agent = VerifierAgent(config=config)
        agent.client = mock_client

        # Patch verify_subagent to make one call raise, two succeed
        call_count = 0
        good_result = VerificationResult(
            verdict=Verdict.CORRECT, critique="All good.", confidence=0.92
        )

        def _patched_verify(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated verifier crash")
            return good_result

        with patch("alethic.verifier_agent.verify_subagent", side_effect=_patched_verify):
            result = agent.verify(
                problem="Is 1+1=2?",
                solution="Yes, 1+1=2 by Peano axioms.",
            )

        # Should succeed with 2 of 3 verifiers
        assert result.num_verifiers == 2
        assert result.verdict == Verdict.CORRECT

    # ── T1: FIXABLE fallthrough to reviser ───────────────────────────

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_fixable_fallthrough_uses_corrected_as_revision_base(self, mock_tools):
        """When FIXABLE correction fails re-verification, the corrected solution
        should become the base for the reviser (not the original)."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Generate
            self._mock_response("Original attempt with errors"),
            # Verify -> fixable with correction
            self._mock_response(
                "VERDICT: fixable\nCONFIDENCE: 0.70\n\n"
                "CRITIQUE:\nSign error.\n\nISSUES:\n- [MAJOR] Sign error\n\n"
                "CORRECTED SOLUTION:\nCorrected by verifier\nEND CORRECTED SOLUTION"
            ),
            # Re-verify correction -> still not acceptable
            self._mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.80\n\n"
                "CRITIQUE:\nStill a gap.\n\nISSUES:\n- [MINOR] Missing step"
            ),
            # Reviser called on the corrected solution (not original)
            self._mock_response(
                "CHANGES MADE:\nFilled gap.\n\nREVISED SOLUTION:\nFinal correct version"
            ),
            # Re-verify revision -> correct
            self._mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.93\n\nCRITIQUE:\nNow correct.\n\nISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.total_revisions == 1
        # The reviser should have received the corrected solution as input.
        # Check the 4th API call (reviser) — its user message should contain
        # "Corrected by verifier" (the FIXABLE correction text), not "Original attempt".
        reviser_call = mock_client.messages.create.call_args_list[3]
        reviser_msg = reviser_call.kwargs.get("messages", reviser_call[1].get("messages", [{}]))[0][
            "content"
        ]
        assert "Corrected by verifier" in reviser_msg
        assert "Original attempt with errors" not in reviser_msg

    # ── T4: Stall tracking records original FIXABLE verdict ──────────

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_stall_tracking_records_original_fixable_verdict(self, mock_tools):
        """iteration_final_verdicts should record the original FIXABLE verdict,
        not the re-verification verdict after the FIXABLE shortcut."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=True,
            stall_window=2,
        )

        responses = [
            # Iteration 1: generate
            self._mock_response("Attempt 1"),
            # Iteration 1: verify -> fixable with correction
            self._mock_response(
                "VERDICT: fixable\nCONFIDENCE: 0.70\n\n"
                "CRITIQUE:\nSign error.\n\nISSUES:\n- [MAJOR] Sign\n\n"
                "CORRECTED SOLUTION:\nFixed version\nEND CORRECTED SOLUTION"
            ),
            # Iteration 1: re-verify correction -> minor_issues (still not acceptable)
            self._mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.80\n\n"
                "CRITIQUE:\nGap.\n\nISSUES:\n- [MINOR] Gap"
            ),
            # Iteration 1: revise
            self._mock_response("CHANGES MADE:\nFix gap.\n\nREVISED SOLUTION:\nRevised"),
            # Iteration 1: re-verify revision -> still not acceptable
            self._mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.82\n\n"
                "CRITIQUE:\nAnother gap.\n\nISSUES:\n- [MINOR] Gap2"
            ),
            # Iteration 2: generate
            self._mock_response("Attempt 2"),
            # Iteration 2: verify -> correct
            self._mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        # Check the events: the iteration_final_verdicts for iteration 1 should be
        # FIXABLE (the original verification verdict), not MINOR_ISSUES (the re-verification)
        # We verify by checking the VERIFY events — the first verification should be FIXABLE
        verify_events = [e for e in result.events if e.type == EventType.VERIFY]
        # First verify event should have the FIXABLE verdict
        first_verify = verify_events[0]
        assert first_verify.data["verdict"] == "fixable"

    # ── T8: CLI --no-variant-b + --variant-b-model warning ───────────

    def test_cli_conflicting_variant_b_flags_warns(self):
        """--no-variant-b + --variant-b-model should warn on stderr and --no-variant-b wins."""
        import io
        import sys

        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "--no-variant-b",
                "--variant-b-model",
                "claude-sonnet-4-6",
                "test problem",
            ]
        )

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            config = _build_config(args)
        finally:
            sys.stderr = old_stderr

        # Warning should have been printed
        assert "Warning" in captured.getvalue()
        assert "--no-variant-b" in captured.getvalue()
        # --no-variant-b wins: variant_b should be None
        assert config.variant_b is None

    # ── T9: Sandbox blocked submodules ───────────────────────────────

    def test_sandbox_blocks_import_os(self):
        """Direct 'import os' must be blocked in the sandbox."""
        result = execute_python("import os\nprint(os.getcwd())")
        assert "not allowed" in result or "ERROR" in result

    def test_sandbox_blocks_numpy_os_attribute(self):
        """Accessing os through numpy (numpy.os) should be blocked or unavailable."""
        result = execute_python("import numpy\nprint(numpy.os.getcwd())")
        # This should fail — either blocked by import gate or AttributeError
        assert "ERROR" in result or "error" in result.lower() or "not allowed" in result

    def test_sandbox_blocks_subprocess(self):
        """'import subprocess' must be blocked in the sandbox."""
        result = execute_python("import subprocess\nsubprocess.run(['echo', 'hi'])")
        assert "not allowed" in result or "ERROR" in result

    def test_sandbox_blocks_os_via_dotted_import(self):
        """'import os.path' should be blocked by the submodule filter."""
        result = execute_python("import os.path\nprint(os.path.exists('/'))")
        assert "not allowed" in result or "ERROR" in result

    def test_sandbox_allows_safe_modules(self):
        """Safe modules like math and fractions should still work."""
        result = execute_python(
            "import math\nfrom fractions import Fraction\nprint(Fraction(1, 3))"
        )
        assert "1/3" in result
