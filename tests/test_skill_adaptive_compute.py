"""Tests for adaptive compute skill parity in orchestrator.md (Task 9)."""

from pathlib import Path

ORCHESTRATOR = Path("skills/alethic-common/orchestrator.md").read_text()


def test_orchestrator_mentions_adaptive_compute():
    assert "adaptive_compute" in ORCHESTRATOR or "dynamic_n" in ORCHESTRATOR


def test_orchestrator_mentions_adaptive_revision():
    assert "adaptive_revision" in ORCHESTRATOR or "adaptive revision" in ORCHESTRATOR.lower()


def test_orchestrator_mentions_difficulty_classification():
    text = ORCHESTRATOR.lower()
    assert "difficulty" in text or "escalat" in text


def test_orchestrator_mentions_layer_checks():
    assert "ALETHIC_L" in ORCHESTRATOR or "layer 0" in ORCHESTRATOR.lower()
