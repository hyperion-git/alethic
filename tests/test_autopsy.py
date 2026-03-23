"""Tests for autopsy mode (feature 1.4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import AgentEvent, AgentResult, EventType, Verdict


def _make_result(
    verdicts: list[str],
    confidences: list[float],
    *,
    approaches: list[str] | None = None,
) -> AgentResult:
    events = []
    for i, (v, c) in enumerate(zip(verdicts, confidences, strict=True)):
        events.append(
            AgentEvent(type=EventType.VERIFY, iteration=i + 1, data={"verdict": v, "confidence": c})
        )
    return AgentResult(
        problem="test problem",
        solution=None,
        verdict=Verdict.UNSOLVED,
        confidence=max(confidences) if confidences else 0.0,
        iterations_used=len(verdicts),
        total_revisions=0,
        admitted_failure=True,
        events=events,
        failed_approaches=approaches or [],
    )


class TestClassifyFailurePattern:
    def test_persistent_flaw(self):
        from alethic.autopsy import _classify_failure_pattern

        result = _make_result(
            ["major_flaw", "major_flaw", "major_flaw"],
            [0.2, 0.2, 0.2],
        )
        assert _classify_failure_pattern(result) == "persistent_flaw"

    def test_stall(self):
        from alethic.autopsy import _classify_failure_pattern

        result = _make_result(
            ["minor_issues", "minor_issues", "minor_issues"],
            [0.7, 0.71, 0.72],
        )
        assert _classify_failure_pattern(result) == "stall"

    def test_regression(self):
        from alethic.autopsy import _classify_failure_pattern

        result = _make_result(
            ["minor_issues", "minor_issues", "major_flaw", "major_flaw"],
            [0.5, 0.85, 0.4, 0.3],
        )
        assert _classify_failure_pattern(result) == "regression"

    def test_oscillation(self):
        from alethic.autopsy import _classify_failure_pattern

        result = _make_result(
            ["major_flaw", "minor_issues", "major_flaw", "minor_issues", "major_flaw"],
            [0.3, 0.7, 0.3, 0.7, 0.3],
        )
        assert _classify_failure_pattern(result) == "oscillation"

    def test_no_events_returns_stall(self):
        from alethic.autopsy import _classify_failure_pattern

        result = _make_result([], [])
        assert _classify_failure_pattern(result) == "stall"


class TestGenerateAutopsy:
    @patch("alethic.autopsy.get_client")
    def test_generate_autopsy_returns_markdown(self, mock_get_client):
        from alethic.autopsy import generate_autopsy

        mock_block = MagicMock()
        mock_block.text = "## Failure Analysis\n\nThe loop stalled.\n\n## Recommended Next Steps\n\n- Try thorough preset"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_get_client.return_value.messages.create.return_value = mock_resp

        result = _make_result(["minor_issues", "minor_issues"], [0.7, 0.72])
        report = generate_autopsy(result, api_key="fake-key")

        assert "# Autopsy Report" in report
        assert "Failure Pattern" in report
        assert "stall" in report.lower() or "Stall" in report

    @patch("alethic.autopsy.get_client")
    def test_generate_autopsy_includes_all_sections(self, mock_get_client):
        from alethic.autopsy import generate_autopsy

        mock_block = MagicMock()
        mock_block.text = "## Failure Analysis\n\nStalled.\n\n## Recommended Next Steps\n\n- Increase N"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_get_client.return_value.messages.create.return_value = mock_resp

        result = _make_result(["major_flaw", "major_flaw"], [0.2, 0.2])
        report = generate_autopsy(result, api_key="fake-key")

        assert "Iterations" in report
        assert "Best Confidence" in report
        assert "Stall Resets" in report
