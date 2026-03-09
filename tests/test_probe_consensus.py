"""Probe H: Consensus pipeline stress cases.

Probes:
    H1 - All K verifiers crash -> RuntimeError, not empty ConsensusResult
    H2 - K=1 consensus: majority-vote, mean, confidence_range all correct
    H3 - Mixed FIXABLE verdicts: corrected_solution not propagated to consensus (design gap)
    H4 - Empty issues from all verifiers: aggregation handles gracefully
    H5 - Synthesis API failure: fallback concatenation produces valid ConsensusResult
    H6 - Domain auto-detection tie: deterministic tiebreak to "math"
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.domain import detect_domain
from alethic.models import (
    ConsensusIssue,
    ConsensusResult,
    Issue,
    IssueSeverity,
    Verdict,
    VerificationResult,
    VerifierConfig,
)
from alethic.synthesizer import aggregate_mechanical, synthesize_critique
from alethic.verifier_agent import CheckerAgent, VerifierAgent


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_response(text: str) -> MagicMock:
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


def _make_vr(
    verdict: Verdict = Verdict.CORRECT,
    confidence: float = 0.90,
    critique: str = "ok",
    issues: list[Issue] | None = None,
    corrected_solution: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        critique=critique,
        confidence=confidence,
        issues=issues or [],
        corrected_solution=corrected_solution,
    )


# ===========================================================================
# H1: All K verifiers crash -> RuntimeError
# ===========================================================================


class TestH1AllVerifiersCrash:
    """When every verifier future raises an exception, _run_consensus must
    raise RuntimeError rather than returning an empty/invalid ConsensusResult."""

    @patch("alethic.verifier_agent.verify_subagent")
    def test_all_k_crash_raises_runtime_error(self, mock_verify):
        """All K=3 verifiers raise -> RuntimeError with descriptive message."""
        mock_verify.side_effect = RuntimeError("API overloaded")

        config = VerifierConfig(num_verifiers=3, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        with pytest.raises(RuntimeError, match="All 3 verifiers failed"):
            agent.verify(problem="Is 1+1=2?", solution="Yes.")

    @patch("alethic.verifier_agent.verify_subagent")
    def test_all_k1_crash_raises_runtime_error(self, mock_verify):
        """K=1, single verifier crashes -> RuntimeError."""
        mock_verify.side_effect = ValueError("parse error")

        config = VerifierConfig(num_verifiers=1, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        with pytest.raises(RuntimeError, match="All 1 verifiers failed"):
            agent.verify(problem="Test", solution="Answer")

    @patch("alethic.verifier_agent.verify_subagent")
    def test_checker_all_crash_raises_runtime_error(self, mock_verify):
        """CheckerAgent: all K crash -> RuntimeError."""
        mock_verify.side_effect = Exception("boom")

        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = CheckerAgent(config=config, api_key="test-key")

        with pytest.raises(RuntimeError, match="All 2 verifiers failed"):
            agent.check(solution="Some derivation")

    @patch("alethic.verifier_agent.verify_subagent")
    def test_partial_crash_still_produces_result(self, mock_verify):
        """If 1 of 3 crashes, the pipeline should still produce a result from 2."""
        call_count = 0
        good = _make_vr(Verdict.CORRECT, 0.92, "All good.")

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Verifier 1 died")
            return good

        mock_verify.side_effect = _side_effect

        synth_resp = _mock_response("Synthesized critique.")
        config = VerifierConfig(num_verifiers=3, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        agent.client = MagicMock()
        agent.client.messages.create.return_value = synth_resp

        result = agent.verify(problem="Test", solution="Answer")
        assert isinstance(result, ConsensusResult)
        # Should have 2 individual results (1 crashed)
        assert result.num_verifiers == 2
        assert len(result.individual_results) == 2


# ===========================================================================
# H2: K=1 consensus — majority vote, mean, and range are all well-defined
# ===========================================================================


class TestH2SingleVerifierConsensus:
    """With num_verifiers=1, all aggregation logic should still produce
    valid results: majority vote of one, mean of one, min==max range."""

    def test_k1_majority_vote_is_sole_verdict(self):
        results = [_make_vr(Verdict.MINOR_ISSUES, 0.75)]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.MINOR_ISSUES

    def test_k1_confidence_equals_sole_value(self):
        results = [_make_vr(Verdict.CORRECT, 0.88)]
        agg = aggregate_mechanical(results)
        assert agg["confidence"] == 0.88

    def test_k1_confidence_range_is_degenerate(self):
        results = [_make_vr(Verdict.CORRECT, 0.88)]
        agg = aggregate_mechanical(results)
        assert agg["confidence_range"] == (0.88, 0.88)

    def test_k1_issues_preserved(self):
        results = [
            _make_vr(
                Verdict.MINOR_ISSUES,
                0.75,
                issues=[Issue(text="Typo in eq 3", severity=IssueSeverity.MINOR)],
            )
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 1
        assert agg["issues"][0].text == "Typo in eq 3"
        assert agg["issues"][0].flagged_by == 1

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_k1_full_pipeline(self, mock_verify, mock_synth):
        """End-to-end K=1 pipeline produces a valid ConsensusResult."""
        mock_verify.return_value = _make_vr(Verdict.CORRECT, 0.93, "Looks right.")
        mock_synth.return_value = "Single verifier agrees."

        config = VerifierConfig(num_verifiers=1, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(problem="Is 2+2=4?", solution="Yes.")

        assert result.verdict == Verdict.CORRECT
        assert result.confidence == 0.93
        assert result.num_verifiers == 1
        assert result.consensus_ratio == "1/1"


# ===========================================================================
# H3: Mixed FIXABLE verdicts — corrected_solution not in ConsensusResult
# ===========================================================================


class TestH3MixedFixableVerdicts:
    """When 2 of 3 verifiers return FIXABLE with different corrected solutions,
    the consensus pipeline does NOT propagate any corrected_solution to the
    ConsensusResult. This is a design gap: the caller has no way to pick
    a correction without inspecting individual_results."""

    def test_fixable_majority_verdict(self):
        """2/3 FIXABLE should produce FIXABLE majority verdict."""
        results = [
            _make_vr(Verdict.FIXABLE, 0.65, "Fix sign", corrected_solution="x = +1"),
            _make_vr(Verdict.FIXABLE, 0.70, "Fix factor", corrected_solution="x = 2"),
            _make_vr(Verdict.CORRECT, 0.90, "Looks good"),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.FIXABLE

    def test_consensus_result_has_no_corrected_solution_field(self):
        """ConsensusResult does not have a corrected_solution attribute.

        This documents the design gap: when FIXABLE is the majority verdict
        with different corrections, the caller must dig into
        individual_results to find corrections.
        """
        assert not hasattr(ConsensusResult, "corrected_solution"), (
            "If ConsensusResult now has corrected_solution, "
            "update this test and verify the selection logic."
        )

    def test_individual_results_retain_corrections(self):
        """Even though ConsensusResult lacks corrected_solution,
        individual_results preserve the per-verifier corrections."""
        r1 = _make_vr(Verdict.FIXABLE, 0.65, "Fix sign", corrected_solution="x = +1")
        r2 = _make_vr(Verdict.FIXABLE, 0.70, "Fix factor", corrected_solution="x = 2")
        r3 = _make_vr(Verdict.CORRECT, 0.90, "Looks good")
        results = [r1, r2, r3]
        agg = aggregate_mechanical(results)

        # Build a ConsensusResult the way verifier_agent.py does:
        cr = ConsensusResult(
            verdict=agg["verdict"],
            confidence=agg["confidence"],
            confidence_range=agg["confidence_range"],
            critique="test",
            issues=agg["issues"],
            individual_results=results,
            domain_detected="math",
            num_verifiers=3,
        )
        # Corrections are accessible via individual_results
        fixable_results = [r for r in cr.individual_results if r.has_correction]
        assert len(fixable_results) == 2
        corrections = {r.corrected_solution for r in fixable_results}
        assert corrections == {"x = +1", "x = 2"}

    def test_fixable_no_correction_not_has_correction(self):
        """A FIXABLE verdict without corrected_solution: has_correction is False."""
        r = _make_vr(Verdict.FIXABLE, 0.65, "Fix needed", corrected_solution=None)
        assert not r.has_correction


# ===========================================================================
# H4: Empty issues from all verifiers
# ===========================================================================


class TestH4EmptyIssues:
    """When all verifiers return zero issues (but possibly different verdicts),
    aggregation must handle empty issue lists without crashing."""

    def test_all_correct_no_issues(self):
        results = [
            _make_vr(Verdict.CORRECT, 0.95, "Perfect", issues=[]),
            _make_vr(Verdict.CORRECT, 0.92, "Excellent", issues=[]),
            _make_vr(Verdict.CORRECT, 0.90, "Good", issues=[]),
        ]
        agg = aggregate_mechanical(results)
        assert agg["issues"] == []
        assert agg["verdict"] == Verdict.CORRECT

    def test_mixed_verdicts_no_issues(self):
        """Different verdicts but no issues should still aggregate cleanly."""
        results = [
            _make_vr(Verdict.CORRECT, 0.90, "ok", issues=[]),
            _make_vr(Verdict.MINOR_ISSUES, 0.80, "hmm", issues=[]),
        ]
        agg = aggregate_mechanical(results)
        assert agg["issues"] == []
        # Tie broken by severity: MINOR_ISSUES is more severe than CORRECT
        assert agg["verdict"] == Verdict.MINOR_ISSUES

    def test_single_verifier_no_issues(self):
        results = [_make_vr(Verdict.CORRECT, 0.95, "All good", issues=[])]
        agg = aggregate_mechanical(results)
        assert agg["issues"] == []

    def test_issues_sorted_returns_empty_list_not_none(self):
        """Ensure the result is an empty list, not None or some other falsy value."""
        results = [_make_vr(Verdict.CORRECT, 0.90, "ok", issues=[])]
        agg = aggregate_mechanical(results)
        assert agg["issues"] is not None
        assert isinstance(agg["issues"], list)
        assert len(agg["issues"]) == 0


# ===========================================================================
# H5: Synthesis API failure — fallback concatenation
# ===========================================================================


class TestH5SynthesisFallback:
    """When the LLM synthesis call fails, the fallback should concatenate
    raw critiques and produce a valid string that populates ConsensusResult."""

    def test_api_exception_falls_back_to_concatenation(self):
        """synthesize_critique should catch the exception and concatenate."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        results = [
            _make_vr(Verdict.CORRECT, 0.90, "Proof is valid."),
            _make_vr(Verdict.MINOR_ISSUES, 0.80, "Minor notation issue."),
        ]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        # Fallback format: "--- Verifier N ---\n{critique}"
        assert "--- Verifier 1 ---" in critique
        assert "--- Verifier 2 ---" in critique
        assert "Proof is valid." in critique
        assert "Minor notation issue." in critique

    def test_fallback_critique_is_nonempty_string(self):
        """Fallback should never be empty or None."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("timeout")

        results = [_make_vr(Verdict.CORRECT, 0.90, "Good.")]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        assert isinstance(critique, str)
        assert len(critique) > 0

    @patch("alethic.verifier_agent.verify_subagent")
    def test_full_pipeline_with_synthesis_failure(self, mock_verify):
        """End-to-end: synthesis fails but ConsensusResult is still valid."""
        mock_verify.return_value = _make_vr(Verdict.CORRECT, 0.91, "Looks correct.")

        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        # Make the synthesis API call fail
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = RuntimeError("API down")

        result = agent.verify(problem="Test", solution="Answer")
        assert isinstance(result, ConsensusResult)
        assert result.verdict == Verdict.CORRECT
        assert result.confidence == 0.91
        # Critique should be the fallback concatenation
        assert "--- Verifier 1 ---" in result.critique
        assert "Looks correct." in result.critique
        assert result.num_verifiers == 2

    def test_synthesis_fallback_with_empty_critique(self):
        """Verifier with empty critique string should not crash fallback."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("fail")

        results = [
            _make_vr(Verdict.CORRECT, 0.90, ""),
            _make_vr(Verdict.CORRECT, 0.88, "Fine."),
        ]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        assert isinstance(critique, str)
        assert "--- Verifier 1 ---" in critique
        assert "--- Verifier 2 ---" in critique

    def test_synthesis_no_issues_text_uses_none_placeholder(self):
        """When there are no issues, issues_text should be 'None' string
        (the str fallback in synthesize_critique's `or 'None'`)."""
        mock_client = MagicMock()
        mock_resp = _mock_response("Clean critique.")
        mock_client.messages.create.return_value = mock_resp

        results = [_make_vr(Verdict.CORRECT, 0.95, "Perfect.", issues=[])]
        agg = aggregate_mechanical(results)

        critique = synthesize_critique(mock_client, results, agg)
        # The call should succeed. Verify the user message sent to the API
        # contains "None" for issues.
        call_args = mock_client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "None" in user_msg


