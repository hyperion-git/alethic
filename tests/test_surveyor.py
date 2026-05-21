"""Tests for the pre-flight surveyor parser and formatter.

The actual API call is mocked in higher-level integration tests; here we
only exercise _parse_survey() and format_survey_block(), which are pure
functions and run without network access.
"""

from __future__ import annotations

from alethic.surveyor import SurveyResult, _parse_survey, format_survey_block


def test_parse_typical_survey() -> None:
    text = """\
KNOWN_PITFALLS:
- Missing factor of 2 in the path integral measure
- Wick rotation sign error for the Euclidean action

CANONICAL_METHODS:
- Saddle-point approximation around the bounce solution

SANITY_CHECK_CANDIDATES:
- [constraint] Result must have dimensions of inverse length
- [constraint] Must reduce to classical action in hbar -> 0 limit
- [conjecture] Order-of-magnitude check against known instanton results
"""
    result = _parse_survey(text)
    assert len(result.pitfalls) == 2
    assert "factor of 2" in result.pitfalls[0]
    assert result.methods == ["Saddle-point approximation around the bounce solution"]
    assert len(result.sanity_checks) == 3
    assert result.sanity_checks[0] == (
        "constraint",
        "Result must have dimensions of inverse length",
    )
    assert result.sanity_checks[2][0] == "conjecture"


def test_parse_none_pitfalls_yields_empty_list() -> None:
    text = """\
KNOWN_PITFALLS:
- NONE

CANONICAL_METHODS:
- Direct integration

SANITY_CHECK_CANDIDATES:
- [constraint] Result must be positive
"""
    result = _parse_survey(text)
    assert result.pitfalls == []
    assert result.methods == ["Direct integration"]
    assert len(result.sanity_checks) == 1


def test_parse_untyped_sanity_check_defaults_to_constraint() -> None:
    text = """\
KNOWN_PITFALLS:
- Sign convention drift

CANONICAL_METHODS:
- N/A

SANITY_CHECK_CANDIDATES:
- A bare predicate without a bracketed type
"""
    result = _parse_survey(text)
    assert result.sanity_checks == [
        ("constraint", "A bare predicate without a bracketed type")
    ]


def test_parse_empty_text_yields_empty_result() -> None:
    result = _parse_survey("")
    assert result.is_empty
    assert result.pitfalls == []
    assert result.methods == []
    assert result.sanity_checks == []


def test_format_block_emits_all_sections() -> None:
    sr = SurveyResult(
        pitfalls=["Watch the Bethe log sign"],
        methods=["Standard QED"],
        sanity_checks=[("constraint", "Has units of energy")],
    )
    block = format_survey_block(sr, role="generator")
    assert "<known-pitfalls>" in block
    assert "Watch the Bethe log sign" in block
    assert "<canonical-methods>" in block
    assert "<sanity-check-candidates>" in block
    assert "[constraint] Has units of energy" in block


def test_format_block_returns_empty_string_when_survey_is_empty() -> None:
    assert format_survey_block(SurveyResult(), role="generator") == ""
    assert format_survey_block(SurveyResult(), role="verifier") == ""
