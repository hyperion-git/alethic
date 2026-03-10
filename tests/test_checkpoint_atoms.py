"""Tests for atom_history checkpoint serialization."""

from __future__ import annotations

from alethic.agent import RunState
from alethic.atoms import AtomAnnotation, content_hash
from alethic.models import OracleType


def test_stall_state_dict_includes_atom_history():
    state = RunState()
    atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
    state.atom_history = [[atom]]
    state.confidence_history = [0.85]
    d = state.stall_state_dict()
    assert "atom_history" in d
    assert len(d["atom_history"]) == 1
    assert d["atom_history"][0]["id"] == 1


def test_stall_state_dict_excludes_synthetic_atoms():
    state = RunState()
    real_atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
    synthetic_atom = AtomAnnotation(id=0, deps=(), oracle=OracleType.LAYER3_LLM, content="y", synthetic=True)
    state.atom_history = [[real_atom, synthetic_atom]]
    state.confidence_history = [0.85]
    d = state.stall_state_dict()
    # Only the real atom should be serialized
    ids = [entry["id"] for entry in d["atom_history"]]
    assert 1 in ids
    assert 0 not in ids


def test_stall_state_dict_empty_atom_history():
    state = RunState()
    d = state.stall_state_dict()
    assert "atom_history" in d
    assert d["atom_history"] == []
