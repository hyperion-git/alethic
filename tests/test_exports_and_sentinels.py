"""Tests for __init__.py exports and sentinel stripping."""

from __future__ import annotations


def test_exports():
    from alethic import BreakerVerdict  # noqa: F401
    from alethic.atoms import AtomAnnotation, AtomStability, parse_atoms  # noqa: F401


def test_corrected_solution_strips_sentinels():
    from alethic.subagents import _strip_sentinels

    text = "Fixed solution.\nALETHIC_L0_CHECK: OK\nMore text."
    cleaned = _strip_sentinels(text)
    assert "ALETHIC_L0_CHECK" not in cleaned
    assert "Fixed solution." in cleaned
    assert "More text." in cleaned


def test_strip_sentinels_multiple_levels():
    from alethic.subagents import _strip_sentinels

    text = (
        "Step 1.\nALETHIC_L0_CHECK: dimensions OK\n"
        "Step 2.\nALETHIC_L1_CHECK: numerics OK\n"
        "Step 3.\nALETHIC_L2_CHECK: consistent\n"
        "Conclusion."
    )
    cleaned = _strip_sentinels(text)
    assert "ALETHIC_L0_CHECK" not in cleaned
    assert "ALETHIC_L1_CHECK" not in cleaned
    assert "ALETHIC_L2_CHECK" not in cleaned
    assert "Step 1." in cleaned
    assert "Conclusion." in cleaned


def test_strip_sentinels_no_op_when_none():
    from alethic.subagents import _strip_sentinels

    text = "Plain solution with no sentinels."
    assert _strip_sentinels(text) == text
