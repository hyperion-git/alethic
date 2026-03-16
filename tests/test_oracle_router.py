"""Tests for OracleRouter — consolidated routing logic."""

import pytest

from alethic.models import AgentConfig, EvidenceState, OracleType, Verdict, VerificationResult
from alethic.oracle_router import OracleRouter, RoutingDecision


class TestRoutingDecision:
    def test_frozen(self):
        """RoutingDecision should be immutable."""
        d = RoutingDecision(
            n_candidates=2,
            is_reset=False,
            reset_context=None,
            verifier_extra_system=None,
            next_oracle=OracleType.LAYER3_LLM,
            force_adversarial=False,
        )
        with pytest.raises(AttributeError):
            d.n_candidates = 5


class TestOracleRouterConstruction:
    def test_default_construction(self):
        config = AgentConfig()
        router = OracleRouter(
            config=config,
            domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "",
        )
        assert router._config is config
        assert router._domain == "math"


class TestRouteFirstIteration:
    def test_route_no_evidence_returns_defaults(self):
        """First iteration (no evidence): default N, no reset, no forced adversarial."""
        config = AgentConfig.from_preset("default")
        router = OracleRouter(
            config=config,
            domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "RESET: {failed_approaches}{atom_stability_context}",
        )
        from alethic.agent import RunState
        state = RunState()
        decision = router.route(state, evidence=None)
        assert decision.n_candidates == config.best_of_n
        assert decision.is_reset is False
        assert decision.reset_context is None


class TestRoutePureFunction:
    def test_same_input_same_output(self):
        """route() called twice with identical input must return identical output."""
        config = AgentConfig.from_preset("default")
        router = OracleRouter(
            config=config,
            domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "RESET: {failed_approaches}{atom_stability_context}",
        )
        from alethic.agent import RunState
        state = RunState()
        d1 = router.route(state, evidence=None)
        d2 = router.route(state, evidence=None)
        assert d1 == d2


class TestRouteStallReset:
    def test_stall_triggers_reset(self):
        """When stall is detected, is_reset=True and N is boosted."""
        config = AgentConfig.from_preset("default")  # stall_window=2
        router = OracleRouter(
            config=config,
            domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "RESET: {failed_approaches}{atom_stability_context}",
        )
        from alethic.agent import RunState
        state = RunState()
        state.iterations_since_meaningful_improvement = config.stall_window  # trigger
        decision = router.route(state, evidence=None)
        assert decision.is_reset is True
        assert decision.n_candidates == config.best_of_n + config.reset_n_boost
        assert decision.reset_context is not None


class TestRankCandidates:
    def test_selects_highest_confidence(self):
        config = AgentConfig()
        router = OracleRouter(
            config=config, domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "",
        )
        vs = [
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="a", confidence=0.6),
            VerificationResult(verdict=Verdict.CORRECT, critique="b", confidence=0.95),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="c", confidence=0.8),
        ]
        assert router.rank_candidates(vs) == 1


class TestDeadCodeActivation:
    def test_oracle_routing_active(self):
        """next_oracle should reflect _ORACLE_ROUTING table from error_taxonomy."""
        config = AgentConfig.from_preset("default")
        router = OracleRouter(
            config=config, domain="math",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "RESET: {failed_approaches}{atom_stability_context}",
        )
        from alethic.agent import RunState
        state = RunState()
        evidence = EvidenceState(
            iteration=2,
            best_confidence=0.7,
            error_category="logic",
        )
        decision = router.route(state, evidence)
        # logic → LAYER3_LLM_ADVERSARIAL, force_adversarial=True
        assert decision.next_oracle == OracleType.LAYER3_LLM_ADVERSARIAL
        assert decision.force_adversarial is True
