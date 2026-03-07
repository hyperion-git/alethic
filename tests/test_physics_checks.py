"""Tests for physics_checks.py — Verification Ladder Layers 0-2 (feature 2.1)."""

from alethic.physics_checks import (
    PHYSICS_CHECK_GUIDANCE,
    MATH_CHECK_GUIDANCE,
    parse_layer_results,
)


def test_physics_check_guidance_contains_layer_templates():
    assert "verify_dimensions" in PHYSICS_CHECK_GUIDANCE
    assert "verify_limit_" in PHYSICS_CHECK_GUIDANCE
    assert "verify_symbolic_numeric" in PHYSICS_CHECK_GUIDANCE
    assert "ALETHIC_L0_CHECK" in PHYSICS_CHECK_GUIDANCE


def test_math_check_guidance_contains_layer_templates():
    assert "verify_structure" in MATH_CHECK_GUIDANCE
    assert "verify_base_cases" in MATH_CHECK_GUIDANCE
    assert "verify_dual_representation" in MATH_CHECK_GUIDANCE
    assert "ALETHIC_L1_CHECK" in MATH_CHECK_GUIDANCE


def test_parse_layer_results_extracts_sentinels():
    text = """
Some solution text here.
ALETHIC_L0_CHECK: DIMENSIONS OK
Some more text.
ALETHIC_L1_CHECK: BASE CASES OK (n=0,1,2,3)
ALETHIC_L2_CHECK: CONSISTENCY OK at n=10 (385==385)
"""
    results = parse_layer_results(text)
    assert results[0] == ["DIMENSIONS OK"]
    assert results[1] == ["BASE CASES OK (n=0,1,2,3)"]
    assert results[2] == ["CONSISTENCY OK at n=10 (385==385)"]


def test_parse_layer_results_missing_layers():
    text = "No layer checks here."
    results = parse_layer_results(text)
    assert results == {}


def test_parse_layer_results_multiple_same_layer():
    text = "ALETHIC_L1_CHECK: limit 1 OK\nALETHIC_L1_CHECK: limit 2 OK"
    results = parse_layer_results(text)
    assert len(results[1]) == 2
