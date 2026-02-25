"""Tests for session input resolution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from alethic.session_reader import resolve_session_input


class TestResolveSessionInput:
    def test_session_with_problem_and_output(self, tmp_path: Path):
        """Session dir with output.md and problem.md returns (problem, solution)."""
        session_dir = tmp_path / "test-session-20260225-a1b2"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(json.dumps({"problem": "Is 1+1=2?"}))
        (session_dir / "problem.md").write_text("Is 1+1=2?")
        (session_dir / "output.md").write_text("Yes, 1+1=2 by Peano axioms.")

        problem, solution = resolve_session_input(str(session_dir))
        assert problem == "Is 1+1=2?"
        assert solution == "Yes, 1+1=2 by Peano axioms."

    def test_session_falls_back_to_worklog_solution(self, tmp_path: Path):
        """If output.md is missing, fall back to worklog/solution.md."""
        session_dir = tmp_path / "test-session-20260225-b2c3"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(json.dumps({"problem": "Prove P=NP"}))
        (session_dir / "problem.md").write_text("Prove P=NP")
        worklog = session_dir / "worklog"
        worklog.mkdir()
        (worklog / "solution.md").write_text("Here is the proof...")

        problem, solution = resolve_session_input(str(session_dir))
        assert problem == "Prove P=NP"
        assert solution == "Here is the proof..."

    def test_session_without_problem(self, tmp_path: Path):
        """Session dir without problem.md returns (None, solution)."""
        session_dir = tmp_path / "test-session-20260225-c3d4"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(json.dumps({}))
        (session_dir / "output.md").write_text("A derivation...")

        problem, solution = resolve_session_input(str(session_dir))
        assert problem is None
        assert solution == "A derivation..."

    def test_non_session_directory_raises(self, tmp_path: Path):
        """Directory without session.json raises ValueError."""
        non_session = tmp_path / "random-dir"
        non_session.mkdir()
        (non_session / "readme.md").write_text("Not a session")

        with pytest.raises(ValueError, match="not a valid alethic session"):
            resolve_session_input(str(non_session))

    def test_nonexistent_path_raises(self, tmp_path: Path):
        """Nonexistent path raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            resolve_session_input(str(tmp_path / "ghost"))

    def test_file_path_raises(self, tmp_path: Path):
        """File path (not directory) raises ValueError."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            resolve_session_input(str(f))

    def test_session_with_no_solution_raises(self, tmp_path: Path):
        """Session with no output.md or worklog fallback raises ValueError."""
        session_dir = tmp_path / "test-session-20260225-e5f6"
        session_dir.mkdir()
        (session_dir / "session.json").write_text(json.dumps({}))

        with pytest.raises(ValueError, match="no solution found"):
            resolve_session_input(str(session_dir))
