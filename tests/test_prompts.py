"""Tests for generator prompt atom annotation instructions."""

from __future__ import annotations


def test_generator_system_contains_atom_instructions():
    from alethic.prompts import GENERATOR_SYSTEM
    assert "ATOM[" in GENERATOR_SYSTEM or "K_ATOMS" in GENERATOR_SYSTEM


def test_physics_generator_contains_atom_instructions():
    from alethic.physics_prompts import PHYSICS_GENERATOR_SYSTEM
    assert "ATOM[" in PHYSICS_GENERATOR_SYSTEM or "K_ATOMS" in PHYSICS_GENERATOR_SYSTEM
