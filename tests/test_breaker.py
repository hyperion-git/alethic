"""Tests for the adversarial breaker module."""

from __future__ import annotations

import pytest

from alethic.atoms import AtomAnnotation
from alethic.breaker import BreakerResult, _parse_breaker, MATH_BREAKER_SYSTEM, PHYSICS_BREAKER_SYSTEM
from alethic.models import BreakerVerdict, OracleType


class TestParseBreaker:
    def test_flaw_found(self):
        text = (
            "BREAKER_VERDICT: FLAW_FOUND\n"
            "TARGET_ATOM: 3\n"
            "FLAW_TYPE: counterexample\n"
            "EVIDENCE: n=0 gives f(0)=-1, but claimed f(n)>=0 for all n.\n"
            "REASONING: The base case was not checked."
        )
        result = _parse_breaker(text)
        assert result.verdict == BreakerVerdict.FLAW_FOUND
        assert result.target_atom == 3
        assert result.flaw_type == "counterexample"
        assert "n=0" in result.evidence
        assert "base case" in result.reasoning

    def test_suspected_flaw(self):
        text = (
            "BREAKER_VERDICT: SUSPECTED_FLAW\n"
            "TARGET_ATOM: 2\n"
            "FLAW_TYPE: logical_gap\n"
            "EVIDENCE: Step 3 claims convergence without justification.\n"
            "REASONING: The dominated convergence theorem requires a dominating function."
        )
        result = _parse_breaker(text)
        assert result.verdict == BreakerVerdict.SUSPECTED_FLAW
        assert result.target_atom == 2

    def test_no_flaw_found(self):
        text = (
            "BREAKER_VERDICT: NO_FLAW_FOUND\n"
            "TARGET_ATOM: 0\n"
            "FLAW_TYPE: none\n"
            "EVIDENCE: All atoms checked, no issues found.\n"
            "REASONING: The proof appears correct."
        )
        result = _parse_breaker(text)
        assert result.verdict == BreakerVerdict.NO_FLAW_FOUND
        assert result.target_atom == 0

    def test_missing_verdict_defaults_to_no_flaw(self):
        result = _parse_breaker("I could not find any issues.")
        assert result.verdict == BreakerVerdict.NO_FLAW_FOUND

    def test_critique_addendum(self):
        text = (
            "BREAKER_VERDICT: FLAW_FOUND\n"
            "TARGET_ATOM: 1\n"
            "FLAW_TYPE: counterexample\n"
            "EVIDENCE: x=0 breaks the formula.\n"
            "REASONING: Division by zero."
        )
        result = _parse_breaker(text)
        addendum = result.critique_addendum
        assert "ADVERSARIAL BREAKER" in addendum
        assert "atom 1" in addendum.lower() or "atom_1" in addendum.lower()
        assert "x=0" in addendum


class TestBreakerPrompts:
    def test_math_prompt_has_key_elements(self):
        assert "base case" in MATH_BREAKER_SYSTEM.lower() or "base-case" in MATH_BREAKER_SYSTEM.lower()
        assert "BREAKER_VERDICT" in MATH_BREAKER_SYSTEM
        assert "FLAW_FOUND" in MATH_BREAKER_SYSTEM

    def test_physics_prompt_has_key_elements(self):
        assert "dimension" in PHYSICS_BREAKER_SYSTEM.lower()
        assert "limit" in PHYSICS_BREAKER_SYSTEM.lower()
        assert "BREAKER_VERDICT" in PHYSICS_BREAKER_SYSTEM
