"""Adversarial tests for the CLI's _detect_subcommand and main() entrypoint.

Stress-tests edge cases around subcommand detection, flag/value confusion,
empty inputs, ambiguous positional arguments, and agent routing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.cli import _detect_subcommand, build_parser, main
from alethic.models import AgentResult, Verdict

# ── Helper ───────────────────────────────────────────────────────────


def _mock_agent_result(problem: str = "test", solved: bool = True) -> AgentResult:
    """Create a mock AgentResult for testing main() routing."""
    return AgentResult(
        problem=problem,
        solution="Mock solution" if solved else None,
        verdict=Verdict.CORRECT if solved else Verdict.UNSOLVED,
        confidence=0.95 if solved else 0.0,
        iterations_used=1,
        total_revisions=0,
        admitted_failure=not solved,
        elapsed_seconds=0.1,
    )


# ── _detect_subcommand edge cases ────────────────────────────────────


class TestDetectSubcommandAdversarial:
    """Adversarial tests for _detect_subcommand."""

    # 1. "derive" as a word inside problem text, not as first positional arg.
    #    When quoted on the CLI, it arrives as a single string with spaces.
    def test_derive_word_in_problem_text_not_treated_as_subcommand(self):
        """'derive the limit' as a single quoted argument should NOT strip 'derive'."""
        cmd, remaining = _detect_subcommand(["derive the limit"])
        assert cmd is None, (
            "A single string 'derive the limit' is not the bare word 'derive'; "
            "it should NOT be treated as a subcommand"
        )
        assert remaining == ["derive the limit"]

    # 2a. "derive" IS the first positional when it's its own element.
    def test_derive_is_first_positional_stripped(self):
        """["derive", "something"] -> derive is stripped as subcommand."""
        cmd, remaining = _detect_subcommand(["derive", "something"])
        assert cmd == "derive"
        assert remaining == ["something"]

    # 2b. "derive" is the first positional after flags.
    def test_derive_first_positional_after_boolean_flags(self):
        """["--quiet", "derive", "something"] -> derive is first positional after flag."""
        cmd, remaining = _detect_subcommand(["--quiet", "derive", "something"])
        assert cmd == "derive"
        assert remaining == ["--quiet", "something"]

    # 3. Problem text is literally "solve" (a single word matching a subcommand).
    def test_problem_text_is_literally_solve(self):
        """alethic "solve" -- _detect_subcommand strips it, leaving no problem."""
        cmd, remaining = _detect_subcommand(["solve"])
        # _detect_subcommand has no way to know this is a problem, not a subcommand.
        # It will strip "solve" as a subcommand, leaving empty remaining.
        assert cmd == "solve"
        assert remaining == []

    def test_main_with_problem_text_literally_solve(self):
        """main(["solve"]) strips 'solve' as subcommand, leaving no problem -> error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["solve"])
        assert exc_info.value.code == 2  # argparse error exit code

    # 4. Problem text is literally "derive" -- ambiguous case.
    def test_problem_text_is_literally_derive(self):
        """_detect_subcommand(["derive"]) strips it, leaving no problem."""
        cmd, remaining = _detect_subcommand(["derive"])
        assert cmd == "derive"
        assert remaining == []

    def test_main_with_problem_text_literally_derive(self):
        """main(["derive"]) strips 'derive' as subcommand, leaving no problem -> error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["derive"])
        assert exc_info.value.code == 2

    # 5. Empty argv.
    def test_empty_argv(self):
        """_detect_subcommand([]) should return (None, [])."""
        cmd, remaining = _detect_subcommand([])
        assert cmd is None
        assert remaining == []

    # 6. Only flags, no positional argument at all.
    def test_only_flags_no_positional(self):
        """_detect_subcommand(["--quiet", "--json"]) -> no subcommand detected."""
        cmd, remaining = _detect_subcommand(["--quiet", "--json"])
        assert cmd is None
        assert remaining == ["--quiet", "--json"]

    def test_only_short_flags_no_positional(self):
        """Short flags only: ["-q"] -> no subcommand."""
        cmd, remaining = _detect_subcommand(["-q"])
        assert cmd is None
        assert remaining == ["-q"]

    # 7. Flag value looks like subcommand.
    #    --preset takes a value, but _detect_subcommand doesn't know flag arities.
    #    ["--preset", "derive", "problem"] -- "derive" is the value for --preset,
    #    but _detect_subcommand skips "--preset" (flag), then sees "derive" as
    #    first positional and INCORRECTLY strips it.
    def test_flag_value_looks_like_subcommand_is_misdetected(self):
        """BUG: ["--preset", "derive", "problem"] -- 'derive' is a flag value,
        not a subcommand, but _detect_subcommand incorrectly strips it.

        This test documents the known limitation: _detect_subcommand does not
        understand flag arities, so a flag value matching a subcommand name
        will be incorrectly consumed.
        """
        cmd, remaining = _detect_subcommand(["--preset", "derive", "problem"])
        # This is the ACTUAL behavior (a bug):
        # _detect_subcommand skips "--preset", sees "derive" as first positional,
        # and strips it as a subcommand.
        assert cmd == "derive", (
            "_detect_subcommand doesn't know --preset takes a value, so it "
            "treats 'derive' as a subcommand (known limitation)"
        )
        assert remaining == ["--preset", "problem"]

    def test_flag_value_solve_also_misdetected(self):
        """Same bug with 'solve': ["--preset", "solve", "problem"]."""
        cmd, remaining = _detect_subcommand(["--preset", "solve", "problem"])
        assert cmd == "solve"
        assert remaining == ["--preset", "problem"]

    # 8. Double subcommand: ["solve", "derive", "problem"].
    def test_double_subcommand(self):
        """["solve", "derive", "problem"] -> 'solve' is stripped, rest is argv."""
        cmd, remaining = _detect_subcommand(["solve", "derive", "problem"])
        assert cmd == "solve"
        assert remaining == ["derive", "problem"]

    def test_double_subcommand_derive_becomes_problem_text(self):
        """After stripping 'solve', argparse sees 'derive' as the problem text."""
        cmd, remaining = _detect_subcommand(["solve", "derive", "problem"])
        parser = build_parser()
        # argparse sees ["derive", "problem"] -- "derive" is the problem positional,
        # "problem" is an extra unrecognized arg. Actually, problem is nargs="?",
        # so argparse takes the first positional as problem and rejects the second.
        # Let's check: with nargs="?", only one positional is consumed.
        # "problem" becomes an unrecognized argument.
        args, unknown = parser.parse_known_args(remaining)
        assert args.problem == "derive"
        assert "problem" in unknown

    # 9. Subcommand after flags with values: ["-p", "quick", "derive", "problem"].
    #    -p is a flag (starts with -), so _detect_subcommand skips it.
    #    Then "quick" is the first non-flag arg, NOT a subcommand -> break.
    def test_subcommand_after_flag_with_value_not_detected(self):
        """["-p", "quick", "derive", "problem"] -- 'quick' is seen first,
        not a subcommand, so 'derive' is never checked."""
        cmd, remaining = _detect_subcommand(["-p", "quick", "derive", "problem"])
        assert cmd is None, (
            "'quick' is the first non-flag arg, not a subcommand, so detection "
            "breaks early and 'derive' is never reached"
        )
        assert remaining == ["-p", "quick", "derive", "problem"]

    def test_subcommand_after_flag_with_value_long_form(self):
        """["--preset", "quick", "derive", "problem"] -- same issue with long flag."""
        cmd, remaining = _detect_subcommand(["--preset", "quick", "derive", "problem"])
        # --preset is skipped (starts with -), "quick" is first positional,
        # not a subcommand -> break. derive never checked.
        assert cmd is None
        assert remaining == ["--preset", "quick", "derive", "problem"]


# ── main() routing tests ─────────────────────────────────────────────


class TestMainRouting:
    """Test that main() routes to the correct agent class."""

    # 10. derive routes to PhysicsAgent.
    @patch("alethic.cli.PhysicsAgent", create=True)
    def test_main_derive_routes_to_physics_agent(self, mock_physics_agent):
        """main(["derive", "test"]) should instantiate PhysicsAgent."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.solve.return_value = _mock_agent_result("test")
        mock_physics_agent.return_value = mock_agent_instance

        with (
            patch("alethic.cli._detect_subcommand", return_value=("derive", ["test"])),
            patch.dict("sys.modules", {}),
            patch("alethic.physics_agent.PhysicsAgent", mock_physics_agent),
        ):
            # We need to patch the import inside main()
            ret = main(["derive", "test"])

        assert ret == 0
        mock_physics_agent.assert_called_once()
        mock_agent_instance.solve.assert_called_once()

    # 11. No subcommand routes to MathAgent.
    @patch("alethic.agent.MathAgent")
    def test_main_no_subcommand_routes_to_math_agent(self, mock_math_agent):
        """main(["test problem"]) should instantiate MathAgent (backward compat)."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.solve.return_value = _mock_agent_result("test problem")
        mock_math_agent.return_value = mock_agent_instance

        ret = main(["test problem"])

        assert ret == 0
        mock_math_agent.assert_called_once()
        mock_agent_instance.solve.assert_called_once_with(
            "test problem", balanced=True
        )

    @patch("alethic.agent.MathAgent")
    def test_main_solve_subcommand_routes_to_math_agent(self, mock_math_agent):
        """main(["solve", "test"]) should also use MathAgent."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.solve.return_value = _mock_agent_result("test")
        mock_math_agent.return_value = mock_agent_instance

        ret = main(["solve", "test"])

        assert ret == 0
        mock_math_agent.assert_called_once()

    # 12. main() with no args at all should error.
    def test_main_no_args_calls_parser_error(self):
        """main([]) should trigger parser.error (SystemExit with code 2)."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_main_only_flags_no_problem_calls_parser_error(self):
        """main(["--quiet", "--json"]) should error -- no problem text."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--quiet", "--json"])
        assert exc_info.value.code == 2


