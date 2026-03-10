"""Tests for CLI argument parsing — breaker flags."""

from __future__ import annotations


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
