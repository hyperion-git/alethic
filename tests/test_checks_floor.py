"""Tests for patch #1 (PR #9) parser-side CHECKS PERFORMED floor.

When the verifier omits or under-populates the CHECKS PERFORMED block, the
prompt rule says CONFIDENCE must be below 0.30. Prompt-only enforcement was
observed to fail against non-Claude models (Kimi K2.6 emitted prose Markdown
headers instead of structured rows during cross-model smoke testing). These
tests cover the parser-side backstop wired into verify() when
config.enforce_checks_floor is True.
"""
from unittest.mock import patch

import pytest

from alethic.models import (
    AgentConfig,
    Issue,
    IssueSeverity,
    Solution,
    Verdict,
    VerificationResult,
)
from alethic.subagents import (
    _CHECKS_FLOOR_CONFIDENCE,
    _CHECKS_FLOOR_MIN_CONSTRAINT_PASS,
    _apply_checks_floor,
    _parse_checks_performed,
    verify,
)


# ---------------------------------------------------------------------------
# _parse_checks_performed — pure parsing
# ---------------------------------------------------------------------------


class TestParseChecksPerformed:
    def test_no_block_returns_zeros(self):
        text = "VERDICT: correct\nCONFIDENCE: 0.95\nCRITIQUE: OK"
        assert _parse_checks_performed(text) == (0, 0, 0)

    def test_three_constraint_pass(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [dim_check | type=constraint | outcome=PASS] dimensions agree\n"
            "- [sign_convention | type=constraint | outcome=PASS] signs match\n"
            "- [limit_case | type=constraint | outcome=PASS] limit reproduced\n"
            "\nISSUES:\n- None"
        )
        n_pass, n_fail, n_total = _parse_checks_performed(text)
        assert n_pass == 3
        assert n_fail == 0
        assert n_total == 3

    def test_constraint_fail_counted_separately(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [dim_check | type=constraint | outcome=PASS] ok\n"
            "- [sign_check | type=constraint | outcome=FAIL] wrong sign\n"
            "\nISSUES:\n- foo"
        )
        n_pass, n_fail, _ = _parse_checks_performed(text)
        assert n_pass == 1
        assert n_fail == 1

    def test_conjecture_entries_not_counted_as_pass(self):
        """type=conjecture is informational, not load-bearing for the floor."""
        text = (
            "CHECKS PERFORMED:\n"
            "- [maybe1 | type=conjecture | outcome=PASS] guess1\n"
            "- [maybe2 | type=conjecture | outcome=PASS] guess2\n"
            "- [maybe3 | type=conjecture | outcome=PASS] guess3\n"
            "\nISSUES:\n- None"
        )
        n_pass, _, n_total = _parse_checks_performed(text)
        assert n_pass == 0
        assert n_total == 3

    def test_n_a_outcome_counted_in_total_only(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [check1 | type=constraint | outcome=PASS] ok\n"
            "- [check2 | type=constraint | outcome=N/A] not applicable here\n"
            "\nISSUES:\n- None"
        )
        n_pass, n_fail, n_total = _parse_checks_performed(text)
        assert n_pass == 1
        assert n_fail == 0
        assert n_total == 2

    def test_tolerates_bold_markdown_wrapper(self):
        """Patch #1 must respect the `1b6f377` parser tolerance for **bold** labels."""
        text = (
            "**CHECKS PERFORMED:**\n"
            "- [a | type=constraint | outcome=PASS] x\n"
            "- [b | type=constraint | outcome=PASS] y\n"
            "- [c | type=constraint | outcome=PASS] z\n"
            "\n**ISSUES:**\n- None"
        )
        n_pass, _, _ = _parse_checks_performed(text)
        assert n_pass == 3

    def test_block_terminated_by_next_section(self):
        """Block ends at ISSUES; subsequent entries outside the block are not counted."""
        text = (
            "CHECKS PERFORMED:\n"
            "- [a | type=constraint | outcome=PASS] x\n"
            "\nISSUES:\n"
            "- [b | type=constraint | outcome=PASS] this is in ISSUES, not CHECKS\n"
        )
        n_pass, _, n_total = _parse_checks_performed(text)
        assert n_pass == 1
        assert n_total == 1


# ---------------------------------------------------------------------------
# _apply_checks_floor — VerificationResult mutation
# ---------------------------------------------------------------------------


def _vr(confidence: float, verdict: Verdict = Verdict.CORRECT) -> VerificationResult:
    """Build a minimal VerificationResult fixture."""
    return VerificationResult(
        verdict=verdict,
        critique="placeholder",
        confidence=confidence,
        issues=[],
    )


