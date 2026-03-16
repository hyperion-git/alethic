"""Atom annotation parsing and classification (v3.5).

Parses ATOM[N] markers from generator-produced solution text into
AtomAnnotation dataclasses. Falls back to a single monolithic atom
when annotations are absent or malformed.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import re
from dataclasses import dataclass

from alethic.models import OracleType
from alethic.physics_checks import parse_layer_results

logger = logging.getLogger("alethic")

# Maximum number of atoms per solution before fallback
MAX_ATOMS = 12

# Map oracle shorthand to OracleType enum
_ORACLE_MAP: dict[str, OracleType] = {
    "L0": OracleType.LAYER0_STRUCTURAL,
    "L1": OracleType.LAYER1_BEHAVIORAL,
    "L2": OracleType.LAYER2_CONSISTENCY,
    "L3": OracleType.LAYER3_LLM,
    "L4": OracleType.LAYER4_CONSENSUS,
}

# Map contiguity depth to OracleType (next level above verified depth)
ORACLE_BY_DEPTH: dict[int, OracleType] = {
    1: OracleType.LAYER1_BEHAVIORAL,
    2: OracleType.LAYER2_CONSISTENCY,
    3: OracleType.LAYER3_LLM,
}

# Regex to extract ATOM[N] headers — only matches outside fenced code blocks
_ATOM_HEADER_RE = re.compile(
    r"^ATOM\[(\d+)\]\s+deps=\[([^\]]*)\]\s+oracle=(L\d+)",
    re.MULTILINE,
)

# Regex to detect fenced code block boundaries
_FENCE_RE = re.compile(r"^(`{3,})", re.MULTILINE)

# Regex to strip verify function bodies before content hashing
_VERIFY_FUNC_RE = re.compile(r"\n\s*```python\s*\ndef verify_.*?```", re.DOTALL)


@dataclass(frozen=True)
class AtomAnnotation:
    """A single atom annotation extracted from a solution.

    Reserved IDs: 0=monolithic, -1=preamble, -2=residual.
    """

    id: int
    deps: tuple[int, ...]
    oracle: OracleType
    content: str
    synthetic: bool = False
    start_offset: int = 0
    end_offset: int = 0


def content_hash(atom: AtomAnnotation) -> str:
    """Hash mathematical content only, stripping verify function bodies."""
    cleaned = _VERIFY_FUNC_RE.sub("", atom.content)
    cleaned = " ".join(cleaned.split())  # normalize whitespace
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


class AtomStability(enum.Enum):
    """Stability classification for an atom across iterations."""

    STABLE = "stable"
    OSCILLATING = "oscillating"
    FAILING = "failing"


def classify_atom_stability(
    atom_history: list[list[AtomAnnotation]],
    confidence_history: list[float],
    confidence_floor: float = 0.70,
) -> dict[int, AtomStability]:
    """Classify each atom as STABLE/OSCILLATING/FAILING across iterations.

    Args:
        atom_history: Per-iteration list of atom annotations (from winning candidates).
        confidence_history: Per-iteration best confidence scores.
        confidence_floor: Minimum confidence for STABLE promotion.

    Returns:
        Dict mapping atom ID to stability classification.
    """
    all_ids: set[int] = set()
    for iteration_atoms in atom_history:
        for atom in iteration_atoms:
            if not atom.synthetic:
                all_ids.add(atom.id)

    stability: dict[int, AtomStability] = {}
    for atom_id in all_ids:
        entries: list[tuple[str, float]] = []
        for iteration_atoms, conf in zip(atom_history, confidence_history, strict=True):
            match = next((a for a in iteration_atoms if a.id == atom_id), None)
            if match is not None:
                entries.append((content_hash(match), conf))

        if not entries:
            continue

        hashes = [h for h, _ in entries]
        confs = [c for _, c in entries]

        if len(set(hashes)) == 1 and all(c >= confidence_floor for c in confs):
            stability[atom_id] = AtomStability.STABLE
        elif len(set(hashes)) >= 2 and hashes[-1] in hashes[:-1]:
            stability[atom_id] = AtomStability.OSCILLATING
        else:
            stability[atom_id] = AtomStability.FAILING

    return stability


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges of fenced code blocks in text."""
    ranges: list[tuple[int, int]] = []
    fences = list(_FENCE_RE.finditer(text))
    i = 0
    while i < len(fences) - 1:
        start = fences[i].start()
        # Find matching close fence (same or more backticks)
        opener_len = len(fences[i].group(1))
        for j in range(i + 1, len(fences)):
            if len(fences[j].group(1)) >= opener_len:
                ranges.append((start, fences[j].end()))
                i = j + 1
                break
        else:
            # No matching close — rest of text is fenced
            ranges.append((start, len(text)))
            break
    return ranges


def _in_fenced_block(offset: int, fenced: list[tuple[int, int]]) -> bool:
    """Check if a byte offset is inside a fenced code block."""
    return any(start <= offset < end for start, end in fenced)


