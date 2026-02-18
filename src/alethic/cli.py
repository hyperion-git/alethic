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
import sys
from typing import TYPE_CHECKING, Any

from alethic.models import AgentConfig

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
""",
    )

    parser.add_argument(
        "problem",
        nargs="?",
        help="The problem to solve (inline)",
    )
    parser.add_argument(
        "--file", "-f",
        help="Read problem from a text file",
    )
    parser.add_argument(
        "--preset", "-p",
        choices=list(AgentConfig.PRESETS),
        help="Use a named preset (quick, default, thorough, extreme)",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Anthropic model ID (default: claude-opus-4-6)",
    )
    parser.add_argument(
        "--iterations", "-n",
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
        "--quiet", "-q",
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
        "--best-of", "-B",
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


_FLAGS_WITH_VALUE = frozenset({
    "-f", "--file",
    "-p", "--preset",
    "-m", "--model",
    "-n", "--iterations",
    "--revisions",
    "--confidence-threshold",
    "--temperature-generator",
    "--temperature-verifier",
    "--temperature-reviser",
    "--api-key",
    "--max-tokens",
    "--thinking-budget",
    "-B", "--best-of",
    "--tools",
    "--stall-window",
    "--stall-epsilon",
})


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
        if arg in ("solve", "derive"):
            return arg, argv[:i] + argv[i + 1:]
        break
    return None, argv


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    command, argv = _detect_subcommand(argv)

    parser = build_parser()
    args = parser.parse_args(argv)

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
        result = agent.solve(problem, balanced=not args.no_balanced)
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
        return 130

    # Output
    if args.json_output:
        output = {
            "problem": result.problem,
            "solved": result.solved,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "iterations_used": result.iterations_used,
            "total_revisions": result.total_revisions,
            "candidates_per_iteration": result.candidates_per_iteration,
            "admitted_failure": result.admitted_failure,
            "elapsed_seconds": result.elapsed_seconds,
            "solution": result.solution,
            "failed_approaches": result.failed_approaches,
            "events": [
                {"type": e.type.value, "iteration": e.iteration, "timestamp": e.timestamp, **e.data}
                for e in result.events
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print(result)

    return 0 if result.solved else 1


if __name__ == "__main__":
    sys.exit(main())