# ===========================================================================
# H6: Domain auto-detection edge cases
# ===========================================================================


class TestH6DomainTieBreaker:
    """When physics and math scores are exactly equal, detect_domain()
    should deterministically default to 'math'."""

    def test_empty_text_defaults_to_math(self):
        assert detect_domain("") == "math"

    def test_whitespace_only_defaults_to_math(self):
        assert detect_domain("   \n\t  ") == "math"

    def test_no_keywords_defaults_to_math(self):
        """Text with zero domain keywords should return 'math'."""
        assert detect_domain("The quick brown fox jumps over the lazy dog.") == "math"

    def test_override_physics(self):
        assert detect_domain("pure algebra problem", override="physics") == "physics"

    def test_override_math(self):
        assert detect_domain("Hamiltonian mechanics problem", override="math") == "math"

    def test_override_invalid_raises(self):
        with pytest.raises(ValueError, match="override must be"):
            detect_domain("anything", override="chemistry")

    def test_tie_defaults_to_math(self):
        """Craft text that should produce equal math and physics scores,
        then verify the tiebreaker is deterministic (math wins)."""
        # Use the actual code path: if physics > math return physics, else math
        # So a tie (equal scores) returns math. We verify this with a direct
        # check of the logic, since crafting a perfect tie from the ~1080 keyword
        # dictionary is fragile.
        # Instead, patch the scoring to verify the branch:
        from alethic import domain as domain_mod

        original_load = domain_mod._load_patterns

        def _fake_patterns():
            """Return two patterns that will both match, one for each domain."""
            import re
            return [
                ("math", 3, re.compile(r"\btestword\b")),
                ("physics", 3, re.compile(r"\btestword\b")),
            ]

        domain_mod._PATTERNS = None  # force reload
        old = domain_mod._PATTERNS
        try:
            domain_mod._PATTERNS = _fake_patterns()
            result = detect_domain("testword")
            assert result == "math", f"Tie should default to math, got {result}"
        finally:
            domain_mod._PATTERNS = None  # reset cache so other tests reload

    def test_physics_wins_when_strictly_greater(self):
        """When physics score > math score, should return 'physics'."""
        from alethic import domain as domain_mod
        import re

        try:
            domain_mod._PATTERNS = [
                ("physics", 3, re.compile(r"\bhamiltonian\b")),
                ("physics", 2, re.compile(r"\benergy\b")),
                ("math", 1, re.compile(r"\bproof\b")),
            ]
            result = detect_domain("Hamiltonian energy proof")
            assert result == "physics"
        finally:
            domain_mod._PATTERNS = None

    def test_math_wins_when_strictly_greater(self):
        """When math score > physics score, should return 'math'."""
        from alethic import domain as domain_mod
        import re

        try:
            domain_mod._PATTERNS = [
                ("math", 3, re.compile(r"\btheorem\b")),
                ("math", 3, re.compile(r"\blemma\b")),
                ("physics", 1, re.compile(r"\bforce\b")),
            ]
            result = detect_domain("theorem lemma force")
            assert result == "math"
        finally:
            domain_mod._PATTERNS = None

    def test_deterministic_across_calls(self):
        """Same input should always produce the same output."""
        text = "Consider the eigenvalue problem for the operator."
        results = [detect_domain(text) for _ in range(10)]
        assert len(set(results)) == 1, f"Non-deterministic results: {results}"


