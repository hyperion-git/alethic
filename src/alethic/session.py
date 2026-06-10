"""Session directory management and checkpoint persistence."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alethic.exceptions import CheckpointError

if TYPE_CHECKING:
    from alethic.models import AgentConfig, TokenLedger

logger = logging.getLogger(__name__)


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert text to a URL-friendly slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-")


def _find_git_root() -> str | None:
    """Find the git repository root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def create_session_dir(
    problem: str,
    domain: str,
    config: AgentConfig,
    base_dir: str | None = None,
) -> str:
    """Create a session directory for tracking agent state.

    Args:
        problem: The problem statement.
        domain: 'math' or 'physics'.
        config: Agent configuration.
        base_dir: Override base directory. If None, uses .alethic/ in git root
                  or /tmp/alethic-{pid}/.

    Returns:
        Absolute path to the created session directory.
    """
    if base_dir is None:
        git_root = _find_git_root()
        base_dir = os.path.join(git_root, ".alethic") if git_root else f"/tmp/alethic-{os.getpid()}"

    now = datetime.now(timezone.utc)
    slug = _slugify(problem)
    date_str = now.strftime("%Y%m%d")
    hex_suffix = os.urandom(2).hex()
    dirname = f"{slug}-{date_str}-{hex_suffix}"

    session_dir = os.path.join(base_dir, dirname)
    os.makedirs(session_dir, exist_ok=True)
    os.makedirs(os.path.join(session_dir, "worklog"), exist_ok=True)

    # Write problem.md
    problem_content = f"<problem_statement>\n{problem}\n</problem_statement>\n"
    Path(session_dir, "problem.md").write_text(problem_content)

    # Write session.json
    session_data: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "domain": domain,
        "problem": problem,
        "current_iteration": 0,
        "best_confidence": 0.0,
        "failed_approaches": [],
        "stall_state": {},
        "token_ledger": {},
        "config": {
            "max_iterations": config.max_iterations,
            "confidence_threshold": config.confidence_threshold,
            "best_of_n": config.best_of_n,
            "context_threshold": config.context_threshold,
        },
        "created_at": now.isoformat(),
        "completed_at": None,
    }
    Path(session_dir, "session.json").write_text(json.dumps(session_data, indent=2))

    logger.info("Session directory created: %s", session_dir)
    return session_dir


def write_checkpoint(
    session_dir: str,
    current_iteration: int,
    best_confidence: float,
    best_solution_text: str | None,
    failed_approaches: list[str],
    stall_state: dict[str, Any],
    token_ledger: TokenLedger,
    status: str = "checkpoint",
) -> None:
    """Write checkpoint state to session directory.

    Updates session.json and writes best_solution.md to worklog.

    Raises:
        CheckpointError: If writing fails.
    """
    try:
        session_path = Path(session_dir)
        session_json_path = session_path / "session.json"

        # Read existing session.json and update
        data = json.loads(session_json_path.read_text()) if session_json_path.exists() else {}

        data["status"] = status
        data["current_iteration"] = current_iteration
        data["best_confidence"] = best_confidence
        data["failed_approaches"] = failed_approaches
        data["stall_state"] = stall_state
        data["token_ledger"] = token_ledger.to_dict()
        data["checkpointed_at"] = datetime.now(timezone.utc).isoformat()

        session_json_path.write_text(json.dumps(data, indent=2))

        # Write best solution if available
        if best_solution_text is not None:
            worklog = session_path / "worklog"
            worklog.mkdir(parents=True, exist_ok=True)
            (worklog / "best_solution.md").write_text(best_solution_text)

        logger.info(
            "Checkpoint written: iter=%d conf=%.2f status=%s",
            current_iteration,
            best_confidence,
            status,
        )

    except OSError as exc:
        raise CheckpointError(f"Failed to write checkpoint: {exc}") from exc


def load_checkpoint(session_dir: str) -> dict[str, Any]:
    """Load checkpoint state from a session directory.

    Args:
        session_dir: Path to session directory.

    Returns:
        Dict with keys: current_iteration, best_confidence, best_solution_text,
        failed_approaches, stall_state, token_ledger, config, problem.

    Raises:
        ValueError: If session.json is missing or session is already completed.
    """
    session_path = Path(session_dir)
    session_json_path = session_path / "session.json"

    if not session_json_path.exists():
        raise ValueError(f"No session.json found in {session_dir}")

    data = json.loads(session_json_path.read_text())
    status = data.get("status", "unknown")

    if status in ("solved", "unsolved"):
        raise ValueError(f"Session already completed with status '{status}'")

    # Read best solution if available
    best_solution_path = session_path / "worklog" / "best_solution.md"
    best_solution_text = best_solution_path.read_text() if best_solution_path.exists() else None

    return {
        "current_iteration": data.get("current_iteration", 0),
        "best_confidence": data.get("best_confidence", 0.0),
        "best_solution_text": best_solution_text,
        "failed_approaches": data.get("failed_approaches", []),
        "stall_state": data.get("stall_state", {}),
        "token_ledger": data.get("token_ledger", {}),
        "config": data.get("config", {}),
        "problem": data.get("problem", ""),
    }


