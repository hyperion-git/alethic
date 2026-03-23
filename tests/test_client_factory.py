"""Tests for client_factory."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from alethic.client_factory import get_client, set_client_factory, reset_client_factory


class TestClientFactory:
    def setup_method(self):
        reset_client_factory()

    def teardown_method(self):
        reset_client_factory()

    @patch("alethic.client_factory.anthropic.Anthropic")
    def test_default_returns_anthropic(self, mock_anthropic):
        mock_anthropic.return_value = MagicMock()
        client = get_client(api_key="test-key")
        mock_anthropic.assert_called_once_with(api_key="test-key")

    def test_custom_factory(self):
        mock_client = MagicMock()
        set_client_factory(lambda api_key: mock_client)
        assert get_client("key") is mock_client

    @patch("alethic.client_factory.anthropic.Anthropic")
    def test_reset_restores_default(self, mock_anthropic):
        set_client_factory(lambda api_key: MagicMock())
        reset_client_factory()
        get_client(api_key="key")
        mock_anthropic.assert_called_once()

    def test_factory_receives_api_key(self):
        received = {}
        def factory(api_key):
            received["key"] = api_key
            return MagicMock()
        set_client_factory(factory)
        get_client("my-secret-key")
        assert received["key"] == "my-secret-key"
