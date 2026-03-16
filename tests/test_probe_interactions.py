"""Probe A: Cross-feature interaction tests between v3.0-v3.4 features.

Naming convention: test_probe_a{N}_{description}

Probe points:
    A1 - Stall reset N and adaptive compute N are mutually exclusive (stall wins)
    A2 - Variant-B odd/even alternation survives dynamic N escalation
    A3 - Adaptive revision budget uses stale evidence_state after FIXABLE fallthrough (BUG)
    A4 - parse_layer_results() extracts sentinels from FIXABLE corrected solutions
    A5 - classify_errors() always reflects the winning candidate's critique
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.error_taxonomy import classify_errors, classify_errors_routed
from alethic.models import (
    AgentConfig,
    EvidenceState,
    EventType,
    Verdict,
)
from alethic.physics_checks import parse_layer_results
from alethic.subagents import _parse_verification


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str):
    """Create a minimal mock Anthropic response with a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    resp.usage = MagicMock()
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 200
    return resp


CORRECT_HIGH = "VERDICT: correct\nCONFIDENCE: 0.95\nCRITIQUE:\nPerfect.\nISSUES:\nNone"
MAJOR_FLAW_ALGEBRA = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.30\n"
    "CRITIQUE:\nThere is a sign error in the algebraic computation.\n"
    "ISSUES:\n- [MAJOR] Incorrect arithmetic step"
)
MAJOR_FLAW_LOGIC = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.30\n"
    "CRITIQUE:\nThe argument does not follow — invalid inference.\n"
    "ISSUES:\n- [MAJOR] The step does not follow logically"
)
MINOR_ALGEBRA_060 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.60\n"
    "CRITIQUE:\nSmall sign error in the last step.\n"
    "ISSUES:\n- [MINOR] Sign error"
)
MINOR_ALGEBRA_050 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.50\n"
    "CRITIQUE:\nSmall arithmetic error.\n"
    "ISSUES:\n- [MINOR] Arithmetic mistake"
)
MINOR_LOGIC_070 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.70\n"
    "CRITIQUE:\nThe inference at step 3 does not follow rigorously.\n"
    "ISSUES:\n- [MINOR] Logical gap"
)
FIXABLE_ALGEBRA_085 = (
    "VERDICT: fixable\nCONFIDENCE: 0.85\n"
    "CRITIQUE:\nSign error in final step but otherwise correct.\n"
    "ISSUES:\n- [MINOR] Sign error\n"
    "CORRECTED SOLUTION:\nCorrected derivation with sign fixed.\n"
    "END CORRECTED SOLUTION"
)
MAJOR_FLAW_LOGIC_FALLTHROUGH = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.40\n"
    "CRITIQUE:\nThe corrected solution has a gap: the argument does not follow.\n"
    "ISSUES:\n- [MAJOR] Invalid inference"
)
REVISER_RESP = "CHANGES MADE:\nFixed it.\n\nREVISED SOLUTION:\nRevised solution text"


# ---------------------------------------------------------------------------
# Probe A1: Stall reset N and adaptive compute N — stall always wins
# ---------------------------------------------------------------------------