# ===========================================================================
# Additional edge cases discovered during analysis
# ===========================================================================


class TestConsensusResultProperties:
    """Verify ConsensusResult helper properties work under stress."""

    def test_consensus_ratio_unanimous(self):
        r = _make_vr(Verdict.CORRECT, 0.90)
        cr = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.90,
            confidence_range=(0.90, 0.90),
            critique="ok",
            issues=[],
            individual_results=[r, r, r],
            domain_detected="math",
            num_verifiers=3,
        )
        assert cr.consensus_ratio == "3/3"

    def test_consensus_ratio_split(self):
        cr = ConsensusResult(
            verdict=Verdict.MINOR_ISSUES,
            confidence=0.80,
            confidence_range=(0.70, 0.90),
            critique="mixed",
            issues=[],
            individual_results=[
                _make_vr(Verdict.MINOR_ISSUES, 0.80),
                _make_vr(Verdict.CORRECT, 0.90),
                _make_vr(Verdict.MINOR_ISSUES, 0.70),
            ],
            domain_detected="math",
            num_verifiers=3,
        )
        assert cr.consensus_ratio == "2/3"

    def test_consensus_ratio_empty_individual_results(self):
        """Edge case: if individual_results is somehow empty."""
        cr = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.90,
            confidence_range=(0.90, 0.90),
            critique="ok",
            issues=[],
            individual_results=[],
            domain_detected="math",
            num_verifiers=0,
        )
        assert cr.consensus_ratio == "0/0"

    def test_to_dict_serializable(self):
        """ConsensusResult.to_dict() should produce JSON-serializable output."""
        import json

        cr = ConsensusResult(
            verdict=Verdict.CORRECT,
            confidence=0.90,
            confidence_range=(0.88, 0.92),
            critique="All good",
            issues=[ConsensusIssue(text="Minor note", severity=IssueSeverity.MINOR, flagged_by=2)],
            individual_results=[_make_vr(Verdict.CORRECT, 0.90, "ok")],
            domain_detected="math",
            num_verifiers=1,
            elapsed_seconds=1.5,
        )
        d = cr.to_dict()
        # Should not raise
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["verdict"] == "correct"
        assert parsed["confidence"] == 0.90


