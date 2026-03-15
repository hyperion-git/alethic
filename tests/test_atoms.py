"""Tests for atom annotation parsing and validation."""

from __future__ import annotations

import pytest

from alethic.atoms import AtomAnnotation, AtomStability, classify_atom_stability, content_hash, parse_atoms
from alethic.models import OracleType


class TestAtomAnnotation:
    """AtomAnnotation dataclass tests."""

    def test_frozen_dataclass(self):
        atom = AtomAnnotation(id=1, deps=(2,), oracle=OracleType.LAYER0_STRUCTURAL, content="text")
        with pytest.raises(AttributeError):
            atom.id = 2

    def test_hashable(self):
        atom = AtomAnnotation(id=1, deps=(2,), oracle=OracleType.LAYER0_STRUCTURAL, content="text")
        {atom}  # should not raise

    def test_synthetic_default_false(self):
        atom = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="text")
        assert atom.synthetic is False

    def test_reserved_ids(self):
        mono = AtomAnnotation(id=0, deps=(), oracle=OracleType.LAYER3_LLM, content="x", synthetic=True)
        preamble = AtomAnnotation(id=-1, deps=(), oracle=OracleType.LAYER3_LLM, content="x", synthetic=True)
        residual = AtomAnnotation(id=-2, deps=(1,), oracle=OracleType.LAYER3_LLM, content="x", synthetic=True)
        assert mono.id == 0
        assert preamble.id == -1
        assert residual.id == -2


class TestParseAtomsBasic:
    """parse_atoms() — well-formed input."""

    def test_no_annotations_returns_monolithic(self):
        text = "This is a plain solution with no atom markers."
        atoms = parse_atoms(text)
        assert len(atoms) == 1
        assert atoms[0].id == 0
        assert atoms[0].synthetic is True
        assert atoms[0].content == text
        assert atoms[0].oracle == OracleType.LAYER3_LLM

    def test_single_atom(self):
        text = "ATOM[1] deps=[] oracle=L3\nSome derivation text."
        atoms = parse_atoms(text)
        real = [a for a in atoms if not a.synthetic]
        assert len(real) == 1
        assert real[0].id == 1
        assert real[0].deps == ()
        assert real[0].oracle == OracleType.LAYER3_LLM

    def test_two_atoms_with_deps(self):
        text = (
            "ATOM[1] deps=[] oracle=L0\nSetup text.\n\n"
            "ATOM[2] deps=[1] oracle=L2\nDerivation text."
        )
        atoms = parse_atoms(text)
        real = [a for a in atoms if not a.synthetic]
        assert len(real) == 2
        assert real[0].id == 1
        assert real[0].deps == ()
        assert real[1].id == 2
        assert real[1].deps == (1,)

    def test_preamble_becomes_synthetic(self):
        text = "Preamble text.\n\nATOM[1] deps=[] oracle=L3\nContent."
        atoms = parse_atoms(text)
        assert atoms[0].id == -1
        assert atoms[0].synthetic is True
        assert "Preamble" in atoms[0].content

    def test_single_atom_trailing_text_included(self):
        """Single atom — trailing text is part of the atom's content (no residual)."""
        text = "ATOM[1] deps=[] oracle=L3\nContent.\n\nTrailing conclusion."
        atoms = parse_atoms(text)
        real = [a for a in atoms if not a.synthetic]
        assert len(real) == 1
        assert "Trailing" in real[0].content


