"""Tests for stochastic reset / stall detection feature."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig, EventType, Verdict


def _mock_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


CORRECT_HIGH = "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nPerfect.\n\nISSUES:\nNone"
MINOR_060 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.60\n\nCRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
)
MAJOR_020 = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.20\n\nCRITIQUE:\nWrong approach.\n\nISSUES:\n- Logic error"
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
        assert agent.router.check_stall(state) is False

    def test_no_stall_on_cooldown(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.reset_cooldown_remaining = 1
        assert agent.router.check_stall(state) is False

    def test_no_stall_max_resets_exhausted(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2, max_iterations=5)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.resets_used = 1  # max(1, 5//4) = 1
        assert agent.router.check_stall(state) is False

    def test_stall_detected_no_progress(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 2
        assert agent.router.check_stall(state) is True

    def test_stall_detected_major_flaw_streak(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)  # high window, shouldn't trigger
        state = RunState()
        state.iterations_since_meaningful_improvement = 0  # no plateau
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent.router.check_stall(state) is True

    def test_no_stall_single_major_flaw(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent.router.check_stall(state) is False

    def test_no_stall_major_then_minor(self):
        from alethic.agent import RunState

        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MINOR_ISSUES)
        assert agent.router.check_stall(state) is False


class TestBuildResetContext:
    """Unit tests for _build_reset_context prompt construction."""

    def _make_agent(self, **kwargs):
        from alethic.agent import MathAgent

        config = AgentConfig(enable_code_execution=False, verbose=False, **kwargs)
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        return agent

    def _make_state(self, approaches):
        from alethic.agent import RunState
        state = RunState()
        state.failed_approaches = approaches
        return state

    def test_builds_context_with_last_five_approaches(self):
        agent = self._make_agent()
        # 6 approaches — only last 5 should appear
        approaches = [
            "Tried direct proof",
            "Tried induction",
            "Tried contradiction",
            "Tried generating functions",
            "Tried algebraic geometry",
            "Tried combinatorics",
        ]
        context = agent.router.build_reset_context(self._make_state(approaches))
        assert "STRATEGY RESET" in context
        # Should only include last 5
        assert "Tried direct proof" not in context
        assert "Tried induction" in context
        assert "Tried combinatorics" in context

    def test_builds_context_with_fewer_than_two(self):
        agent = self._make_agent()
        context = agent.router.build_reset_context(self._make_state(["Only one"]))
        assert "STRATEGY RESET" in context
        assert "Only one" in context

    def test_builds_context_empty_approaches(self):
        agent = self._make_agent()
        context = agent.router.build_reset_context(self._make_state([]))
        assert "STRATEGY RESET" in context


class TestStallResetIntegration:
    """Integration tests: full solve() loop with mocked confidence trajectories."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_plateau_triggers_reset(self, _mock_tools):
        """Confidence plateau (0.6, 0.6) should trigger reset on iteration 3.

        Iter 1 always improves from 0.0, so stall_window=1 means one non-improving
        iteration (iter 2) is enough to trigger on iter 3.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=4,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_window=1,
            stall_epsilon=0.03,
            stall_reset=True,
            reset_n_boost=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iter 1: gen -> verify (0.6 minor) -> revise -> re-verify (0.6 minor)
            _mock_response("Attempt 1"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV1"),
            _mock_response(MINOR_060),
            # Iter 2: gen -> verify (0.6 minor) -> revise -> re-verify (0.6 minor)
            _mock_response("Attempt 2"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV2"),
            _mock_response(MINOR_060),
            # Iter 3 (RESET): gen x2 (N=1+1=2) -> verify x2 -> correct
            _mock_response("Fresh attempt A"),
            _mock_response("Fresh attempt B"),
            _mock_response(MINOR_060),
            _mock_response(CORRECT_HIGH),  # candidate B nails it
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        # Should have a STALL_RESET event
        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 1
        assert reset_events[0].iteration == 3

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_major_flaw_streak_triggers_reset(self, _mock_tools):
        """Two consecutive MAJOR_FLAW should trigger reset."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=4,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_window=10,  # High — so only major-flaw detector fires
            stall_reset=True,
            reset_n_boost=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iter 1: gen -> verify (major) -> revise -> re-verify (major) -> break
            _mock_response("Bad 1"),
            _mock_response(MAJOR_020),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nStill bad"),
            _mock_response(MAJOR_020),
            # Iter 2: gen -> verify (major) -> revise -> re-verify (major) -> break
            _mock_response("Bad 2"),
            _mock_response(MAJOR_020),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nStill bad"),
            _mock_response(MAJOR_020),
            # Iter 3 (RESET — major flaw streak): gen x2 -> verify x2 -> correct
            _mock_response("Fresh A"),
            _mock_response("Fresh B"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_060),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 1
        assert reset_events[0].data["reason"] == "major_flaw_streak"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_disabled_stall_reset_no_trigger(self, _mock_tools):
        """With stall_reset=False, no STALL_RESET events should appear."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_reset=False,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            _mock_response("A1"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV1"),
            _mock_response(MINOR_060),
            _mock_response("A2"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV2"),
            _mock_response(MINOR_060),
            _mock_response("A3"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV3"),
            _mock_response(MINOR_060),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test")

        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 0


class TestNegativePrompting:
    """1.5: Stall reset prompts must use explicit prohibition language."""

    def _make_state(self, approaches):
        from alethic.agent import RunState
        state = RunState()
        state.failed_approaches = approaches
        return state

    def test_reset_context_uses_do_not_language(self):
        from alethic.agent import MathAgent
        from alethic.models import AgentConfig

        agent = MathAgent(config=AgentConfig(verbose=False))
        ctx = agent.router.build_reset_context(self._make_state(["tried induction", "tried contradiction"]))
        assert "DO NOT" in ctx, "Reset context must contain explicit 'DO NOT' prohibition"

    def test_reset_context_lists_all_recent_approaches(self):
        from alethic.agent import MathAgent
        from alethic.models import AgentConfig

        agent = MathAgent(config=AgentConfig(verbose=False))
        approaches = ["a1", "a2", "a3", "a4", "a5", "a6"]
        ctx = agent.router.build_reset_context(self._make_state(approaches))
        # Should include last 5, not just last 2
        assert "a2" in ctx
        assert "a6" in ctx
        assert "a1" not in ctx  # oldest entry pruned at 5-cap

    def test_physics_reset_context_uses_do_not_language(self):
        from alethic.models import AgentConfig
        from alethic.physics_agent import PhysicsAgent

        agent = PhysicsAgent(config=AgentConfig(verbose=False))
        ctx = agent.router.build_reset_context(self._make_state(["tried Lagrangian"]))
        assert "DO NOT" in ctx
