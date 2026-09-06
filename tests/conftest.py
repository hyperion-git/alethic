"""Unit tests must never make an unmocked provider request."""

from importlib import import_module

import pytest


@pytest.fixture(autouse=True)
def block_unmocked_provider_calls(request, monkeypatch):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("integration"):
        return

    def unmocked(*args, **kwargs):
        raise RuntimeError("Unmocked provider call in an offline test")

    for module, cls in (
        ("anthropic.resources.messages", "Messages"),
        ("openai.resources.chat.completions", "Completions"),
    ):
        try:
            api = getattr(import_module(module), cls)
        except ImportError:
            continue
        monkeypatch.setattr(api, "create", unmocked)
        if hasattr(api, "stream"):
            monkeypatch.setattr(api, "stream", unmocked)
