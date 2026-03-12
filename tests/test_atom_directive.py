"""Tests for _combine() and _build_atom_focus_directive()."""

import pytest
from alethic.agent import _build_atom_focus_directive, _combine
from alethic.atoms import AtomAnnotation, AtomStability
from alethic.models import OracleType


def _make_atom(
    id: int,
    oracle: OracleType,
    content: str = "content",
    synthetic: bool = False,
) -> AtomAnnotation:
    return AtomAnnotation(id=id, deps=(), oracle=oracle, content=content, synthetic=synthetic)


class TestCombine:
    def test_both_none(self):
        assert _combine(None, None) is None

    def test_a_only(self):
        assert _combine("foo", None) == "foo"

    def test_b_only(self):
        assert _combine(None, "bar") == "bar"

    def test_both_present(self):
        assert _combine("foo", "bar") == "foo\n\nbar"

    def test_strips_leading_newline_a(self):
        assert _combine("\nfoo", "bar") == "foo\n\nbar"

    def test_strips_trailing_newline_a(self):
        assert _combine("foo\n", "bar") == "foo\n\nbar"

    def test_strips_leading_newline_b(self):
        assert _combine("foo", "\nbar") == "foo\n\nbar"

    def test_strips_trailing_newline_b(self):
        assert _combine("foo", "bar\n") == "foo\n\nbar"

    def test_empty_string_a_treated_as_falsy(self):
        assert _combine("", "bar") == "bar"

    def test_empty_string_b_treated_as_falsy(self):
        assert _combine("foo", "") == "foo"

    def test_newlines_only_a_treated_as_falsy(self):
        assert _combine("\n", "bar") == "bar"


class TestBuildAtomFocusDirective:

    def test_empty_atoms_returns_none(self):
        assert _build_atom_focus_directive([], {}) is None

    def test_all_stable_returns_none(self):
        atom = _make_atom(1, OracleType.LAYER3_LLM)
        stability = {1: AtomStability.STABLE}
        assert _build_atom_focus_directive([atom], stability) is None

    def test_all_synthetic_returns_none(self):
        atom = _make_atom(0, OracleType.LAYER3_LLM, synthetic=True)
        stability = {0: AtomStability.FAILING}
        assert _build_atom_focus_directive([atom], stability) is None

    def test_failing_l3_oracle_goes_high(self):
        atom = _make_atom(1, OracleType.LAYER3_LLM)
        stability = {1: AtomStability.FAILING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "ATOM[1]" in result
        assert "HIGH" in result

    def test_failing_l4_oracle_goes_high(self):
        atom = _make_atom(2, OracleType.LAYER4_CONSENSUS)
        stability = {2: AtomStability.FAILING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "HIGH" in result
        assert "ATOM[2]" in result

    def test_oscillating_l2_passing_sentinel_goes_reduced(self):
        # Content has a non-failing ALETHIC_L2_CHECK sentinel
        content = "some math\nALETHIC_L2_CHECK: PASS consistency verified\nmore math"
        atom = _make_atom(3, OracleType.LAYER2_CONSISTENCY, content=content)
        stability = {3: AtomStability.OSCILLATING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "ATOM[3]" in result
        assert "REDUCED" in result

    def test_oscillating_l2_absent_sentinel_goes_high(self):
        # No ALETHIC_L2_CHECK line in content
        atom = _make_atom(4, OracleType.LAYER2_CONSISTENCY, content="pure prose")
        stability = {4: AtomStability.OSCILLATING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "HIGH" in result
        assert "ATOM[4]" in result

    def test_explicit_failed_sentinel_routes_to_high(self):
        """Explicit FAILED marker must route to HIGH, not REDUCED."""
        content = "math\nALETHIC_L1_CHECK: FAILED — base case fails at n=0\nmore"
        atom = _make_atom(5, OracleType.LAYER1_BEHAVIORAL, content=content)
        stability = {5: AtomStability.OSCILLATING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "HIGH" in result
        assert "ATOM[5]" in result
        # Must NOT be in REDUCED tier
        if "REDUCED" in result:
            assert "ATOM[5]" not in result.split("REDUCED")[1].split("\n")[0]

    def test_l2_no_sentinel_line_goes_high(self):
        atom = _make_atom(6, OracleType.LAYER2_CONSISTENCY, content="no sentinel here")
        stability = {6: AtomStability.FAILING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "HIGH" in result

    def test_mixed_high_and_reduced(self):
        atoms = [
            _make_atom(7, OracleType.LAYER4_CONSENSUS),
            _make_atom(
                8,
                OracleType.LAYER2_CONSISTENCY,
                content="math\nALETHIC_L2_CHECK: confirmed\nmore",
            ),
        ]
        stability = {7: AtomStability.FAILING, 8: AtomStability.OSCILLATING}
        result = _build_atom_focus_directive(atoms, stability)
        assert result is not None
        assert "HIGH" in result
        assert "REDUCED" in result
        assert "ATOM[7]" in result
        assert "ATOM[8]" in result
        # HIGH section appears before REDUCED section
        assert result.index("HIGH") < result.index("REDUCED")

    def test_adversarial_oracle_goes_high(self):
        """LAYER3_LLM_ADVERSARIAL must be treated as HIGH."""
        atom = _make_atom(9, OracleType.LAYER3_LLM_ADVERSARIAL)
        stability = {9: AtomStability.FAILING}
        result = _build_atom_focus_directive([atom], stability)
        assert result is not None
        assert "HIGH" in result

    def test_synthetic_excluded_alongside_real_atom(self):
        """Synthetic atoms filtered out even when real atoms present."""
        real = _make_atom(1, OracleType.LAYER3_LLM)
        synthetic = _make_atom(0, OracleType.LAYER3_LLM, synthetic=True)
        stability = {1: AtomStability.FAILING, 0: AtomStability.FAILING}
        result = _build_atom_focus_directive([real, synthetic], stability)
        assert result is not None
        assert "ATOM[1]" in result
        assert "ATOM[0]" not in result

    def test_synthetic_positive_id_excluded(self):
        """synthetic=True filter applies regardless of id value."""
        synthetic = _make_atom(99, OracleType.LAYER3_LLM, synthetic=True)
        stability = {99: AtomStability.FAILING}
        result = _build_atom_focus_directive([synthetic], stability)
        assert result is None  # all-synthetic → None

    def test_unknown_stability_treated_as_failing(self):
        """Atoms not in stability dict default to FAILING → go through tier logic."""
        atom = _make_atom(10, OracleType.LAYER3_LLM)
        result = _build_atom_focus_directive([atom], {})  # empty stability dict
        assert result is not None
        assert "HIGH" in result