class TestParseAtomsValidation:
    """parse_atoms() — validation and fallback."""

    def test_duplicate_ids_fallback_to_monolithic(self):
        text = "ATOM[1] deps=[] oracle=L3\nA.\n\nATOM[1] deps=[] oracle=L3\nB."
        atoms = parse_atoms(text)
        assert len(atoms) == 1
        assert atoms[0].synthetic is True
        assert atoms[0].id == 0

    def test_cycle_in_deps_fallback(self):
        text = "ATOM[1] deps=[2] oracle=L3\nA.\n\nATOM[2] deps=[1] oracle=L3\nB."
        atoms = parse_atoms(text)
        assert len(atoms) == 1
        assert atoms[0].id == 0

    def test_self_loop_fallback(self):
        text = "ATOM[1] deps=[1] oracle=L3\nA."
        atoms = parse_atoms(text)
        assert len(atoms) == 1
        assert atoms[0].id == 0

    def test_atom_count_cap(self):
        lines = [f"ATOM[{i}] deps=[] oracle=L3\nContent {i}." for i in range(1, 20)]
        text = "\n\n".join(lines)
        atoms = parse_atoms(text)
        # Exceeds cap of 12 → monolithic fallback
        assert len(atoms) == 1
        assert atoms[0].id == 0

    def test_unknown_oracle_defaults_to_L3(self):
        text = "ATOM[1] deps=[] oracle=L99\nContent."
        atoms = parse_atoms(text)
        real = [a for a in atoms if not a.synthetic]
        assert real[0].oracle == OracleType.LAYER3_LLM

    def test_dep_referencing_nonexistent_atom_fallback(self):
        text = "ATOM[1] deps=[99] oracle=L3\nContent."
        atoms = parse_atoms(text)
        assert len(atoms) == 1
        assert atoms[0].id == 0

    def test_atom_inside_fenced_code_block_ignored(self):
        text = (
            "ATOM[1] deps=[] oracle=L3\nReal content.\n\n"
            "```\nATOM[2] deps=[] oracle=L0\nFake inside code.\n```\n"
        )
        atoms = parse_atoms(text)
        real = [a for a in atoms if not a.synthetic]
        assert len(real) == 1
        assert real[0].id == 1


class TestParseAtomsFallbackOracle:
    """Monolithic fallback oracle derivation from existing sentinels."""

    def test_no_sentinels_gives_L3(self):
        text = "Just plain text, no ALETHIC checks."
        atoms = parse_atoms(text)
        assert atoms[0].oracle == OracleType.LAYER3_LLM

    def test_L0_sentinel_gives_L1(self):
        text = "Solution.\nALETHIC_L0_CHECK: DIMENSIONS OK\nMore text."
        atoms = parse_atoms(text)
        assert atoms[0].oracle == OracleType.LAYER1_BEHAVIORAL

    def test_L0_L1_sentinels_give_L2(self):
        text = (
            "Solution.\n"
            "ALETHIC_L0_CHECK: DIMENSIONS OK\n"
            "ALETHIC_L1_CHECK: BASE CASES OK\n"
        )
        atoms = parse_atoms(text)
        assert atoms[0].oracle == OracleType.LAYER2_CONSISTENCY

    def test_L0_L2_gap_gives_L1(self):
        """L0 and L2 present but L1 missing → contiguity depth is 1 → oracle=L1."""
        text = (
            "Solution.\n"
            "ALETHIC_L0_CHECK: OK\n"
            "ALETHIC_L2_CHECK: OK\n"
        )
        atoms = parse_atoms(text)
        assert atoms[0].oracle == OracleType.LAYER1_BEHAVIORAL

    def test_L0_L1_L2_gives_L3(self):
        text = (
            "Solution.\n"
            "ALETHIC_L0_CHECK: OK\n"
            "ALETHIC_L1_CHECK: OK\n"
            "ALETHIC_L2_CHECK: OK\n"
        )
        atoms = parse_atoms(text)
        assert atoms[0].oracle == OracleType.LAYER3_LLM


