"""Tests for eval harness (feature 2.3)."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch


def _make_benchmark(problems: list[dict]) -> str:
    """Write a benchmark JSON to a temp file and return the path."""
    data = {"name": "test-bench", "version": "1.0", "problems": problems}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(data, tmp)
        return tmp.name


class TestBenchmarkFormat:
    def test_load_benchmark_returns_problems(self):
        from alethic.eval.harness import load_benchmark

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "Is 2 prime?", "expected_solvable": True},
        ])
        bench = load_benchmark(path)
        assert bench["name"] == "test-bench"
        assert len(bench["problems"]) == 1

    def test_load_benchmark_raises_on_missing_fields(self):
        import pytest

        from alethic.eval.harness import load_benchmark

        # A problem missing required fields
        path = _make_benchmark([{"id": "p1"}])
        with pytest.raises(ValueError, match="missing required field"):
            load_benchmark(path)


class TestRunBenchmark:
    @patch("alethic.eval.harness.MathAgent")
    def test_run_benchmark_returns_score_report(self, mock_agent):
        from alethic.eval.harness import run_benchmark

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.confidence = 0.95
        mock_result.iterations_used = 2
        mock_result.verdict.value = "correct"
        mock_agent.return_value.solve.return_value = mock_result

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "Prove 2 is prime.", "expected_solvable": True},
            {"id": "p2", "domain": "math", "problem": "Prove 1+1=3.", "expected_solvable": False},
        ])
        report = run_benchmark(path, api_key="fake-key", preset="quick")

        assert "total" in report
        assert "solve_rate" in report
        assert "results" in report
        assert report["total"] == 2

    @patch("alethic.eval.harness.MathAgent")
    def test_run_benchmark_score_rate_is_fraction(self, mock_agent):
        from alethic.eval.harness import run_benchmark

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.confidence = 0.9
        mock_result.iterations_used = 1
        mock_result.verdict.value = "correct"
        mock_agent.return_value.solve.return_value = mock_result

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "p1", "expected_solvable": True},
        ])
        report = run_benchmark(path, api_key="fake-key", preset="quick")
        assert 0.0 <= report["solve_rate"] <= 1.0
