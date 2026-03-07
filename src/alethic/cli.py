"""CLI interface for the Alethic agent.

Usage:
    alethic "Prove that sqrt(2) is irrational"
    alethic solve "Prove that sqrt(2) is irrational"
    alethic derive "Derive the energy levels of the quantum harmonic oscillator"
    alethic --preset thorough "Prove the Cayley-Hamilton theorem"
    alethic derive --preset thorough "Derive the hydrogen atom spectrum"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING, Any

from alethic.models import AgentConfig, Verdict, VerifierConfig
from alethic.output import format_consensus
from alethic.verifier_agent import CheckerAgent, VerifierAgent

if TYPE_CHECKING:
    from alethic.agent import MathAgent  # noqa: TC004


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alethic",
        description=(
            "Alethic — A reasoning agent powered by Claude.\n"
            "Implements a Generate → Verify → Revise loop with decoupled verification.\n\n"
            "Subcommands (optional):\n"
            "  solve   Solve a mathematical problem (default)\n"
            "  derive  Derive a physics result"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s "Prove that there are infinitely many primes"
  %(prog)s solve "Prove the Cayley-Hamilton theorem"
  %(prog)s derive "Derive the energy levels of the quantum harmonic oscillator"
  %(prog)s --preset quick "Is 17 prime?"
  %(prog)s derive --preset thorough "Derive the hydrogen atom spectrum"
  %(prog)s --file problem.txt --iterations 3
  %(prog)s --model claude-sonnet-4-5-20250929 "What is 17 * 23?"
  %(prog)s --no-code "Prove the AM-GM inequality"
  %(prog)s --json "Solve x^2 - 5x + 6 = 0"
  %(prog)s --thinking "Prove the Basel problem"
  %(prog)s verify solution.md --problem-text "Is 1+1=2?"
  %(prog)s verify .alethic/session-dir/ --verifiers 5
  %(prog)s check derivation.md --domain physics
  %(prog)s check solution.md --json
""",
    )

    parser.add_argument(
        "problem",
        nargs="?",
        help="The problem to solve (inline)",
    )
    parser.add_argument(
        "--file",
        "-f",
        help="Read problem from a text file",
    )
    parser.add_argument(
        "--preset",
        "-p",
        choices=list(AgentConfig.PRESETS),
        help="Use a named preset (quick, default, thorough, extreme)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Anthropic model ID (default: claude-opus-4-6)",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=None,
        help="Max generate-verify-revise iterations (default: 5)",
    )
    parser.add_argument(
        "--revisions",
        type=int,
        default=None,
        help="Max revisions per cycle (default: 3)",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        help="Confidence threshold for acceptance (default: 0.90)",
    )
    parser.add_argument(
        "--temperature-generator",
        type=float,
        default=None,
        help="Sampling temperature for the generator (default: 1.0)",
    )
    parser.add_argument(
        "--temperature-verifier",
        type=float,
        default=None,
        help="Sampling temperature for the verifier (default: 0.2)",
    )
    parser.add_argument(
        "--temperature-reviser",
        type=float,
        default=None,
        help="Sampling temperature for the reviser (default: 0.7)",
    )
    parser.add_argument(
        "--no-code",
        action="store_true",
        help="Disable Python code execution tool",
    )
    parser.add_argument(
        "--no-balanced",
        action="store_true",
        help="Disable balanced prompting (counterexample exploration)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress output, only print final result",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max tokens per API call (default: 16384)",
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable extended thinking for deeper reasoning (uses more tokens)",
    )
    parser.add_argument(
        "--thinking-budget",
        type=int,
        default=None,
        help="Token budget for extended thinking (default: 10000)",
    )
    parser.add_argument(
        "--best-of",
        "-B",
        type=int,
        default=None,
        dest="best_of_n",
        help="Number of candidates per iteration (default: from preset, or 1)",
    )
    parser.add_argument(
        "--tools",
        default="sympy,numpy",
        help="Comma-separated tool guidance to include (sympy, numpy, none). Default: sympy,numpy",
    )
    parser.add_argument(
        "--no-stall-reset",
        action="store_true",
        help="Disable stall-triggered strategy reset",
    )
    parser.add_argument(
        "--stall-window",
        type=int,
        default=None,
        help="Iterations without improvement before triggering reset (default: 2)",
    )
    parser.add_argument(
        "--stall-epsilon",
        type=float,
        default=None,
        help="Minimum confidence improvement to count as progress (default: 0.03)",
    )
    parser.add_argument(
        "--variant-b-model",
        default=None,
        help="Model ID for variant B candidates (shorthand for variant_b={'model': VALUE})",
    )
    parser.add_argument(
        "--no-variant-b",
        action="store_true",
        help="Disable variant B generation even if preset enables it",
    )
    parser.add_argument(
        "--context-threshold",
        type=float,
        default=None,
        help="Context window utilization threshold before checkpoint (default: 0.8)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Resume from a checkpoint session directory",
    )

    # verify/check specific arguments
    parser.add_argument(
        "--problem-text",
        default=None,
        help="Problem statement text (verify only)",
    )
    parser.add_argument(
        "--problem-file",
        "-P",
        default=None,
        help="Read problem from file (verify only)",
    )
    parser.add_argument(
        "--domain",
        choices=["math", "physics"],
        default=None,
        help="Override domain auto-detection (verify/check only)",
    )
    parser.add_argument(
        "--verifiers",
        "-K",
        type=int,
        default=None,
        help="Number of independent verifiers (verify/check only, default: 3)",
    )

    return parser