class TestApplyChecksFloor:
    def test_passes_through_when_block_satisfied(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [a | type=constraint | outcome=PASS] x\n"
            "- [b | type=constraint | outcome=PASS] y\n"
            "- [c | type=constraint | outcome=PASS] z\n"
        )
        result = _vr(confidence=0.95)
        out = _apply_checks_floor(result, text)
        assert out.confidence == 0.95

    def test_floors_when_no_block(self):
        text = "VERDICT: correct\nCONFIDENCE: 0.95\nCRITIQUE: trust me"
        result = _vr(confidence=0.95)
        out = _apply_checks_floor(result, text)
        assert out.confidence == _CHECKS_FLOOR_CONFIDENCE
        assert out.confidence == 0.30

    def test_floors_when_only_two_constraint_pass(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [a | type=constraint | outcome=PASS] x\n"
            "- [b | type=constraint | outcome=PASS] y\n"
        )
        result = _vr(confidence=0.85)
        out = _apply_checks_floor(result, text)
        assert out.confidence == 0.30

    def test_floors_when_only_conjecture(self):
        text = (
            "CHECKS PERFORMED:\n"
            "- [a | type=conjecture | outcome=PASS] x\n"
            "- [b | type=conjecture | outcome=PASS] y\n"
            "- [c | type=conjecture | outcome=PASS] z\n"
        )
        result = _vr(confidence=0.85)
        out = _apply_checks_floor(result, text)
        assert out.confidence == 0.30

    def test_does_not_modify_when_already_below_floor(self):
        """Confidence ≤ floor is untouched (no inflation, no fake elevation)."""
        text = "no checks block here"
        result = _vr(confidence=0.10)
        out = _apply_checks_floor(result, text)
        assert out.confidence == 0.10

    def test_does_not_modify_when_equal_to_floor(self):
        text = "no checks block here"
        result = _vr(confidence=_CHECKS_FLOOR_CONFIDENCE)
        out = _apply_checks_floor(result, text)
        assert out.confidence == _CHECKS_FLOOR_CONFIDENCE

    def test_threshold_constants_match_prompt_rule(self):
        """Sanity: the constants match the prompt's stated rule."""
        assert _CHECKS_FLOOR_CONFIDENCE == 0.30
        assert _CHECKS_FLOOR_MIN_CONSTRAINT_PASS == 3

    def test_preserves_other_fields_on_floor(self):
        """Flooring only modifies confidence — verdict/critique/issues unchanged."""
        text = "no block"
        original = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="ORIGINAL CRITIQUE",
            confidence=0.99,
            issues=[Issue(text="x", severity=IssueSeverity.MAJOR)],
            reason="ORIGINAL REASON",
        )
        out = _apply_checks_floor(original, text)
        assert out.confidence == 0.30
        assert out.verdict == Verdict.CORRECT
        assert out.critique == "ORIGINAL CRITIQUE"
        assert out.reason == "ORIGINAL REASON"
        assert len(out.issues) == 1


# ---------------------------------------------------------------------------
# verify() integration — config flag gating
# ---------------------------------------------------------------------------


def _make_client(response_text: str):
    """Build a stub Anthropic client that returns `response_text` from messages.create."""
    from unittest.mock import MagicMock
    from alethic.openrouter import Message, TextBlock, Usage

    client = MagicMock()
    msg = Message(
        content=[TextBlock(type="text", text=response_text)],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=20),
    )
    client.messages.create.return_value = msg
    return client


class TestVerifyIntegration:
    """The verify() entry point should gate the floor on config.enforce_checks_floor."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_bare_agent_config_does_not_apply_floor(self, _mock_tools):
        """Default AgentConfig() has enforce_checks_floor=False — back-compat."""
        client = _make_client(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nlooks fine\n\nISSUES:\nNone"
        )
        config = AgentConfig()
        assert config.enforce_checks_floor is False  # explicit invariant
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(client, "P", sol, config)

        assert result.confidence == 0.95  # untouched

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_floor_applied_when_flag_enabled_and_no_block(self, _mock_tools):
        client = _make_client(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nlooks fine\n\nISSUES:\nNone"
        )
        config = AgentConfig(enforce_checks_floor=True)
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(client, "P", sol, config)

        assert result.confidence == 0.30  # floored

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_floor_not_applied_when_block_satisfies_rule(self, _mock_tools):
        client = _make_client(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
            "CRITIQUE:\nlooks fine\n\n"
            "CHECKS PERFORMED:\n"
            "- [a | type=constraint | outcome=PASS] x\n"
            "- [b | type=constraint | outcome=PASS] y\n"
            "- [c | type=constraint | outcome=PASS] z\n\n"
            "ISSUES:\n- None"
        )
        config = AgentConfig(enforce_checks_floor=True)
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = verify(client, "P", sol, config)

        assert result.confidence == 0.95  # block satisfied → no flooring

    @pytest.mark.parametrize("preset", ["quick", "default", "thorough", "extreme"])
    def test_all_presets_enable_floor(self, preset):
        config = AgentConfig.from_preset(preset)
        assert config.enforce_checks_floor is True
