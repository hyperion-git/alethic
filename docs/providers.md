# Backend configuration

## Choosing an endpoint

The Python API and all CLI workflows (`solve`, `derive`, `verify`, `check`, and
`eval run`) use the same model settings.

| Provider | Install extra | Credential variable | Default endpoint | Token-limit field |
| --- | --- | --- | --- | --- |
| `anthropic` | `anthropic` | `ANTHROPIC_API_KEY` | Native SDK default | `max_tokens` |
| `openai` | `openai` | `OPENAI_API_KEY` | Native SDK default | `max_completion_tokens` |
| `openrouter` | `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `max_tokens` |

`base_url` / `--base-url` selects a custom endpoint. For a Chat Completions server
that expects the older token field, set `token_parameter="max_tokens"` or
`--token-parameter max_tokens`. Supply the complete API base path, including `/v1`
when required. A no-auth local server still needs a dummy key for the OpenAI SDK;
Alethic never sends another provider's environment key as a fallback.

Model IDs are opaque strings. The `model` in each request takes precedence over
an adapter's constructor default. This applies to alternate generation and
breaker models as well as the main model. There is no automatic model-name
translation or routing between providers.

`context_window` is the serving context limit, in tokens. A small backwards-compatible
lookup remains for the original Claude defaults; otherwise the estimate defaults
to 200,000 tokens. Override it for your deployment. Character-based estimates are
heuristic and cannot guarantee that a provider will accept a particular request.

## Reasoning and endpoint-specific options

The shared configuration supports `extended_thinking` and `thinking_budget`.
The Anthropic adapter translates these to native thinking and enforces its
sampling restriction. OpenRouter translates the budget to `extra_body.reasoning`.
The OpenAI-compatible API has no portable numerical thinking budget; it emits a
warning and leaves reasoning control to explicit `request_options`.

```python
from alethic import AgentConfig

config = AgentConfig.from_preset(
    "quick",
    provider="openai",
    model="YOUR_REASONING_MODEL",
    request_options={"temperature": None, "reasoning_effort": "high"},
)
```

A `None` option removes the corresponding request field. Use only parameters
supported by your chosen model and API. For servers requiring custom JSON fields,
use `request_options={"extra_body": {...}}`. Options are copied before translation;
requests cannot mutate a shared caller dictionary. Model routing and conversation
fields cannot be overridden through `request_options`.

Reasoning continuation data and tool-call signatures are retained in the private
conversation that produced them. Text extraction excludes that data, so an
independent verifier receives only the candidate's written solution.

## Custom clients and offline reproduction

A backend needs only `client.messages.create(**kwargs)`. It returns content blocks,
a stop reason and token usage. The following example is runnable with the base
package alone and makes no network calls:

```python
from alethic import AgentConfig, MathAgent
from alethic.llm import Message, TextBlock, Usage

class DemoClient:
    def __init__(self):
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        text = (
            "The sum of 1 and 1 is 2."
            if self.calls == 1 else
            "VERDICT: correct\nCONFIDENCE: 0.99\n\n"
            "CRITIQUE:\nThe arithmetic is correct.\n\nREASON: N/A\n\nISSUES:\nNone"
        )
        return Message(content=[TextBlock(text=text)], usage=Usage(10, 10))

agent = MathAgent(
    AgentConfig(model="demo", max_iterations=1, enable_code_execution=False,
                tool_guidance=frozenset(), verbose=False),
    client=DemoClient(),
)
assert agent.solve("What is 1 + 1?").solved
```

Environment: Python 3.10+; `pip install -e .` from the clone. No random sampling is
used, so no seed is required. A production custom client must be thread-safe when
best-of-N or consensus uses more than one worker. A caller owns the lifecycle of
an injected client. Built-in adapters expose `close()`.

Responses use `TextBlock`, `ToolUseBlock`, `Usage`, and `Message` from `alethic.llm`.
The names retain compatibility with the former Anthropic-shaped interface without
requiring its SDK. `max_tokens` as a stop reason means truncation; `tool_use`
means another tool round is needed. Tool IDs must survive the round trip exactly.
Malformed JSON tool arguments produce a tool error that the model can correct.
Empty completion choices and provider refusals raise `ModelResponseError`.

## Migration from the previous release

Install a provider extra: `pip install -e '.[anthropic]'` preserves the original
backend. `.[dev]` includes both SDKs and the scientific stack for offline tests.
The Anthropic SDK is constrained below its 1.0 API break; the OpenAI SDK below 3.0.

Use keyword arguments when constructing configuration dataclasses: the shared
`ModelConfig` base changes the positional field order.

Replace process-wide `set_client_factory(...)` setup with `provider=...` settings
or `MathAgent(client=...)`. The factory functions remain available for existing
scripts, but affect all subsequently created agents in that process.

Existing imports from `alethic.openrouter` still work. Its constructor model is
now a fallback, so remove dummy `model="ignored"` arguments: a request's explicit
model is honored. Existing code that relied on the forced override must put its
actual model into `AgentConfig`.

`thorough` and `extreme` no longer select Claude Sonnet implicitly for secondary
roles. Set `variant_b={"model": "YOUR_ALTERNATE"}` and/or
`breaker_model="YOUR_BREAKER"` explicitly to restore model diversity.

There is no built-in native Gemini, Bedrock, Azure-specific, or Responses API
adapter. Use a compatible endpoint where available, or implement `ModelClient`.
Switching providers changes model behavior and invalidates assumptions about
calibrated confidence; rerun the benchmark with false-claim anchors before relying
on a new model for research judgments.