_FLAG_TO_CONFIG = {
    "model": "model",
    "iterations": "max_iterations",
    "revisions": "max_revisions_per_cycle",
    "confidence_threshold": "confidence_threshold",
    "temperature_generator": "temperature_generator",
    "temperature_verifier": "temperature_verifier",
    "temperature_reviser": "temperature_reviser",
    "max_tokens": "max_tokens",
    "thinking_budget": "thinking_budget",
    "best_of_n": "best_of_n",
    "stall_window": "stall_window",
    "stall_epsilon": "stall_epsilon",
    "context_threshold": "context_threshold",
}


def _build_config(args: argparse.Namespace) -> AgentConfig:
    """Build AgentConfig from parsed CLI args with preset -> explicit flag precedence."""
    # Collect overrides from explicit CLI flags (non-None values only)
    overrides: dict[str, Any] = {}

    for arg_name, config_name in _FLAG_TO_CONFIG.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            overrides[config_name] = val

    if args.thinking:
        overrides["extended_thinking"] = True

    overrides["enable_code_execution"] = not args.no_code
    overrides["verbose"] = not args.quiet

    tool_guidance = frozenset() if args.tools == "none" else frozenset(args.tools.split(","))
    overrides["tool_guidance"] = tool_guidance

    if args.no_stall_reset:
        overrides["stall_reset"] = False

    if args.no_variant_b and args.variant_b_model:
        print(
            "Warning: --no-variant-b and --variant-b-model both specified; --no-variant-b takes precedence",
            file=sys.stderr,
        )

    if args.no_variant_b:
        overrides["variant_b"] = None
    elif args.variant_b_model:
        overrides["variant_b"] = {"model": args.variant_b_model}

    # Auto-bump max_tokens for extended thinking when not explicitly set.
    # Resolve thinking state and budget *before* constructing the config so we
    # only build it once (AgentConfig is frozen).
    if "max_tokens" not in overrides and overrides.get("extended_thinking", False):
        preset_vals = AgentConfig.PRESETS.get(args.preset, {}) if args.preset else {}
        budget = overrides.get("thinking_budget", preset_vals.get("thinking_budget", 10000))
        preset_tokens = preset_vals.get("max_tokens", 16384)
        min_tokens = budget + 8192
        if preset_tokens < min_tokens:
            overrides["max_tokens"] = min_tokens

    if args.preset:
        return AgentConfig.from_preset(args.preset, **overrides)
    return AgentConfig(**overrides)


