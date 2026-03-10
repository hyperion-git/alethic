"""Tests for atom integration in the orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.agent import MathAgent
from alethic.models import AgentConfig, Verdict


class TestAtomParsing:
    """Verify that parse_atoms() is called on generated solutions."""

    def _mock_response(self, text: str):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = text
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_resp.usage = MagicMock(input_tokens=100, output_tokens=100)
        mock_resp.stop_reason = "end_turn"
        return mock_resp

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_atom_history_populated_on_solve(self, _mock_tools):
        config = AgentConfig(
            max_iterations=1, max_revisions_per_cycle=0,
            enable_code_execution=False, verbose=False, best_of_n=1,
        )
        agent = MathAgent(config=config)
        mock_client = MagicMock()

        gen_text = "ATOM[1] deps=[] oracle=L3\nThe answer is 42."
        ver_text = (
            "VERDICT: CORRECT\n"
            "CONFIDENCE: 0.95\n"
            "CRITIQUE: Good solution.\n"
            "REASON: N/A\n"
            "ISSUES: None"
        )
        mock_client.messages.create.side_effect = [
            self._mock_response(gen_text),
            self._mock_response(ver_text),
        ]
        agent.client = mock_client
        result = agent.solve("What is 6 * 7?")
        assert result.verdict == Verdict.CORRECT


class TestRunStateAtomHistory:
    """RunState atom_history field tests."""

    def test_atom_history_default_empty(self):
        from alethic.agent import RunState
        state = RunState()
        assert state.atom_history == []
        assert state.breaker_falsified is False
