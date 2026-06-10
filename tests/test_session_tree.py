"""Tests for tree-mode checkpoint persistence (v3.8 integration)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alethic.exceptions import CheckpointError
from alethic.models import TokenLedger
from alethic.session import load_tree_checkpoint, write_tree_checkpoint


def _graph_dict() -> dict:
    return {
        "atoms": {
            "1": {
                "id": 1, "deps": [], "oracle": "layer3_llm", "content": "A",
                "synthetic": False, "status": "anchored", "level": 0,
                "parent_id": None, "child_ids": [], "visit_count": 0,
                "total_value": 0.0, "techniques_tried": [],
            },
        },
        "next_id": 1000000,
    }


def _write(tmp_path: Path, **overrides) -> str:
    session_dir = str(tmp_path)
    (tmp_path / "session.json").write_text(json.dumps({"status": "running"}))
    kwargs = dict(
        graph_dict=_graph_dict(),
        bridge_index=1,
        bridge_confidence=0.7,
        failed_bridges=["bridge 0 summary"],
        gap_states={2: {"failures": 1, "last_error_category": "logic",
                        "technique_attempts": {"induction": 1}}},
        atom_confs={1: 0.95},
        best_confidence=0.8,
        best_solution_text="partial proof",
        token_ledger=TokenLedger(input_tokens=10, output_tokens=20, api_calls=2),
    )
    kwargs.update(overrides)
    write_tree_checkpoint(session_dir, **kwargs)
    return session_dir


class TestWriteTreeCheckpoint:
    def test_writes_tree_state_json(self, tmp_path):
        session_dir = _write(tmp_path)
        state = json.loads((tmp_path / "tree_state.json").read_text())
        assert state["mode"] == "tree"
        assert state["bridge_index"] == 1
        assert state["graph"]["next_id"] == 1000000
        assert state["gap_states"]["2"]["last_error_category"] == "logic"
        assert state["atom_confs"]["1"] == 0.95

    def test_updates_session_json_and_best_solution(self, tmp_path):
        _write(tmp_path)
        session = json.loads((tmp_path / "session.json").read_text())
        assert session["status"] == "checkpoint"
        assert session["mode"] == "tree"
        assert session["best_confidence"] == 0.8
        assert (tmp_path / "worklog" / "best_solution.md").read_text() == "partial proof"

    def test_status_override_for_final_write(self, tmp_path):
        _write(tmp_path, status="solved")
        session = json.loads((tmp_path / "session.json").read_text())
        assert session["status"] == "solved"

    def test_null_graph_allowed(self, tmp_path):
        """Exhaustion during Phase 1 of bridge 0: no graph yet."""
        _write(tmp_path, graph_dict=None, best_solution_text=None)
        state = json.loads((tmp_path / "tree_state.json").read_text())
        assert state["graph"] is None


class TestLoadTreeCheckpoint:
    def test_roundtrip(self, tmp_path):
        session_dir = _write(tmp_path)
        loaded = load_tree_checkpoint(session_dir)
        assert loaded["bridge_index"] == 1
        assert loaded["bridge_confidence"] == 0.7
        assert loaded["failed_bridges"] == ["bridge 0 summary"]
        assert loaded["gap_states"][2]["failures"] == 1          # int keys restored
        assert loaded["atom_confs"][1] == 0.95                   # int keys restored
        assert loaded["best_solution_text"] == "partial proof"
        assert loaded["graph"]["next_id"] == 1000000

    def test_missing_tree_state_raises_checkpoint_error(self, tmp_path):
        (tmp_path / "session.json").write_text(json.dumps({"status": "checkpoint"}))
        with pytest.raises(CheckpointError, match="tree_state.json"):
            load_tree_checkpoint(str(tmp_path))

    def test_corrupt_tree_state_raises_checkpoint_error(self, tmp_path):
        (tmp_path / "tree_state.json").write_text("{not json")
        with pytest.raises(CheckpointError):
            load_tree_checkpoint(str(tmp_path))
