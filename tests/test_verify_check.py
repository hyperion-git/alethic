"""Tests for VerifierAgent and CheckerAgent."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from alethic.models import (
    ConsensusResult,
    Verdict,
    VerificationResult,
    VerifierConfig,
)
from alethic.verifier_agent import CheckerAgent, VerifierAgent


class TestVerifierAgent:
    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_verify_runs_k_verifiers(self, mock_verify, mock_synth):
        """verify() should call the verify subagent K times."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "Synthesized critique"

        config = VerifierConfig(num_verifiers=3, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(problem="Is 1+1=2?", solution="Yes, 1+1=2.")

        assert mock_verify.call_count == 3
        assert isinstance(result, ConsensusResult)
        assert result.num_verifiers == 3
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_verify_returns_consensus_fields(self, mock_verify, mock_synth):
        """ConsensusResult should have all expected fields populated."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.91
        )
        mock_synth.return_value = "All good"

        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(problem="Test", solution="Answer")

        assert result.confidence_range == (0.91, 0.91)
        assert result.critique == "All good"
        assert result.elapsed_seconds >= 0
        assert len(result.individual_results) == 2

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_domain_auto_detected(self, mock_verify, mock_synth):
        """Domain should be auto-detected from solution text."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(
            problem="Derive the energy levels",
            solution="Starting from the Hamiltonian H = p²/2m + V(x)...",
        )
        assert result.domain_detected == "physics"

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_domain_override(self, mock_verify, mock_synth):
        """Explicit domain should override auto-detection."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=2, domain="math", verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        result = agent.verify(
            problem="Derive the energy levels",
            solution="Starting from the Hamiltonian...",
        )
        assert result.domain_detected == "math"

    def test_verify_check_raises_not_implemented(self):
        """VerifierAgent.check() should raise."""
        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = VerifierAgent(config=config, api_key="test-key")
        with pytest.raises(NotImplementedError):
            agent.check(solution="anything")


class TestCheckerAgent:
    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_check_runs_without_problem(self, mock_verify, mock_synth):
        """check() should work with solution only."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="valid", confidence=0.88
        )
        mock_synth.return_value = "Looks valid"

        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = CheckerAgent(config=config, api_key="test-key")
        result = agent.check(solution="2+2=4 because of Peano axioms...")

        assert mock_verify.call_count == 2
        assert isinstance(result, ConsensusResult)

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_check_uses_checker_prompts(self, mock_verify, mock_synth):
        """check() should use CHECKER_SYSTEM, not VERIFIER_SYSTEM."""
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="valid", confidence=0.90
        )
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=1, verbose=False)
        agent = CheckerAgent(config=config, api_key="test-key")
        agent.check(solution="Some derivation...")

        # Inspect the system_prompt kwarg passed to verify_subagent
        call_kwargs = mock_verify.call_args
        system_used = call_kwargs.kwargs.get("system_prompt", call_kwargs[1].get("system_prompt", ""))
        assert "proof auditor" in system_used.lower() or "internally valid" in system_used.lower()

    def test_checker_verify_raises_not_implemented(self):
        """CheckerAgent.verify() should raise."""
        config = VerifierConfig(num_verifiers=2, verbose=False)
        agent = CheckerAgent(config=config, api_key="test-key")
        with pytest.raises(NotImplementedError):
            agent.verify(problem="test", solution="test")