_FLAGS_WITH_VALUE = frozenset(
    {
        "-f",
        "--file",
        "-p",
        "--preset",
        "-m",
        "--model",
        "-n",
        "--iterations",
        "--revisions",
        "--confidence-threshold",
        "--temperature-generator",
        "--temperature-verifier",
        "--temperature-reviser",
        "--api-key",
        "--max-tokens",
        "--thinking-budget",
        "-B",
        "--best-of",
        "--tools",
        "--stall-window",
        "--stall-epsilon",
        "--variant-b-model",
        "--context-threshold",
        "--resume",
        "--problem-text",
        "-P",
        "--problem-file",
        "--domain",
        "-K",
        "--verifiers",
    }
)


def _detect_subcommand(argv: list[str]) -> tuple[str | None, list[str]]:
    """Detect and strip a 'solve' or 'derive' subcommand from argv.

    Returns (command, remaining_argv). If the first non-flag argument is
    'solve' or 'derive', it is removed from argv and returned as the command.
    Otherwise, command is None and argv is unchanged.

    Flags listed in ``_FLAGS_WITH_VALUE`` consume the next token as their
    value, preventing flag values like ``--preset derive`` from being
    misidentified as subcommands.  Flags using ``=`` syntax (e.g.
    ``--preset=quick``) are handled as a single token and do not consume
    the next argument.
    """
    skip_next = False
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if "=" not in arg and arg in _FLAGS_WITH_VALUE:
                skip_next = True
            continue
        # First positional argument found
        if arg in ("solve", "derive", "verify", "check", "eval"):
            return arg, argv[:i] + argv[i + 1 :]
        break
    return None, argv


_VERIFIER_FLAG_TO_CONFIG = {
    "model": "model",
    "verifiers": "num_verifiers",
    "domain": "domain",
    "max_tokens": "max_tokens",
    "thinking_budget": "thinking_budget",
}

# Default tools for verify/check expand to the full set (including scipy, matplotlib)
_VERIFIER_DEFAULT_TOOLS = frozenset({"sympy", "numpy", "scipy", "matplotlib"})


def _build_verifier_config(args: argparse.Namespace) -> VerifierConfig:
    """Build VerifierConfig from parsed CLI args."""
    overrides: dict[str, Any] = {}

    for arg_name, config_name in _VERIFIER_FLAG_TO_CONFIG.items():
        val = getattr(args, arg_name, None)
        if val is not None:
            overrides[config_name] = val

    if args.thinking:
        overrides["extended_thinking"] = True

    overrides["enable_code_execution"] = not args.no_code
    overrides["verbose"] = not args.quiet

    if args.tools == "none":
        overrides["tool_guidance"] = frozenset()
    elif args.tools == "sympy,numpy":
        overrides["tool_guidance"] = _VERIFIER_DEFAULT_TOOLS
    else:
        overrides["tool_guidance"] = frozenset(args.tools.split(","))

    if args.preset:
        return VerifierConfig.from_preset(args.preset, **overrides)
    return VerifierConfig(**overrides)


