"""Tests for stochastic reset / stall detection feature."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.models import AgentConfig, EventType, Verdict


def _mock_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


CORRECT_HIGH = (
    "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
    "CRITIQUE:\nPerfect.\n\nISSUES:\nNone"
)
MINOR_060 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.60\n\n"
    "CRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
)
MAJOR_020 = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.20\n\n"
    "CRITIQUE:\nWrong approach.\n\nISSUES:\n- Logic error"
)


class TestRevisionLoopMaxRevisions:
    """_run_revision_loop should respect max_revisions parameter."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_max_revisions_override_limits_revisions(self, _mock_tools):
        from alethic.agent import EventLog, MathAgent, RunState

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=3,
            enable_code_execution=False,
            verbose=False,
        )
        agent = MathAgent(config=config)

        mock_client = MagicMock()
        # Only 1 revision + 1 re-verify should happen (not 3)
        mock_client.messages.create.side_effect = [
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nFixed"),
            _mock_response(CORRECT_HIGH),
        ]
        agent.client = mock_client

        from alethic.models import Solution
        from alethic.subagents import _parse_verification

        state = RunState()
        log = EventLog()
        solution = Solution(problem="test", solution_text="original", iteration=1)
        verification = _parse_verification(MINOR_060)

        result = agent._run_revision_loop(
            problem="test",
            solution=solution,
            verification=verification,
            prompts={},
            iteration=1,
            state=state,
            log=log,
            threshold=0.90,
            max_revisions=1,
        )

        assert result is not None
        assert result.solved
        assert state.total_revisions == 1
