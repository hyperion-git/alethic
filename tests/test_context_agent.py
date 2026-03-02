"""Tests for context monitoring integration in MathAgent."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alethic.agent import MathAgent
from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
from alethic.models import AgentConfig, Verdict


def _mock_response(text: str, stop_reason: str = "end_turn",
                   input_tokens: int = 500, output_tokens: int = 200):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


CORRECT_HIGH = (
    "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
    "CRITIQUE:\nPerfect.\n\nISSUES:\nNone"
)


class TestTokenLedgerIntegration:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_solve_populates_ledger(self, _ptc, tmp_path):
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("solution", input_tokens=2000, output_tokens=1000),
            _mock_response(CORRECT_HIGH, input_tokens=3000, output_tokens=800),
        ]

        with patch("alethic.agent.create_session_dir", return_value=str(tmp_path / "session")):
            Path(tmp_path / "session" / "worklog").mkdir(parents=True)
            result = agent.solve("test problem")

        assert result.token_ledger is not None
        assert result.token_ledger.api_calls == 2
        assert result.token_ledger.input_tokens == 5000
        assert result.token_ledger.output_tokens == 1800

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_session_dir_populated(self, _ptc, tmp_path):
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("solution"),
            _mock_response(CORRECT_HIGH),
        ]

        session_dir = str(tmp_path / "session")
        with patch("alethic.agent.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            result = agent.solve("test problem")

        assert result.session_dir == session_dir


class TestContextExhaustedCheckpoint:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_checkpoint_on_context_exhaustion(self, _ptc, tmp_path):
        """When ContextExhaustedError fires, agent checkpoints and returns."""
        config = AgentConfig(
            max_iterations=5, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()

        # Iter 1: generate succeeds, verify returns minor_issues
        # Iter 2: generate raises ContextExhaustedError
        minor = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.7\n\n"
            "CRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
        )
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response("solution v1")
            elif call_count == 2:
                return _mock_response(minor)
            else:
                raise ContextExhaustedError("context full")

        agent.client.messages.create.side_effect = side_effect

        session_dir = str(tmp_path / "session")
        with patch("alethic.agent.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            with patch("alethic.agent.write_checkpoint") as mock_cp:
                result = agent.solve("test problem")

        assert result.verdict == Verdict.UNSOLVED
        assert not result.admitted_failure
        assert result.checkpoint_path is not None
        mock_cp.assert_called_once()


class TestTruncatedResponseHandling:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generator_truncation_skips_candidate(self, _ptc, tmp_path):
        """A truncated generator response should skip that candidate, not crash."""
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.return_value = _mock_response(
            "partial", stop_reason="max_tokens"
        )

        session_dir = str(tmp_path / "session")
        with patch("alethic.agent.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            result = agent.solve("test problem")

        # Agent should fail gracefully, not crash
        assert result.verdict == Verdict.UNSOLVED


class TestResumeFromCheckpoint:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_resume_starts_from_saved_iteration(self, _ptc, tmp_path):
        """Resume should start from current_iteration + 1."""
        config = AgentConfig(
            max_iterations=5, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("resumed solution"),
            _mock_response(CORRECT_HIGH),
        ]

        # Create a checkpoint to resume from
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir(parents=True)
        (Path(session_dir) / "worklog").mkdir()
        checkpoint_data = {
            "status": "checkpoint",
            "problem": "test problem",
            "current_iteration": 3,
            "best_confidence": 0.7,
            "failed_approaches": ["first try failed"],
            "stall_state": {
                "iterations_since_meaningful_improvement": 1,
                "iteration_final_verdicts": ["major_flaw"],
                "resets_used": 0,
                "reset_cooldown_remaining": 0,
            },
            "token_ledger": {"input_tokens": 10000, "output_tokens": 5000, "api_calls": 8},
            "config": {"max_iterations": 5, "confidence_threshold": 0.9},
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(checkpoint_data))
        (Path(session_dir) / "worklog" / "best_solution.md").write_text("old best")

        result = agent.solve("test problem", resume_from=session_dir)

        assert result.solved
        assert result.iterations_used == 4  # resumed at iter 4 (3+1)
        assert len(result.failed_approaches) >= 1  # inherited from checkpoint
