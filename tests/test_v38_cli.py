"""Tests for the --search CLI flag (v3.8 integration)."""

from __future__ import annotations

from unittest import mock

import pytest

from alethic.cli import _build_config, build_parser
from alethic.models import SearchConfig


def _parse(argv):
    return build_parser().parse_args(argv)


class TestSearchFlag:
    def test_default_is_flat(self):
        args = _parse(["some problem"])
        cfg = _build_config(args)
        assert cfg.search_mode == "flat"
        assert cfg.search is None

    def test_search_tree_sets_mode_and_default_config(self):
        args = _parse(["--search", "tree", "some problem"])
        cfg = _build_config(args)
        assert cfg.search_mode == "tree"
        assert cfg.search == SearchConfig()

    def test_search_tree_with_preset_uses_search_preset(self):
        args = _parse(["-p", "thorough", "--search", "tree", "some problem"])
        cfg = _build_config(args)
        assert cfg.search_mode == "tree"
        assert cfg.search == SearchConfig.from_preset("thorough")

    def test_search_tree_with_quick_preset_falls_back_to_defaults(self):
        """quick has no SearchConfig preset row — explicit opt-in still works."""
        args = _parse(["-p", "quick", "--search", "tree", "some problem"])
        cfg = _build_config(args)
        assert cfg.search_mode == "tree"
        assert cfg.search == SearchConfig()

    def test_search_rejects_unknown_value(self):
        with pytest.raises(SystemExit):
            _parse(["--search", "puct", "some problem"])


class TestEvalSearchFlag:
    def test_eval_run_passes_search_mode(self, tmp_path):
        # _eval_handler takes an argv list and builds its own argparse parser.
        # run_benchmark is imported locally in _eval_handler, so we patch at
        # the harness module level — the local import picks up the mock.
        from alethic.cli import _eval_handler

        bench = tmp_path / "bench.json"
        bench.write_text("{}")
        with mock.patch(
            "alethic.eval.harness.run_benchmark",
            return_value={"ok": True},
        ) as rb:
            rc = _eval_handler(["run", str(bench), "--search", "tree"])
        assert rc == 0
        assert rb.call_args.kwargs["search_mode"] == "tree"

    def test_eval_run_defaults_to_flat(self, tmp_path):
        from alethic.cli import _eval_handler

        bench = tmp_path / "bench.json"
        bench.write_text("{}")
        with mock.patch(
            "alethic.eval.harness.run_benchmark",
            return_value={"ok": True},
        ) as rb:
            _eval_handler(["run", str(bench)])
        assert rb.call_args.kwargs["search_mode"] == "flat"
