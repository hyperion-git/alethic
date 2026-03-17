"""Tests for scripts/run_gate.py harvester logic."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts/ to path so we can import run_gate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_gate


class TestNormalizeText(unittest.TestCase):
    def test_strips_whitespace(self):
        assert run_gate.normalize_text("  hello  ") == "hello"

    def test_nfc_normalization(self):
        # ℏ can be represented as single char or combining sequence
        import unicodedata
        text = unicodedata.normalize("NFD", "ℏω")
        result = run_gate.normalize_text(text)
        assert result == unicodedata.normalize("NFC", "ℏω")


class TestFindExistingSessions(unittest.TestCase):
    def test_matches_by_problem_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            session_dir = alethic_dir / "test-session-20260317-abcd"
            session_dir.mkdir(parents=True)

            session_json = {
                "problem": "Prove that 17 is prime.",
                "status": "solved",
                "created_at": "2026-03-17T10:00:00",
            }
            (session_dir / "session.json").write_text(json.dumps(session_json))

            problems = [{"id": "prime-17", "problem": "Prove that 17 is prime."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert "prime-17" in result
            assert result["prime-17"] == session_dir

    def test_dedup_prefers_solved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"

            # Unsolved session (earlier)
            s1 = alethic_dir / "session-1"
            s1.mkdir(parents=True)
            (s1 / "session.json").write_text(json.dumps({
                "problem": "Prove X.",
                "status": "unsolved",
                "created_at": "2026-03-17T10:00:00",
            }))

            # Solved session (later)
            s2 = alethic_dir / "session-2"
            s2.mkdir(parents=True)
            (s2 / "session.json").write_text(json.dumps({
                "problem": "Prove X.",
                "status": "solved",
                "created_at": "2026-03-17T11:00:00",
            }))

            problems = [{"id": "test-x", "problem": "Prove X."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert result["test-x"] == s2  # solved wins

    def test_ignores_unrelated_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            s1 = alethic_dir / "unrelated-session"
            s1.mkdir(parents=True)
            (s1 / "session.json").write_text(json.dumps({
                "problem": "Something else entirely.",
                "status": "solved",
                "created_at": "2026-03-17T10:00:00",
            }))

            problems = [{"id": "prime-17", "problem": "Prove that 17 is prime."}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.find_existing_sessions(problems)

            assert len(result) == 0


class TestComputePuctFromEvents(unittest.TestCase):
    def test_no_events_returns_zero(self):
        result = run_gate._compute_puct_from_events([])
        assert result["divergence_rate"] == 0.0
        assert result["total_iterations"] == 0

    def test_single_candidate_not_counted(self):
        events = [
            {"type": "verify", "iteration": 1, "candidate": 1,
             "verdict": "correct", "confidence": 0.95, "error_category": "general"},
        ]
        result = run_gate._compute_puct_from_events(events)
        assert result["total_iterations"] == 0  # N=1, no PUCT signal


class TestMeasureAtomsFromFiles(unittest.TestCase):
    def test_no_worklog_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_gate._measure_atoms_from_files(Path(tmpdir), [])
            assert result is None

    def test_reads_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            worklog = session_dir / "worklog"
            worklog.mkdir()

            # Write a candidate file with atom annotations
            (worklog / "candidate_1.md").write_text(
                "ATOM[1] deps=[] oracle=L1\nStep 1: proof.\n"
                "ATOM[2] deps=[1] oracle=L2\nStep 2: conclusion.\n"
            )

            events = [
                {"type": "verify", "iteration": 1, "candidate": 1,
                 "confidence": 0.95},
            ]

            result = run_gate._measure_atoms_from_files(session_dir, events)
            assert result is not None
            assert result["annotation_rate"] == 1.0
            assert result["atom_counts"] == [2]


class TestHarvestEndToEnd(unittest.TestCase):
    def test_harvest_session(self):
        """Full pipeline: session.json + events.jsonl + candidate file -> metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            alethic_dir = Path(tmpdir) / ".alethic"
            session_dir = alethic_dir / "prime-17-20260317-abcd"
            worklog = session_dir / "worklog"
            worklog.mkdir(parents=True)

            # session.json
            (session_dir / "session.json").write_text(json.dumps({
                "problem": "Prove that 17 is prime.",
                "status": "solved",
                "best_confidence": 0.95,
                "created_at": "2026-03-17T10:00:00",
            }))

            # events.jsonl (2 candidates, iteration 1)
            events = [
                {"type": "verify", "iteration": 1, "candidate": 1,
                 "verdict": "correct", "confidence": 0.95,
                 "error_category": "general"},
                {"type": "verify", "iteration": 1, "candidate": 2,
                 "verdict": "minor_issues", "confidence": 0.80,
                 "error_category": "algebra"},
            ]
            with open(worklog / "events.jsonl", "w") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")

            # candidate file with atoms
            (worklog / "candidate_1.md").write_text(
                "ATOM[1] deps=[] oracle=L1\nStep 1: 17 is odd.\n"
            )

            problems = [{"id": "prime-17", "domain": "math",
                         "problem": "Prove that 17 is prime.",
                         "expected_solvable": True}]

            with patch.object(run_gate, "ALETHIC_DIR", alethic_dir):
                result = run_gate.harvest(problems)

            assert result["solved"] == 1
            assert result["solve_rate"] == 1.0
            assert result["mean_annotation_rate"] == 1.0
            assert result["results"][0]["solved"] is True


class TestErrorCategoryClassification(unittest.TestCase):
    def test_counterexample_is_separate_from_missing_case(self):
        """Verify counterexample is its own category (spec requirement 2)."""
        from alethic.error_taxonomy import classify_errors

        assert classify_errors("counterexample found at x=5") == "counterexample"
        assert classify_errors("breaker found a flaw") == "counterexample"
        assert classify_errors("missing case: n=0 not handled") == "missing_case"
