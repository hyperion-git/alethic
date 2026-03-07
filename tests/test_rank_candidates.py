"""Tests for rank_candidates() module-level function (Task 4 — tree-search prep)."""

from alethic.agent import rank_candidates
from alethic.models import Verdict, VerificationResult


def _make_vr(confidence: float, verdict: Verdict = Verdict.MINOR_ISSUES) -> VerificationResult:
    return VerificationResult(verdict=verdict, confidence=confidence, critique="", issues=[])


def test_rank_candidates_selects_highest_confidence():
    verifications = [_make_vr(0.7), _make_vr(0.9), _make_vr(0.6)]
    assert rank_candidates(verifications) == 1


def test_rank_candidates_single_candidate():
    verifications = [_make_vr(0.85)]
    assert rank_candidates(verifications) == 0


def test_rank_candidates_correct_verdict_wins_over_minor():
    verifications = [
        _make_vr(0.85, Verdict.MINOR_ISSUES),
        _make_vr(0.95, Verdict.CORRECT),
    ]
    assert rank_candidates(verifications) == 1