# ── Additional adversarial combinations ──────────────────────────────


class TestDetectSubcommandCombinations:
    """Additional combinations to exercise boundary conditions."""

    def test_flag_at_end_no_positional(self):
        """["problem_text", "--quiet"] -- 'problem_text' is first positional,
        not a subcommand."""
        cmd, remaining = _detect_subcommand(["problem_text", "--quiet"])
        assert cmd is None
        assert remaining == ["problem_text", "--quiet"]

    def test_solve_with_flags_interleaved(self):
        """["solve", "--quiet", "--json", "problem"] -> solve stripped, flags kept."""
        cmd, remaining = _detect_subcommand(["solve", "--quiet", "--json", "problem"])
        assert cmd == "solve"
        assert remaining == ["--quiet", "--json", "problem"]

    def test_derive_with_equals_flag(self):
        """["--preset=quick", "derive", "problem"] -- equals-style flag."""
        cmd, remaining = _detect_subcommand(["--preset=quick", "derive", "problem"])
        # "--preset=quick" starts with -, so skipped.
        # "derive" is first positional -> stripped as subcommand.
        assert cmd == "derive"
        assert remaining == ["--preset=quick", "problem"]

    def test_single_dash_arg(self):
        """["-", "derive", "problem"] -- bare '-' starts with '-', so skipped."""
        cmd, remaining = _detect_subcommand(["-", "derive", "problem"])
        # "-" starts with "-", so it's treated as a flag and skipped.
        # "derive" is the first positional -> stripped.
        assert cmd == "derive"
        assert remaining == ["-", "problem"]

    def test_double_dash_separator(self):
        """["--", "derive", "problem"] -- '--' starts with '-', so skipped."""
        cmd, remaining = _detect_subcommand(["--", "derive", "problem"])
        # "--" starts with "-", so it's skipped as a flag.
        # "derive" is the first positional -> stripped.
        assert cmd == "derive"
        assert remaining == ["--", "problem"]

    def test_non_subcommand_first_positional_with_trailing_derive(self):
        """["problem", "derive"] -- 'problem' is first positional, not a subcommand;
        detection breaks immediately, 'derive' is never reached."""
        cmd, remaining = _detect_subcommand(["problem", "derive"])
        assert cmd is None
        assert remaining == ["problem", "derive"]

    def test_subcommand_case_sensitive(self):
        """["Derive", "problem"] -- case matters; 'Derive' != 'derive'."""
        cmd, remaining = _detect_subcommand(["Derive", "problem"])
        assert cmd is None
        assert remaining == ["Derive", "problem"]

    def test_subcommand_solve_case_sensitive(self):
        """["SOLVE", "problem"] -- case matters; 'SOLVE' != 'solve'."""
        cmd, remaining = _detect_subcommand(["SOLVE", "problem"])
        assert cmd is None
        assert remaining == ["SOLVE", "problem"]
