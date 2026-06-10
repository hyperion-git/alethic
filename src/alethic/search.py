"""v3.8 hierarchical proof search — PUCT gap selection + re-bridge management.

The search layer owns the proof graph. It picks the next gap to attack via
PUCT (Polynomial Upper Confidence bounds for Trees), drives the exploration
layer to enumerate bridging techniques, dispatches gap-filling work to the
GVR microkernel, and decides when a bridge has failed badly enough to
re-bridge (regenerate the full solution under a strategy reset).

Layered on top of (and importing from) three modules landed earlier in PR
#10:

- ``proof_graph.py`` — the ``ProofGraph`` / ``AtomNode`` data structures.
- ``microkernel.py`` — atom-scoped Generate → Verify → Revise.
- ``explorer.py``    — Alien-style technique enumeration with coherence.

This module does NOT modify ``AgentConfig``. v3.8 tree-search-specific
configuration lives in a dedicated ``SearchConfig`` dataclass, mirroring
the spec's preset table (lines 295-302). Wiring those fields into
``AgentConfig`` is intentionally deferred to a follow-up commit so each
PR-#10 commit is independently reviewable.

See ``docs/superpowers/specs/2026-04-11-v3.8-tree-search-design.md``
§Search Layer (lines 21-39, 126-202, 204-218, 258-267) and §Presets
(lines 295-302).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from alethic.atoms import parse_atoms
from alethic.error_taxonomy import _ORACLE_ROUTING
from alethic.exceptions import (
    CheckpointError,
    ContextExhaustedError,
    TruncatedResponseError,
)
from alethic.explorer import Technique, enumerate_techniques
from alethic.microkernel import MicrokernelTask, gvr_microkernel
from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    EventType,
    SearchConfig,
    Verdict,
    VerificationResult,
)
from alethic.proof_graph import AtomNode, AtomStatus, ProofGraph
from alethic.session import load_tree_checkpoint, write_tree_checkpoint
from alethic.subagents import generate, verify

if TYPE_CHECKING:
    from alethic.models import TokenLedger

logger = logging.getLogger("alethic")


# ── SearchConfig ──────────────────────────────────────────────────────────
# SearchConfig moved to alethic.models in the v3.8 integration commit;
# re-exported here so `from alethic.search import SearchConfig` keeps working.


# ── Per-gap search-layer state ────────────────────────────────────────────


@dataclass
class _GapState:
    """Per-gap auxiliary state tracked by the search layer.

    Not on ``AtomNode`` because (a) ``AtomNode`` is the data-layer artifact
    for ``proof_graph.py`` and shouldn't know about search policy, and
    (b) this state is recreated per ``solve()`` invocation while
    ``AtomNode`` persists inside ``ProofGraph``.

    Attributes:
        failures: Count of microkernel ``failed`` results on this gap.
            Drives the failure-count subdivision trigger (K=3 default).
        last_error_category: ``error_category`` from the most recent
            microkernel result on this gap. Feeds ``gap_prior``.
        technique_attempts: Map from technique name → attempt count.
            Drives the per-gap ``technique_budget`` cap and the
            ``puct_select_technique`` novelty term.
    """

    failures: int = 0
    last_error_category: str | None = None
    technique_attempts: dict[str, int] = field(default_factory=dict)


# ── PUCT primitives (pure functions) ──────────────────────────────────────


def puct_score(
    *,
    q: float,
    prior: float,
    c_puct: float,
    n_total: int,
    n_this: int,
) -> float:
    """Standard PUCT formula.

    ``PUCT = Q + c_puct · prior · √N_total / (1 + N_this)``

    ``n_total`` is bumped to at least 1 for the square root so unvisited
    siblings still get a nonzero exploration term — otherwise the very
    first selection over an all-cold gap set degenerates to "pure Q"
    (i.e. uniform zero) and the search has no incentive to expand
    anything. The standard MCTS PUCT papers use ``√(N_parent)`` which is
    nonzero by definition at the root; we mirror that behavior.
    """
    n_total_safe = max(n_total, 1)
    return q + c_puct * prior * math.sqrt(n_total_safe) / (1 + n_this)


# Error-category prior weights. Mechanically fixable categories keep the
# baseline weight (worth more attempts on the same gap); categories that
# typically need a categorically different technique get a 0.5× multiplier
# (search budget is better spent exploring other gaps instead).
#
# Rationale per spec §PUCT Scoring (lines 213-218): "algebra gaps get higher
# prior (mechanically fixable), logic gaps get lower (likely need different
# technique)." Default is 1.0 — applied to unclassified gaps and to
# categories not in this table.
_PRIOR_WEIGHTS: dict[str, float] = {
    "algebra": 1.0,
    "citation": 1.0,
    "logic": 0.5,
    "missing_case": 0.5,
    "interpretation": 0.5,
}


def gap_prior(*, error_category: str | None, n_gaps: int) -> float:
    """Prior probability for PUCT gap selection.

    Returns ``weight / max(n_gaps, 1)`` where ``weight`` comes from
    ``_PRIOR_WEIGHTS`` (1.0 default). ``max(n_gaps, 1)`` guards the
    degenerate "no gaps" case so the helper can be called defensively
    without crashing.
    """
    weight = _PRIOR_WEIGHTS.get(error_category or "", 1.0)
    return weight / max(n_gaps, 1)


def _technique_attempts_total(state: _GapState) -> int:
    return sum(state.technique_attempts.values())


def puct_select_gap(
    *,
    graph: ProofGraph,
    gap_states: dict[int, _GapState],
    c_puct: float,
    technique_budget: int,
) -> AtomNode | None:
    """Select the next gap to attack, or None if all gaps are exhausted.

    "Exhausted" means a gap's total technique attempts have hit
    ``technique_budget``. Ties (e.g., two cold gaps with identical PUCT
    scores) are broken by ascending atom ID for determinism — the
    spec leaves tie-breaking unspecified, and ascending ID is the
    convention already used by ``ProofGraph._topo_order``.
    """
    gaps = graph.gaps()
    if not gaps:
        return None

    eligible: list[AtomNode] = []
    for gap in gaps:
        state = gap_states.get(gap.id)
        attempts = _technique_attempts_total(state) if state else 0
        if attempts >= technique_budget:
            continue
        eligible.append(gap)
    if not eligible:
        return None

    n_total = sum(g.visit_count for g in eligible)

    scored: list[tuple[float, int, AtomNode]] = []
    for gap in eligible:
        state = gap_states.get(gap.id, _GapState())
        prior = gap_prior(
            error_category=state.last_error_category,
            n_gaps=len(eligible),
        )
        score = puct_score(
            q=gap.q_value,
            prior=prior,
            c_puct=c_puct,
            n_total=n_total,
            n_this=gap.visit_count,
        )
        # Negate score for ascending sort (so higher PUCT wins), keep ID
        # ascending as the second key so ties go to the lowest ID.
        scored.append((-score, gap.id, gap))

    scored.sort()
    return scored[0][2]


def puct_select_technique(
    *,
    techniques: list[Technique],
    gap_state: _GapState,
    c_puct: float,
) -> Technique | None:
    """Select the next technique to attempt on a gap, or None if list empty.

    Coherence acts as Q (the LLM's prior estimate of "will this work?").
    Novelty acts as the prior — 1.0 for untried, 0.1 for previously-tried
    (matches spec §Technique Selection lines 220-228). Ties are broken
    by input list position so the explorer's ordering propagates through.
    """
    if not techniques:
        return None

    n_gap = _technique_attempts_total(gap_state)

    scored: list[tuple[float, int, Technique]] = []
    for idx, tech in enumerate(techniques):
        attempts = gap_state.technique_attempts.get(tech.name, 0)
        novelty = 1.0 if attempts == 0 else 0.1
        score = puct_score(
            q=tech.coherence,
            prior=novelty,
            c_puct=c_puct,
            n_total=n_gap,
            n_this=attempts,
        )
        scored.append((-score, idx, tech))

    scored.sort()
    return scored[0][2]


# ── Atom classification from initial-bridge verification ──────────────────


def _classify_atoms_from_verification(
    graph: ProofGraph,
    verification: VerificationResult,
    *,
    threshold: float,
) -> None:
    """Mark each non-synthetic real atom as ANCHORED or FAILED in-place.

    Strategy:

    - If the verifier returned per-atom data (``atom_confidences``, set
      when the verifier produces ATOM CONFIDENCES output — v3.5+
      annotation), classify each atom individually against ``threshold``.
    - Otherwise, fall back to verdict: CORRECT → anchor all real atoms;
      anything else → mark all real atoms FAILED (conservative — the
      gap-filling layer will figure out which atoms actually need work).

    Synthetic preamble/residual atoms (id < 0) are never touched —
    ``ProofGraph.from_annotation`` already auto-anchored them so they
    don't enter the gap pool.
    """
    by_atom_conf: dict[int, float] = {
        ac.id: ac.confidence for ac in verification.atom_confidences
    }
    correct = verification.verdict == Verdict.CORRECT

    for node in graph.atoms.values():
        if node.synthetic and node.id < 0:
            continue
        if node.id in by_atom_conf:
            confidence = by_atom_conf[node.id]
            node.status = (
                AtomStatus.ANCHORED
                if confidence >= threshold
                else AtomStatus.FAILED
            )
        elif correct:
            node.status = AtomStatus.ANCHORED
        else:
            node.status = AtomStatus.FAILED


# ── Re-bridge summary ─────────────────────────────────────────────────────


def summarize_failed_path(
    graph: ProofGraph, gap_states: dict[int, _GapState],
) -> str:
    """Produce a one-paragraph summary of the failed bridge.

    Fed to the next bridge's strategy reset (via the existing
    ``subagents.generate(failed_approaches=...)`` keyword arg, which the
    generator already wires into the user message). Includes:

    - How many atoms anchored vs failed.
    - For each unfilled gap: tried techniques, latest error category.

    Format is plain text — the search layer joins multiple summaries by
    newline when ``failed_bridges`` has accumulated more than one entry.
    """
    real_anchored = [a for a in graph.anchors() if not a.synthetic]
    gaps = graph.gaps()
    n_anchored = len(real_anchored)
    n_gaps = len(gaps)

    parts: list[str] = [
        f"Previous bridge: {n_anchored} atom(s) anchored, {n_gaps} gap(s) unfilled."
    ]
    for gap in gaps:
        state = gap_states.get(gap.id)
        if state and state.technique_attempts:
            tried = ", ".join(sorted(state.technique_attempts))
        else:
            tried = "(no techniques attempted)"
        category = (
            state.last_error_category if state and state.last_error_category else "unknown"
        )
        parts.append(
            f"- Gap atom {gap.id} (level {gap.level}): "
            f"tried [{tried}]; latest error category: {category}."
        )
    return "\n".join(parts)


# ── Helpers used by solve() ──────────────────────────────────────────────


def _make_result(
    *,
    problem: str,
    solution: str | None,
    verdict: Verdict,
    confidence: float,
    iterations_used: int,
    total_revisions: int,
    events: list[AgentEvent],
    failed_approaches: list[str],
    token_ledger: TokenLedger | None,
    admitted_failure: bool = False,
) -> AgentResult:
    """Build an AgentResult for either an accept-path or a UNSOLVED-path exit.

    ``iterations_used`` in v3.8 counts bridges (one bridge = one full
    solution generation) — the closest analog of the flat-GVR iteration.
    The ``candidates_per_iteration`` field is always 1 here: the search
    layer's parallelism is at the gap level (within a bridge), not at
    the candidate level, so the field name doesn't quite apply but 1 is
    the truthful answer for "candidates per bridge generation".
    """
    return AgentResult(
        problem=problem,
        solution=solution,
        verdict=verdict,
        confidence=confidence,
        iterations_used=iterations_used,
        total_revisions=total_revisions,
        admitted_failure=admitted_failure,
        events=events,
        candidates_per_iteration=1,
        failed_approaches=list(failed_approaches),
        token_ledger=token_ledger,
    )


def _record_gap_attempt(
    gap: AtomNode, state: _GapState, *, technique_name: str, confidence: float,
) -> None:
    """Bookkeeping for one microkernel attempt on a gap.

    Updates both the per-gap _GapState (technique_attempts) and the
    AtomNode's PUCT counters (visit_count, total_value, techniques_tried).
    Kept in one place so the three different status branches of solve()
    can't drift apart in what they record.
    """
    state.technique_attempts[technique_name] = (
        state.technique_attempts.get(technique_name, 0) + 1
    )
    gap.techniques_tried.append(technique_name)
    gap.visit_count += 1
    gap.total_value += confidence


def _mark_children_as_gaps(graph: ProofGraph, child_ids: list[int]) -> None:
    """Flip newly subdivided children from PENDING to FAILED.

    ``proof_graph.subdivide()`` creates children with ``status=PENDING``
    (semantically correct: never verified). ``graph.gaps()`` filters
    strictly on ``status == FAILED`` (also semantically correct:
    "gap" = "verified and failed"). The search layer needs unverified
    children to enter the gap pool so subsequent ``puct_select_gap``
    calls can target them. This flip is the policy decision that
    bridges the two semantics — kept in search.py rather than baked
    into ``subdivide()`` so the data-layer module stays oblivious to
    search-layer policy.
    """
    for cid in child_ids:
        graph.atoms[cid].status = AtomStatus.FAILED


# ── solve() — main entry point ────────────────────────────────────────────


def solve(
    problem: str,
    *,
    config: AgentConfig,
    search_config: SearchConfig | None = None,
    domain: str = "math",
    client: Any,
    ledger: TokenLedger | None = None,
    balanced: bool = True,
    session_dir: str | None = None,
    resume_from: str | None = None,
) -> AgentResult:
    """v3.8 hierarchical proof search entry point.

    Phase 1 (Bridge): generate a full candidate solution.
    Phase 2 (Verify): classify each atom as anchored or gap from the
        verifier's ATOM CONFIDENCES (fallback: verdict-based).
    Phase 3 (Gap-fill): PUCT-driven gap selection → technique enumeration
        → GVR microkernel → status update. Repeats until the graph
        completes OR every gap exhausts its ``technique_budget`` /
        becomes terminally subdivided.
    Phase 4 (Re-bridge): summarize the failed bridge, push the summary
        into ``failed_approaches`` for the next bridge's strategy reset,
        and start over. Bounded by ``search_config.max_bridges``.

    Args:
        problem: The problem statement.
        config: The standard ``AgentConfig`` — drives the underlying
            generate/verify/revise temperature, model, threshold, etc.
        search_config: v3.8 tree-search knobs. Defaults to
            ``SearchConfig()`` (which matches the spec "default" preset).
        domain: ``"math"`` or ``"physics"``. Routes the microkernel and
            explorer to the appropriate prompt set.
        client: The Anthropic client (passed through to subagents).
        ledger: Optional ``TokenLedger`` for cumulative token tracking.
        balanced: Whether the bridge generator uses the balanced
            counterexample-first addendum (forwarded to ``generate()``).
        session_dir: When set, context-exhaustion errors are caught and the
            live search state is checkpointed to ``tree_state.json`` in this
            directory (a partial UNSOLVED result with ``checkpoint_path`` is
            returned instead of raising). When None, those errors propagate.
        resume_from: Session directory containing a ``tree_state.json`` to
            resume from. The checkpointed bridge's graph and PUCT state are
            restored and the search re-enters gap-filling; defaults
            ``session_dir`` to the same directory. Not persisted across
            resume: the events list — a resumed run's events cover only the
            current process. The restored token_ledger from the checkpoint is
            currently not merged (cost accounting restarts; see agent-level
            integration).

    Returns:
        An ``AgentResult`` with ``solved=True`` and ``verdict=CORRECT``
        if any bridge completes; otherwise an UNSOLVED result with the
        best-confidence solution observed across all bridges.
    """
    cfg = search_config if search_config is not None else SearchConfig()
    events: list[AgentEvent] = []
    failed_bridges: list[str] = []
    best_solution_text: str | None = None
    best_confidence: float = 0.0
    total_revisions = 0
    bridges_used = 0

    start_bridge = 0
    restored: dict[str, Any] | None = None
    if resume_from is not None:
        restored = load_tree_checkpoint(resume_from)
        start_bridge = restored["bridge_index"]
        failed_bridges = list(restored["failed_bridges"])
        best_confidence = restored["best_confidence"]
        best_solution_text = restored["best_solution_text"]
        total_revisions = restored["total_revisions"]
        if session_dir is None:
            session_dir = resume_from
        logger.info(
            "Search: resuming from %s (bridge %d, best_conf=%.2f)",
            resume_from, start_bridge, best_confidence,
        )

    # Pre-initialized so the except-path checkpoint can always read them,
    # even when exhaustion hits during Phase 1 of each bridge.
    graph: ProofGraph | None = None
    gap_states: dict[int, _GapState] = {}
    atom_confs: dict[int, float] = {}
    bridge_idx = start_bridge
    bridge_confidence = 0.0

    try:
        for bridge_idx in range(start_bridge, cfg.max_bridges):
            bridges_used = bridge_idx + 1
            logger.info(
                "Search: starting bridge %d/%d (failed_bridges=%d)",
                bridge_idx, cfg.max_bridges, len(failed_bridges),
            )

            if restored is not None and restored.get("graph") is not None:
                # ── Resume path: restore Phase 1+2 outputs from checkpoint ──
                try:
                    graph = ProofGraph.from_dict(restored["graph"])
                    gap_states = {
                        gid: _GapState(
                            failures=gs.get("failures", 0),
                            last_error_category=gs.get("last_error_category"),
                            technique_attempts=dict(gs.get("technique_attempts", {})),
                        )
                        for gid, gs in restored["gap_states"].items()
                    }
                except (KeyError, ValueError, TypeError) as exc:
                    raise CheckpointError(
                        f"Corrupt tree checkpoint state: {exc}"
                    ) from exc
                atom_confs = dict(restored["atom_confs"])
                bridge_confidence = restored["bridge_confidence"]
                restored = None
                logger.info(
                    "Search: bridge %d restored from checkpoint (%d gaps open)",
                    bridge_idx, len(graph.gaps()),
                )
            else:
                restored = None  # a null-graph checkpoint restarts the bridge fresh
                # Reset cross-bridge locals BEFORE the new generation: if
                # exhaustion hits during this bridge's Phase 1, the checkpoint
                # must not capture the previous bridge's exhausted graph under
                # this bridge's index.
                graph = None
                gap_states = {}
                atom_confs = {}
                bridge_confidence = 0.0

                # ── Phase 1: Bridge ─────────────────────────────────────────
                bridge_solution = generate(
                    client,
                    problem=problem,
                    config=config,
                    iteration=bridge_idx,
                    balanced=balanced,
                    failed_approaches=tuple(failed_bridges),
                    ledger=ledger,
                )
                bridge_ver = verify(
                    client,
                    problem=problem,
                    solution=bridge_solution,
                    config=config,
                    ledger=ledger,
                )
                events.append(AgentEvent(
                    type=EventType.BRIDGE_GENERATED,
                    iteration=bridge_idx,
                    data={
                        "bridge_index": bridge_idx,
                        "verdict": bridge_ver.verdict.value,
                        "confidence": bridge_ver.confidence,
                    },
                ))

                # Track best raw bridge as a fallback for the UNSOLVED return
                if bridge_ver.confidence > best_confidence:
                    best_solution_text = bridge_solution.solution_text
                    best_confidence = bridge_ver.confidence

                # ── Phase 2: Atom classification ────────────────────────────
                annotations = parse_atoms(bridge_solution.solution_text)
                graph = ProofGraph.from_atoms(annotations)
                _classify_atoms_from_verification(
                    graph, bridge_ver, threshold=config.confidence_threshold,
                )

                # Per-atom confidences for final aggregation. Initially seeded
                # from the verifier's atom_confidences; atoms anchored without
                # explicit per-atom data fall back to the bridge's overall
                # confidence.
                atom_confs = {
                    ac.id: ac.confidence for ac in bridge_ver.atom_confidences
                }
                for node in graph.atoms.values():
                    if node.synthetic and node.id < 0:
                        continue
                    if node.status == AtomStatus.ANCHORED and node.id not in atom_confs:
                        atom_confs[node.id] = bridge_ver.confidence

                gap_states = {}
                bridge_confidence = bridge_ver.confidence

            if graph.is_complete():
                events.append(AgentEvent(
                    type=EventType.ACCEPT,
                    iteration=bridge_idx,
                    data={
                        "reason": "bridge_complete",
                        "confidence": bridge_confidence,
                        "bridge_index": bridge_idx,
                    },
                ))
                return _make_result(
                    problem=problem,
                    solution=graph.assemble_solution(),
                    verdict=Verdict.CORRECT,
                    confidence=bridge_confidence,
                    iterations_used=bridges_used,
                    total_revisions=total_revisions,
                    events=events,
                    failed_approaches=failed_bridges,
                    token_ledger=ledger,
                )

            # ── Phase 3: Gap-Filling Search ─────────────────────────────────
            while True:
                gap = puct_select_gap(
                    graph=graph, gap_states=gap_states,
                    c_puct=cfg.c_puct,
                    technique_budget=cfg.technique_budget,
                )
                if gap is None:
                    break  # all gaps exhausted → Phase 4

                state = gap_states.setdefault(gap.id, _GapState())
                left, right = graph.neighbors(gap.id)
                left_text = left.content if left is not None else "(beginning of proof)"
                right_text = right.content if right is not None else "(end of proof)"

                techniques = enumerate_techniques(
                    left_anchor=left_text,
                    right_anchor=right_text,
                    tried_techniques=list(state.technique_attempts),
                    problem_context=problem,
                    config=config,
                    domain=domain,
                    client=client,
                    ledger=ledger,
                )

                technique = puct_select_technique(
                    techniques=techniques, gap_state=state, c_puct=cfg.c_puct,
                )

                if technique is None:
                    # Explorer returned no novel candidates. Mark this gap as
                    # exhausted (visit-budget hit) so the next puct_select_gap
                    # call skips it instead of looping forever.
                    state.technique_attempts["__exhausted__"] = cfg.technique_budget
                    continue

                oracle = None
                force_adversarial = False
                if state.last_error_category is not None:
                    oracle, force_adversarial = _ORACLE_ROUTING.get(
                        state.last_error_category, (None, False)
                    )
                task = MicrokernelTask(
                    gap_id=gap.id,
                    left_anchor=left_text,
                    right_anchor=right_text,
                    technique=technique.name,
                    problem_context=problem,
                    max_revisions=cfg.atom_revisions,
                    oracle=oracle,
                    force_adversarial=force_adversarial,
                )
                mk_result = gvr_microkernel(
                    task,
                    config=config,
                    domain=domain,
                    client=client,
                    ledger=ledger,
                )
                total_revisions += mk_result.revisions_used

                _record_gap_attempt(
                    gap, state,
                    technique_name=technique.name,
                    confidence=mk_result.confidence,
                )

                if mk_result.status == "filled":
                    gap.content = mk_result.replacement_content
                    gap.status = AtomStatus.ANCHORED
                    atom_confs[gap.id] = mk_result.confidence
                    state.last_error_category = None
                    events.append(AgentEvent(
                        type=EventType.GAP_FILLED,
                        iteration=bridge_idx,
                        data={
                            "gap_id": gap.id,
                            "technique": technique.name,
                            "confidence": mk_result.confidence,
                            "oracle": oracle.value if oracle else None,
                            "force_adversarial": force_adversarial,
                        },
                    ))
                elif mk_result.status == "too_large":
                    if gap.level < cfg.max_depth:
                        new_ids = graph.subdivide(gap.id, n_children=cfg.n_subdivide)
                        _mark_children_as_gaps(graph, new_ids)
                        events.append(AgentEvent(
                            type=EventType.GAP_SUBDIVIDED,
                            iteration=bridge_idx,
                            data={
                                "gap_id": gap.id,
                                "children": new_ids,
                                "reason": "too_large",
                            },
                        ))
                    else:
                        # At max depth — cannot subdivide further. Counts as
                        # a terminal failure for this gap; further selections
                        # will keep selecting it until budget exhausts.
                        state.failures += 1
                        state.last_error_category = mk_result.error_category
                        events.append(AgentEvent(
                            type=EventType.GAP_FAILED,
                            iteration=bridge_idx,
                            data={
                                "gap_id": gap.id,
                                "technique": technique.name,
                                "confidence": mk_result.confidence,
                                "error_category": mk_result.error_category,
                                "reason": "too_large_at_max_depth",
                                "oracle": oracle.value if oracle else None,
                                "force_adversarial": force_adversarial,
                            },
                        ))
                else:  # mk_result.status == "failed"
                    state.failures += 1
                    state.last_error_category = mk_result.error_category
                    events.append(AgentEvent(
                        type=EventType.GAP_FAILED,
                        iteration=bridge_idx,
                        data={
                            "gap_id": gap.id,
                            "technique": technique.name,
                            "confidence": mk_result.confidence,
                            "error_category": mk_result.error_category,
                            "oracle": oracle.value if oracle else None,
                            "force_adversarial": force_adversarial,
                        },
                    ))
                    if (
                        state.failures >= cfg.failure_subdivision_threshold
                        and gap.level < cfg.max_depth
                    ):
                        new_ids = graph.subdivide(gap.id, n_children=cfg.n_subdivide)
                        _mark_children_as_gaps(graph, new_ids)
                        events.append(AgentEvent(
                            type=EventType.GAP_SUBDIVIDED,
                            iteration=bridge_idx,
                            data={
                                "gap_id": gap.id,
                                "children": new_ids,
                                "reason": "failure_count",
                            },
                        ))

                if graph.is_complete():
                    final_text = graph.assemble_solution()
                    final_conf = min(atom_confs.values()) if atom_confs else bridge_confidence
                    events.append(AgentEvent(
                        type=EventType.ACCEPT,
                        iteration=bridge_idx,
                        data={
                            "reason": "gaps_filled",
                            "confidence": final_conf,
                            "atoms_anchored": len(graph.anchors()),
                        },
                    ))
                    if final_conf > best_confidence:
                        best_solution_text = final_text
                        best_confidence = final_conf
                    return _make_result(
                        problem=problem,
                        solution=final_text,
                        verdict=Verdict.CORRECT,
                        confidence=final_conf,
                        iterations_used=bridges_used,
                        total_revisions=total_revisions,
                        events=events,
                        failed_approaches=failed_bridges,
                        token_ledger=ledger,
                    )

            # ── Phase 4: Re-bridge ──────────────────────────────────────────
            # Only triggered when Phase 3 exits via exhaustion (gap is None).
            if bridge_idx + 1 < cfg.max_bridges:
                summary = summarize_failed_path(graph, gap_states)
                failed_bridges.append(summary)
                events.append(AgentEvent(
                    type=EventType.RE_BRIDGE_TRIGGERED,
                    iteration=bridge_idx,
                    data={
                        "summary": summary,
                        "next_bridge_index": bridge_idx + 1,
                    },
                ))

    except (ContextExhaustedError, TruncatedResponseError):
        if session_dir is None:
            raise
        ckpt_path = write_tree_checkpoint(
            session_dir,
            graph_dict=graph.to_dict() if graph is not None else None,
            bridge_index=bridge_idx,
            bridge_confidence=bridge_confidence,
            failed_bridges=failed_bridges,
            gap_states={
                gid: {
                    "failures": gs.failures,
                    "last_error_category": gs.last_error_category,
                    "technique_attempts": dict(gs.technique_attempts),
                }
                for gid, gs in gap_states.items()
            },
            atom_confs=atom_confs,
            best_confidence=best_confidence,
            best_solution_text=best_solution_text,
            token_ledger=ledger,
            total_revisions=total_revisions,
        )
        logger.warning("Search: context exhausted — checkpoint at %s", ckpt_path)
        logger.info("Resume with: --search tree --resume %s", session_dir)
        result = _make_result(
            problem=problem,
            solution=best_solution_text,
            verdict=Verdict.UNSOLVED,
            confidence=best_confidence,
            iterations_used=bridges_used,
            total_revisions=total_revisions,
            events=events,
            failed_approaches=failed_bridges,
            token_ledger=ledger,
            admitted_failure=True,
        )
        result.session_dir = session_dir
        result.checkpoint_path = ckpt_path
        return result

    # ── Bridges exhausted ──────────────────────────────────────────────
    return _make_result(
        problem=problem,
        solution=best_solution_text,
        verdict=Verdict.UNSOLVED,
        confidence=best_confidence,
        iterations_used=bridges_used,
        total_revisions=total_revisions,
        events=events,
        failed_approaches=failed_bridges,
        token_ledger=ledger,
        admitted_failure=True,
    )
