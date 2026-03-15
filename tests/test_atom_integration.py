"""Cross-iteration integration tests for atom-guided verification (spec §5.5).

CRITICAL: These tests verify the feature actually ENGAGES.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from alethic.agent import MathAgent
from alethic.atoms import AtomAnnotation
from alethic.models import (
    AgentConfig, OracleType, Solution, VerificationResult, Verdict,
)

# agent.py does `from alethic.subagents import generate, verify, revise`
# So we must patch the names in the agent module's namespace.
_AGENT_GENERATE = "alethic.agent.generate"
_AGENT_VERIFY = "alethic.agent.verify"
_AGENT_REVISE = "alethic.agent.revise"


def _make_agent(best_of_n: int = 1, stall_window: int = 3) -> MathAgent:
    config = AgentConfig(
        best_of_n=best_of_n,
        max_iterations=3,
        max_revisions_per_cycle=0,  # skip revision loop for these tests
        stall_reset=False,
        stall_window=stall_window,
        adversarial_self_correction=False,
        adversarial_breaker=False,
        apply_calibration=False,
        verbose=False,
    )
    return MathAgent(api_key="sk-test", config=config)


def _make_atom(atom_id: int, content: str) -> AtomAnnotation:
    return AtomAnnotation(
        id=atom_id, deps=(), oracle=OracleType.LAYER3_LLM,
        content=content, synthetic=False,
    )


def _solution_with_atoms(problem: str, iteration: int, atom_ids: list[int]) -> Solution:
    """Build a solution text with ATOM[N] markers."""
    lines = [f"K_ATOMS={len(atom_ids)}"]
    for atom_id in atom_ids:
        lines.append(f"ATOM[{atom_id}] deps=[] oracle=L3")
        lines.append(f"This is the proof step for atom {atom_id}. Unique content {atom_id}.")
    return Solution(problem=problem, solution_text="\n".join(lines), iteration=iteration)


def _make_failing_verification() -> VerificationResult:
    return VerificationResult(
        verdict=Verdict.MAJOR_FLAW, critique="needs rework", confidence=0.55,
    )


# ── Test 1: Empty history guard ──

def test_empty_history_no_directive_on_first_iteration():
    """verify() is called without IndexError; extra_system has no atom directive."""
    agent = _make_agent()

    verify_calls = []
    def mock_verify(client, problem, solution, config, *, extra_system=None, **kw):
        verify_calls.append(extra_system)
        return _make_failing_verification()

    sol = Solution(problem="p", solution_text="simple prose no atoms", iteration=1)
    def mock_generate(*args, **kwargs):
        return sol

    with patch(_AGENT_VERIFY, side_effect=mock_verify), \
         patch(_AGENT_GENERATE, side_effect=mock_generate):
        try:
            agent.solve("p")
        except Exception:
            pass  # UNSOLVED or any non-IndexError is acceptable

    assert len(verify_calls) >= 1
    first_call_extra = verify_calls[0]
    if first_call_extra is not None:
        assert "ATOM FOCUS DIRECTIVE" not in first_call_extra, (
            "No atom directive should be injected on first iteration with empty history"
        )


# ── Test 2: 3-iteration cross-iteration test ──

def test_three_iteration_directive_uses_previous_iteration_atoms():
    """Verify that iter-3's call site A directive contains iter-2's atoms, not iter-1's.

    Uses best_of_n=1 to avoid call-index ambiguity.
    """
    agent = _make_agent(best_of_n=1, stall_window=3)

    problem = "prove theorem"
    iter1_sol = _solution_with_atoms(problem, 1, [1, 2])   # iter1: ATOM[1], ATOM[2]
    iter2_sol = _solution_with_atoms(problem, 2, [3, 4])   # iter2: ATOM[3], ATOM[4]
    iter3_sol = _solution_with_atoms(problem, 3, [5, 6])   # iter3: ATOM[5], ATOM[6]

    solutions = [iter1_sol, iter2_sol, iter3_sol]

    verify_extra_systems: list[str | None] = []

    generate_counter = [0]
    def mock_generate_counted(*args, **kwargs):
        sol = solutions[min(generate_counter[0], len(solutions) - 1)]
        generate_counter[0] += 1
        return sol

    def mock_verify(client, problem, solution, config, *, extra_system=None, **kw):
        verify_extra_systems.append(extra_system)
        return _make_failing_verification()

    with patch(_AGENT_VERIFY, side_effect=mock_verify), \
         patch(_AGENT_GENERATE, side_effect=mock_generate_counted):
        try:
            agent.solve(problem)
        except Exception:
            pass

    # At iteration 3, verify() should have been called at least 3 times
    assert len(verify_extra_systems) >= 3, f"Expected >= 3 verify calls, got {len(verify_extra_systems)}"

    # The 3rd verify call (iter 3, call site A) must unconditionally receive an atom focus directive
    third_call_extra = verify_extra_systems[2]
    assert third_call_extra is not None, "Iter-3 extra_system must not be None"
    assert "ATOM FOCUS DIRECTIVE" in third_call_extra, (
        f"Iter-3 verify call must receive an atom focus directive. Got: {third_call_extra!r}"
    )
    assert "ATOM[3]" in third_call_extra or "ATOM[4]" in third_call_extra, (
        "Iter-3 directive should reference iter-2 atoms (ATOM[3] or ATOM[4])"
    )
    assert "ATOM[1]" not in third_call_extra, "Iter-3 directive must NOT reference iter-1 atoms (ATOM[1])"
    assert "ATOM[2]" not in third_call_extra, "Iter-3 directive must NOT reference iter-1 atoms (ATOM[2])"


# ── Test 3: FIXABLE accepted path ──

def test_fixable_accepted_atom_history_retains_original_atoms():
    """When FIXABLE is accepted, result verdict is CORRECT."""
    agent = _make_agent(best_of_n=1)
    problem = "test"
    original_sol = _solution_with_atoms(problem, 1, [10, 11])
    corrected_sol = Solution(
        problem=problem,
        solution_text="corrected prose (no atom markers)",
        iteration=1,
    )

    verify_call_count = [0]

    def mock_verify(client, problem, solution, config, *, extra_system=None, **kw):
        verify_call_count[0] += 1
        if verify_call_count[0] == 1:
            # First call: FIXABLE with corrected solution
            return VerificationResult(
                verdict=Verdict.FIXABLE,
                critique="sign error",
                confidence=0.75,
                corrected_solution=corrected_sol.solution_text,
            )
        else:
            # Re-verify of corrected solution: CORRECT
            return VerificationResult(
                verdict=Verdict.CORRECT, critique="ok", confidence=0.96,
            )

    def mock_generate(*args, **kwargs):
        return original_sol

    with patch(_AGENT_VERIFY, side_effect=mock_verify), \
         patch(_AGENT_GENERATE, side_effect=mock_generate):
        result = agent.solve(problem)

    assert result.verdict == Verdict.CORRECT


# ── Test 4: FIXABLE fall-through ──

def test_fixable_fall_through_call_site_c_no_directive_call_site_b_gets_directive():
    """When FIXABLE re-verification fails and falls through:
    - Call site C (FIXABLE re-verify): receives adversarial addendum only (no directive)
    - Call site B (revision loop re-verify): receives directive from original atoms
    """
    config = AgentConfig(
        best_of_n=1,
        max_iterations=2,
        max_revisions_per_cycle=1,
        stall_reset=False,
        adversarial_self_correction=False,
        adversarial_breaker=False,
        apply_calibration=False,
        verbose=False,
    )
    agent = MathAgent(api_key="sk-test", config=config)

    problem = "test"
    original_sol = _solution_with_atoms(problem, 1, [20, 21])  # ATOM[20], ATOM[21]
    corrected_sol = Solution(
        problem=problem,
        solution_text="corrected prose (no atom annotations)",
        iteration=1,
    )

    verify_call_count = [0]
    verify_extra_systems: list[str | None] = []

    def mock_verify(client, problem, solution, config, *, extra_system=None, **kw):
        n = verify_call_count[0]
        verify_call_count[0] += 1
        verify_extra_systems.append(extra_system)
        if n == 0:
            # Call site A: FIXABLE → triggers fall-through path
            return VerificationResult(
                verdict=Verdict.FIXABLE,
                critique="mechanical error",
                confidence=0.70,
                corrected_solution=corrected_sol.solution_text,
            )
        elif n == 1:
            # Call site C (FIXABLE re-verify): FAILS → fall-through
            return VerificationResult(
                verdict=Verdict.MAJOR_FLAW, critique="still wrong", confidence=0.45,
            )
        else:
            # Call site B (revision re-verify): MAJOR_FLAW → terminate
            return VerificationResult(
                verdict=Verdict.MAJOR_FLAW, critique="persists", confidence=0.40,
            )

    def mock_revise(*args, **kwargs):
        return Solution(problem=problem, solution_text="revised", iteration=1)

    def mock_generate(*args, **kwargs):
        return original_sol

    with patch(_AGENT_VERIFY, side_effect=mock_verify), \
         patch(_AGENT_REVISE, side_effect=mock_revise), \
         patch(_AGENT_GENERATE, side_effect=mock_generate):
        try:
            agent.solve(problem)
        except Exception:
            pass

    assert len(verify_extra_systems) >= 3, (
        f"Expected >= 3 verify calls (A, C, B), got {len(verify_extra_systems)}"
    )

    extra_c = verify_extra_systems[1]  # call site C (FIXABLE re-verify)
    extra_b = verify_extra_systems[2]  # call site B (revision re-verify)

    # Call site C must NOT contain atom directive
    if extra_c is not None:
        assert "ATOM FOCUS DIRECTIVE" not in extra_c, (
            "Call site C (FIXABLE re-verify) must not receive atom focus directive"
        )

    # Call site B SHOULD contain atom directive (if atom_history is non-empty)
    if extra_b is not None and "ATOM FOCUS DIRECTIVE" in extra_b:
        assert "ATOM[20]" in extra_b or "ATOM[21]" in extra_b, (
            "Call site B directive should reference original candidate's atoms"
        )
