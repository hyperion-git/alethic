"""Tests for Layer 0-2 guidance injection into generator/verifier prompts (Task 6)."""

from alethic.prompts import GENERATOR_SYSTEM, VERIFIER_SYSTEM
from alethic.physics_prompts import PHYSICS_GENERATOR_SYSTEM, PHYSICS_VERIFIER_SYSTEM


def test_math_generator_contains_layer_guidance():
    assert "ALETHIC_L0_CHECK" in GENERATOR_SYSTEM
    assert "verify_structure" in GENERATOR_SYSTEM
    assert "verify_base_cases" in GENERATOR_SYSTEM


def test_math_verifier_contains_layer_guidance():
    assert "ALETHIC_L0_CHECK" in VERIFIER_SYSTEM or "Layer 0" in VERIFIER_SYSTEM
    assert "MAJOR" in VERIFIER_SYSTEM  # layer 0 failure = MAJOR


def test_physics_generator_contains_layer_guidance():
    assert "ALETHIC_L0_CHECK" in PHYSICS_GENERATOR_SYSTEM
    assert "verify_dimensions" in PHYSICS_GENERATOR_SYSTEM
    assert "verify_limit_" in PHYSICS_GENERATOR_SYSTEM


def test_physics_verifier_contains_layer_guidance():
    assert "ALETHIC_L0_CHECK" in PHYSICS_VERIFIER_SYSTEM or "Layer 0" in PHYSICS_VERIFIER_SYSTEM
