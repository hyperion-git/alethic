"""Tests for consensus synthesis."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alethic.models import (
    Issue,
    IssueSeverity,
    Verdict,
    VerificationResult,
)
from alethic.synthesizer import aggregate_mechanical, synthesize_critique


class TestMechanicalAggregation:
    def test_unanimous_correct(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="Good", confidence=0.95),
            VerificationResult(verdict=Verdict.CORRECT, critique="Also good", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="Agreed", confidence=0.92),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT
        assert abs(agg["confidence"] - 0.9233) < 0.01
        assert agg["confidence_range"] == (0.90, 0.95)

    def test_majority_verdict(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.85),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.75),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT

    def test_no_majority_takes_most_severe(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.80),
            VerificationResult(verdict=Verdict.MAJOR_FLAW, critique="bad", confidence=0.60),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.MAJOR_FLAW

    def test_issues_union_with_vote_counts(self):
        results = [
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES,
                critique="a",
                confidence=0.80,
                issues=[Issue(text="Sign error in step 3", severity=IssueSeverity.MAJOR)],
            ),
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES,
                critique="b",
                confidence=0.82,
                issues=[
                    Issue(text="Sign error in step 3", severity=IssueSeverity.MAJOR),
                    Issue(text="Missing edge case", severity=IssueSeverity.MINOR),
                ],
            ),
            VerificationResult(
                verdict=Verdict.CORRECT,
                critique="c",
                confidence=0.90,
                issues=[],
            ),
        ]
        agg = aggregate_mechanical(results)
        # "Sign error" should be flagged by 2, "Missing edge case" by 1
        sign_issues = [i for i in agg["issues"] if "sign error" in i.text.lower()]
        assert len(sign_issues) == 1
        assert sign_issues[0].flagged_by == 2

    def test_single_verifier(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.CORRECT
        assert agg["confidence"] == 0.90
        assert agg["confidence_range"] == (0.90, 0.90)

    def test_two_way_tie_takes_most_severe(self):
        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="hmm", confidence=0.80),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.MINOR_ISSUES

    def test_issues_sorted_by_flagged_by_then_severity(self):
        results = [
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES,
                critique="a",
                confidence=0.80,
                issues=[
                    Issue(text="Minor notation issue", severity=IssueSeverity.MINOR),
                    Issue(text="Critical flaw", severity=IssueSeverity.CRITICAL),
                ],
            ),
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES,
                critique="b",
                confidence=0.80,
                issues=[Issue(text="Minor notation issue", severity=IssueSeverity.MINOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        # "Minor notation issue" flagged by 2 should come first (higher vote count)
        assert agg["issues"][0].flagged_by == 2
        assert "notation" in agg["issues"][0].text.lower()

    def test_empty_results_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            aggregate_mechanical([])


class TestSynthesizeCritique:
    def test_synthesize_calls_api(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_block = MagicMock()
        mock_block.text = "Unified critique text"
        mock_response.content = [mock_block]
        mock_client.messages.create.return_value = mock_response

        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="Good work", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="Solid proof", confidence=0.92),
        ]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        assert critique == "Unified critique text"
        mock_client.messages.create.assert_called_once()

    def test_synthesize_handles_empty_content(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = []
        mock_client.messages.create.return_value = mock_response

        results = [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
        ]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        assert critique == "[Synthesis failed]"
