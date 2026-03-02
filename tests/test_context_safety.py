"""Tests for context window safety in subagents."""

from unittest.mock import MagicMock, patch

import pytest

from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
from alethic.models import AgentConfig, TokenLedger


def _mock_response(
    text: str, stop_reason: str = "end_turn", input_tokens: int = 500, output_tokens: int = 200
):
    """Create a mock Anthropic response with usage and stop_reason."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


class TestTokenTracking:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_ledger_records_usage(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "hello", input_tokens=1000, output_tokens=300
        )
        config = AgentConfig(verbose=False, enable_code_execution=False)
        ledger = TokenLedger()

        _call_model(
            client, system="sys", user_message="hi", config=config, temperature=1.0, ledger=ledger
        )

        assert ledger.input_tokens == 1000
        assert ledger.output_tokens == 300
        assert ledger.api_calls == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_no_ledger_still_works(self, _ptc):
        """Backward compat: ledger=None means no tracking, no errors."""
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("hello")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(
            client, system="sys", user_message="hi", config=config, temperature=1.0
        )
        assert result == "hello"


class TestTruncatedResponseDetection:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_max_tokens_raises(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "partial output", stop_reason="max_tokens"
        )
        config = AgentConfig(verbose=False, enable_code_execution=False)

        with pytest.raises(TruncatedResponseError, match="max_tokens"):
            _call_model(client, system="sys", user_message="hi", config=config, temperature=1.0)

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_end_turn_does_not_raise(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("full output", stop_reason="end_turn")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(
            client, system="sys", user_message="hi", config=config, temperature=1.0
        )
        assert result == "full output"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_truncation_still_records_ledger(self, _ptc):
        """Even on truncation, the ledger should record the usage."""
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "partial", stop_reason="max_tokens", input_tokens=5000, output_tokens=16384
        )
        config = AgentConfig(verbose=False, enable_code_execution=False)
        ledger = TokenLedger()

        with pytest.raises(TruncatedResponseError):
            _call_model(
                client,
                system="sys",
                user_message="hi",
                config=config,
                temperature=1.0,
                ledger=ledger,
            )

        assert ledger.input_tokens == 5000
        assert ledger.api_calls == 1


class TestPreFlightEstimate:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_context_exhausted_before_call(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        config = AgentConfig(verbose=False, enable_code_execution=False)

        # 800K chars / 4 = 200K tokens estimate, exceeds 0.8 * 200K = 160K
        big_message = "x" * 800_000

        with pytest.raises(ContextExhaustedError, match="estimated"):
            _call_model(
                client,
                system="sys",
                user_message=big_message,
                config=config,
                temperature=1.0,
                context_limit=200_000,
                context_threshold=0.8,
            )

        # API was never called
        client.messages.create.assert_not_called()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_within_limit_proceeds(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("ok")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(
            client,
            system="sys",
            user_message="short",
            config=config,
            temperature=1.0,
            context_limit=200_000,
            context_threshold=0.8,
        )
        assert result == "ok"
