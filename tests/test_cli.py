"""Tests for CLI argument parsing — breaker flags."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from alethic.cli import (
    _build_config,
    _build_verifier_config,
    _detect_subcommand,
    build_parser,
    main,
)


@pytest.mark.parametrize("builder", [_build_config, _build_verifier_config])
def test_provider_options_reach_both_config_types(builder):
    args = build_parser().parse_args(
        [
            "--provider",
            "openai",
            "--model",
            "custom/model",
            "--base-url",
            "http://localhost:8000/v1",
            "--context-window",
            "32768",
            "--token-parameter",
            "max_tokens",
            "--request-options",
            '{"temperature":null}',
            "problem",
        ]
    )
    cfg = builder(args)
    assert cfg.provider == "openai"
    assert cfg.model == "custom/model"
    assert cfg.base_url == "http://localhost:8000/v1"
    assert cfg.context_window == 32768
    assert cfg.token_parameter == "max_tokens"
    assert cfg.request_options == {"temperature": None}


def test_cli_environment_defaults_and_flag_precedence(monkeypatch):
    monkeypatch.setenv("ALETHIC_PROVIDER", "openrouter")
    monkeypatch.setenv("ALETHIC_MODEL", "default/model")
    parser = build_parser()
    cfg = _build_config(parser.parse_args(["p"]))
    assert (cfg.provider, cfg.model) == ("openrouter", "default/model")
    cfg = _build_config(parser.parse_args(["--provider", "openai", "--model", "override", "p"]))
    assert (cfg.provider, cfg.model) == ("openai", "override")


@pytest.mark.parametrize("value", ["[]", "null", "invalid"])
def test_request_options_require_json_object(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--request-options", value, "p"])


@pytest.mark.parametrize(
    "flag",
    ["--provider", "--base-url", "--context-window", "--token-parameter", "--request-options"],
)
def test_new_option_values_are_not_mistaken_for_subcommands(flag):
    assert _detect_subcommand([flag, "derive", "solve", "p"]) == ("solve", [flag, "derive", "p"])


def test_eval_routes_model_options(capsys):
    with patch("alethic.eval.harness.run_benchmark", return_value={}) as run:
        assert (
            main(["eval", "run", "benchmark.json", "--provider", "openai", "--model", "other"]) == 0
        )
    assert run.call_args.kwargs["model_options"] == {"provider": "openai", "model": "other"}


@pytest.mark.parametrize("preset", ["thorough", "extreme"])
def test_effort_presets_do_not_choose_secondary_models(preset):
    cfg = _build_config(
        build_parser().parse_args(
            [
                "--provider",
                "openrouter",
                "--model",
                "other/model",
                "--preset",
                preset,
                "p",
            ]
        )
    )
    assert cfg.variant_b is None
    assert cfg.breaker_model is None


def test_no_breaker_flag():
    from alethic.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--no-breaker", "problem"])
    assert args.no_breaker is True


def test_breaker_model_flag():
    from alethic.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--breaker-model", "claude-haiku-4-5-20251001", "problem"])
    assert args.breaker_model == "claude-haiku-4-5-20251001"
