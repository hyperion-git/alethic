"""Tests for context monitoring CLI flags."""

from unittest.mock import MagicMock, patch

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

    @patch("alethic.agent.MathAgent")
    def test_resume_forwarded_to_solve(self, mock_agent_cls):
        """Verify --resume actually reaches agent.solve()."""
        from alethic.cli import main

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.verdict.value = "correct"
        mock_result.confidence = 0.95
        mock_result.solution = "answer"
        mock_result.iterations_used = 1
        mock_result.total_revisions = 0
        mock_result.token_ledger = None
        mock_result.session_dir = None
        mock_result.checkpoint_path = None
        mock_agent.solve.return_value = mock_result
        mock_agent_cls.return_value = mock_agent

        main(["--resume", "/tmp/my-session", "test problem"])

        mock_agent.solve.assert_called_once()
        call_kwargs = mock_agent.solve.call_args
        assert call_kwargs.kwargs.get("resume_from") == "/tmp/my-session"
