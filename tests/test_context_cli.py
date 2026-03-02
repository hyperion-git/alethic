"""Tests for context monitoring CLI flags."""

from alethic.cli import _build_config, build_parser


def _parse(args: list[str]):
    parser = build_parser()
    return parser.parse_args(args)


class TestContextThresholdFlag:
    def test_default(self):
        args = _parse(["test problem"])
        config = _build_config(args)
        assert config.context_threshold == 0.8

    def test_explicit(self):
        args = _parse(["--context-threshold", "0.9", "test problem"])
        config = _build_config(args)
        assert config.context_threshold == 0.9

    def test_with_preset(self):
        args = _parse(["--preset", "quick", "--context-threshold", "0.7", "test problem"])
        config = _build_config(args)
        assert config.context_threshold == 0.7


class TestResumeFlag:
    def test_resume_flag_parsed(self):
        args = _parse(["--resume", "/tmp/session", "test problem"])
        assert args.resume == "/tmp/session"

    def test_resume_default_none(self):
        args = _parse(["test problem"])
        assert args.resume is None
