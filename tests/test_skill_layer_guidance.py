"""Tests for Layer 0-2 guidance in skill generator/verifier reference files (Task 8)."""

from pathlib import Path

SOLVE_GEN = Path("skills/alethic-solve/references/generator.md").read_text()
DERIVE_GEN = Path("skills/alethic-derive/references/generator.md").read_text()
SOLVE_VER = Path("skills/alethic-solve/references/verifier.md").read_text()
DERIVE_VER = Path("skills/alethic-derive/references/verifier.md").read_text()


def test_solve_generator_has_math_layer_guidance():
    assert "verify_structure" in SOLVE_GEN
    assert "verify_base_cases" in SOLVE_GEN
    assert "verify_dual_representation" in SOLVE_GEN
    assert "ALETHIC_L0_CHECK" in SOLVE_GEN


def test_derive_generator_has_physics_layer_guidance():
    assert "verify_dimensions" in DERIVE_GEN
    assert "verify_limit_" in DERIVE_GEN
    assert "verify_symbolic_numeric" in DERIVE_GEN
    assert "ALETHIC_L0_CHECK" in DERIVE_GEN


def test_solve_verifier_has_layer_interpretation():
    assert "ALETHIC_L0_CHECK" in SOLVE_VER or "Layer 0" in SOLVE_VER


def test_derive_verifier_has_layer_interpretation():
    assert "ALETHIC_L0_CHECK" in DERIVE_VER or "Layer 0" in DERIVE_VER
