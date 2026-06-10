"""Tests for src/alethic/proof_graph.py (v3.8 foundation)."""

from __future__ import annotations

import pytest

from alethic.atoms import AtomAnnotation
from alethic.models import OracleType
from alethic.proof_graph import (
    _SUBDIVISION_ID_FLOOR,
    AtomNode,
    AtomStatus,
    ProofGraph,
)

# ──────────────────────────────────────────────────────────────────────────
# AtomNode
# ──────────────────────────────────────────────────────────────────────────


class TestAtomNode:
    def test_q_value_zero_visits_returns_zero(self):
        node = AtomNode(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
        assert node.q_value == 0.0

    def test_q_value_mean_across_visits(self):
        node = AtomNode(id=1, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
        node.visit_count = 3
        node.total_value = 2.4  # mean = 0.8
        assert node.q_value == pytest.approx(0.8)

    def test_from_annotation_real_atom_defaults_pending(self):
        ann = AtomAnnotation(id=5, deps=(), oracle=OracleType.LAYER3_LLM, content="step five")
        node = AtomNode.from_annotation(ann)
        assert node.status == AtomStatus.PENDING
        assert node.id == 5
        assert node.deps == ()
        assert node.oracle == OracleType.LAYER3_LLM
        assert node.content == "step five"
        assert node.synthetic is False

    def test_from_annotation_preamble_auto_anchors(self):
        ann = AtomAnnotation(id=-1, deps=(), oracle=OracleType.LAYER3_LLM,
                             content="problem statement", synthetic=True)
        node = AtomNode.from_annotation(ann)
        assert node.status == AtomStatus.ANCHORED
        assert node.synthetic is True

    def test_from_annotation_residual_auto_anchors(self):
        ann = AtomAnnotation(id=-2, deps=(1,), oracle=OracleType.LAYER3_LLM,
                             content="trailing prose", synthetic=True)
        node = AtomNode.from_annotation(ann)
        assert node.status == AtomStatus.ANCHORED

    def test_from_annotation_monolithic_not_auto_anchored(self):
        """id=0 monolithic is synthetic but IS the solution — must verify."""
        ann = AtomAnnotation(id=0, deps=(), oracle=OracleType.LAYER3_LLM,
                             content="whole proof", synthetic=True)
        node = AtomNode.from_annotation(ann)
        assert node.status == AtomStatus.PENDING

    def test_from_annotation_explicit_status_override(self):
        ann = AtomAnnotation(id=3, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
        node = AtomNode.from_annotation(ann, status=AtomStatus.ANCHORED)
        assert node.status == AtomStatus.ANCHORED

    def test_from_annotation_explicit_status_ignored_for_preamble(self):
        """Caller cannot un-anchor a preamble even by passing status=PENDING —
        these are problem text by construction."""
        ann = AtomAnnotation(id=-1, deps=(), oracle=OracleType.LAYER3_LLM,
                             content="x", synthetic=True)
        node = AtomNode.from_annotation(ann, status=AtomStatus.PENDING)
        assert node.status == AtomStatus.ANCHORED


# ──────────────────────────────────────────────────────────────────────────
# ProofGraph construction
# ──────────────────────────────────────────────────────────────────────────


_DEFAULT_CONTENT = object()  # sentinel so callers can request empty content


def _make_atom(id_: int, deps: tuple[int, ...] = (), content=_DEFAULT_CONTENT,
               synthetic: bool = False) -> AtomAnnotation:
    """Concise atom-annotation factory for tests.

    ``content`` defaults to ``f"atom_{id}"`` but accepts ``""`` explicitly
    (the empty-content path is needed to test that ``assemble_solution``
    skips contentless atoms).
    """
    if content is _DEFAULT_CONTENT:
        content = f"atom_{id_}"
    return AtomAnnotation(
        id=id_, deps=deps, oracle=OracleType.LAYER3_LLM,
        content=content, synthetic=synthetic,
    )


class TestProofGraphConstruction:
    def test_empty_iterable(self):
        graph = ProofGraph.from_atoms([])
        assert graph.atoms == {}
        assert graph.next_id == _SUBDIVISION_ID_FLOOR

    def test_normal_atoms(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        assert set(graph.atoms.keys()) == {1, 2, 3}
        assert graph.next_id == _SUBDIVISION_ID_FLOOR
        assert all(n.status == AtomStatus.PENDING for n in graph.atoms.values())

    def test_monolithic_only(self):
        graph = ProofGraph.from_atoms([_make_atom(0, content="whole", synthetic=True)])
        assert graph.atoms[0].status == AtomStatus.PENDING
        assert graph.atoms[0].synthetic is True

    def test_with_preamble_and_residual(self):
        atoms = [
            _make_atom(-1, content="setup", synthetic=True),
            _make_atom(1, content="step1"),
            _make_atom(2, deps=(1,), content="step2"),
            _make_atom(-2, deps=(2,), content="conclusion", synthetic=True),
        ]
        graph = ProofGraph.from_atoms(atoms)
        assert graph.atoms[-1].status == AtomStatus.ANCHORED
        assert graph.atoms[1].status == AtomStatus.PENDING
        assert graph.atoms[2].status == AtomStatus.PENDING
        assert graph.atoms[-2].status == AtomStatus.ANCHORED

    def test_next_id_above_floor_when_ids_small(self):
        graph = ProofGraph.from_atoms([_make_atom(5)])
        assert graph.next_id == _SUBDIVISION_ID_FLOOR

    def test_next_id_above_max_when_large_ids(self):
        """Hypothetical: if an atom somehow had id above the floor, next_id
        must still be strictly greater so subdivision children can't clash."""
        atoms = [_make_atom(_SUBDIVISION_ID_FLOOR + 5)]
        graph = ProofGraph.from_atoms(atoms)
        assert graph.next_id == _SUBDIVISION_ID_FLOOR + 6


# ──────────────────────────────────────────────────────────────────────────
# Status views (gaps / anchors)
# ──────────────────────────────────────────────────────────────────────────


class TestStatusViews:
    def test_gaps_empty_when_none_failed(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        assert graph.gaps() == []

    def test_anchors_empty_initially(self):
        graph = ProofGraph.from_atoms([_make_atom(1), _make_atom(2, (1,))])
        assert graph.anchors() == []

    def test_gaps_and_anchors_after_verification(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.ANCHORED
        assert [n.id for n in graph.gaps()] == [2]
        assert [n.id for n in graph.anchors()] == [1, 3]

    def test_gaps_in_topo_order(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.FAILED
        # 1 -> 2 and 1 -> 3 (2 and 3 are independent siblings)
        # Both 2 and 3 should appear; relative order is by ID due to determinism
        assert [n.id for n in graph.gaps()] == [2, 3]


# ──────────────────────────────────────────────────────────────────────────
# Neighbors
# ──────────────────────────────────────────────────────────────────────────


class TestNeighbors:
    def test_middle_gap_has_both_flanks(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.ANCHORED
        left, right = graph.neighbors(2)
        assert left is not None and left.id == 1
        assert right is not None and right.id == 3

    def test_first_atom_no_left_flank(self):
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.FAILED
        graph.atoms[2].status = AtomStatus.ANCHORED
        left, right = graph.neighbors(1)
        assert left is None
        assert right is not None and right.id == 2

    def test_last_atom_no_right_flank(self):
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        left, right = graph.neighbors(2)
        assert left is not None and left.id == 1
        assert right is None

    def test_no_flanks_when_all_pending(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        left, right = graph.neighbors(2)
        assert left is None
        assert right is None

    def test_skips_failed_neighbors(self):
        """If neighbour atoms exist but are failed/pending, they don't count
        — only ANCHORED atoms provide microkernel context."""
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,)),
                 _make_atom(4, (3,)), _make_atom(5, (4,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.FAILED
        graph.atoms[4].status = AtomStatus.FAILED
        graph.atoms[5].status = AtomStatus.ANCHORED
        left, right = graph.neighbors(3)
        assert left is not None and left.id == 1
        assert right is not None and right.id == 5

    def test_unknown_atom_id_raises(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        with pytest.raises(KeyError, match="not in graph"):
            graph.neighbors(99)


# ──────────────────────────────────────────────────────────────────────────
# Subdivide
# ──────────────────────────────────────────────────────────────────────────


class TestSubdivide:
    def test_basic_two_child_subdivision(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.ANCHORED

        new_ids = graph.subdivide(2)

        assert len(new_ids) == 2
        assert graph.atoms[2].status == AtomStatus.SUBDIVIDED
        assert graph.atoms[2].child_ids == new_ids

    def test_default_n_children_is_two(self):
        atoms = [_make_atom(1)]
        graph = ProofGraph.from_atoms(atoms)
        new_ids = graph.subdivide(1)
        assert len(new_ids) == 2

    def test_children_inherit_level_and_parent(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        new_ids = graph.subdivide(1, n_children=3)
        for cid in new_ids:
            child = graph.atoms[cid]
            assert child.level == 1
            assert child.parent_id == 1

    def test_children_are_chained(self):
        """child_0.deps = parent.deps; child_i.deps = (child_{i-1}.id,)."""
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        new_ids = graph.subdivide(2, n_children=3)

        child_0 = graph.atoms[new_ids[0]]
        child_1 = graph.atoms[new_ids[1]]
        child_2 = graph.atoms[new_ids[2]]
        assert child_0.deps == (1,)              # inherits parent's deps
        assert child_1.deps == (new_ids[0],)
        assert child_2.deps == (new_ids[1],)

    def test_dependents_rewired_to_chain_tail(self):
        """Atoms that depended on the parent now depend on the chain tail."""
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        new_ids = graph.subdivide(2)
        # Atom 3 used to depend on 2 → now depends on tail of chain
        assert graph.atoms[3].deps == (new_ids[-1],)

    def test_unrelated_atoms_deps_untouched(self):
        atoms = [_make_atom(1), _make_atom(2), _make_atom(3, (1,)), _make_atom(4, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.subdivide(1)
        # Atom 4 depends on 2, not 1 — must stay
        assert graph.atoms[4].deps == (2,)

    def test_n_children_one_rejected(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        with pytest.raises(ValueError, match="n_children must be >= 2"):
            graph.subdivide(1, n_children=1)

    def test_resubdividing_rejected(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        graph.subdivide(1)
        with pytest.raises(ValueError, match="already subdivided"):
            graph.subdivide(1)

    def test_unknown_atom_id_rejected(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        with pytest.raises(KeyError, match="not in graph"):
            graph.subdivide(99)

    def test_next_id_advances_per_child(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        start = graph.next_id
        graph.subdivide(1, n_children=3)
        assert graph.next_id == start + 3


# ──────────────────────────────────────────────────────────────────────────
# Downstream
# ──────────────────────────────────────────────────────────────────────────


class TestDownstream:
    def test_linear_chain(self):
        atoms = [_make_atom(1), _make_atom(2, (1,)), _make_atom(3, (2,))]
        graph = ProofGraph.from_atoms(atoms)
        assert set(graph.downstream(1)) == {2, 3}
        assert set(graph.downstream(2)) == {3}
        assert graph.downstream(3) == []

    def test_dag_branching(self):
        # 1 -> 2, 1 -> 3, 2 -> 4, 3 -> 4
        atoms = [_make_atom(1), _make_atom(2, (1,)),
                 _make_atom(3, (1,)), _make_atom(4, (2, 3))]
        graph = ProofGraph.from_atoms(atoms)
        assert set(graph.downstream(1)) == {2, 3, 4}
        assert set(graph.downstream(2)) == {4}

    def test_unknown_atom_id_rejected(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        with pytest.raises(KeyError, match="not in graph"):
            graph.downstream(99)


# ──────────────────────────────────────────────────────────────────────────
# is_complete
# ──────────────────────────────────────────────────────────────────────────


class TestIsComplete:
    def test_all_anchored_complete(self):
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        for n in graph.atoms.values():
            n.status = AtomStatus.ANCHORED
        assert graph.is_complete() is True

    def test_any_pending_incomplete(self):
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        # atom 2 still PENDING
        assert graph.is_complete() is False

    def test_any_failed_incomplete(self):
        atoms = [_make_atom(1), _make_atom(2, (1,))]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        assert graph.is_complete() is False

    def test_subdivided_with_all_children_anchored_complete(self):
        atoms = [_make_atom(1)]
        graph = ProofGraph.from_atoms(atoms)
        child_ids = graph.subdivide(1)
        for cid in child_ids:
            graph.atoms[cid].status = AtomStatus.ANCHORED
        assert graph.is_complete() is True

    def test_subdivided_with_one_pending_child_incomplete(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        child_ids = graph.subdivide(1, n_children=3)
        graph.atoms[child_ids[0]].status = AtomStatus.ANCHORED
        graph.atoms[child_ids[1]].status = AtomStatus.ANCHORED
        # child_ids[2] still PENDING
        assert graph.is_complete() is False

    def test_nested_subdivision_complete(self):
        graph = ProofGraph.from_atoms([_make_atom(1)])
        level1_ids = graph.subdivide(1)
        # Subdivide the first child
        level2_ids = graph.subdivide(level1_ids[0])
        # Anchor everything that needs anchoring
        for cid in level2_ids:
            graph.atoms[cid].status = AtomStatus.ANCHORED
        graph.atoms[level1_ids[1]].status = AtomStatus.ANCHORED
        assert graph.is_complete() is True

    def test_preamble_and_residual_dont_block(self):
        atoms = [
            _make_atom(-1, content="setup", synthetic=True),
            _make_atom(1, content="step"),
            _make_atom(-2, deps=(1,), content="end", synthetic=True),
        ]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        assert graph.is_complete() is True

    def test_monolithic_must_anchor(self):
        graph = ProofGraph.from_atoms([_make_atom(0, content="whole", synthetic=True)])
        assert graph.is_complete() is False
        graph.atoms[0].status = AtomStatus.ANCHORED
        assert graph.is_complete() is True


# ──────────────────────────────────────────────────────────────────────────
# assemble_solution
# ──────────────────────────────────────────────────────────────────────────


class TestAssembleSolution:
    def test_empty_graph(self):
        graph = ProofGraph()
        assert graph.assemble_solution() == ""

    def test_all_pending_returns_empty(self):
        graph = ProofGraph.from_atoms([_make_atom(1, content="x"), _make_atom(2, (1,), content="y")])
        assert graph.assemble_solution() == ""

    def test_all_anchored_in_topo_order(self):
        atoms = [_make_atom(1, content="first"),
                 _make_atom(2, deps=(1,), content="second"),
                 _make_atom(3, deps=(2,), content="third")]
        graph = ProofGraph.from_atoms(atoms)
        for n in graph.atoms.values():
            n.status = AtomStatus.ANCHORED
        assert graph.assemble_solution() == "first\n\nsecond\n\nthird"

    def test_partial_assembly_uses_anchored_only(self):
        atoms = [_make_atom(1, content="A"), _make_atom(2, (1,), content="B"),
                 _make_atom(3, (2,), content="C")]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[2].status = AtomStatus.FAILED
        graph.atoms[3].status = AtomStatus.ANCHORED
        # Only A and C should appear (B is failed); topo order preserved
        assert graph.assemble_solution() == "A\n\nC"

    def test_subdivided_parent_skipped_children_appear(self):
        """After subdivision, the parent is skipped; children's anchored content
        takes its place — positioned correctly relative to dependents."""
        atoms = [_make_atom(1, content="A"),
                 _make_atom(2, (1,), content="OLD_B"),
                 _make_atom(3, (2,), content="C")]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        graph.atoms[3].status = AtomStatus.ANCHORED
        child_ids = graph.subdivide(2)
        # Populate children
        graph.atoms[child_ids[0]].content = "B1"
        graph.atoms[child_ids[0]].status = AtomStatus.ANCHORED
        graph.atoms[child_ids[1]].content = "B2"
        graph.atoms[child_ids[1]].status = AtomStatus.ANCHORED

        # Expected order: A, B1, B2, C (because C's deps got rewritten to chain tail)
        assert graph.assemble_solution() == "A\n\nB1\n\nB2\n\nC"

    def test_preamble_and_residual_included(self):
        atoms = [
            _make_atom(-1, content="setup", synthetic=True),
            _make_atom(1, content="proof"),
            _make_atom(-2, deps=(1,), content="conclude", synthetic=True),
        ]
        graph = ProofGraph.from_atoms(atoms)
        graph.atoms[1].status = AtomStatus.ANCHORED
        # -1 and -2 auto-anchor; all should be in output
        out = graph.assemble_solution()
        assert "setup" in out
        assert "proof" in out
        assert "conclude" in out

    def test_empty_content_atoms_skipped(self):
        atoms = [_make_atom(1, content="A"), _make_atom(2, (1,), content="")]
        graph = ProofGraph.from_atoms(atoms)
        for n in graph.atoms.values():
            n.status = AtomStatus.ANCHORED
        assert graph.assemble_solution() == "A"


# ──────────────────────────────────────────────────────────────────────────
# Serialization (to_dict / from_dict)
# ──────────────────────────────────────────────────────────────────────────


class TestSerialization:
    """to_dict/from_dict round-trips for tree-mode checkpointing."""

    def _make_node(self) -> AtomNode:
        return AtomNode(
            id=3,
            deps=(1, 2),
            oracle=OracleType.LAYER3_LLM,
            content="Step three",
            synthetic=False,
            status=AtomStatus.FAILED,
            level=1,
            parent_id=7,
            child_ids=[1000000, 1000001],
            visit_count=4,
            total_value=2.5,
            techniques_tried=["induction", "telescoping"],
        )

    def test_atomnode_roundtrip(self):
        node = self._make_node()
        restored = AtomNode.from_dict(node.to_dict())
        assert restored == node

    def test_atomnode_to_dict_is_json_safe(self):
        import json

        node = self._make_node()
        text = json.dumps(node.to_dict())
        assert "layer3_llm" in text
        assert "failed" in text

    def test_proofgraph_roundtrip_preserves_topology_and_puct(self):
        g = ProofGraph()
        g.atoms[1] = AtomNode(
            id=1, deps=(), oracle=OracleType.LAYER3_LLM,
            content="A", status=AtomStatus.ANCHORED,
        )
        g.atoms[2] = AtomNode(
            id=2, deps=(1,), oracle=OracleType.LAYER1_BEHAVIORAL,
            content="B", status=AtomStatus.FAILED, visit_count=2, total_value=0.9,
        )
        g.subdivide(2, n_children=2)

        restored = ProofGraph.from_dict(g.to_dict())
        assert restored.next_id == g.next_id
        assert set(restored.atoms) == set(g.atoms)
        assert restored.atoms[2].status == AtomStatus.SUBDIVIDED
        assert restored.atoms[2].child_ids == g.atoms[2].child_ids
        assert restored.atoms[2].visit_count == 2
        assert restored._topo_order() == g._topo_order()

    def test_proofgraph_dict_keys_are_strings(self):
        """JSON object keys must be strings; from_dict converts back to int."""
        g = ProofGraph()
        g.atoms[5] = AtomNode(id=5, deps=(), oracle=OracleType.LAYER3_LLM, content="x")
        d = g.to_dict()
        assert list(d["atoms"].keys()) == ["5"]
        restored = ProofGraph.from_dict(d)
        assert 5 in restored.atoms

    def test_to_dict_covers_all_dataclass_fields(self):
        """A field added to AtomNode without updating to_dict would silently
        lose search state on checkpoint resume — fail loudly here instead."""
        from dataclasses import fields

        assert set(self._make_node().to_dict()) == {f.name for f in fields(AtomNode)}
