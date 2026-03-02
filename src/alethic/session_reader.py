"""Session input resolution for verify/check commands.

Reads problem and solution from an existing alethic session directory,
enabling `alethic verify .alethic/session-dir/` and similar workflows.
"""

from __future__ import annotations

from pathlib import Path


def resolve_session_input(path: str) -> tuple[str | None, str]:
    """Extract (problem, solution) from an alethic session directory.

    Args:
        path: Path to a session directory containing session.json.

    Returns:
        Tuple of (problem_text_or_none, solution_text).

    Raises:
        ValueError: If path is invalid, not a session, or has no solution.
    """
    p = Path(path)

    if not p.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not p.is_dir():
        raise ValueError(f"Path is not a directory: {path}")
    if not (p / "session.json").exists():
        raise ValueError(f"Path is not a valid alethic session directory (no session.json): {path}")

    # Read problem (optional)
    problem: str | None = None
    problem_file = p / "problem.md"
    if problem_file.exists():
        problem = problem_file.read_text(encoding="utf-8").strip() or None

    # Read solution — try output.md first, then worklog/solution.md
    solution: str | None = None
    for candidate in [p / "output.md", p / "worklog" / "solution.md"]:
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                solution = text
                break

    if solution is None:
        raise ValueError(
            f"Session has no solution found in output.md or worklog/solution.md: {path}"
        )

    return problem, solution
