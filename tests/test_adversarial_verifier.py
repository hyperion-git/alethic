"""Tests for adversarial verifier self-correction (feature 2.7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig


class TestAdversarialConfig:
    """AgentConfig must have adversarial_self_correction field."""

    def test_default_is_false(self):
        config = AgentConfig()
        assert config.adversarial_self_correction is False

    def test_quick_preset_is_false(self):
        config = AgentConfig.from_preset("quick")
        assert config.adversarial_self_correction is False

    def test_default_preset_is_false(self):
        config = AgentConfig.from_preset("default")
        assert config.adversarial_self_correction is False

    def test_thorough_preset_is_true(self):
        config = AgentConfig.from_preset("thorough")
        assert config.adversarial_self_correction is True

    def test_extreme_preset_is_true(self):
        config = AgentConfig.from_preset("extreme")
        assert config.adversarial_self_correction is True


class TestAdversarialAddendum:
    """Adversarial verifier addendum must contain self-correction protocol."""

    def test_math_addendum_contains_round_2(self):
        from alethic.prompts import ADVERSARIAL_VERIFIER_ADDENDUM

        text = ADVERSARIAL_VERIFIER_ADDENDUM
        assert "Round 2" in text or "round 2" in text.lower()

    def test_math_addendum_contains_hallucination_check(self):
        from alethic.prompts import ADVERSARIAL_VERIFIER_ADDENDUM

        text = ADVERSARIAL_VERIFIER_ADDENDUM.lower()
        assert "hallucination" in text or "hallucinate" in text

    def test_math_addendum_contains_complete_proof_tag(self):
        from alethic.prompts import ADVERSARIAL_VERIFIER_ADDENDUM

        assert "COMPLETE PROOF" in ADVERSARIAL_VERIFIER_ADDENDUM

    def test_math_addendum_contains_structured_partial_tag(self):
        from alethic.prompts import ADVERSARIAL_VERIFIER_ADDENDUM

        assert "STRUCTURED PARTIAL PROGRESS" in ADVERSARIAL_VERIFIER_ADDENDUM

    def test_physics_addendum_contains_hallucination_check(self):
        from alethic.physics_prompts import PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM

        text = PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM.lower()
        assert "hallucination" in text or "hallucinate" in text


class TestVerifyExtraSystem:
    """verify() must accept and apply extra_system parameter."""

    @patch("alethic.subagents._call_model")
    def test_extra_system_appended_to_default(self, mock_call):
        from alethic.models import AgentConfig, Solution
        from alethic.subagents import verify

        mock_call.return_value = (
            "VERDICT: correct\nCONFIDENCE: 0.9\nCRITIQUE: Looks good.\n"
            "REASON: N/A\nISSUES: None"
        )
        config = AgentConfig(verbose=False)
        sol = Solution(problem="p", solution_text="s", iteration=1)
        client = MagicMock()

        verify(client, "p", sol, config, extra_system="EXTRA INSTRUCTIONS HERE")

        call_kwargs = mock_call.call_args
        system_used = call_kwargs[1]["system"] if call_kwargs[1] else call_kwargs[0][1]
        assert "EXTRA INSTRUCTIONS HERE" in system_used

    @patch("alethic.subagents._call_model")
    def test_extra_system_none_does_not_change_prompt(self, mock_call):
        from alethic.models import AgentConfig, Solution
        from alethic.prompts import VERIFIER_SYSTEM
        from alethic.subagents import verify

        mock_call.return_value = (
            "VERDICT: correct\nCONFIDENCE: 0.9\nCRITIQUE: Good.\n"
            "REASON: N/A\nISSUES: None"
        )
        config = AgentConfig(verbose=False)
        sol = Solution(problem="p", solution_text="s", iteration=1)
        client = MagicMock()

        verify(client, "p", sol, config, extra_system=None)

        call_kwargs = mock_call.call_args
        system_used = call_kwargs[1]["system"] if call_kwargs[1] else call_kwargs[0][1]
        assert system_used == VERIFIER_SYSTEM
