"""Provider behavior and end-to-end portability, with no external requests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alethic import AgentConfig, MathAgent, ModelConfig, VerifierAgent, VerifierConfig
from alethic.autopsy import generate_autopsy
from alethic.client_factory import get_client
from alethic.exceptions import ContextExhaustedError, ModelResponseError, TruncatedResponseError
from alethic.llm import Message, TextBlock, ToolUseBlock
from alethic.models import AgentResult, Verdict
from alethic.providers import (
    _AnthropicMessages,
    _OpenAIMessages,
    translate_kwargs,
    translate_messages,
    translate_response,
)
from alethic.subagents import _call_model


def completion(text="answer", *, finish="stop", calls=None, native=None):
    message = SimpleNamespace(content=text, tool_calls=calls, refusal=None)
    if native is not None:
        message.model_dump = lambda **kw: native
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )


def adapter(*responses, **options):
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = responses
    return SimpleNamespace(messages=_OpenAIMessages(sdk, **options)), sdk


@pytest.mark.parametrize("provider", ["anthropic", "openai", "openrouter"])
def test_provider_key_isolation(provider, monkeypatch):
    for name in ("anthropic", "openai", "openrouter"):
        monkeypatch.setenv(f"{name.upper()}_API_KEY", f"{name}-test-key")
    target = {
        "anthropic": "alethic.providers.AnthropicClient",
        "openai": "alethic.providers.OpenAICompatibleClient",
        "openrouter": "alethic.openrouter.OpenRouterClient",
    }[provider]
    with patch(target) as constructor:
        cfg = AgentConfig(provider=provider, model="opaque-model")
        get_client(config=cfg)
        assert constructor.call_args.kwargs["api_key"] == f"{provider}-test-key"
        get_client("explicit-test-key", config=cfg)
        assert constructor.call_args.kwargs["api_key"] == "explicit-test-key"


def test_request_model_overrides_default_and_options_are_not_mutated():
    options = {"temperature": None, "extra_body": {"reasoning": {"effort": "high"}}}
    before = json.dumps(options, sort_keys=True)
    client, sdk = adapter(completion(), completion(), model="default", request_options=options)
    for model in ("generator", "breaker"):
        client.messages.create(model=model, messages=[], max_tokens=512, temperature=0.2)
    calls = sdk.chat.completions.create.call_args_list
    assert [call.kwargs["model"] for call in calls] == ["generator", "breaker"]
    assert all("temperature" not in call.kwargs for call in calls)
    assert all(call.kwargs["max_completion_tokens"] == 512 for call in calls)
    assert json.dumps(options, sort_keys=True) == before


def test_default_model_and_legacy_token_parameter():
    client, sdk = adapter(completion(), model="fallback", token_parameter="max_tokens")
    client.messages.create(messages=[], max_tokens=123)
    assert sdk.chat.completions.create.call_args.kwargs["model"] == "fallback"
    assert sdk.chat.completions.create.call_args.kwargs["max_tokens"] == 123


def test_native_anthropic_temperature_rule_and_streaming():
    sdk = MagicMock()
    api = _AnthropicMessages(sdk, {})
    kwargs = {"temperature": 0.2, "thinking": {"type": "enabled", "budget_tokens": 1024}}
    api.create(**kwargs)
    api.stream(**kwargs)
    assert sdk.create.call_args.kwargs["temperature"] == 1
    assert sdk.stream.call_args.kwargs["temperature"] == 1
    assert kwargs["temperature"] == 0.2


def test_reasoning_translation_has_no_model_name_dependency():
    outputs = [
        translate_kwargs(
            {
                "model": model,
                "messages": [],
                "temperature": 0.2,
                "thinking": {"type": "enabled", "budget_tokens": 2048},
            }
        )
        for model in ("nvidia/nemotron", "new-vendor/new-model")
    ]
    assert (
        outputs[0]["extra_body"] == outputs[1]["extra_body"] == {"reasoning": {"max_tokens": 2048}}
    )
    assert all(x["temperature"] == 0.2 for x in outputs)


def test_openai_budget_is_not_silently_claimed_supported(caplog):
    result = translate_kwargs(
        {
            "model": "opaque",
            "messages": [],
            "thinking": {
                "type": "enabled",
                "budget_tokens": 2048,
            },
        },
        provider="openai",
    )
    assert "thinking" not in result
    assert "no portable thinking-token budget" in caplog.text


def test_tool_round_trip_retains_reasoning_without_leaking_it_to_verifier():
    call = SimpleNamespace(
        id="call-unique",
        function=SimpleNamespace(
            name="execute_python",
            arguments='{"code":"print(2)"}',
        ),
    )
    native = {
        "role": "assistant",
        "content": None,
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "PRIVATE_REASONING"}],
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
        ],
    }
    client, sdk = adapter(
        completion(None, finish="tool_calls", calls=[call], native=native),
        completion("The answer is 2."),
        completion(
            "VERDICT: correct\nCONFIDENCE: 0.99\n\nCRITIQUE:\nSound.\n\nREASON: N/A\n\nISSUES:\nNone"
        ),
    )
    cfg = AgentConfig(provider="openai", model="arbitrary", max_iterations=1, verbose=False)
    with patch(
        "alethic.subagents.process_tool_calls",
        side_effect=[
            [{"tool_use_id": call.id, "result": "2"}],
            [],
            [],
        ],
    ):
        result = MathAgent(cfg, client=client).solve("What is 1+1?")
    assert result.solved
    requests = [c.kwargs for c in sdk.chat.completions.create.call_args_list]
    assert requests[1]["messages"][-2] == native
    assert requests[1]["messages"][-1]["tool_call_id"] == call.id
    assert "PRIVATE_REASONING" not in json.dumps(requests[2])
    assert "The answer is 2." in json.dumps(requests[2])
    assert result.token_ledger.api_calls == 3


def test_mixed_user_text_and_tool_results():
    messages = translate_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "id", "content": "4"},
                    {"type": "text", "text": "Check the result."},
                ],
            }
        ]
    )
    assert [m["role"] for m in messages] == ["tool", "user"]
    assert messages[1]["content"] == "Check the result."


@pytest.mark.parametrize("arguments", ["[]", "null", '"code"', "123", "{invalid"])
def test_invalid_tool_arguments_are_always_objects(arguments):
    call = SimpleNamespace(
        id="id", function=SimpleNamespace(name="execute_python", arguments=arguments)
    )
    response = translate_response(completion(None, finish="tool_calls", calls=[call]))
    assert response.content[0].input == {}


def test_truncated_tool_calls_never_execute():
    client = MagicMock()
    client.messages.create.return_value = Message(
        content=[ToolUseBlock(id="id", name="execute_python", input={"code": "partial"})],
        stop_reason="max_tokens",
    )
    with patch("alethic.subagents.process_tool_calls") as execute:
        with pytest.raises(TruncatedResponseError):
            _call_model(
                client,
                system="s",
                user_message="u",
                config=AgentConfig(),
                temperature=0.2,
                tools=[{}],
            )
        execute.assert_not_called()


@pytest.mark.parametrize(
    "refused", [completion(finish="content_filter"), SimpleNamespace(choices=[])]
)
def test_unusable_completions_fail_closed(refused):
    with pytest.raises(ModelResponseError):
        translate_response(refused)


def test_context_limit_reaches_standalone_verifier():
    cfg = VerifierConfig(
        provider="openai", model="custom", context_window=16, request_options={"temperature": None}
    )
    agent = VerifierAgent(cfg, client=MagicMock())
    converted = agent._build_agent_config()
    assert converted.model_settings() == cfg.model_settings()
    with pytest.raises(ContextExhaustedError):
        _call_model(
            agent.client, system="x" * 200, user_message="y", config=converted, temperature=0.2
        )
    agent.client.messages.create.assert_not_called()


def test_injected_agents_do_not_consult_global_factory():
    one, two = MagicMock(), MagicMock()
    with patch("alethic.agent.get_client") as factory:
        assert MathAgent(client=one).client is one
        assert MathAgent(client=two).client is two
        factory.assert_not_called()


def test_cross_provider_variant_does_not_forward_primary_key():
    cfg = AgentConfig(
        model="primary",
        provider="anthropic",
        best_of_n=2,
        variant_b={"provider": "openai", "model": "other"},
    )
    agent = MathAgent(cfg, api_key="primary-secret", client=MagicMock())
    with (
        patch("alethic.agent.get_client", return_value=MagicMock()) as factory,
        patch("alethic.agent.generate", return_value=MagicMock()),
    ):
        agent._generate_candidates(
            problem="p", n=2, iteration=1, balanced=True, prompts={}, failed_approaches=()
        )
    factory.assert_called_once_with(api_key=None, config=cfg.build_variant_b_config())


def test_autopsy_reuses_injected_backend():
    client = MagicMock()
    client.messages.create.return_value = Message(content=[TextBlock(text="Failure analysis")])
    result = AgentResult(
        problem="p",
        solution=None,
        verdict=Verdict.UNSOLVED,
        confidence=0,
        iterations_used=1,
        total_revisions=0,
        admitted_failure=True,
    )
    with patch("alethic.autopsy.get_client") as factory:
        generate_autopsy(
            result, client=client, config=ModelConfig(provider="openai", model="audit")
        )
        factory.assert_not_called()
    assert client.messages.create.call_args.kwargs["model"] == "audit"


def test_model_settings_survive_json_round_trip():
    cfg = AgentConfig(
        provider="openai",
        model="custom",
        base_url="http://localhost:8000/v1",
        request_options={"temperature": None},
        context_window=4096,
    )
    data = asdict(cfg)
    data["tool_guidance"] = list(data["tool_guidance"])
    restored = json.loads(json.dumps(data))
    restored["tool_guidance"] = frozenset(restored["tool_guidance"])
    assert AgentConfig(**restored).model_settings() == cfg.model_settings()


@pytest.mark.parametrize(
    "options",
    [
        {"provider": "typo"},
        {"model": " "},
        {"context_window": 0},
        {"token_parameter": "wrong"},
        {"request_options": {"model": "hidden"}},
    ],
)
def test_invalid_backend_configuration(options):
    with pytest.raises(ValueError):
        AgentConfig(**options)


@pytest.mark.parametrize(
    "options",
    [
        {"model": "hidden"},
        {"extra_body": {"messages": []}},
        {"extra_body": {"model": "hidden"}},
        {"extra_body": "invalid"},
    ],
)
def test_options_cannot_replace_model_or_conversation(options):
    with pytest.raises(ValueError):
        AgentConfig(request_options=options)
    client, sdk = adapter(completion(), request_options=options)
    with pytest.raises(ValueError):
        client.messages.create(model="intended", messages=[])
    sdk.chat.completions.create.assert_not_called()


def test_base_package_and_custom_backend_work_without_site_packages(tmp_path):
    # -S removes every installed SDK/scientific dependency from the interpreter.
    doc = Path(__file__).parents[1] / "docs/providers.md"
    example = doc.read_text().split("```python\n")[2].split("```")[0]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-S", "-c", example],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
