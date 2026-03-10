"""Tests for atom annotation parsing and validation."""

from __future__ import annotations

import pytest

from alethic.atoms import AtomAnnotation, parse_atoms
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