class TestContentHash:
    """content_hash() strips verify bodies and normalizes whitespace."""

    def test_same_math_different_whitespace(self):
        a1 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x = 1 + 2")
        a2 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x  =  1  +  2")
        assert content_hash(a1) == content_hash(a2)

    def test_different_verify_bodies_same_hash(self):
        content_a = "Math here.\n```python\ndef verify_atom_1():\n    x = 1\n    print('OK')\n```"
        content_b = "Math here.\n```python\ndef verify_atom_1():\n    x = 99\n    print('OK')\n```"
        a1 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER2_CONSISTENCY, content=content_a)
        a2 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER2_CONSISTENCY, content=content_b)
        assert content_hash(a1) == content_hash(a2)

    def test_different_math_different_hash(self):
        a1 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x = 1")
        a2 = AtomAnnotation(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x = 2")
        assert content_hash(a1) != content_hash(a2)


class TestClassifyAtomStability:
    """classify_atom_stability() classification tests."""

    def _make_atom(self, atom_id: int, content: str) -> AtomAnnotation:
        return AtomAnnotation(id=atom_id, deps=(), oracle=OracleType.LAYER3_LLM, content=content)

    def test_stable_atom(self):
        """Same content across 3 iterations with good confidence → STABLE."""
        history = [
            [self._make_atom(1, "x = 1")],
            [self._make_atom(1, "x = 1")],
            [self._make_atom(1, "x = 1")],
        ]
        result = classify_atom_stability(history, [0.8, 0.8, 0.8])
        assert result[1] == AtomStability.STABLE

    def test_stable_requires_confidence_floor(self):
        """Consistent content but low confidence → NOT STABLE (FAILING)."""
        history = [
            [self._make_atom(1, "x = 1")],
            [self._make_atom(1, "x = 1")],
            [self._make_atom(1, "x = 1")],
        ]
        result = classify_atom_stability(history, [0.3, 0.3, 0.3])
        assert result[1] == AtomStability.FAILING

    def test_oscillating_period_2(self):
        """A-B-A pattern → OSCILLATING."""
        history = [
            [self._make_atom(1, "form A")],
            [self._make_atom(1, "form B")],
            [self._make_atom(1, "form A")],
        ]
        result = classify_atom_stability(history, [0.8, 0.8, 0.8])
        assert result[1] == AtomStability.OSCILLATING

    def test_oscillating_period_3(self):
        """A-B-C-A pattern → OSCILLATING (any-cycle detection)."""
        history = [
            [self._make_atom(1, "form A")],
            [self._make_atom(1, "form B")],
            [self._make_atom(1, "form C")],
            [self._make_atom(1, "form A")],
        ]
        result = classify_atom_stability(history, [0.8, 0.8, 0.8, 0.8])
        assert result[1] == AtomStability.OSCILLATING

    def test_failing_atom(self):
        """Different content each iteration → FAILING."""
        history = [
            [self._make_atom(1, "attempt 1")],
            [self._make_atom(1, "attempt 2")],
            [self._make_atom(1, "attempt 3")],
        ]
        result = classify_atom_stability(history, [0.8, 0.8, 0.8])
        assert result[1] == AtomStability.FAILING

    def test_atom_missing_from_some_iterations(self):
        """Atom absent from iteration 2 → only iterations 1 and 3 tracked."""
        history = [
            [self._make_atom(1, "x = 1")],
            [],  # atom 1 absent
            [self._make_atom(1, "x = 1")],
        ]
        result = classify_atom_stability(history, [0.8, 0.8, 0.8])
        # Present in 2 iterations with same hash → STABLE
        assert result[1] == AtomStability.STABLE

    def test_empty_history(self):
        result = classify_atom_stability([], [])
        assert result == {}


# ---------------------------------------------------------------------------
# Helpers for TestBuildAtomContext
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402
from alethic.agent import MathAgent  # noqa: E402
from alethic.models import AgentConfig  # noqa: E402


def _make_real_atom(atom_id: int, content: str) -> AtomAnnotation:
    return AtomAnnotation(
        id=atom_id, deps=(), oracle=OracleType.LAYER3_LLM,
        content=content, synthetic=False,
    )


def _make_synthetic_atom(content: str = "whole solution") -> AtomAnnotation:
    return AtomAnnotation(
        id=0, deps=(), oracle=OracleType.LAYER3_LLM,
        content=content, synthetic=True,
    )


def _make_agent(**overrides) -> MathAgent:
    config = AgentConfig(confidence_threshold=0.82, **overrides)
    return MathAgent(api_key="sk-test", config=config)


class TestBuildAtomContext:

    def test_insufficient_history_returns_none(self):
        agent = _make_agent()
        atom = _make_real_atom(1, "step 1")
        result = agent._build_atom_context([[atom]], [0.8])
        assert result is None

    def test_all_synthetic_returns_none_and_does_not_call_classify(self):
        agent = _make_agent()
        synth = _make_synthetic_atom()
        atom_history = [[synth], [synth]]
        conf_history = [0.7, 0.75]
        with patch("alethic.agent.classify_atom_stability") as mock_classify:
            result = agent._build_atom_context(atom_history, conf_history)
        assert result is None
        mock_classify.assert_not_called()  # explicit guard, not just accident

    def test_variant_b_returns_none(self):
        agent = _make_agent(variant_b={"model": "claude-sonnet-4-6"})
        atom1 = _make_real_atom(1, "content A")
        atom2 = _make_real_atom(1, "content B")  # same id, different content = FAILING
        atom_history = [[atom1], [atom2]]
        conf_history = [0.7, 0.65]
        result = agent._build_atom_context(atom_history, conf_history)
        assert result is None

    def test_stable_atoms_advisory_contains_do_not_discard(self):
        agent = _make_agent()
        same_content = "the exact same proof step"
        atom1 = _make_real_atom(1, same_content)
        atom2 = _make_real_atom(1, same_content)
        atom_history = [[atom1], [atom2]]
        conf_history = [0.75, 0.80]  # both >= floor (0.82 * 0.85 = 0.697)
        result = agent._build_atom_context(atom_history, conf_history)
        assert result is not None
        assert "do not discard" in result.lower()
        assert "repetition" not in result.lower()

    def test_oscillating_atoms_advisory_warns_against_repetition(self):
        agent = _make_agent()
        content_a = "approach using Fourier transform"
        content_b = "approach using integration by parts"
        content_c = content_a  # cycles back → OSCILLATING
        atom1 = _make_real_atom(2, content_a)
        atom2 = _make_real_atom(2, content_b)
        atom3 = _make_real_atom(2, content_c)
        atom_history = [[atom1], [atom2], [atom3]]
        conf_history = [0.72, 0.68, 0.70]
        result = agent._build_atom_context(atom_history, conf_history)
        assert result is not None
        # Must warn about repetition
        assert "oscillat" in result.lower() or "repeat" in result.lower()
        # Must NOT say "do not discard"
        assert "do not discard" not in result.lower()

    def test_failing_atoms_advisory_distinct_from_stable(self):
        agent = _make_agent()
        atom1 = _make_real_atom(3, "attempt A")
        atom2 = _make_real_atom(3, "attempt B")
        atom_history = [[atom1], [atom2]]
        conf_history = [0.72, 0.61]  # declining
        result = agent._build_atom_context(atom_history, conf_history)
        assert result is not None
        assert "do not discard" not in result.lower()

    def test_empty_stability_dict_returns_none(self):
        """classify_atom_stability() returning empty dict → advisory is None."""
        agent = _make_agent()
        atom1 = _make_real_atom(1, "some content")
        atom2 = _make_real_atom(1, "different content")
        atom_history = [[atom1], [atom2]]
        conf_history = [0.7, 0.75]
        with patch("alethic.agent.classify_atom_stability", return_value={}):
            result = agent._build_atom_context(atom_history, conf_history)
        assert result is None

    def test_wiring_revise_receives_atom_context(self):
        """Advisory text must reach the user_message passed to _call_model."""
        agent = _make_agent()
        atom_a = _make_real_atom(1, "step A content alpha")
        atom_b = _make_real_atom(1, "step B content beta")
        atom_history = [[atom_a], [atom_b]]
        conf_history = [0.72, 0.68]

        from alethic.models import Solution, VerificationResult, Verdict
        from alethic.subagents import revise

        solution = Solution(problem="test", solution_text="sol", iteration=1)
        verification = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="needs work", confidence=0.6
        )

        captured_messages = []
        def fake_call_model(client, *, system, user_message, **kwargs):
            captured_messages.append(user_message)
            return "CHANGES MADE:\nnone\n\nREVISED SOLUTION:\nfixed\n\nCONCLUSION: done"

        advisory = agent._build_atom_context(atom_history, conf_history)
        assert advisory is not None, "pre-condition: advisory must be non-None for this test"

        import alethic.subagents as subagents_module
        with patch.object(subagents_module, "_call_model", side_effect=fake_call_model):
            revise(
                None,  # client (unused with mock)
                problem="test",
                solution=solution,
                verification=verification,
                config=agent.config,
                revision_number=1,
                atom_context=advisory,
            )

        assert len(captured_messages) == 1
        assert advisory in captured_messages[0], (
            "Advisory text must appear in user_message passed to _call_model"
        )
