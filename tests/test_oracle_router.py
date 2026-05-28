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
            disproof_escalation=False,
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


class TestSaturation:
    """Patch #3 (PR #9) — saturation awareness injection.

    When the same critique category recurs SATURATION_TRIGGER_COUNT (=2) times
    across the loop's history, the next iteration's verifier system prompt is
    augmented with a saturation-awareness block. Only category labels and
    counts cross from prior iterations — never critique text. This preserves
    the decoupled-verification invariant.
    """

    @staticmethod
    def _make_router(saturation_fn=None):
        config = AgentConfig.from_preset("default")
        return OracleRouter(
            config=config,
            domain="physics",
            adversarial_addendum_fn=lambda: None,
            reset_addendum_fn=lambda: "RESET",
            saturation_addendum_fn=saturation_fn,
        )

    @staticmethod
    def _make_state(history):
        from alethic.agent import RunState
        state = RunState()
        state.critique_category_history = list(history)
        return state

    def test_saturation_signal_empty_history(self):
        router = self._make_router()
        state = self._make_state([])
        assert router.saturation_signal(state) == {}

    def test_saturation_signal_counts_by_category(self):
        router = self._make_router()
        state = self._make_state([(1, "algebra"), (2, "units"), (3, "algebra")])
        assert router.saturation_signal(state) == {"algebra": 2, "units": 1}

    def test_build_saturation_block_returns_none_without_fn(self):
        """Backwards-compat: no saturation_addendum_fn → no block ever."""
        router = self._make_router(saturation_fn=None)
        state = self._make_state([(1, "algebra"), (2, "algebra")])
        assert router._build_saturation_block(state) is None

    def test_build_saturation_block_below_threshold(self):
        """No category has SATURATION_TRIGGER_COUNT occurrences → no block."""
        router = self._make_router(saturation_fn=lambda: "ADDENDUM")
        state = self._make_state([(1, "algebra"), (2, "units")])
        assert router._build_saturation_block(state) is None

    def test_build_saturation_block_fires_at_threshold(self):
        """Same category SATURATION_TRIGGER_COUNT (=2) times → block returned."""
        addendum = (
            "## Loop Saturation Awareness\n"
            "<critique-category-history>\n{category_history}\n"
            "</critique-category-history>\n"
            "Top: {top_category}"
        )
        router = self._make_router(saturation_fn=lambda: addendum)
        state = self._make_state([(1, "algebra"), (3, "algebra")])
        block = router._build_saturation_block(state)
        assert block is not None
        assert "Loop Saturation Awareness" in block
        assert "algebra: 2 occurrence(s)" in block
        assert "Top: algebra" in block

    def test_build_saturation_block_picks_most_frequent_category(self):
        """When several categories appear, {top_category} is the highest-count one."""
        router = self._make_router(saturation_fn=lambda: "Top: {top_category}")
        state = self._make_state([
            (1, "units"), (2, "algebra"), (3, "algebra"), (4, "units"), (5, "algebra"),
        ])
        block = router._build_saturation_block(state)
        assert block is not None
        assert "Top: algebra" in block

    def test_route_injects_saturation_into_verifier_extra_system(self):
        """End-to-end: route() composes saturation block into verifier_extra_system."""
        marker = "## Loop Saturation Awareness MARKER_42"
        router = self._make_router(
            saturation_fn=lambda: marker + "\n{category_history}\nTop: {top_category}"
        )
        state = self._make_state([(1, "algebra"), (2, "algebra")])
        evidence = EvidenceState(
            iteration=3,
            best_confidence=0.5,
            error_category="algebra",
        )
        decision = router.route(state, evidence)
        assert decision.verifier_extra_system is not None
        assert marker in decision.verifier_extra_system

    def test_route_with_real_physics_addendum(self):
        """Integration: real PHYSICS_SATURATION_AWARENESS_ADDENDUM survives substitution."""
        from alethic.physics_prompts import PHYSICS_SATURATION_AWARENESS_ADDENDUM
        router = self._make_router(saturation_fn=lambda: PHYSICS_SATURATION_AWARENESS_ADDENDUM)
        state = self._make_state([(1, "units"), (2, "units"), (3, "units")])
        evidence = EvidenceState(
            iteration=4,
            best_confidence=0.5,
            error_category="units",
        )
        decision = router.route(state, evidence)
        assert decision.verifier_extra_system is not None
        assert "Loop Saturation Awareness" in decision.verifier_extra_system
        assert "units: 3 occurrence(s)" in decision.verifier_extra_system
        # Placeholder substitutions must have happened — no stray template syntax
        assert "{category_history}" not in decision.verifier_extra_system
        assert "{top_category}" not in decision.verifier_extra_system

    def test_route_no_saturation_when_history_below_threshold(self):
        """route() must NOT inject saturation block when threshold not met."""
        marker = "SATURATION_MARKER"
        router = self._make_router(saturation_fn=lambda: marker)
        state = self._make_state([(1, "algebra")])  # only 1 occurrence
        evidence = EvidenceState(
            iteration=2,
            best_confidence=0.5,
            error_category="algebra",
        )
        decision = router.route(state, evidence)
        if decision.verifier_extra_system is not None:
            assert marker not in decision.verifier_extra_system
