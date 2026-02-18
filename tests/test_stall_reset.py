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


class TestCheckStall:
    """Unit tests for _check_stall detection logic."""

    def _make_agent(self, **kwargs):
        from alethic.agent import MathAgent

        config = AgentConfig(enable_code_execution=False, verbose=False, **kwargs)
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        return agent

    def test_no_stall_when_disabled(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_reset=False)
        state = RunState()
        state.iterations_since_meaningful_improvement = 10
        assert agent._check_stall(state) is False

    def test_no_stall_on_cooldown(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.reset_cooldown_remaining = 1
        assert agent._check_stall(state) is False

    def test_no_stall_max_resets_exhausted(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2, max_iterations=5)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.resets_used = 1  # max(1, 5//4) = 1
        assert agent._check_stall(state) is False

    def test_stall_detected_no_progress(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 2
        assert agent._check_stall(state) is True

    def test_stall_detected_major_flaw_streak(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)  # high window, shouldn't trigger
        state = RunState()
        state.iterations_since_meaningful_improvement = 0  # no plateau
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent._check_stall(state) is True

    def test_no_stall_single_major_flaw(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent._check_stall(state) is False

    def test_no_stall_major_then_minor(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MINOR_ISSUES)
        assert agent._check_stall(state) is False


class TestBuildResetContext:
    """Unit tests for _build_reset_context prompt construction."""

    def _make_agent(self, **kwargs):
        from alethic.agent import MathAgent

        config = AgentConfig(enable_code_execution=False, verbose=False, **kwargs)
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        return agent

    def test_builds_context_with_last_two_approaches(self):
        agent = self._make_agent()
        approaches = ["Tried induction", "Tried contradiction", "Tried generating functions"]
        context = agent._build_reset_context(approaches)
        assert "STRATEGY RESET" in context
        # Should only include last 2
        assert "Tried induction" not in context
        assert "Tried contradiction" in context
        assert "Tried generating functions" in context

    def test_builds_context_with_fewer_than_two(self):
        agent = self._make_agent()
        context = agent._build_reset_context(["Only one"])
        assert "STRATEGY RESET" in context
        assert "Only one" in context

    def test_builds_context_empty_approaches(self):
        agent = self._make_agent()
        context = agent._build_reset_context([])
        assert "STRATEGY RESET" in context