def _oracle_from_sentinels(text: str) -> OracleType:
    """Derive oracle level from existing ALETHIC_L{N}_CHECK sentinels using contiguity."""
    layer_results = parse_layer_results(text)
    if not layer_results:
        return OracleType.LAYER3_LLM
    depth = 0
    while depth in layer_results:
        depth += 1
    if depth == 0:
        return OracleType.LAYER3_LLM
    return ORACLE_BY_DEPTH.get(depth, OracleType.LAYER3_LLM)


def _validate_dag(atoms: list[AtomAnnotation]) -> bool:
    """Check that the dependency graph is a DAG (no cycles). Kahn's algorithm."""
    ids = {a.id for a in atoms if not a.synthetic}
    adj: dict[int, list[int]] = {a.id: [] for a in atoms if not a.synthetic}
    in_degree: dict[int, int] = {a.id: 0 for a in atoms if not a.synthetic}

    for a in atoms:
        if a.synthetic:
            continue
        for dep in a.deps:
            if dep not in ids:
                return False  # references non-existent atom
            adj[dep].append(a.id)
            in_degree[a.id] += 1

    queue = [n for n, d in in_degree.items() if d == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return visited == len(ids)


def _monolithic_fallback(solution_text: str) -> list[AtomAnnotation]:
    """Return a single synthetic monolithic atom covering the entire solution."""
    return [AtomAnnotation(
        id=0, deps=(), oracle=_oracle_from_sentinels(solution_text),
        content=solution_text, synthetic=True,
        end_offset=len(solution_text),
    )]


# Pre-computed oracle ordering for max() in residual handling
_ORACLE_INDEX: dict[OracleType, int] = {o: i for i, o in enumerate(OracleType)}


def parse_atoms(solution_text: str) -> list[AtomAnnotation]:
    """Extract atom annotations from generator-produced solution text.

    Returns a list of AtomAnnotation objects. Falls back to a single
    monolithic atom when annotations are absent or malformed.

    IMPORTANT: Only call on generator-produced solution text, never on
    verifier output, breaker output, or critique text.
    """
    fenced = _fenced_ranges(solution_text)

    # Extract ATOM[N] headers, skipping those inside code blocks
    raw_atoms: list[tuple[int, int, tuple[int, ...], OracleType, int]] = []
    for m in _ATOM_HEADER_RE.finditer(solution_text):
        if _in_fenced_block(m.start(), fenced):
            continue
        atom_id = int(m.group(1))
        deps_str = m.group(2).strip()
        deps = tuple(int(d.strip()) for d in deps_str.split(",") if d.strip()) if deps_str else ()
        oracle = _ORACLE_MAP.get(m.group(3), OracleType.LAYER3_LLM)
        raw_atoms.append((atom_id, m.start(), deps, oracle, m.end()))

    if not raw_atoms:
        return _monolithic_fallback(solution_text)

    # Validate: count cap
    if len(raw_atoms) > MAX_ATOMS:
        logger.warning("Atom count %d exceeds cap %d; falling back to monolithic", len(raw_atoms), MAX_ATOMS)
        return _monolithic_fallback(solution_text)

    # Validate: no duplicate IDs
    ids = [a[0] for a in raw_atoms]
    if len(ids) != len(set(ids)):
        logger.warning("Duplicate atom IDs detected; falling back to monolithic")
        return _monolithic_fallback(solution_text)

    # Build AtomAnnotation objects with content slicing
    atoms: list[AtomAnnotation] = []
    for i, (atom_id, header_start, deps, oracle, header_end) in enumerate(raw_atoms):
        content_end = raw_atoms[i + 1][1] if i + 1 < len(raw_atoms) else len(solution_text)
        content = solution_text[header_end:content_end].strip()
        atoms.append(AtomAnnotation(
            id=atom_id, deps=deps, oracle=oracle, content=content,
            start_offset=header_start, end_offset=content_end,
        ))

    # Validate: deps reference existing atoms + DAG check
    if not _validate_dag(atoms):
        logger.warning("Invalid dependency graph (missing dep or cycle); falling back to monolithic")
        return _monolithic_fallback(solution_text)

    # Handle orphan preamble text
    if atoms[0].start_offset > 0:
        preamble_text = solution_text[:atoms[0].start_offset]
        first_atom_start = atoms[0].start_offset
        atoms.insert(0, AtomAnnotation(
            id=-1, deps=(), oracle=_oracle_from_sentinels(preamble_text),
            content=preamble_text.strip(), synthetic=True,
            end_offset=first_atom_start,
        ))

    # Handle orphan residual text
    if atoms[-1].end_offset < len(solution_text):
        residual_text = solution_text[atoms[-1].end_offset:]
        if residual_text.strip():
            last_real = [a for a in atoms if not a.synthetic][-1]
            max_oracle = max(
                (a.oracle for a in atoms if not a.synthetic),
                key=lambda o: _ORACLE_INDEX[o],
            )
            atoms.append(AtomAnnotation(
                id=-2, deps=(last_real.id,), oracle=max_oracle,
                content=residual_text.strip(), synthetic=True,
                start_offset=last_real.end_offset,
                end_offset=len(solution_text),
            ))

    return atoms
