"""Tests for scripts/run_gate.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Add scripts/ to path so we can import run_gate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_gate


class TestProgressBar(unittest.TestCase):
    def test_zero_progress(self):
        result = run_gate._progress_bar(0, 100)
        assert "0/100" in result
        assert "0%" in result

    def test_full_progress(self):
        result = run_gate._progress_bar(100, 100)
        assert "100/100" in result
        assert "100%" in result

    def test_extra_text(self):
        result = run_gate._progress_bar(50, 100, extra="hello")
        assert "hello" in result


class TestFormatEta(unittest.TestCase):
    def test_seconds(self):
        assert run_gate._format_eta(45) == "45s"

    def test_minutes(self):
        assert run_gate._format_eta(300) == "5m"

    def test_hours(self):
        assert run_gate._format_eta(7200) == "2.0h"


class TestErrorCategoryClassification(unittest.TestCase):
    def test_counterexample_is_separate_from_missing_case(self):
        """Verify counterexample is its own category (spec requirement)."""
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("counterexample found at x=5") == "counterexample"
        assert classify_errors("breaker found a flaw") == "counterexample"
        assert classify_errors("missing case: n=0 not handled") == "missing_case"
