"""Tests for VerifierConfig.verification_ladder field (Task 7)."""

from alethic.models import VerifierConfig


def test_verifier_config_has_verification_ladder():
    cfg = VerifierConfig()
    assert hasattr(cfg, "verification_ladder")
    assert cfg.verification_ladder is True  # on by default


def test_verifier_config_quick_preset_verification_ladder():
    cfg = VerifierConfig.from_preset("quick")
    assert cfg.verification_ladder is True


def test_verifier_config_can_disable_ladder():
    cfg = VerifierConfig(verification_ladder=False)
    assert cfg.verification_ladder is False
