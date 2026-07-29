"""Tests for v3.8 integration config surface (SearchConfig move + AgentConfig fields)."""

from __future__ import annotations

import pytest

from alethic.models import AgentConfig, SearchConfig


class TestSearchConfigMove:
    def test_searchconfig_importable_from_models(self):
        cfg = SearchConfig()
        assert cfg.max_bridges == 2

    def test_searchconfig_reexported_from_search(self):
        from alethic.search import SearchConfig as SearchConfigViaSearch

        assert SearchConfigViaSearch is SearchConfig

    def test_searchconfig_exported_from_package(self):
        import alethic

        assert alethic.SearchConfig is SearchConfig


class TestAgentConfigSearchFields:
    def test_search_mode_defaults_to_flat(self):
        cfg = AgentConfig()
        assert cfg.search_mode == "flat"
        assert cfg.search is None

    def test_search_mode_tree_accepted(self):
        cfg = AgentConfig(search_mode="tree", search=SearchConfig())
        assert cfg.search_mode == "tree"
        assert cfg.search.max_bridges == 2

    def test_search_mode_invalid_raises(self):
        with pytest.raises(ValueError, match="search_mode"):
            AgentConfig(search_mode="puct")

    def test_presets_all_default_to_flat(self):
        for name in AgentConfig.PRESETS:
            cfg = AgentConfig.from_preset(name)
            assert cfg.search_mode == "flat", f"preset {name} must stay flat (opt-in rollout)"
            assert cfg.search is None

    def test_from_preset_accepts_search_overrides(self):
        cfg = AgentConfig.from_preset(
            "thorough", search_mode="tree", search=SearchConfig.from_preset("thorough")
        )
        assert cfg.search_mode == "tree"
        assert cfg.search.max_bridges == 3
