"""Regression tests for subagents.py parser fixes (Task 2: v3.6 atom-guided).

Tests document bugs in _parse_issues(), _parse_section_confidences(), and
AtomConfidence parsing — all caused by the new ATOM CONFIDENCES block that
appears between ISSUES: and SECTION CONFIDENCES: in verifier output.

Write order: tests written BEFORE fixes so they fail first (TDD).
"""

from alethic.subagents import _parse_verification
from alethic.models import AtomConfidence

# ---------------------------------------------------------------------------
# Shared fixture: full verifier output with ATOM CONFIDENCES block
# ---------------------------------------------------------------------------

_VERIFIER_WITH_ATOM_CONFIDENCES = """\
VERDICT: correct
CONFIDENCE: 0.92
CRITIQUE:
Looks good.
REASON: N/A
ISSUES:
- [MINOR] minor note
ATOM CONFIDENCES:
ATOM[1]: 0.95
ATOM[2]: 0.88 sign check
SECTION CONFIDENCES:
Setup: 0.96
"""

# ---------------------------------------------------------------------------
# Test 1: _parse_issues() bleeds ATOM CONFIDENCES without stop-word
# ---------------------------------------------------------------------------


def test_parse_issues_does_not_bleed_into_atom_confidences_block():
    """After fix: ATOM CONFIDENCES lines must not appear in parsed issues."""
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    issue_texts = [i.text for i in result.issues]
    for text in issue_texts:
        assert "ATOM[" not in text, f"ATOM line bled into issues: {text!r}"
    assert len(result.issues) == 1  # only the [MINOR] issue


# ---------------------------------------------------------------------------
# Test 2: _parse_section_confidences() ingests ATOM[N]: lines as sections
# ---------------------------------------------------------------------------


def test_parse_section_confidences_does_not_ingest_atom_lines():
    """After fix: ATOM[N]: lines must not appear as spurious SectionConfidence entries."""
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    section_names = [sc.section for sc in result.section_confidences]
    for name in section_names:
        assert not name.startswith("ATOM"), f"ATOM line ingested as section: {name!r}"
    assert any(sc.section == "Setup" for sc in result.section_confidences)


# ---------------------------------------------------------------------------
# Tests 4-6: AtomConfidence parsing
# ---------------------------------------------------------------------------


def test_parse_atom_confidence_no_note():
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    atom1 = next((a for a in result.atom_confidences if a.id == 1), None)
    assert atom1 is not None
    assert atom1.confidence == 0.95
    assert atom1.note is None  # not empty string


def test_parse_atom_confidence_with_note():
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    atom2 = next((a for a in result.atom_confidences if a.id == 2), None)
    assert atom2 is not None
    assert atom2.confidence == 0.88
    assert atom2.note == "sign check"


def test_parse_verification_atom_confidences_populated():
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    assert len(result.atom_confidences) == 2
    ids = {a.id for a in result.atom_confidences}
    assert ids == {1, 2}


# ---------------------------------------------------------------------------
# Test 9: Known limitation — ATOM[1,2]: multi-ID not caught by guard
# ---------------------------------------------------------------------------

_SECTION_CONF_WITH_MULTI_ID = """\
VERDICT: correct
CONFIDENCE: 0.90
CRITIQUE: ok
REASON: N/A
ISSUES:
None
SECTION CONFIDENCES:
ATOM[1,2]: 0.88
Setup: 0.96
"""


def test_multi_id_atom_format_ingested_as_spurious_section_confidence():
    """Known limitation: ATOM[1,2]: format is NOT caught by the guard.

    This test documents the behavior. If it starts failing, multi-ID support
    was added — update the guard and this test together.
    """
    result = _parse_verification(_SECTION_CONF_WITH_MULTI_ID)
    section_names = [sc.section for sc in result.section_confidences]
    # Multi-ID format slips through — this is expected/documented behavior
    assert any("ATOM[1,2]" in name or "ATOM" in name for name in section_names), (
        "Expected multi-ID ATOM line to be ingested as spurious SectionConfidence"
    )


# ---------------------------------------------------------------------------
# Test 10: REASON: block not extended by ATOM CONFIDENCES presence
# ---------------------------------------------------------------------------


def test_reason_block_not_extended_by_atom_confidences():
    result = _parse_verification(_VERIFIER_WITH_ATOM_CONFIDENCES)
    assert result.reason == "N/A"
    assert "ATOM[" not in result.reason
