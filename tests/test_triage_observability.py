"""Tests for patch #2 (PR #9) all_declined observability.

When the reviser declines or dismisses every issue in the ISSUE TRIAGE block,
the resulting "revision" is functionally a no-op — the prior solution is
returned verbatim. The orchestrator does NOT special-case this (the verifier
remains the sole authoritative gatekeeper, per the decoupled-verification
invariant), but it DOES emit a REVISER_ALL_DECLINED event so the pattern is
detectable in events.jsonl.
"""
from unittest.mock import MagicMock, patch

from alethic.models import (
    AgentConfig,
    EventType,
    Solution,
    Verdict,
    VerificationResult,
)
from alethic.subagents import _parse_triage_verdicts, revise


# ---------------------------------------------------------------------------
# _parse_triage_verdicts — pure parsing
# ---------------------------------------------------------------------------


class TestParseTriageVerdicts:
    def test_no_block_returns_empty(self):
        assert _parse_triage_verdicts("CHANGES MADE:\n- foo\n") == {}

    def test_all_accept(self):
        text = (
            "ISSUE TRIAGE:\n"
            "- [issue1 | verdict=accept] real problem\n"
            "- [issue2 | verdict=accept] another\n"
            "\nCHANGES MADE:\n- foo"
        )
        assert _parse_triage_verdicts(text) == {"accept": 2}

    def test_all_decline(self):
        text = (
            "ISSUE TRIAGE:\n"
            "- [issue1 | verdict=decline] spurious\n"
            "- [issue2 | verdict=decline] also spurious\n"
            "- [issue3 | verdict=decline] still spurious\n"
            "\nCHANGES MADE:\n- (none)"
        )
        assert _parse_triage_verdicts(text) == {"decline": 3}

    def test_mixed_verdicts(self):
        text = (
            "ISSUE TRIAGE:\n"
            "- [a | verdict=accept] valid\n"
            "- [b | verdict=decline] not really\n"
            "- [c | verdict=dismiss] off-topic\n"
            "- [d | verdict=accept] also valid\n"
            "\nCHANGES MADE:\n- partial fix"
        )
        counts = _parse_triage_verdicts(text)
        assert counts == {"accept": 2, "decline": 1, "dismiss": 1}

    def test_tolerates_bold_markdown_wrapper(self):
        """1b6f377 parser fix: tolerate **ISSUE TRIAGE** style headers."""
        text = (
            "**ISSUE TRIAGE**\n"
            "- [a | verdict=accept] x\n"
            "- [b | verdict=dismiss] y\n"
            "\n**CHANGES MADE:**\n- ok"
        )
        assert _parse_triage_verdicts(text) == {"accept": 1, "dismiss": 1}

    def test_block_terminated_by_revised_solution_when_changes_made_absent(self):
        text = (
            "ISSUE TRIAGE:\n"
            "- [x | verdict=decline] no\n"
            "\nREVISED SOLUTION:\n[content]"
        )
        assert _parse_triage_verdicts(text) == {"decline": 1}


# ---------------------------------------------------------------------------
# Solution.triage_summary plumbing — revise() populates the field
# ---------------------------------------------------------------------------


def _make_client(response_text: str):
    from alethic.openrouter import Message, TextBlock, Usage

    client = MagicMock()
    msg = Message(
        content=[TextBlock(type="text", text=response_text)],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=20),
    )
    client.messages.create.return_value = msg
    return client


def _verification():
    return VerificationResult(
        verdict=Verdict.FIXABLE,
        critique="foo",
        confidence=0.6,
        issues=[],
    )


class TestRevisePopulatesTriageSummary:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_populates_when_triage_block_present(self, _mock_tools):
        response = (
            "ISSUE TRIAGE:\n"
            "- [a | verdict=decline] no\n"
            "- [b | verdict=dismiss] off-topic\n"
            "\nCHANGES MADE:\n- (none)\n"
            "\nREVISED SOLUTION:\n[unchanged]"
        )
        client = _make_client(response)
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = revise(client, "P", sol, _verification(), AgentConfig(), revision_number=1)

        assert result.triage_summary == {"decline": 1, "dismiss": 1}

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_none_when_no_triage_block(self, _mock_tools):
        """Pre-patch-2 reviser output (no ISSUE TRIAGE) → triage_summary is None, not {}."""
        response = (
            "CHANGES MADE:\n- fixed something\n"
            "\nREVISED SOLUTION:\n[new content]"
        )
        client = _make_client(response)
        sol = Solution(problem="P", solution_text="S", iteration=1)

        result = revise(client, "P", sol, _verification(), AgentConfig(), revision_number=1)

        assert result.triage_summary is None

    def test_generator_solution_has_none_triage_summary(self):
        """Solutions built directly (generator output) have no triage info."""
        sol = Solution(problem="P", solution_text="S", iteration=1)
        assert sol.triage_summary is None


# ---------------------------------------------------------------------------
# all_declined detection — the orchestrator's reading of the field
# ---------------------------------------------------------------------------


class TestAllDeclinedDetection:
    """Verifies the boolean predicate used by agent.py."""

    @staticmethod
    def _is_all_declined(triage: dict[str, int] | None) -> bool:
        """Mirror agent.py's condition: counts present, accept=0, total>0."""
        if not triage:
            return False
        return triage.get("accept", 0) == 0 and sum(triage.values()) > 0

    def test_all_decline_is_detected(self):
        assert self._is_all_declined({"decline": 3}) is True

    def test_all_dismiss_is_detected(self):
        assert self._is_all_declined({"dismiss": 2}) is True

    def test_mixed_decline_dismiss_is_detected(self):
        assert self._is_all_declined({"decline": 1, "dismiss": 2}) is True

    def test_any_accept_means_not_all_declined(self):
        assert self._is_all_declined({"accept": 1, "decline": 3}) is False

    def test_empty_dict_is_not_all_declined(self):
        assert self._is_all_declined({}) is False

    def test_none_is_not_all_declined(self):
        assert self._is_all_declined(None) is False


# ---------------------------------------------------------------------------
# EventType — new value is reachable
# ---------------------------------------------------------------------------


class TestEventType:
    def test_reviser_all_declined_exists(self):
        assert EventType.REVISER_ALL_DECLINED.value == "reviser_all_declined"

    def test_reviser_all_declined_distinct_from_revise(self):
        assert EventType.REVISER_ALL_DECLINED is not EventType.REVISE
