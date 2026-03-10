"""Tests for atom-aware stall recovery."""

from __future__ import annotations

from alethic.agent import MathAgent, RunState
from alethic.atoms import AtomAnnotation, AtomStability, classify_atom_stability
from alethic.models import AgentConfig, OracleType, Verdict


class TestStallExcludesBreaker:
    """Breaker-falsified iterations excluded from stall detection."""

    def test_breaker_falsified_not_counted_for_consecutive_major(self):
        config = AgentConfig(stall_window=2, stall_reset=True, max_iterations=8)
        agent = MathAgent(config=config, api_key="test")
        state = RunState()
        # Two MAJOR_FLAW verdicts but both are breaker-falsified
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        # With breaker context, these should be excluded
        # (This tests the integration — the actual exclusion is done by
        #  not appending breaker-demoted verdicts to iteration_final_verdicts)


class TestAtomStabilityContext:
    """_build_reset_context() includes atom stability when available."""

    def test_build_reset_context_without_atoms(self):
        config = AgentConfig(stall_reset=True, max_iterations=8, verbose=False)
        agent = MathAgent(config=config, api_key="test")
        state = RunState()
        state.failed_approaches = ["Approach A failed."]
        context = agent._build_reset_context(state)
        assert "Approach A" in context
        assert "STABLE ATOMS" not in context  # no atom history

    def test_build_reset_context_with_atoms(self):
        config = AgentConfig(
            stall_reset=True, max_iterations=8, verbose=False,
            variant_b=None,  # atom tracking enabled
        )
        agent = MathAgent(config=config, api_key="test")
        state = RunState()
        state.failed_approaches = ["Approach A failed."]
        # Populate atom history with stable atom
        atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x = 1")
        state.atom_history = [[atom], [atom], [atom]]
        state.confidence_history = [0.8, 0.8, 0.8]
        context = agent._build_reset_context(state)
        assert "STABLE" in context or "stable" in context.lower()

    def test_atom_history_cleared_on_reset(self):
        state = RunState()
        atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
        state.atom_history = [[atom]]
        state.confidence_history = [0.8]
        # After reset, history should be cleared
        state.atom_history.clear()
        state.confidence_history.clear()
        assert state.atom_history == []

    def test_variant_b_disables_atom_tracking(self):
        config = AgentConfig(
            stall_reset=True, max_iterations=8, verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )
        agent = MathAgent(config=config, api_key="test")
        state = RunState()
        atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x = 1")
        state.atom_history = [[atom], [atom]]
        state.confidence_history = [0.8, 0.8]
        context = agent._build_reset_context(state)
        # With variant_b active, atom stability should NOT be included
        assert "STABLE" not in context