def write_tree_checkpoint(
    session_dir: str,
    *,
    graph_dict: dict[str, Any] | None,
    bridge_index: int,
    bridge_confidence: float,
    failed_bridges: list[str],
    gap_states: dict[int, dict[str, Any]],
    atom_confs: dict[int, float],
    best_confidence: float,
    best_solution_text: str | None,
    token_ledger: TokenLedger | None,
    total_revisions: int = 0,
    status: str = "checkpoint",
) -> str:
    """Persist v3.8 tree-search state to ``tree_state.json``.

    ``graph_dict`` is the pre-serialized ``ProofGraph.to_dict()`` output (or
    None when exhaustion hit before the first bridge was decomposed — resume
    then restarts that bridge fresh). Also updates ``session.json`` with the
    given status and writes ``worklog/best_solution.md``.

    ``gap_states`` values are opaque to this layer — produced and consumed as
    _GapState dicts by search.py.

    ``total_revisions`` is the cumulative microkernel revision count so far;
    persisted so a resumed run can continue the tally rather than restarting.

    Returns the absolute path to ``tree_state.json``.

    Raises:
        CheckpointError: If writing fails.
    """
    from alethic import __version__

    try:
        session_path = Path(session_dir)
        state = {
            "mode": "tree",
            "version": __version__,
            "bridge_index": bridge_index,
            "bridge_confidence": bridge_confidence,
            "failed_bridges": failed_bridges,
            "graph": graph_dict,
            "gap_states": {str(k): v for k, v in gap_states.items()},
            "atom_confs": {str(k): v for k, v in atom_confs.items()},
            "best_confidence": best_confidence,
            "total_revisions": total_revisions,
            "token_ledger": token_ledger.to_dict() if token_ledger else {},
            "checkpointed_at": datetime.now(timezone.utc).isoformat(),
        }
        tree_state_path = session_path / "tree_state.json"
        tree_state_path.write_text(json.dumps(state, indent=2))

        session_json_path = session_path / "session.json"
        data = json.loads(session_json_path.read_text()) if session_json_path.exists() else {}
        data["status"] = status
        data["mode"] = "tree"
        data["best_confidence"] = best_confidence
        data["checkpointed_at"] = state["checkpointed_at"]
        session_json_path.write_text(json.dumps(data, indent=2))

        if best_solution_text is not None:
            worklog = session_path / "worklog"
            worklog.mkdir(parents=True, exist_ok=True)
            (worklog / "best_solution.md").write_text(best_solution_text)

        logger.info(
            "Tree checkpoint written: bridge=%d conf=%.2f status=%s",
            bridge_index, best_confidence, status,
        )
        return str(tree_state_path)

    except OSError as exc:
        raise CheckpointError(f"Failed to write tree checkpoint: {exc}") from exc


def load_tree_checkpoint(session_dir: str) -> dict[str, Any]:
    """Load v3.8 tree-search state from a session directory.

    Returns a dict with keys: bridge_index, bridge_confidence, failed_bridges,
    graph (dict or None), gap_states (int keys), atom_confs (int keys),
    best_confidence, best_solution_text, token_ledger.

    ``gap_states`` values are opaque to this layer — produced and consumed as
    _GapState dicts by search.py.

    Raises:
        CheckpointError: ``tree_state.json`` missing (e.g. a flat-mode
            checkpoint) or unreadable; or the session is already completed
            (status ``"solved"`` or ``"unsolved"``).
    """
    session_path = Path(session_dir)
    tree_state_path = session_path / "tree_state.json"
    if not tree_state_path.exists():
        raise CheckpointError(
            f"No tree_state.json in {session_dir} — this looks like a flat-mode "
            "checkpoint. Resume it without search_mode='tree'."
        )

    session_json_path = session_path / "session.json"
    if session_json_path.exists():
        try:
            session_status = json.loads(session_json_path.read_text()).get("status")
        except (json.JSONDecodeError, OSError):
            session_status = None
        if session_status in ("solved", "unsolved"):
            raise CheckpointError(
                f"Session already completed with status '{session_status}'"
            )

    try:
        state = json.loads(tree_state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise CheckpointError(f"Unreadable tree_state.json: {exc}") from exc

    best_solution_path = session_path / "worklog" / "best_solution.md"
    best_solution_text = (
        best_solution_path.read_text() if best_solution_path.exists() else None
    )

    return {
        "bridge_index": state.get("bridge_index", 0),
        "bridge_confidence": state.get("bridge_confidence", 0.0),
        "failed_bridges": state.get("failed_bridges", []),
        "graph": state.get("graph"),
        "gap_states": {int(k): v for k, v in state.get("gap_states", {}).items()},
        "atom_confs": {int(k): v for k, v in state.get("atom_confs", {}).items()},
        "best_confidence": state.get("best_confidence", 0.0),
        "best_solution_text": best_solution_text,
        "total_revisions": state.get("total_revisions", 0),
        "token_ledger": state.get("token_ledger", {}),
    }


def scan_incomplete_sessions(alethic_dir: str) -> list[dict[str, Any]]:
    """Scan for incomplete sessions in a .alethic directory.

    Args:
        alethic_dir: Path to the .alethic directory.

    Returns:
        List of summary dicts for sessions with status 'running' or 'checkpoint'.
    """
    results: list[dict[str, Any]] = []
    dir_path = Path(alethic_dir)

    if not dir_path.exists():
        return results

    for child in sorted(dir_path.iterdir()):
        if not child.is_dir():
            continue
        session_json = child / "session.json"
        if not session_json.exists():
            continue
        try:
            data = json.loads(session_json.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        status = data.get("status", "unknown")
        if status in ("running", "checkpoint"):
            results.append(
                {
                    "session_dir": str(child),
                    "problem": data.get("problem", ""),
                    "current_iteration": data.get("current_iteration", 0),
                    "best_confidence": data.get("best_confidence", 0.0),
                    "status": status,
                    "config": data.get("config", {}),
                }
            )

    return results