class TestIssueDeduplication:
    """Edge cases in issue deduplication via SequenceMatcher."""

    def test_exact_duplicate_issues_merged(self):
        results = [
            _make_vr(
                Verdict.MINOR_ISSUES, 0.80,
                issues=[Issue(text="Sign error in equation 5", severity=IssueSeverity.MAJOR)],
            ),
            _make_vr(
                Verdict.MINOR_ISSUES, 0.80,
                issues=[Issue(text="Sign error in equation 5", severity=IssueSeverity.MAJOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 1
        assert agg["issues"][0].flagged_by == 2

    def test_similar_issues_merged(self):
        """Issues that are similar enough (>0.6 ratio) should merge."""
        results = [
            _make_vr(
                Verdict.MINOR_ISSUES, 0.80,
                issues=[Issue(text="Sign error in equation 5", severity=IssueSeverity.MAJOR)],
            ),
            _make_vr(
                Verdict.MINOR_ISSUES, 0.80,
                issues=[Issue(text="Sign error in equation five", severity=IssueSeverity.MINOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        # Should merge since they are similar enough
        assert len(agg["issues"]) == 1
        assert agg["issues"][0].flagged_by == 2
        # Severity should escalate to most severe
        assert agg["issues"][0].severity == IssueSeverity.MAJOR

    def test_dissimilar_issues_kept_separate(self):
        """Issues that are too different should remain separate."""
        results = [
            _make_vr(
                Verdict.MAJOR_FLAW, 0.40,
                issues=[Issue(text="Division by zero in step 3", severity=IssueSeverity.CRITICAL)],
            ),
            _make_vr(
                Verdict.MAJOR_FLAW, 0.40,
                issues=[Issue(text="Missing edge case for n=0", severity=IssueSeverity.MAJOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 2
        assert all(i.flagged_by == 1 for i in agg["issues"])