class TestProbeA1StallWinsOverAdaptiveCompute:
    """Probe A1: The if/else at agent.py lines 713-752 ensures that when stall
    fires (is_reset=True), the adaptive compute branch never executes in the same
    iteration. The STALL_RESET n_override equals best_of_n + reset_n_boost.
    """

    def test_probe_a1_values_diverge_so_exclusion_matters(self):
        """_compute_dynamic_n(algebra) returns 1; stall N = best_of_n+boost = 4.

        Confirms the two paths produce different N values, so the mutual exclusion
        at lines 713-752 is semantically meaningful.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=3,
            reset_n_boost=1,
            adaptive_compute=True,
            enable_code_execution=False,
            verbose=False,
        )
        agent = MathAgent(config=config)

        adaptive_n = agent.router._compute_dynamic_n(
            EvidenceState(iteration=2, best_confidence=0.30, error_category="algebra")
        )
        stall_n = config.best_of_n + config.reset_n_boost

        assert adaptive_n == 1, f"algebra -> adaptive N=1, got {adaptive_n}"
        assert stall_n == 4, f"stall N = 3+1 = 4, got {stall_n}"
        assert adaptive_n != stall_n, "Values must differ for the exclusion to matter"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_a1_stall_n_logged_in_event_not_adaptive_n(self, _mock_tools):
        """Integration: stall fires on iter 3 (two consecutive MAJOR_FLAW).

        With best_of_n=3, reset_n_boost=1, adaptive_compute=True:
          - algebra error -> _compute_dynamic_n returns 1 (revise-first)
          - stall fires -> n_this_iter = 3+1 = 4 (NOT adaptive's 1)

        The STALL_RESET event must log n_override=4.
        API call count: iter1(3+3) + iter2(1+1) + iter3(4+4) = 16 total.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            max_revisions_per_cycle=0,
            best_of_n=3,
            reset_n_boost=1,
            stall_window=10,           # only major_flaw_streak triggers stall
            stall_reset=True,
            adaptive_compute=True,
            adaptive_revision_budget=False,
            enable_code_execution=False,
            verbose=False,
            confidence_threshold=0.90,
        )

        responses = [
            # Iter 1: N=3 (adaptive inactive on iter 1, uses best_of_n=3)
            _mock_response("A1"), _mock_response("A2"), _mock_response("A3"),
            _mock_response(MAJOR_FLAW_ALGEBRA),  # verify A1
            _mock_response(MAJOR_FLAW_ALGEBRA),  # verify A2
            _mock_response(MAJOR_FLAW_ALGEBRA),  # verify A3
            # Iter 2: adaptive active, algebra -> N=1
            _mock_response("B1"),
            _mock_response(MAJOR_FLAW_ALGEBRA),  # verify B1
            # Iter 3: STALL fires (2x MAJOR_FLAW streak) -> N=4, NOT adaptive's N=1
            _mock_response("C1"), _mock_response("C2"),
            _mock_response("C3"), _mock_response("C4"),
            _mock_response(CORRECT_HIGH),        # winner
            _mock_response(MAJOR_FLAW_ALGEBRA),
            _mock_response(MAJOR_FLAW_ALGEBRA),
            _mock_response(MAJOR_FLAW_ALGEBRA),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        with patch("alethic.agent.create_session_dir", return_value=None):
            agent = MathAgent(config=config)
            agent.client = mock_client
            result = agent.solve("Prove something")

        assert result.solved

        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 1
        assert reset_events[0].data["n_override"] == 4, (
            f"Expected n_override=4 (best_of_n+reset_n_boost=4), "
            f"got {reset_events[0].data['n_override']}. "
            "Stall reset N must win over adaptive compute's N=1."
        )
        assert mock_client.messages.create.call_count == 16, (
            f"Expected 16 calls (6+2+8), got {mock_client.messages.create.call_count}"
        )


# ---------------------------------------------------------------------------
# Probe A2: Variant-B alternation with escalated N
# ---------------------------------------------------------------------------


class TestProbeA2VariantBWithEscalatedN:
    """Probe A2: _generate_candidates uses i % 2 == 1 for i in range(n).
    With n=4 (escalated by dynamic N), candidates 1 and 3 get variant-B config.
    Same-model variant-B reuses self.client; different-model creates a new one.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_a2_n4_same_model_reuses_primary_client(self, _mock_tools):
        """N=4, same-model variant-B: all 4 candidates use the same client.
        No new Anthropic() is created during solve(). Total: 4 gen + 4 verify = 8 calls.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=4,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # same as primary
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("C0"), _mock_response("C1"),
            _mock_response("C2"), _mock_response("C3"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_ALGEBRA_060),
            _mock_response(MINOR_ALGEBRA_060),
            _mock_response(MINOR_ALGEBRA_060),
        ]

        # Create agent outside the Anthropic patch so __init__'s client
        # creation doesn't count against the variant-B assertion.
        with patch("alethic.agent.create_session_dir", return_value=None):
            agent = MathAgent(config=config)
            agent.client = mock_client
            agent._api_key = "test-key"
            with patch("alethic.agent.anthropic.Anthropic") as mock_cls:
                result = agent.solve("test")

        mock_cls.assert_not_called()
        assert result.solved
        assert mock_client.messages.create.call_count == 8

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_a2_n4_different_model_creates_variant_client_for_odd_indices(
        self, _mock_tools
    ):
        """N=4, different-model variant-B: new Anthropic() created once during solve().
        Variant client handles candidates at odd indices (1, 3) = 2 calls.
        Primary client handles candidates at even indices (0, 2) + all verifications.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=4,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )

        primary = MagicMock(name="primary")
        variant = MagicMock(name="variant")

        # Primary: candidates 0,2 (even) + all 4 verifications
        primary.messages.create.side_effect = [
            _mock_response("C0"), _mock_response("C2"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_ALGEBRA_060),
            _mock_response(MINOR_ALGEBRA_060),
            _mock_response(MINOR_ALGEBRA_060),
        ]
        # Variant: candidates 1,3 (odd)
        variant.messages.create.side_effect = [
            _mock_response("C1"), _mock_response("C3"),
        ]

        # Create agent outside the Anthropic patch so __init__'s client
        # creation doesn't count against the variant-B assertion.
        with patch("alethic.agent.create_session_dir", return_value=None):
            agent = MathAgent(config=config)
            agent.client = primary
            agent._api_key = "test-key"
            with patch("alethic.agent.anthropic.Anthropic", return_value=variant) as mock_cls:
                result = agent.solve("test")

        mock_cls.assert_called_once_with(api_key="test-key")
        assert variant.messages.create.call_count == 2, (
            f"Variant client should handle 2 candidates (odd indices 1,3), "
            f"got {variant.messages.create.call_count}"
        )
        assert result.solved


# ---------------------------------------------------------------------------
# Probe A3: FIXABLE fallthrough uses stale evidence_state (BUG)
# ---------------------------------------------------------------------------


class TestProbeA3FixableFallthroughStaleEvidence:
    """Probe A3: evidence_state is set at agent.py line 842 from the original
    verification. After FIXABLE correction fails re-verification (line 962),
    the revision loop at line 969-970 uses the stale evidence_state.error_category.

    BUG: when original="algebra" (budget=1) and re-verification="logic" (budget=3),
    only 1 revision fires. The fix is to update evidence_state after failed re-verify.
    """

    def test_probe_a3_original_algebra_and_reverify_logic_diverge(self):
        """Unit: original FIXABLE critique classifies as 'algebra'; re-verification
        critique classifies as 'logic'. They diverge, proving the stale-category bug.
        """
        original = _parse_verification(FIXABLE_ALGEBRA_085)
        re_verif = _parse_verification(MAJOR_FLAW_LOGIC_FALLTHROUGH)

        assert classify_errors(original.critique) == "algebra"
        assert classify_errors(re_verif.critique) == "logic"

    def test_probe_a3_stale_algebra_evidence_yields_fewer_revisions_than_logic(self):
        """Unit: _compute_adaptive_revisions with stale algebra evidence returns 1,
        but with correct logic evidence returns 3. Quantifies the under-revision.
        """
        from alethic.agent import MathAgent

        agent = MathAgent(config=AgentConfig(
            max_revisions_per_cycle=3,
            enable_code_execution=False,
            verbose=False,
        ))

        stale = EvidenceState(iteration=1, best_confidence=0.85, error_category="algebra")
        fresh = EvidenceState(iteration=1, best_confidence=0.85, error_category="logic")

        assert agent.router.revision_budget(stale) == 1, (
            "algebra + conf>=0.80 -> budget=1"
        )
        assert agent.router.revision_budget(fresh) == 3, (
            "logic + conf>=0.70 -> base budget=3"
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_a3_only_one_revision_fires_due_to_stale_evidence(self, _mock_tools):
        """Integration: FIXABLE (algebra, 0.85) -> re-verify fails (logic MAJOR_FLAW)
        -> revision loop receives budget=1 from stale algebra evidence_state.

        Observable: exactly 1 REVISE event fires despite max_revisions_per_cycle=3
        and the logic error warranting 3 revisions.

        If this assertion fails with 3 REVISE events, the bug was fixed.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=3,
            best_of_n=1,
            adaptive_revision_budget=True,
            adaptive_compute=False,
            stall_reset=False,
            enable_code_execution=False,
            verbose=False,
            confidence_threshold=0.90,
        )

        responses = [
            _mock_response("Initial solution"),           # generate
            _mock_response(FIXABLE_ALGEBRA_085),          # verify -> FIXABLE algebra
            _mock_response(MAJOR_FLAW_LOGIC_FALLTHROUGH), # re-verify corrected -> logic fail
            _mock_response(REVISER_RESP),                 # revise (budget=1 from stale algebra)
            _mock_response(MAJOR_FLAW_LOGIC),             # re-verify after revision -> still fails
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        with patch("alethic.agent.create_session_dir", return_value=None):
            agent = MathAgent(config=config)
            agent.client = mock_client
            result = agent.solve("test problem")

        revise_events = [e for e in result.events if e.type == EventType.REVISE]

        # Documents the bug: 1 revision fires (stale algebra) instead of 3 (correct logic)
        assert len(revise_events) == 1, (
            f"Expected 1 revision (stale algebra evidence -> budget=1), "
            f"got {len(revise_events)}. "
            "If 3 revisions fired, the stale evidence_state bug was fixed."
        )
        assert mock_client.messages.create.call_count == 5, (
            f"Expected 5 API calls (gen+verify+reverify+revise+reverify), "
            f"got {mock_client.messages.create.call_count}"
        )


# ---------------------------------------------------------------------------
# Probe A4: parse_layer_results() on FIXABLE corrected solutions
# ---------------------------------------------------------------------------


class TestProbeA4SentinelsInFixableCorrectedSolution:
    """Probe A4: parse_layer_results() is a pure line-by-line regex scanner.
    It correctly extracts ALETHIC_L{N}_CHECK: sentinels from any text string,
    including the corrected_solution field of a FIXABLE VerificationResult.
    """

    def test_probe_a4_three_layer_sentinels_extracted_from_corrected_solution(self):
        """Full FIXABLE response with L0, L1, L2 sentinels in corrected solution."""
        raw = (
            "VERDICT: fixable\nCONFIDENCE: 0.85\n"
            "CRITIQUE:\nSign error in step 3 only.\n"
            "ISSUES:\n- [MINOR] Sign error\n"
            "CORRECTED SOLUTION:\n"
            "Step 1: derivation text\n"
            "ALETHIC_L0_CHECK: DIMENSIONS OK\n"
            "Step 2: corrected calculation\n"
            "ALETHIC_L1_CHECK: LIMIT nonrelativistic OK\n"
            "Step 3: final result\n"
            "ALETHIC_L2_CHECK: CONSISTENCY OK (1.000000==1.000000)\n"
            "END CORRECTED SOLUTION"
        )

        result = _parse_verification(raw)
        assert result.verdict == Verdict.FIXABLE
        assert result.corrected_solution is not None

        layers = parse_layer_results(result.corrected_solution)

        assert 0 in layers, "Layer 0 sentinel must be found"
        assert 1 in layers, "Layer 1 sentinel must be found"
        assert 2 in layers, "Layer 2 sentinel must be found"
        assert "DIMENSIONS OK" in layers[0][0]
        assert "LIMIT nonrelativistic OK" in layers[1][0]
        assert "CONSISTENCY OK" in layers[2][0]

    def test_probe_a4_no_sentinels_returns_empty_dict(self):
        """Corrected solution with no sentinels yields {}."""
        assert parse_layer_results("x = 1\ny = 2\nResult: 3") == {}

    def test_probe_a4_single_sentinel_extracted(self):
        """Only Layer 0 in corrected solution: extracted correctly."""
        text = "ALETHIC_L0_CHECK: STRUCTURE OK\nSome derivation."
        layers = parse_layer_results(text)
        assert layers == {0: ["STRUCTURE OK"]}, f"Got {layers}"

    def test_probe_a4_multiple_sentinels_same_layer_all_collected(self):
        """Two sentinels at the same layer level are both collected into the list."""
        text = (
            "ALETHIC_L1_CHECK: BASE CASES OK (n=0..4)\n"
            "Some text.\n"
            "ALETHIC_L1_CHECK: BASE CASES OK (n=5..10)\n"
        )
        layers = parse_layer_results(text)
        assert len(layers[1]) == 2
        assert "n=0..4" in layers[1][0]
        assert "n=5..10" in layers[1][1]

    def test_probe_a4_sentinel_before_end_marker_is_captured(self):
        """Sentinel in the corrected solution block (before END CORRECTED SOLUTION) is found."""
        raw = (
            "VERDICT: fixable\nCONFIDENCE: 0.80\nCRITIQUE:\nFix needed.\nISSUES:\nNone\n"
            "CORRECTED SOLUTION:\n"
            "ALETHIC_L0_CHECK: STRUCTURE OK\n"
            "END CORRECTED SOLUTION"
        )
        result = _parse_verification(raw)
        assert result.corrected_solution is not None
        layers = parse_layer_results(result.corrected_solution)
        assert layers == {0: ["STRUCTURE OK"]}, f"Got {layers}"


# ---------------------------------------------------------------------------
# Probe A5: Error taxonomy always uses the winning candidate's critique
# ---------------------------------------------------------------------------


class TestProbeA5TaxonomyUsesWinnerCritique:
    """Probe A5: classify_errors at agent.py line 841 is called on verified[0].critique.
    After _verify_candidates() sorts by confidence descending, verified[0] is the
    highest-confidence candidate. The winning candidate's critique always drives
    the taxonomy and downstream dynamic N computation.
    """

    def test_probe_a5_algebra_vs_logic_produce_different_dynamic_n(self):
        """_compute_dynamic_n returns different N for algebra (1) vs logic (3).

        This means using the wrong candidate's critique changes the N on the next
        iteration, making correct taxonomy routing semantically important.
        """
        from alethic.agent import MathAgent

        agent = MathAgent(config=AgentConfig(
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        ))

        n_algebra = agent.router._compute_dynamic_n(
            EvidenceState(iteration=1, best_confidence=0.60, error_category="algebra")
        )
        n_logic = agent.router._compute_dynamic_n(
            EvidenceState(iteration=1, best_confidence=0.60, error_category="logic")
        )

        assert n_algebra == 1, f"algebra -> N=1 (revise-first), got {n_algebra}"
        assert n_logic == 3, f"logic -> N=3 (escalate), got {n_logic}"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_a5_winner_logic_critique_causes_n3_on_iter2(self, _mock_tools):
        """Integration: iter1 has 3 candidates. Candidate with logic critique (0.70)
        beats algebra candidates (0.60, 0.50) and becomes the winner.

        Winner's 'logic' error -> _compute_dynamic_n returns N=3 on iter 2.
        Bug case (loser's algebra used): N=1 -> 2 fewer gen+verify calls on iter 2.

        Expected total: iter1(3+3) + iter2(3+3) = 12 API calls.
        Bug total:      iter1(3+3) + iter2(1+1) = 8 API calls.
        """
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=0,
            best_of_n=3,
            adaptive_compute=True,
            adaptive_revision_budget=False,
            stall_reset=False,
            enable_code_execution=False,
            verbose=False,
            confidence_threshold=0.90,
        )

        responses = [
            # Iter 1: N=3
            _mock_response("C0"), _mock_response("C1"), _mock_response("C2"),
            # Verify: logic winner (0.70) > algebra (0.60) > algebra (0.50)
            _mock_response(MINOR_ALGEBRA_060),   # 0.60 loser — algebra
            _mock_response(MINOR_LOGIC_070),     # 0.70 WINNER — logic
            _mock_response(MINOR_ALGEBRA_050),   # 0.50 loser — algebra
            # Iter 2: logic -> N=3 (escalate), NOT N=1 (algebra revise-first)
            _mock_response("D0"), _mock_response("D1"), _mock_response("D2"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_ALGEBRA_060),
            _mock_response(MINOR_ALGEBRA_060),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        with patch("alethic.agent.create_session_dir", return_value=None):
            agent = MathAgent(config=config)
            agent.client = mock_client
            result = agent.solve("test")

        assert result.solved
        assert mock_client.messages.create.call_count == 12, (
            f"Expected 12 calls (logic winner -> N=3 escalation on iter 2). "
            f"Got {mock_client.messages.create.call_count}. "
            "If 8: algebra loser's critique was used (wrong); "
            "if 12: logic winner's critique was used (correct)."
        )

    def test_probe_a5_logic_routes_to_adversarial_oracle(self):
        """classify_errors_routed with logic critique returns adversarial oracle."""
        from alethic.models import OracleType

        cat, oracle, force_adv = classify_errors_routed(
            "The argument does not follow — there is a non sequitur."
        )
        assert cat == "logic"
        assert oracle == OracleType.LAYER3_LLM_ADVERSARIAL
        assert force_adv is True

    def test_probe_a5_algebra_routes_to_consistency_oracle_no_adversarial(self):
        """classify_errors_routed with algebra critique returns consistency oracle."""
        from alethic.models import OracleType

        cat, oracle, force_adv = classify_errors_routed(
            "There is a sign error in the arithmetic step."
        )
        assert cat == "algebra"
        assert oracle == OracleType.LAYER2_CONSISTENCY
        assert force_adv is False
