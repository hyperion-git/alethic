"""Example problems demonstrating the Alethic math agent.

Run with:
    python -m alethic.examples           # run all examples
    python -m alethic.examples --pick 1  # run just example 1
"""

from __future__ import annotations

import argparse
import sys

EXAMPLES = [
    {
        "name": "Irrationality of sqrt(2)",
        "problem": "Prove that the square root of 2 is irrational.",
        "difficulty": "undergraduate",
    },
    {
        "name": "Infinitely many primes",
        "problem": "Prove that there are infinitely many prime numbers.",
        "difficulty": "undergraduate",
    },
    {
        "name": "Sum of geometric series",
        "problem": (
            "Prove that for |r| < 1, the infinite geometric series "
            "sum_{n=0}^{infinity} r^n converges to 1/(1-r)."
        ),
        "difficulty": "undergraduate",
    },
    {
        "name": "AM-GM inequality",
        "problem": (
            "Prove the Arithmetic Mean - Geometric Mean inequality: "
            "for non-negative real numbers a_1, a_2, ..., a_n, "
            "(a_1 + a_2 + ... + a_n)/n >= (a_1 * a_2 * ... * a_n)^(1/n)."
        ),
        "difficulty": "intermediate",
    },
    {
        "name": "Computational verification",
        "problem": (
            "Find all integer solutions to x^3 + y^3 = z^3 for "
            "1 <= x, y, z <= 1000. Use computational search to verify "
            "your answer."
        ),
        "difficulty": "intermediate",
    },
    {
        "name": "Euler's identity",
        "problem": (
            "Prove Euler's identity: e^(i*pi) + 1 = 0. "
            "Start from the Taylor series definition of e^x and "
            "provide a rigorous derivation."
        ),
        "difficulty": "undergraduate",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Alethic example problems")
    parser.add_argument("--pick", type=int, help="Run only the specified example (1-indexed)")
    parser.add_argument("--list", action="store_true", help="List available examples")
    parser.add_argument("--iterations", "-n", type=int, default=3, help="Max iterations")
    args = parser.parse_args()

    if args.list:
        for i, ex in enumerate(EXAMPLES, 1):
            print(f"  {i}. [{ex['difficulty']}] {ex['name']}")
            print(f"     {ex['problem'][:80]}...")
            print()
        return 0

    from alethic.agent import MathAgent
    from alethic.models import AgentConfig

    config = AgentConfig(max_iterations=args.iterations)
    agent = MathAgent(config=config)

    examples_to_run = EXAMPLES
    if args.pick:
        if 1 <= args.pick <= len(EXAMPLES):
            examples_to_run = [EXAMPLES[args.pick - 1]]
        else:
            print(f"Invalid example number. Choose 1-{len(EXAMPLES)}.")
            return 1

    for i, ex in enumerate(examples_to_run, 1):
        print(f"\n{'#' * 60}")
        print(f"# Example {i}: {ex['name']} ({ex['difficulty']})")
        print(f"{'#' * 60}\n")

        result = agent.solve(ex["problem"])
        print(f"\n{result}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
