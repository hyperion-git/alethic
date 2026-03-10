"""Tests for adversarial breaker integration in the orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.agent import MathAgent
from alethic.models import AgentConfig, EventType, Verdict


class TestBreakerIntegration:

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
    def test_breaker_survived_accepts_solution(self, _mock_tools):
        config = AgentConfig(
            max_iterations=1, max_revisions_per_cycle=0,
            enable_code_execution=False, verbose=False,
            adversarial_breaker=True, breaker_model="claude-sonnet-4-6",
        )
        agent = MathAgent(config=config)
        mock_client = MagicMock()

        gen_text = "ATOM[1] deps=[] oracle=L3\nProof that sqrt(2) is irrational."
        ver_text = "VERDICT: CORRECT\nCONFIDENCE: 0.95\nCRITIQUE: Correct.\nREASON: N/A\nISSUES: None"
        breaker_text = "BREAKER_VERDICT: NO_FLAW_FOUND\nTARGET_ATOM: 0\nFLAW_TYPE: none\nEVIDENCE: None.\nREASONING: Proof is valid."

        mock_client.messages.create.side_effect = [
            self._mock_response(gen_text),
            self._mock_response(ver_text),
            self._mock_response(breaker_text),
        ]
        agent.client = mock_client
        result = agent.solve("Prove sqrt(2) is irrational")
        assert result.verdict == Verdict.CORRECT
        breaker_events = [e for e in result.events if e.type == EventType.BREAKER_SURVIVED]
        assert len(breaker_events) == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_breaker_flaw_demotes_to_major_flaw(self, _mock_tools):
        config = AgentConfig(
            max_iterations=2, max_revisions_per_cycle=0,
            enable_code_execution=False, verbose=False,
            adversarial_breaker=True, breaker_model="claude-sonnet-4-6",
        )
        agent = MathAgent(config=config)
        mock_client = MagicMock()

        gen_text = "ATOM[1] deps=[] oracle=L3\nBad proof."
        ver_correct = "VERDICT: CORRECT\nCONFIDENCE: 0.95\nCRITIQUE: Looks good.\nREASON: N/A\nISSUES: None"
        breaker_flaw = "BREAKER_VERDICT: FLAW_FOUND\nTARGET_ATOM: 1\nFLAW_TYPE: counterexample\nEVIDENCE: n=0 fails.\nREASONING: Base case wrong."
        # After demotion, iter 2: generate, verify (give up)
        gen_text_2 = "ATOM[1] deps=[] oracle=L3\nNew attempt."
        ver_text_2 = "VERDICT: MAJOR_FLAW\nCONFIDENCE: 0.4\nCRITIQUE: Still wrong.\nREASON: N/A\nISSUES: [MAJOR] Logic error"

        mock_client.messages.create.side_effect = [
            self._mock_response(gen_text),
            self._mock_response(ver_correct),
            self._mock_response(breaker_flaw),
            self._mock_response(gen_text_2),
            self._mock_response(ver_text_2),
        ]
        agent.client = mock_client
        result = agent.solve("Prove something")
        flaw_events = [e for e in result.events if e.type == EventType.BREAKER_FLAW_FOUND]
        assert len(flaw_events) == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_breaker_skipped_for_monolithic(self, _mock_tools):
        """Breaker should not fire when solution has no atom annotations."""
        config = AgentConfig(
            max_iterations=1, max_revisions_per_cycle=0,
            enable_code_execution=False, verbose=False,
            adversarial_breaker=True, breaker_model="claude-sonnet-4-6",
        )
        agent = MathAgent(config=config)
        mock_client = MagicMock()

        gen_text = "Plain solution with no atom markers."
        ver_text = "VERDICT: CORRECT\nCONFIDENCE: 0.95\nCRITIQUE: Good.\nREASON: N/A\nISSUES: None"

        mock_client.messages.create.side_effect = [
            self._mock_response(gen_text),
            self._mock_response(ver_text),
        ]
        agent.client = mock_client
        result = agent.solve("Simple problem")
        assert result.verdict == Verdict.CORRECT
        # Only 2 API calls (gen + verify), no breaker
        assert mock_client.messages.create.call_count == 2
