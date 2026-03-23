"""Tests for TokenLedger and context-related model changes."""

from unittest.mock import MagicMock

import pytest

from alethic.models import MODEL_CONTEXT_LIMITS, AgentConfig, AgentResult, TokenLedger, Verdict


class TestTokenLedger:
    def test_initial_state(self):
        ledger = TokenLedger()
        assert ledger.input_tokens == 0
        assert ledger.output_tokens == 0
        assert ledger.api_calls == 0
        assert ledger.total_tokens == 0

    def test_record_usage(self):
        ledger = TokenLedger()
        usage = MagicMock()
        usage.input_tokens = 1500
        usage.output_tokens = 500
        ledger.record(usage)

        assert ledger.input_tokens == 1500
        assert ledger.output_tokens == 500
        assert ledger.api_calls == 1
        assert ledger.total_tokens == 2000

    def test_record_accumulates(self):
        ledger = TokenLedger()
        for _i in range(3):
            usage = MagicMock()
            usage.input_tokens = 1000
            usage.output_tokens = 400
            ledger.record(usage)

        assert ledger.input_tokens == 3000
        assert ledger.output_tokens == 1200
        assert ledger.api_calls == 3
        assert ledger.total_tokens == 4200

    def test_to_dict(self):
        ledger = TokenLedger()
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50
        ledger.record(usage)
        d = ledger.to_dict()
        assert d == {"input_tokens": 100, "output_tokens": 50, "api_calls": 1}

    def test_from_dict(self):
        d = {"input_tokens": 2000, "output_tokens": 800, "api_calls": 5}
        ledger = TokenLedger.from_dict(d)
        assert ledger.input_tokens == 2000
        assert ledger.output_tokens == 800
        assert ledger.api_calls == 5

    def test_from_dict_empty(self):
        ledger = TokenLedger.from_dict({})
        assert ledger.input_tokens == 0
        assert ledger.output_tokens == 0
        assert ledger.api_calls == 0


class TestModelContextLimits:
    def test_known_models(self):
        assert MODEL_CONTEXT_LIMITS["claude-opus-4-6"] == 1_000_000
        assert MODEL_CONTEXT_LIMITS["claude-sonnet-4-6"] == 1_000_000
        assert MODEL_CONTEXT_LIMITS["claude-haiku-4-5-20251001"] == 200_000

    def test_default_fallback(self):
        assert MODEL_CONTEXT_LIMITS.get("unknown-model", 200_000) == 200_000


class TestAgentConfigContextThreshold:
    def test_default_threshold(self):
        config = AgentConfig()
        assert config.context_threshold == 0.8

    def test_custom_threshold(self):
        config = AgentConfig(context_threshold=0.9)
        assert config.context_threshold == 0.9

    def test_threshold_validation(self):
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=1.5)
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=-0.1)

    def test_preset_preserves_default(self):
        config = AgentConfig.from_preset("quick")
        assert config.context_threshold == 0.85  # preset overrides

    def test_explicit_override(self):
        config = AgentConfig.from_preset("quick", context_threshold=0.7)
        assert config.context_threshold == 0.7


class TestAgentResultNewFields:
    def test_token_ledger_default(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
        )
        assert result.token_ledger is None
        assert result.session_dir is None
        assert result.checkpoint_path is None

    def test_token_ledger_populated(self):
        ledger = TokenLedger(input_tokens=5000, output_tokens=2000, api_calls=3)
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            token_ledger=ledger,
            session_dir="/tmp/alethic-test/",
        )
        assert result.token_ledger.total_tokens == 7000
        assert result.session_dir == "/tmp/alethic-test/"

    def test_checkpoint_path(self):
        result = AgentResult(
            problem="test",
            solution=None,
            verdict=Verdict.UNSOLVED,
            confidence=0.7,
            iterations_used=3,
            total_revisions=2,
            admitted_failure=False,
            checkpoint_path="/tmp/alethic-test/",
        )
        assert result.checkpoint_path == "/tmp/alethic-test/"
        assert not result.admitted_failure
