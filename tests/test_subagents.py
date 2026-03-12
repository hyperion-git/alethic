"""Regression tests for subagents.py parser fixes (Task 2: v3.6 atom-guided).

Tests document bugs in _parse_issues(), _parse_section_confidences(), and
AtomConfidence parsing — all caused by the new ATOM CONFIDENCES block that
appears between ISSUES: and SECTION CONFIDENCES: in verifier output.

Write order: tests written BEFORE fixes so they fail first (TDD).
"""

from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig, AtomConfidence, Solution, VerificationResult, Verdict
from alethic.subagents import _parse_verification, revise

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


# ---------------------------------------------------------------------------
# Tests 11-12: atom_context wiring in revise()
# Ref: MEMORY.md — "Wiring test must inspect _call_model's user_message,
#      not just revise() kwarg."
# ---------------------------------------------------------------------------

_REVISE_RESPONSE = "CHANGES MADE:\nFixed sign error.\nREVISED SOLUTION:\nThe answer is 42."


def _make_revise_fixtures():
    """Return (client, solution, verification, config) for revise() call."""
    client = MagicMock()
    solution = Solution(problem="What is 6×7?", solution_text="The answer is 41.", iteration=1)
    verification = VerificationResult(
        verdict=Verdict.MAJOR_FLAW,
        critique="Sign error in step 2.",
        confidence=0.4,
        issues=[],
        reason="",
        section_confidences=[],
        atom_confidences=[],
        corrected_solution=None,
    )
    config = AgentConfig.from_preset("quick")
    return client, solution, verification, config


def test_atom_context_appears_in_user_message():
    """atom_context string must reach _call_model's user_message argument."""
    client, solution, verification, config = _make_revise_fixtures()
    atom_advisory = "ATOM STABILITY ADVISORY: Atom[1] is STABLE; Atom[2] is UNSTABLE."

    captured = {}

    def fake_call_model(c, *, system, user_message, **kwargs):
        captured["user_message"] = user_message
        return _REVISE_RESPONSE

    with patch("alethic.subagents._call_model", side_effect=fake_call_model):
        revise(
            client,
            solution.problem,
            solution,
            verification,
            config,
            revision_number=1,
            atom_context=atom_advisory,
        )

    assert atom_advisory in captured["user_message"], (
        f"atom_context not found in user_message.\nuser_message was:\n{captured['user_message']}"
    )


def test_atom_context_none_adds_no_extra_content():
    """When atom_context=None, user_message must not contain advisory sentinel text."""
    client, solution, verification, config = _make_revise_fixtures()

    captured = {}

    def fake_call_model(c, *, system, user_message, **kwargs):
        captured["user_message"] = user_message
        return _REVISE_RESPONSE

    with patch("alethic.subagents._call_model", side_effect=fake_call_model):
        revise(
            client,
            solution.problem,
            solution,
            verification,
            config,
            revision_number=1,
            atom_context=None,
        )

    assert "ATOM STABILITY ADVISORY" not in captured["user_message"], (
        "atom_context=None should not inject any advisory text into user_message"
    )