def _verify_check_handler(args: argparse.Namespace, command: str) -> int:
    """Handle verify and check subcommands."""
    from pathlib import Path

    from alethic.session_reader import resolve_session_input

    config = _build_verifier_config(args)

    # Resolve input: positional arg is a file or session dir
    input_path = args.problem  # reuses the positional "problem" slot
    if not input_path:
        print("Error: provide a solution file or session directory", file=sys.stderr)
        return 2

    problem: str | None = None
    solution: str | None = None
    p = Path(input_path)

    if p.is_dir():
        # Session directory
        try:
            problem, solution = resolve_session_input(str(p))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    elif p.is_file():
        solution = p.read_text(encoding="utf-8").strip()
    else:
        print(f"Error: path does not exist: {input_path}", file=sys.stderr)
        return 2

    if not solution:
        print("Error: solution is empty", file=sys.stderr)
        return 2

    # For verify: resolve problem from flags or session
    if command == "verify":
        if args.problem_text:
            problem = args.problem_text
        elif args.problem_file:
            try:
                problem = Path(args.problem_file).read_text(encoding="utf-8").strip()
            except OSError as e:
                print(f"Error: cannot read problem file: {e}", file=sys.stderr)
                return 2

        if not problem:
            print(
                "Error: verify requires a problem statement (--problem-text or --problem-file)",
                file=sys.stderr,
            )
            return 2

    # Run
    try:
        if command == "verify":
            agent = VerifierAgent(config=config, api_key=args.api_key)
            result = agent.verify(problem=problem, solution=solution)  # type: ignore[arg-type]
        else:
            agent = CheckerAgent(config=config, api_key=args.api_key)  # type: ignore[assignment]
            result = agent.check(solution=solution)
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
        return 130

    # Output
    if args.json_output:
        mode = "json"
    elif args.quiet:
        mode = "quiet"
    else:
        mode = "text"
    print(format_consensus(result, mode=mode, command=command))

    return 0 if result.verdict == Verdict.CORRECT else 1


def _eval_handler(argv: list[str]) -> int:
    """Handle the 'eval' subcommand with its own argument parser."""
    eval_parser = argparse.ArgumentParser(
        prog="alethic eval",
        description="Benchmark evaluation commands",
    )
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)

    eval_run_parser = eval_sub.add_parser("run", help="Run a benchmark file")
    eval_run_parser.add_argument("benchmark_file", help="Path to benchmark JSON file")
    eval_run_parser.add_argument(
        "--preset",
        "-p",
        choices=["quick", "default", "thorough", "extreme"],
        default="quick",
        help="Agent preset to use (default: quick)",
    )
    eval_run_parser.add_argument(
        "--output",
        "-o",
        help="Write JSON report to this file (default: stdout)",
    )
    eval_run_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-problem progress",
    )
    eval_run_parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )

    args = eval_parser.parse_args(argv)

    from alethic.eval.harness import run_benchmark

    report = run_benchmark(
        args.benchmark_file,
        api_key=args.api_key,
        preset=args.preset,
        verbose=args.verbose,
    )
    output_json = json.dumps(report, indent=2)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"Report written to: {args.output}")
    else:
        print(output_json)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    command, argv = _detect_subcommand(argv)

    # Dispatch eval to its own handler (has its own argument parser)
    if command == "eval":
        return _eval_handler(argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    # Dispatch verify/check to dedicated handler
    if command in ("verify", "check"):
        return _verify_check_handler(args, command)

    # Get problem text
    if args.file:
        try:
            with open(args.file) as f:
                problem = f.read().strip()
        except OSError as e:
            parser.error(f"Cannot read file '{args.file}': {e}")
    elif args.problem:
        problem = args.problem
    else:
        parser.error("Provide a problem as an argument or via --file")

    if not problem:
        parser.error("Problem text is empty")

    # Build config
    config = _build_config(args)

    # Select agent based on command
    agent: MathAgent
    if command == "derive":
        from alethic.physics_agent import PhysicsAgent

        agent = PhysicsAgent(config=config, api_key=args.api_key)
    else:
        from alethic.agent import MathAgent

        agent = MathAgent(config=config, api_key=args.api_key)

    try:
        result = agent.solve(
            problem, balanced=not args.no_balanced, resume_from=args.resume
        )
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
        return 130

    # Output
    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result)
        # Print autopsy path hint if session dir exists and loop failed
        if not result.solved and result.session_dir:
            autopsy_path = os.path.join(result.session_dir, "worklog", "autopsy.md")
            if os.path.exists(autopsy_path):
                print(f"\n[AUTOPSY] Failure analysis written to: {autopsy_path}")

    return 0 if result.solved else 1


if __name__ == "__main__":
    sys.exit(main())
