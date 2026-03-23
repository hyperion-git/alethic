"""Integration smoke test for OpenRouter adapter.

Requires OPENROUTER_API_KEY environment variable.
Run with: pytest tests/test_openrouter_integration.py -v -m integration
Skip with: pytest -m "not integration"
"""
from __future__ import annotations
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set"
)


@pytest.mark.integration
def test_single_verification_roundtrip():
    """Smoke test: create client, call verify-like prompt, parse response."""
    from alethic.openrouter import OpenRouterClient

    client = OpenRouterClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model="nvidia/nemotron-3-nano-30b-a3b:free",
    )

    result = client.messages.create(
        model="ignored",
        system="You are a math verifier. Output: VERDICT: correct\nCONFIDENCE: 0.95\nCRITIQUE:\nLooks good.",
        messages=[{"role": "user", "content": "Is 1+1=2? Answer in the format above."}],
        max_tokens=200,
        temperature=0.2,
    )

    assert len(result.content) >= 1
    assert result.content[0].type == "text"
    assert result.stop_reason in ("end_turn", "max_tokens")
    assert result.usage.input_tokens > 0
    text = result.content[0].text
    assert "VERDICT" in text.upper() or "correct" in text.lower()


@pytest.mark.integration
def test_tool_use_roundtrip():
    """Smoke test: model calls execute_python tool."""
    from alethic.openrouter import OpenRouterClient
    from alethic.tools import PYTHON_TOOL

    client = OpenRouterClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model="nvidia/nemotron-3-nano-30b-a3b:free",
    )

    result = client.messages.create(
        model="ignored",
        system="Use the execute_python tool to compute 2+2.",
        messages=[{"role": "user", "content": "What is 2+2? Use the tool."}],
        max_tokens=500,
        temperature=0.2,
        tools=[PYTHON_TOOL],
    )

    has_tool = any(b.type == "tool_use" for b in result.content)
    has_text = any(b.type == "text" for b in result.content)
    assert has_tool or has_text, "Expected either tool call or text response"
