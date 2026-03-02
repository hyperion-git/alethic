"""Tests for session directory creation, checkpoint write/load."""

import json
from pathlib import Path

import pytest

from alethic.exceptions import CheckpointError
from alethic.models import AgentConfig, TokenLedger
from alethic.session import (
    create_session_dir,
    load_checkpoint,
    scan_incomplete_sessions,
    write_checkpoint,
)


class TestCreateSessionDir:
    def test_creates_directory_structure(self, tmp_path):
        session_dir = create_session_dir(
            problem="Prove sqrt(2) is irrational",
            domain="math",
            config=AgentConfig(max_iterations=2, verbose=False),
            base_dir=str(tmp_path),
        )
        p = Path(session_dir)
        assert p.exists()
        assert (p / "worklog").is_dir()
        assert (p / "problem.md").exists()
        assert (p / "session.json").exists()

    def test_problem_wrapped_in_tags(self, tmp_path):
        session_dir = create_session_dir(
            problem="Is 17 prime?",
            domain="math",
            config=AgentConfig(verbose=False),
            base_dir=str(tmp_path),
        )
        content = (Path(session_dir) / "problem.md").read_text()
        assert "<problem_statement>" in content
        assert "Is 17 prime?" in content
        assert "</problem_statement>" in content

    def test_session_json_fields(self, tmp_path):
        config = AgentConfig(max_iterations=5, confidence_threshold=0.9, verbose=False)
        session_dir = create_session_dir(
            problem="test problem",
            domain="physics",
            config=config,
            base_dir=str(tmp_path),
        )
        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert data["status"] == "running"
        assert data["domain"] == "physics"
        assert data["current_iteration"] == 0
        assert data["best_confidence"] == 0.0
        assert data["config"]["max_iterations"] == 5

    def test_slug_generation(self, tmp_path):
        session_dir = create_session_dir(
            problem="Prove that sqrt(2) is irrational!!!",
            domain="math",
            config=AgentConfig(verbose=False),
            base_dir=str(tmp_path),
        )
        dirname = Path(session_dir).name
        assert dirname.startswith("prove-that-sqrt2-is-irrational")
        assert not dirname.startswith("-")


class TestWriteCheckpoint:
    def test_writes_session_json(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        session_json = {
            "status": "running",
            "current_iteration": 2,
            "best_confidence": 0.75,
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(session_json))

        write_checkpoint(
            session_dir=session_dir,
            current_iteration=3,
            best_confidence=0.85,
            best_solution_text="My solution",
            failed_approaches=["approach 1", "approach 2"],
            stall_state={"iterations_since_meaningful_improvement": 1},
            token_ledger=TokenLedger(input_tokens=5000, output_tokens=2000, api_calls=4),
            status="checkpoint",
        )

        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert data["status"] == "checkpoint"
        assert data["current_iteration"] == 3
        assert data["best_confidence"] == 0.85
        assert data["token_ledger"]["api_calls"] == 4

        best = (Path(session_dir) / "worklog" / "best_solution.md").read_text()
        assert best == "My solution"

    def test_checkpoint_error_on_failure(self, tmp_path):
        with pytest.raises(CheckpointError):
            write_checkpoint(
                session_dir=str(tmp_path / "nonexistent" / "deep" / "path"),
                current_iteration=1,
                best_confidence=0.5,
                best_solution_text=None,
                failed_approaches=[],
                stall_state={},
                token_ledger=TokenLedger(),
                status="checkpoint",
            )


class TestLoadCheckpoint:
    def test_load_running_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        data = {
            "status": "running",
            "problem": "test problem",
            "current_iteration": 2,
            "best_confidence": 0.8,
            "failed_approaches": ["approach 1"],
            "stall_state": {
                "iterations_since_meaningful_improvement": 1,
                "iteration_final_verdicts": ["major_flaw"],
                "resets_used": 0,
                "reset_cooldown_remaining": 0,
            },
            "token_ledger": {"input_tokens": 3000, "output_tokens": 1000, "api_calls": 3},
            "config": {"max_iterations": 5, "confidence_threshold": 0.9},
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(data))
        (Path(session_dir) / "worklog" / "best_solution.md").write_text("best so far")

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 2
        assert checkpoint["best_confidence"] == 0.8
        assert checkpoint["best_solution_text"] == "best so far"
        assert len(checkpoint["failed_approaches"]) == 1

    def test_load_checkpoint_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        data = {"status": "checkpoint", "current_iteration": 4, "best_confidence": 0.7,
                "failed_approaches": [], "stall_state": {}, "token_ledger": {},
                "config": {}, "problem": "test"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 4

    def test_reject_solved_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        data = {"status": "solved"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        with pytest.raises(ValueError, match="already completed"):
            load_checkpoint(session_dir)

    def test_reject_unsolved_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        data = {"status": "unsolved"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        with pytest.raises(ValueError, match="already completed"):
            load_checkpoint(session_dir)

    def test_reject_missing_session_json(self, tmp_path):
        with pytest.raises(ValueError, match="session.json"):
            load_checkpoint(str(tmp_path))


class TestScanIncompleteSessions:
    def test_finds_running_session(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "test-20260302-ab12"
        session_dir.mkdir()
        data = {"status": "running", "problem": "Is 17 prime?", "current_iteration": 2,
                "best_confidence": 0.7, "config": {"max_iterations": 5}}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 1
        assert results[0]["session_dir"] == str(session_dir)
        assert results[0]["problem"] == "Is 17 prime?"

    def test_finds_checkpoint_session(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "test-20260302-cd34"
        session_dir.mkdir()
        data = {"status": "checkpoint", "problem": "Prove X", "current_iteration": 5,
                "best_confidence": 0.88, "config": {"max_iterations": 8}}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 1

    def test_ignores_solved_sessions(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "done-20260302-ef56"
        session_dir.mkdir()
        data = {"status": "solved", "problem": "solved one"}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 0

    def test_empty_alethic_dir(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 0

    def test_nonexistent_dir(self, tmp_path):
        results = scan_incomplete_sessions(str(tmp_path / "nope"))
        assert len(results) == 0
