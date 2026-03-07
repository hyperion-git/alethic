"""Tests for v3.4 models — OracleType enum and EvidenceState dataclass."""

from alethic.models import OracleType, EvidenceState


def test_oracle_type_values():
    assert OracleType.LAYER0_STRUCTURAL.value == "layer0_structural"
    assert OracleType.LAYER3_LLM.value == "layer3_llm"
    assert OracleType.LAYER3_LLM_ADVERSARIAL.value == "layer3_llm_adversarial"


def test_evidence_state_defaults():
    es = EvidenceState(iteration=1, best_confidence=0.7, error_category="logic")
    assert es.dynamic_n == 1
    assert es.oracle_calls_used == 0
    assert es.confidence_history == []
    assert es.domain_check_results == {}


def test_evidence_state_iteration_shape_default():
    es = EvidenceState(iteration=2, best_confidence=0.8, error_category="algebra")
    assert es.iteration_shape == "improving"
