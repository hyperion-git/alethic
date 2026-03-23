"""Unit tests for OpenRouter adapter translation functions."""
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from alethic.openrouter import (
    TextBlock, ToolUseBlock, Usage, Message,
    translate_response, translate_tools, translate_messages,
    translate_kwargs, _map_stop_reason,
)


class TestStopReasonMapping:
    def test_stop_maps_to_end_turn(self):
        assert _map_stop_reason("stop") == "end_turn"

    def test_length_maps_to_max_tokens(self):
        assert _map_stop_reason("length") == "max_tokens"

    def test_tool_calls_maps_to_tool_use(self):
        assert _map_stop_reason("tool_calls") == "tool_use"

    def test_content_filter_maps_to_end_turn(self):
        assert _map_stop_reason("content_filter") == "end_turn"

    def test_none_maps_to_end_turn(self):
        assert _map_stop_reason(None) == "end_turn"

    def test_unknown_maps_to_end_turn(self):
        assert _map_stop_reason("banana") == "end_turn"


class TestTranslateResponse:
    def _mock_response(self, *, content=None, tool_calls=None,
                       finish_reason="stop", prompt_tokens=10, completion_tokens=5):
        msg = MagicMock()
        msg.content = content
        msg.tool_calls = tool_calls
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = finish_reason
        resp = MagicMock()
        resp.choices = [choice]
        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        resp.usage = usage
        return resp

    def test_text_only_response(self):
        resp = self._mock_response(content="Hello world")
        msg = translate_response(resp)
        assert len(msg.content) == 1
        assert msg.content[0].type == "text"
        assert msg.content[0].text == "Hello world"
        assert msg.stop_reason == "end_turn"
        assert msg.usage.input_tokens == 10

    def test_null_content_no_text_block(self):
        """S-3: null content should not produce TextBlock('None')."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "print(1)"}'
        resp = self._mock_response(content=None, tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert len(msg.content) == 1
        assert msg.content[0].type == "tool_use"

    def test_mixed_text_and_tools(self):
        """S-2: text + tool_calls merge into single content list."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "2+2"}'
        resp = self._mock_response(content="Let me check", tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert len(msg.content) == 2
        assert msg.content[0].type == "text"
        assert msg.content[1].type == "tool_use"
        assert msg.stop_reason == "tool_use"

    def test_malformed_tool_arguments(self):
        """S-11: invalid JSON in arguments → empty dict."""
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = "execute_python"
        tc.function.arguments = '{"code": "print(1)'  # Missing closing brace
        resp = self._mock_response(content=None, tool_calls=[tc], finish_reason="tool_calls")
        msg = translate_response(resp)
        assert msg.content[0].input == {}

    def test_usage_translation(self):
        """S-22: prompt_tokens → input_tokens."""
        resp = self._mock_response(content="ok", prompt_tokens=100, completion_tokens=50)
        msg = translate_response(resp)
        assert msg.usage.input_tokens == 100
        assert msg.usage.output_tokens == 50

    def test_empty_choices(self):
        """S-15: empty choices array."""
        resp = MagicMock()
        resp.choices = []
        msg = translate_response(resp)
        assert msg.content == []
        assert msg.stop_reason == "end_turn"

    def test_truncated_response(self):
        """S-6: finish_reason='length' → stop_reason='max_tokens'."""
        resp = self._mock_response(content="partial...", finish_reason="length")
        msg = translate_response(resp)
        assert msg.stop_reason == "max_tokens"


class TestTranslateTools:
    def test_anthropic_to_openai(self):
        tools = [{"name": "execute_python", "description": "Run code", "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}}}]
        result = translate_tools(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "execute_python"
        assert result[0]["function"]["parameters"]["type"] == "object"

    def test_none_tools(self):
        assert translate_tools(None) is None

    def test_does_not_mutate_original(self):
        """S-12: deep copy prevents mutation."""
        tools = [{"name": "test", "input_schema": {"type": "object"}}]
        translate_tools(tools)
        assert "input_schema" in tools[0]


class TestTranslateMessages:
    def test_system_prepended(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = translate_messages(msgs, system="You are a verifier")
        assert result[0] == {"role": "system", "content": "You are a verifier"}
        assert result[1] == {"role": "user", "content": "Hello"}

    def test_assistant_with_block_objects(self):
        """S-9/S-21: round-trip — block objects in history serialized back to OpenAI."""
        blocks = [TextBlock(text="Here is my answer"), ToolUseBlock(id="t1", name="execute_python", input={"code": "2+2"})]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Here is my answer"
        assert result[0]["tool_calls"][0]["id"] == "t1"
        assert json.loads(result[0]["tool_calls"][0]["function"]["arguments"]) == {"code": "2+2"}

    def test_tool_result_blocks(self):
        """Tool result translation: Anthropic → OpenAI."""
        msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "4"}]}]
        result = translate_messages(msgs)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "t1"
        assert result[0]["content"] == "4"

    def test_plain_string_passthrough(self):
        msgs = [{"role": "user", "content": "Just a string"}]
        result = translate_messages(msgs)
        assert result[0] == {"role": "user", "content": "Just a string"}

    def test_tool_id_preserved_verbatim(self):
        """S-27: tool IDs survive round-trip exactly."""
        blocks = [ToolUseBlock(id="toolu_abc123", name="execute_python", input={})]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        assert result[0]["tool_calls"][0]["id"] == "toolu_abc123"

    def test_interleaved_text_and_tool_blocks(self):
        """S-28: interleaved blocks produce correct OpenAI message sequence."""
        blocks = [
            TextBlock(text="Step 1"),
            ToolUseBlock(id="t1", name="execute_python", input={"code": "1+1"}),
            TextBlock(text="Step 2"),
            ToolUseBlock(id="t2", name="execute_python", input={"code": "2+2"}),
        ]
        msgs = [{"role": "assistant", "content": blocks}]
        result = translate_messages(msgs)
        # All text merged, all tools in tool_calls (OpenAI doesn't support interleaving)
        assert result[0]["role"] == "assistant"
        assert "Step 1" in result[0].get("content", "")
        assert "Step 2" in result[0].get("content", "")
        assert len(result[0]["tool_calls"]) == 2


class TestApiKeySanitization:
    def test_key_redacted(self):
        """S-42: API keys removed from error messages."""
        from alethic.openrouter import _sanitize_error
        err = Exception("Auth failed for key sk-or-v1-794ac50b5117b2967bd6a855185b5d45")
        sanitized = _sanitize_error(err)
        assert "794ac50b" not in sanitized
        assert "***REDACTED***" in sanitized

    def test_no_key_unchanged(self):
        from alethic.openrouter import _sanitize_error
        err = Exception("Normal error message")
        assert _sanitize_error(err) == "Normal error message"


class TestTranslateKwargs:
    def test_system_extracted(self):
        kw = {"model": "test", "system": "Be helpful", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100}
        result = translate_kwargs(kw)
        assert "system" not in result
        assert result["messages"][0]["role"] == "system"

    def test_thinking_mapped_to_nemotron_reasoning(self):
        """Anthropic thinking → Nemotron enable_thinking + reasoning_budget."""
        kw = {"model": "test", "messages": [], "thinking": {"type": "enabled", "budget_tokens": 15000}, "temperature": 1}
        result = translate_kwargs(kw)
        assert "thinking" not in result
        assert result["temperature"] == 1  # Keep 1.0 — required for Nemotron reasoning
        assert result["extra_body"]["reasoning_budget"] == 15000
        assert result["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
        assert result["extra_body"]["chat_template_kwargs"]["force_nonempty_content"] is True

    def test_no_thinking_still_forces_nonempty(self):
        """force_nonempty_content set even without thinking mode."""
        kw = {"model": "test", "messages": [], "temperature": 0.5}
        result = translate_kwargs(kw)
        assert result["extra_body"]["chat_template_kwargs"]["force_nonempty_content"] is True
        assert "reasoning_budget" not in result

    def test_tools_translated(self):
        kw = {"model": "test", "messages": [], "tools": [{"name": "t", "input_schema": {}}]}
        result = translate_kwargs(kw)
        assert result["tools"][0]["type"] == "function"
        assert "tools" in kw  # Original not mutated (popped from copy)


class TestOpenRouterClient:
    def test_create_calls_openai_and_translates(self):
        from unittest.mock import MagicMock
        from alethic.openrouter import OpenRouterClient, _MessagesAPI, Message

        mock_openai = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "VERDICT: correct\nCONFIDENCE: 0.95"
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.usage.prompt_tokens = 50
        mock_resp.usage.completion_tokens = 20
        mock_openai.chat.completions.create.return_value = mock_resp

        client = OpenRouterClient.__new__(OpenRouterClient)
        client._openai = mock_openai
        client._model = "test-model"
        client.messages = _MessagesAPI(mock_openai, "test-model")

        result = client.messages.create(
            model="ignored",
            system="You are a verifier",
            messages=[{"role": "user", "content": "Check this"}],
            max_tokens=1000,
        )

        assert isinstance(result, Message)
        assert result.content[0].text == "VERDICT: correct\nCONFIDENCE: 0.95"
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 50
        call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["messages"][0]["role"] == "system"

    def test_stream_raises_not_implemented(self):
        from unittest.mock import MagicMock
        from alethic.openrouter import _MessagesAPI
        api = _MessagesAPI(MagicMock(), "test")
        with pytest.raises(NotImplementedError):
            api.stream(model="test", messages=[])


class TestExceptionMapping:
    def test_rate_limit_tuple_contains_anthropic(self):
        from alethic.subagents import _RATE_LIMIT_ERRORS
        import anthropic
        assert anthropic.RateLimitError in _RATE_LIMIT_ERRORS

    def test_rate_limit_tuple_contains_openai(self):
        from alethic.subagents import _RATE_LIMIT_ERRORS
        try:
            import openai
            assert openai.RateLimitError in _RATE_LIMIT_ERRORS
        except ImportError:
            pytest.skip("openai not installed")

    def test_api_error_tuple_contains_anthropic(self):
        from alethic.agent import _API_ERRORS
        import anthropic
        assert anthropic.APIError in _API_ERRORS

    def test_api_error_tuple_contains_openai(self):
        from alethic.agent import _API_ERRORS
        try:
            import openai
            assert openai.APIError in _API_ERRORS
        except ImportError:
            pytest.skip("openai not installed")
