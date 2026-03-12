"""Tests for models.py — AtomConfidence dataclass and VerificationResult/AgentConfig updates."""

from alethic.models import AtomConfidence, VerificationResult, AgentConfig, Verdict


def test_atom_confidence_basic():
    ac = AtomConfidence(id=1, confidence=0.88)
    assert ac.id == 1
    assert ac.confidence == 0.88
    assert ac.note is None  # not empty string


def test_atom_confidence_with_note():
    ac = AtomConfidence(id=2, confidence=0.75, note="sign error in step 3")
    assert ac.note == "sign error in step 3"


def test_verification_result_atom_confidences_defaults_to_empty_list():
    vr = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.95)
    assert vr.atom_confidences == []
    assert isinstance(vr.atom_confidences, list)


def test_agent_config_calibration_fields_have_defaults():
    config = AgentConfig()
    assert config.apply_calibration is True
    assert config.calibration_store is None


def test_agent_config_calibration_fields_accept_overrides():
    config = AgentConfig(apply_calibration=False, calibration_store="/tmp/cal.jsonl")
    assert config.apply_calibration is False
    assert config.calibration_store == "/tmp/cal.jsonl"
