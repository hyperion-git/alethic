#!/usr/bin/env python3
"""Diagnose API call failures by testing each feature combination independently.

Tests both raw SDK calls and the patched _do_create fallback.
"""

import os
import sys

import anthropic

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)

TESTS = [
    (
        "Basic call (max_tokens=100)",
        {
            "model": "claude-opus-4-6",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Is 17 prime? Answer in one word."}],
        },
    ),
    (
        "Extended thinking (max_tokens=32768)",
        {
            "model": "claude-opus-4-6",
            "max_tokens": 32768,
            "temperature": 1,
            "thinking": {"type": "enabled", "budget_tokens": 15000},
            "messages": [{"role": "user", "content": "Is 17 prime? Answer in one word."}],
        },
    ),
    (
        "Tools only (max_tokens=32768)",
        {
            "model": "claude-opus-4-6",
            "max_tokens": 32768,
            "messages": [{"role": "user", "content": "Is 17 prime? Answer in one word."}],
            "tools": [
                {
                    "name": "python",
                    "description": "Execute Python code.",
                    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}},
                }
            ],
        },
    ),
    (
        "Extended thinking + tools (thorough preset path)",
        {
            "model": "claude-opus-4-6",
            "max_tokens": 32768,
            "temperature": 1,
            "thinking": {"type": "enabled", "budget_tokens": 15000},
            "messages": [{"role": "user", "content": "Is 17 prime? Answer in one word."}],
            "tools": [
                {
                    "name": "python",
                    "description": "Execute Python code.",
                    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}},
                }
            ],
        },
    ),
]

from alethic.subagents import _do_create  # noqa: E402

for name, kwargs in TESTS:
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        response = _do_create(client, kwargs)
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        print(f"  OK: {text[:100]}")
        print(f"  Tokens: in={response.usage.input_tokens} out={response.usage.output_tokens}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
