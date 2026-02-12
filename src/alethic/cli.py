"""CLI interface for the Alethic math agent.

Usage:
    python -m alethic "Prove that sqrt(2) is irrational"
    python -m alethic --file problem.txt
    python -m alethic --iterations 3 --no-code "Find all primes below 100"
"""

from __future__ import annotations

import argparse
import json
import sys

from alethic.models import AgentConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alethic",
        description=(
            "Alethic — A mathematical reasoning agent powered by Claude.\n"
            "Implements a Generate → Verify → Revise loop with decoupled verification."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s "Prove that there are infinitely many primes"
  %(prog)s --file problem.txt --iterations 3
  %(prog)s --model claude-sonnet-4-5-20250929 "What is 17 * 23?"
  %(prog)s --no-code "Prove the AM-GM inequality"
  %(prog)s --json "Solve x^2 - 5x + 6 = 0"
""",
    )

    parser.add_argument(
        "problem",
        nargs="?",
        help="The mathematical problem to solve (inline)",
    )
    parser.add_argument(
        "--file", "-f",
        help="Read problem from a text file",
    )
    parser.add_argument(
        "--model", "-m",
        default="claude-opus-4-6",
        help="Anthropic model ID (default: claude-opus-4-6)",
    )
    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=5,
        help="Max generate-verify-revise iterations (default: 5)",
    )
    parser.add_argument(
        "--revisions",
        type=int,
        default=3,
        help="Max revisions per cycle (default: 3)",
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
        default=16384,
        help="Max tokens per API call (default: 16384)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Get problem text
    if args.file:
        with open(args.file) as f:
            problem = f.read().strip()
    elif args.problem:
        problem = args.problem
    else:
        parser.error("Provide a problem as an argument or via --file")
        return 1

    if not problem:
        parser.error("Problem text is empty")
        return 1

    # Build config
    config = AgentConfig(
        model=args.model,
        max_iterations=args.iterations,
        max_revisions_per_cycle=args.revisions,
        enable_code_execution=not args.no_code,
        max_tokens=args.max_tokens,
        verbose=not args.quiet,
    )

    # Import here to avoid slow import on --help
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
            "admitted_failure": result.admitted_failure,
            "elapsed_seconds": result.elapsed_seconds,
            "solution": result.solution,
        }
        print(json.dumps(output, indent=2))
    else:
        print(result)

    return 0 if result.solved else 1


if __name__ == "__main__":
    sys.exit(main())
