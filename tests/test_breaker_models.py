"""Tests for breaker-related model additions."""

from __future__ import annotations

import pytest

from alethic.models import AgentConfig, BreakerVerdict, EventType


class TestBreakerVerdict:
    def test_values(self):
        assert BreakerVerdict.FLAW_FOUND.value == "flaw_found"
        assert BreakerVerdict.SUSPECTED_FLAW.value == "suspected_flaw"
        assert BreakerVerdict.NO_FLAW_FOUND.value == "no_flaw_found"


class TestBreakerEventTypes:
    def test_breaker_event_types_exist(self):
        assert EventType.BREAKER_FLAW_FOUND.value == "breaker_flaw_found"
        assert EventType.BREAKER_SUSPECTED.value == "breaker_suspected"
        assert EventType.BREAKER_SURVIVED.value == "breaker_survived"


class TestAgentConfigBreakerFields:
    def test_default_breaker_off(self):
        config = AgentConfig()
        assert config.adversarial_breaker is False
        assert config.breaker_model is None
        assert config.breaker_temperature == 0.8

    def test_thorough_preset_breaker_on(self):
        config = AgentConfig.from_preset("thorough")
        assert config.adversarial_breaker is True
        assert config.breaker_model is None  # inherit the selected model

    def test_extreme_preset_breaker_on(self):
        config = AgentConfig.from_preset("extreme")
        assert config.adversarial_breaker is True

    def test_quick_preset_breaker_off(self):
        config = AgentConfig.from_preset("quick")
        assert config.adversarial_breaker is False

    def test_override_breaker_model(self):
        config = AgentConfig.from_preset("thorough", breaker_model="claude-haiku-4-5-20251001")
        assert config.breaker_model == "claude-haiku-4-5-20251001"

    def test_breaker_temperature_validation(self):
        with pytest.raises(ValueError):
            AgentConfig(breaker_temperature=-0.1)
