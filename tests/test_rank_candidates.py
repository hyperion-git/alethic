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


def test_rank_candidates_called_during_verify(monkeypatch):
    """rank_candidates() should be called within _verify_candidates."""
    from alethic import agent as agent_module

    call_log = []
    _original = agent_module.rank_candidates

    def tracking_rank(verifications):
        call_log.append(len(verifications))
        return _original(verifications)

    monkeypatch.setattr(agent_module, "rank_candidates", tracking_rank)

    from alethic.models import AgentConfig, Solution

    config = AgentConfig.from_preset("quick")
    ma = agent_module.MathAgent(config=config, api_key="test-key")

    v_lo = VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="x", confidence=0.60)
    v_hi = VerificationResult(verdict=Verdict.CORRECT, critique="ok", confidence=0.95)
    sol_a = Solution(problem="p", solution_text="A", iteration=1)
    sol_b = Solution(problem="p", solution_text="B", iteration=1)

    verify_results = iter([v_lo, v_hi])
    monkeypatch.setattr(
        agent_module,
        "verify",
        lambda *a, **kw: next(verify_results),
    )

    from alethic.agent import RunState

    state = RunState()
    result = ma._verify_candidates(
        problem="p",
        candidates=[(sol_a, 0.1), (sol_b, 0.2)],
        prompts={},
        state=state,
    )

    assert len(call_log) == 1, "rank_candidates should be called exactly once"
    assert call_log[0] == 2, "rank_candidates should receive 2 verifications"
    assert result[0][1].confidence == 0.95
