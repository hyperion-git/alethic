"""Tests for consensus output formatting."""

from __future__ import annotations

import json

from alethic.models import (
    ConsensusIssue,
    ConsensusResult,
    IssueSeverity,
    Verdict,
    VerificationResult,
)
from alethic.output import format_consensus


def _make_result(**overrides):
    defaults = {
        "verdict": Verdict.CORRECT,
        "confidence": 0.91,
        "confidence_range": (0.85, 0.95),
        "critique": "The proof is sound.",
        "issues": [],
        "individual_results": [
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.90),
            VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.92),
        ],
        "domain_detected": "math",
        "num_verifiers": 2,
        "elapsed_seconds": 12.5,
    }
    defaults.update(overrides)
    return ConsensusResult(**defaults)


class TestFormatText:
    def test_contains_verdict(self):
        output = format_consensus(_make_result(), mode="text", command="verify")
        assert "CORRECT" in output

    def test_contains_confidence(self):
        output = format_consensus(_make_result(), mode="text", command="verify")
        assert "0.91" in output

    def test_contains_domain(self):
        output = format_consensus(_make_result(), mode="text", command="verify")
        assert "math" in output

    def test_contains_consensus_ratio(self):
        output = format_consensus(_make_result(), mode="text", command="verify")
        assert "2/2" in output

    def test_contains_critique(self):
        output = format_consensus(_make_result(), mode="text", command="verify")
        assert "The proof is sound." in output

    def test_issues_shown(self):
        result = _make_result(
            issues=[
                ConsensusIssue(text="Sign error", severity=IssueSeverity.MAJOR, flagged_by=2),
            ]
        )
        output = format_consensus(result, mode="text", command="verify")
        assert "Sign error" in output
        assert "MAJOR" in output
        assert "2/2" in output

    def test_check_command_label(self):
        output = format_consensus(_make_result(), mode="text", command="check")
        assert "CHECK" in output


class TestFormatJson:
    def test_valid_json(self):
        output = format_consensus(_make_result(), mode="json", command="verify")
        data = json.loads(output)
        assert data["verdict"] == "correct"
        assert data["confidence"] == 0.91
        assert data["domain_detected"] == "math"
        assert data["num_verifiers"] == 2

    def test_issues_in_json(self):
        result = _make_result(
            issues=[
                ConsensusIssue(text="Missing step", severity=IssueSeverity.MINOR, flagged_by=1),
            ]
        )
        data = json.loads(format_consensus(result, mode="json", command="verify"))
        assert len(data["issues"]) == 1
        assert data["issues"][0]["text"] == "Missing step"
        assert data["issues"][0]["flagged_by"] == 1


class TestFormatQuiet:
    def test_single_line(self):
        output = format_consensus(_make_result(), mode="quiet", command="verify")
        assert "\n" not in output.strip()

    def test_contains_key_fields(self):
        output = format_consensus(_make_result(), mode="quiet", command="verify")
        assert "CORRECT" in output
        assert "0.91" in output
        assert "2/2" in output
        assert "math" in output
