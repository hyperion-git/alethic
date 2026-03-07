"""Tests for adaptive compute — dynamic N and adaptive revision budget (feature 2.4)."""

from alethic.models import AgentConfig


def test_default_preset_has_adaptive_revision_not_compute():
    cfg = AgentConfig.from_preset("default")
    assert cfg.adaptive_revision_budget is True
    assert cfg.adaptive_compute is False


def test_thorough_preset_has_adaptive_compute():
    cfg = AgentConfig.from_preset("thorough")
    assert cfg.adaptive_compute is True
    assert cfg.adaptive_revision_budget is False  # thorough uses N escalation, not budget adaptation


def test_extreme_preset_has_adaptive_compute():
    cfg = AgentConfig.from_preset("extreme")
    assert cfg.adaptive_compute is True


def test_quick_preset_no_adaptive():
    cfg = AgentConfig.from_preset("quick")
    assert cfg.adaptive_compute is False
    assert cfg.adaptive_revision_budget is False
