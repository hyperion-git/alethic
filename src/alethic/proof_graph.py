"""Proof graph for v3.8 hierarchical proof search.

A ``ProofGraph`` tracks the status of every atom in a candidate solution as
verification progresses. Failed atoms become "gaps" that the search layer
fills via the GVR microkernel; verified atoms become "anchors" that provide
flanking context for filling neighbouring gaps. The graph is the foundation
layer for v3.8 (see
``docs/superpowers/specs/2026-04-11-v3.8-tree-search-design.md``) — pure
data, no API calls. The search layer in ``search.py`` drives state
transitions; this module just stores them.

The structure parallels ``alethic.atoms.AtomAnnotation`` but is mutable.
``AtomAnnotation`` stays frozen so existing v3.5+ atom-parsing code is
unaffected; ``AtomNode`` carries the evolving PUCT and subdivision state
required by the tree search.
"""

from __future__ import annotations

import bisect
import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from alethic.atoms import AtomAnnotation
    from alethic.models import OracleType

# Subdivision IDs start above the typical atom-ID space so newly created
# child atoms can never collide with IDs the generator originally emitted.
# Existing ``parse_atoms`` enforces ``MAX_ATOMS = 12`` per solution, so
# 1,000,000 leaves plenty of headroom even after repeated subdivision.
_SUBDIVISION_ID_FLOOR = 1_000_000


class AtomStatus(enum.Enum):
    """Lifecycle state of an atom in the proof graph."""

    PENDING = "pending"          # not yet verified
    ANCHORED = "anchored"        # verified — usable as a neighbour context
    FAILED = "failed"            # verification rejected; this is a gap
    SUBDIVIDED = "subdivided"    # replaced by a chain of child atoms


@dataclass
class AtomNode:
    """A single node in the proof graph.

    Mirrors the data shape of ``AtomAnnotation`` (id, deps, oracle, content,
    synthetic) and adds mutable search state (status, PUCT counters,
    subdivision tracking). ``AtomNode`` is intentionally not ``frozen`` —
    search progress requires mutating ``visit_count``, ``total_value``,
    ``techniques_tried``, ``status``, and ``child_ids``.
    """

    id: int
    deps: tuple[int, ...]
    oracle: OracleType
    content: str
    synthetic: bool = False

    status: AtomStatus = AtomStatus.PENDING
    level: int = 0                                # subdivision depth
    parent_id: int | None = None
    child_ids: list[int] = field(default_factory=list)

    visit_count: int = 0
    total_value: float = 0.0                      # sum of verification confidences
    techniques_tried: list[str] = field(default_factory=list)

    @property
    def q_value(self) -> float:
        """Mean verification confidence across all attempts on this atom.

        Returns 0.0 when no visits have occurred — PUCT then falls back to
        the exploration term, which is the desired behaviour for cold gaps.
        """
        if self.visit_count == 0:
            return 0.0
        return self.total_value / self.visit_count

    @classmethod
    def from_annotation(
        cls,
        annotation: AtomAnnotation,
        *,
        status: AtomStatus = AtomStatus.PENDING,
    ) -> AtomNode:
        """Lift an ``AtomAnnotation`` into a mutable ``AtomNode``.

        Preamble (id=-1) and residual (id=-2) synthetic atoms auto-anchor:
        they are problem text or trailing boilerplate, not substantive
        proof steps, so they must never enter the gap pool. The monolithic
        fallback (id=0, synthetic=True) is *not* auto-anchored — it is the
        whole solution and must be verified normally.
        """
        if annotation.synthetic and annotation.id < 0:
            effective_status = AtomStatus.ANCHORED
        else:
            effective_status = status
        return cls(
            id=annotation.id,
            deps=annotation.deps,
            oracle=annotation.oracle,
            content=annotation.content,
            synthetic=annotation.synthetic,
            status=effective_status,
        )


@dataclass
class ProofGraph:
    """Mutable DAG of ``AtomNode`` instances tracking proof-search progress."""

    atoms: dict[int, AtomNode] = field(default_factory=dict)
    next_id: int = _SUBDIVISION_ID_FLOOR

    @classmethod
    def from_atoms(cls, annotations: Iterable[AtomAnnotation]) -> ProofGraph:
        """Build a fresh ProofGraph from parsed ``AtomAnnotation`` objects."""
        atoms: dict[int, AtomNode] = {}
        max_id = 0
        for ann in annotations:
            node = AtomNode.from_annotation(ann)
            atoms[node.id] = node
            if node.id > max_id:
                max_id = node.id
        next_id = max(max_id + 1, _SUBDIVISION_ID_FLOOR)
        return cls(atoms=atoms, next_id=next_id)

    # ── Status views ─────────────────────────────────────────────────────

    def gaps(self) -> list[AtomNode]:
        """All FAILED atoms in topological order."""
        order = self._topo_order()
        by_id = self.atoms
        return [by_id[i] for i in order if by_id[i].status == AtomStatus.FAILED]

    def anchors(self) -> list[AtomNode]:
        """All ANCHORED atoms in topological order."""
        order = self._topo_order()
        by_id = self.atoms
        return [by_id[i] for i in order if by_id[i].status == AtomStatus.ANCHORED]

    def neighbors(
        self, atom_id: int
    ) -> tuple[AtomNode | None, AtomNode | None]:
        """Return ``(left_anchor, right_anchor)`` flanking the given atom.

        Uses topological order rather than ID order so subdivision (which
        assigns child IDs from the high-numbered subdivision pool) still
        produces semantically correct flanks. Only ANCHORED atoms count —
        the microkernel needs solid context, not unverified or failed text.
        Either side may be ``None`` if no anchor is found in that direction.
        """
        if atom_id not in self.atoms:
            raise KeyError(f"atom_id {atom_id} not in graph")
        order = self._topo_order()
        target_idx = order.index(atom_id)

        left: AtomNode | None = None
        for i in range(target_idx - 1, -1, -1):
            node = self.atoms[order[i]]
            if node.status == AtomStatus.ANCHORED:
                left = node
                break

        right: AtomNode | None = None
        for i in range(target_idx + 1, len(order)):
            node = self.atoms[order[i]]
            if node.status == AtomStatus.ANCHORED:
                right = node
                break

        return (left, right)

    # ── Structural mutation ──────────────────────────────────────────────

    def subdivide(self, atom_id: int, n_children: int = 2) -> list[int]:
        """Replace a single atom with a chain of ``n_children`` sub-atoms.

        The parent transitions to SUBDIVIDED. Children form a dependency
        chain: ``child_0`` inherits the parent's deps, ``child_i`` (i > 0)
        depends on ``child_{i-1}``. Any atom that previously depended on
        the parent has its dep list rewritten to point at the *last* child
        — without this rewrite, topological assembly would place those
        dependents incorrectly relative to the new chain.

        Returns the list of newly assigned child IDs in chain order.

        Raises:
            KeyError: ``atom_id`` is not in the graph.
            ValueError: ``n_children`` < 2, or the atom is already subdivided.
        """
        if atom_id not in self.atoms:
            raise KeyError(f"atom_id {atom_id} not in graph")
        if n_children < 2:
            raise ValueError(
                f"n_children must be >= 2 to be a meaningful subdivision, got {n_children}"
            )
        parent = self.atoms[atom_id]
        if parent.status == AtomStatus.SUBDIVIDED:
            raise ValueError(f"atom {atom_id} already subdivided")

        new_ids: list[int] = []
        for i in range(n_children):
            child_id = self.next_id
            self.next_id += 1
            deps = parent.deps if i == 0 else (new_ids[-1],)
            child = AtomNode(
                id=child_id,
                deps=deps,
                oracle=parent.oracle,
                content="",
                synthetic=False,
                status=AtomStatus.PENDING,
                level=parent.level + 1,
                parent_id=parent.id,
            )
            self.atoms[child_id] = child
            new_ids.append(child_id)

        parent.status = AtomStatus.SUBDIVIDED
        parent.child_ids = list(new_ids)

        # Rewrite dependents: anyone who depended on the parent now depends
        # on the chain's tail. This preserves topological correctness when
        # the parent is skipped during assembly.
        tail_id = new_ids[-1]
        for node in self.atoms.values():
            if node.id == parent.id or node.id in new_ids:
                continue
            if parent.id in node.deps:
                node.deps = tuple(
                    tail_id if d == parent.id else d for d in node.deps
                )

        return new_ids

    # ── Reachability / completion ────────────────────────────────────────

    def downstream(self, atom_id: int) -> list[int]:
        """Atom IDs that transitively depend on ``atom_id``.

        BFS through the reverse adjacency graph. Result excludes the input
        atom itself. Order reflects BFS discovery — callers that need
        topological order should compose with ``_topo_order()``.
        """
        if atom_id not in self.atoms:
            raise KeyError(f"atom_id {atom_id} not in graph")

        reverse: dict[int, list[int]] = {}
        for node in self.atoms.values():
            for dep in node.deps:
                reverse.setdefault(dep, []).append(node.id)

        visited: set[int] = set()
        queue: list[int] = list(reverse.get(atom_id, []))
        order: list[int] = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            order.append(current)
            queue.extend(reverse.get(current, []))
        return order

    def is_complete(self) -> bool:
        """True when every non-synthetic atom is anchored (transitively).

        Synthetic preamble/residual atoms (id < 0) auto-anchor in
        ``from_annotation`` so they never block completion. The monolithic
        atom (id = 0, synthetic=True) does count — it represents the whole
        solution and must be verified.
        """
        for node in self.atoms.values():
            if node.synthetic and node.id < 0:
                continue
            if not self._is_resolved(node.id, set()):
                return False
        return True

    def _is_resolved(self, atom_id: int, visited: set[int]) -> bool:
        """An atom resolves if anchored, or subdivided with resolved children."""
        if atom_id in visited:
            # Cycle — defensive guard. The DAG invariant should prevent this,
            # but a search-layer bug could violate it; fail safely.
            return False
        visited.add(atom_id)
        node = self.atoms[atom_id]
        if node.status == AtomStatus.ANCHORED:
            return True
        if node.status == AtomStatus.SUBDIVIDED:
            return all(self._is_resolved(c, visited) for c in node.child_ids)
        return False

    # ── Assembly ─────────────────────────────────────────────────────────

    def assemble_solution(self) -> str:
        """Concatenate anchored atom contents in topological order.

        Subdivided atoms are skipped (their children appear in their place
        through topological ordering, because dep-rewriting in
        ``subdivide()`` redirects dependents through the child chain).
        Auto-anchored synthetics (preamble/residual) are included so the
        emitted text retains the framing prose the generator produced.

        Returns whatever portion of the proof is currently anchored — when
        ``is_complete()`` returns True this is the full proof; otherwise it
        is the best partial assembly the graph can support.
        """
        parts: list[str] = []
        for atom_id in self._topo_order():
            node = self.atoms[atom_id]
            if node.status == AtomStatus.SUBDIVIDED:
                continue
            if not node.content:
                continue
            include = node.status == AtomStatus.ANCHORED or (
                node.synthetic and node.id < 0
            )
            if include:
                parts.append(node.content)
        return "\n\n".join(parts)

    def _topo_order(self) -> list[int]:
        """Kahn's-algorithm topological sort of the atom DAG.

        Atoms with the same in-degree-zero status are processed in
        ascending-ID order for determinism. Deps that reference IDs not
        present in the graph are ignored (defensive — should not happen
        under normal usage but a search bug could leave a dangling dep).
        """
        in_degree: dict[int, int] = {aid: 0 for aid in self.atoms}
        adj: dict[int, list[int]] = {aid: [] for aid in self.atoms}
        for node in self.atoms.values():
            for dep in node.deps:
                if dep in self.atoms:
                    adj[dep].append(node.id)
                    in_degree[node.id] += 1

        queue: list[int] = sorted(aid for aid, d in in_degree.items() if d == 0)
        order: list[int] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for nxt in adj[current]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    bisect.insort(queue, nxt)
        return order
