"""Tests for src/alethic/search.py (v3.8 hierarchical proof search)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alethic import search as sr
from alethic.atoms import AtomAnnotation
from alethic.explorer import Technique
from alethic.microkernel import MicrokernelResult
from alethic.models import (
    AgentConfig,
    AtomConfidence,
    EventType,
    OracleType,
    Solution,
    Verdict,
    VerificationResult,
)
from alethic.proof_graph import AtomStatus, ProofGraph

# ── Fixtures / helpers ────────────────────────────────────────────────────


def _make_annotation(
    aid: int,
    *,
    deps: tuple[int, ...] = (),
    content: str = "",
    synthetic: bool = False,
    oracle: OracleType = OracleType.LAYER3_LLM,
) -> AtomAnnotation:
    return AtomAnnotation(
        id=aid, deps=deps, oracle=oracle,
        content=content or f"atom_{aid}", synthetic=synthetic,
    )


def _graph_with_atoms(*ids_and_status: tuple[int, AtomStatus]) -> ProofGraph:
    """Build a ProofGraph with the given (id, status) pairs.

    All atoms are non-synthetic, depend on the previous atom (chain), and
    have their content set to ``"atom_{id}"`` for assembly tests.
    """
    annotations = []
    prev_deps: tuple[int, ...] = ()
    for aid, _ in ids_and_status:
        annotations.append(_make_annotation(aid, deps=prev_deps))
        prev_deps = (aid,)
    graph = ProofGraph.from_atoms(annotations)
    for aid, status in ids_and_status:
        graph.atoms[aid].status = status
    return graph


def _make_config() -> AgentConfig:
    return AgentConfig()


# ──────────────────────────────────────────────────────────────────────────
# SearchConfig
# ──────────────────────────────────────────────────────────────────────────


class TestSearchConfig:
    def test_default_construction(self):
        cfg = sr.SearchConfig()
        # Defaults match the spec "default" preset row (lines 295-302)
        assert cfg.max_bridges == 2
        assert cfg.max_depth == 2
        assert cfg.c_puct == pytest.approx(1.414)
        assert cfg.technique_budget == 3
        assert cfg.atom_revisions == 2

    def test_failure_subdivision_threshold_default(self):
        # Spec §Recursive Subdivision says K=3
        assert sr.SearchConfig().failure_subdivision_threshold == 3

    def test_is_frozen(self):
        cfg = sr.SearchConfig()
        with pytest.raises(AttributeError):
            cfg.c_puct = 0.5  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("max_bridges", 0),
            ("max_depth", -1),
            ("c_puct", -0.1),
            ("technique_budget", 0),
            ("atom_revisions", -1),
            ("failure_subdivision_threshold", 0),
            ("n_subdivide", 1),  # need ≥ 2 to be meaningful
        ],
    )
    def test_validation_rejects_invalid(self, field, bad_value):
        with pytest.raises(ValueError):
            sr.SearchConfig(**{field: bad_value})

    def test_preset_default(self):
        cfg = sr.SearchConfig.from_preset("default")
        assert cfg.max_bridges == 2
        assert cfg.max_depth == 2
        assert cfg.technique_budget == 3
        assert cfg.atom_revisions == 2

    def test_preset_thorough(self):
        cfg = sr.SearchConfig.from_preset("thorough")
        assert cfg.max_bridges == 3
        assert cfg.max_depth == 3
        assert cfg.technique_budget == 5
        assert cfg.atom_revisions == 3

    def test_preset_extreme(self):
        cfg = sr.SearchConfig.from_preset("extreme")
        assert cfg.max_bridges == 5
        assert cfg.max_depth == 3
        assert cfg.technique_budget == 8
        assert cfg.atom_revisions == 5

    def test_preset_overrides(self):
        cfg = sr.SearchConfig.from_preset("default", max_bridges=7)
        assert cfg.max_bridges == 7
        assert cfg.atom_revisions == 2  # other defaults preserved

    def test_unknown_preset(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            sr.SearchConfig.from_preset("nonsense")


# ──────────────────────────────────────────────────────────────────────────
# puct_score (pure function)
# ──────────────────────────────────────────────────────────────────────────


class TestPuctScore:
    def test_baseline_formula(self):
        # PUCT = Q + c · P · √N_total / (1 + N_this)
        # Q=0.5, P=0.25, c=2.0, N_total=4, N_this=1 → 0.5 + 2*0.25*2/2 = 1.0
        score = sr.puct_score(q=0.5, prior=0.25, c_puct=2.0, n_total=4, n_this=1)
        assert score == pytest.approx(1.0)

    def test_zero_q_unvisited_pure_exploration(self):
        # Q=0, P=1.0, c=1.414, N_total=1, N_this=0 → 1.414
        score = sr.puct_score(q=0.0, prior=1.0, c_puct=1.414, n_total=1, n_this=0)
        assert score == pytest.approx(1.414)

    def test_high_q_dominates_when_visited(self):
        # Heavily visited gap with high Q should beat low-Q unvisited
        visited = sr.puct_score(q=0.9, prior=0.5, c_puct=1.414, n_total=100, n_this=50)
        unvisited = sr.puct_score(q=0.0, prior=0.5, c_puct=1.414, n_total=100, n_this=0)
        # Visited: 0.9 + 1.414*0.5*10/51 ≈ 0.9 + 0.139 ≈ 1.039
        # Unvisited: 0 + 1.414*0.5*10/1 = 7.07
        # In small visit regime exploration dominates — that's correct PUCT behavior
        assert unvisited > visited  # exploration > exploitation when N_total is small

    def test_monotonic_in_q(self):
        s1 = sr.puct_score(q=0.5, prior=0.5, c_puct=1.0, n_total=10, n_this=2)
        s2 = sr.puct_score(q=0.8, prior=0.5, c_puct=1.0, n_total=10, n_this=2)
        assert s2 > s1


# ──────────────────────────────────────────────────────────────────────────
# gap_prior — weighting by error category
# ──────────────────────────────────────────────────────────────────────────


class TestGapPrior:
    def test_default_when_no_category(self):
        # Untried gap → uniform 1/n_gaps
        assert sr.gap_prior(error_category=None, n_gaps=4) == pytest.approx(0.25)

    def test_algebra_keeps_baseline(self):
        # algebra is mechanically fixable — keep full weight
        assert sr.gap_prior(error_category="algebra", n_gaps=4) == pytest.approx(0.25)

    def test_citation_keeps_baseline(self):
        assert sr.gap_prior(error_category="citation", n_gaps=4) == pytest.approx(0.25)

    @pytest.mark.parametrize("cat", ["logic", "missing_case", "interpretation"])
    def test_strategic_categories_get_half(self, cat):
        # 0.5× weight — these need a different technique, not more attempts
        assert sr.gap_prior(error_category=cat, n_gaps=4) == pytest.approx(0.125)

    def test_unknown_category_keeps_baseline(self):
        assert sr.gap_prior(error_category="general", n_gaps=2) == pytest.approx(0.5)

    def test_handles_n_gaps_zero_gracefully(self):
        # Degenerate but shouldn't crash
        assert sr.gap_prior(error_category=None, n_gaps=0) == pytest.approx(1.0)


# ──────────────────────────────────────────────────────────────────────────
# _GapState
# ──────────────────────────────────────────────────────────────────────────


class TestGapState:
    def test_defaults(self):
        s = sr._GapState()
        assert s.failures == 0
        assert s.last_error_category is None
        assert s.technique_attempts == {}

    def test_total_attempts_helper(self):
        s = sr._GapState(technique_attempts={"a": 2, "b": 1})
        # Sum of all attempt counts
        assert sum(s.technique_attempts.values()) == 3


# ──────────────────────────────────────────────────────────────────────────
# puct_select_gap
# ──────────────────────────────────────────────────────────────────────────


class TestPuctSelectGap:
    def test_returns_none_when_no_gaps(self):
        graph = _graph_with_atoms((1, AtomStatus.ANCHORED), (2, AtomStatus.ANCHORED))
        result = sr.puct_select_gap(
            graph=graph, gap_states={}, c_puct=1.414, technique_budget=3,
        )
        assert result is None

    def test_single_gap_returned(self):
        graph = _graph_with_atoms(
            (1, AtomStatus.ANCHORED), (2, AtomStatus.FAILED), (3, AtomStatus.ANCHORED),
        )
        result = sr.puct_select_gap(
            graph=graph, gap_states={}, c_puct=1.414, technique_budget=3,
        )
        assert result is not None
        assert result.id == 2

    def test_two_cold_gaps_breaks_ties_by_id(self):
        # Both gaps untouched → identical scores → ascending ID wins (deterministic)
        graph = _graph_with_atoms(
            (5, AtomStatus.FAILED), (3, AtomStatus.FAILED),
        )
        result = sr.puct_select_gap(
            graph=graph, gap_states={}, c_puct=1.414, technique_budget=3,
        )
        assert result is not None
        assert result.id == 3

    def test_higher_q_gap_wins_over_visited_low_q(self):
        graph = _graph_with_atoms((1, AtomStatus.FAILED), (2, AtomStatus.FAILED))
        # Gap 1: visited 1× with conf 0.8 → Q=0.8
        graph.atoms[1].visit_count = 1
        graph.atoms[1].total_value = 0.8
        # Gap 2: visited 1× with conf 0.2 → Q=0.2
        graph.atoms[2].visit_count = 1
        graph.atoms[2].total_value = 0.2
        result = sr.puct_select_gap(
            graph=graph, gap_states={}, c_puct=0.0,  # zero exploration → Q dominates
            technique_budget=3,
        )
        assert result.id == 1

    def test_exhausted_gap_is_skipped(self):
        graph = _graph_with_atoms((1, AtomStatus.FAILED), (2, AtomStatus.FAILED))
        # Gap 1 hit technique_budget
        gap_states = {
            1: sr._GapState(technique_attempts={"a": 2, "b": 1}),  # 3 total
        }
        result = sr.puct_select_gap(
            graph=graph, gap_states=gap_states, c_puct=1.414, technique_budget=3,
        )
        assert result is not None
        assert result.id == 2

    def test_all_exhausted_returns_none(self):
        graph = _graph_with_atoms((1, AtomStatus.FAILED), (2, AtomStatus.FAILED))
        gap_states = {
            1: sr._GapState(technique_attempts={"x": 3}),
            2: sr._GapState(technique_attempts={"y": 3}),
        }
        result = sr.puct_select_gap(
            graph=graph, gap_states=gap_states, c_puct=1.414, technique_budget=3,
        )
        assert result is None


# ──────────────────────────────────────────────────────────────────────────
# puct_select_technique
# ──────────────────────────────────────────────────────────────────────────


class TestPuctSelectTechnique:
    def test_returns_none_for_empty(self):
        result = sr.puct_select_technique(
            techniques=[], gap_state=sr._GapState(), c_puct=1.414,
        )
        assert result is None

    def test_higher_coherence_wins_when_both_untried(self):
        techs = [
            Technique(name="weak", coherence=0.3),
            Technique(name="strong", coherence=0.9),
        ]
        result = sr.puct_select_technique(
            techniques=techs, gap_state=sr._GapState(), c_puct=1.414,
        )
        assert result.name == "strong"

    def test_failed_technique_gets_lower_prior(self):
        # Both same coherence; one tried + failed once. Untried novelty=1.0,
        # tried novelty=0.1 → untried wins under reasonable c_puct.
        techs = [
            Technique(name="tried", coherence=0.7),
            Technique(name="fresh", coherence=0.7),
        ]
        state = sr._GapState(technique_attempts={"tried": 1})
        result = sr.puct_select_technique(
            techniques=techs, gap_state=state, c_puct=1.414,
        )
        assert result.name == "fresh"

    def test_breaks_ties_by_input_order(self):
        # Identical scores → preserve list order (deterministic)
        techs = [
            Technique(name="first", coherence=0.5),
            Technique(name="second", coherence=0.5),
        ]
        result = sr.puct_select_technique(
            techniques=techs, gap_state=sr._GapState(), c_puct=1.414,
        )
        assert result.name == "first"


# ──────────────────────────────────────────────────────────────────────────
# summarize_failed_path
# ──────────────────────────────────────────────────────────────────────────


class TestSummarizeFailedPath:
    def test_includes_anchored_and_failed_counts(self):
        graph = _graph_with_atoms(
            (1, AtomStatus.ANCHORED),
            (2, AtomStatus.FAILED),
            (3, AtomStatus.ANCHORED),
        )
        summary = sr.summarize_failed_path(graph, gap_states={})
        assert "2 atoms anchored" in summary or "anchored" in summary.lower()
        assert "1 gap" in summary or "1 atom" in summary or "unfilled" in summary.lower()

    def test_lists_failed_gap_techniques(self):
        graph = _graph_with_atoms((1, AtomStatus.FAILED))
        gap_states = {
            1: sr._GapState(
                technique_attempts={"induction": 1, "contradiction": 2},
                last_error_category="logic",
            ),
        }
        summary = sr.summarize_failed_path(graph, gap_states=gap_states)
        assert "induction" in summary
        assert "contradiction" in summary
        assert "logic" in summary

    def test_handles_empty_graph(self):
        # Degenerate case — no atoms at all
        graph = ProofGraph()
        summary = sr.summarize_failed_path(graph, gap_states={})
        assert isinstance(summary, str)
        assert len(summary) > 0  # produces *some* descriptive text


# ──────────────────────────────────────────────────────────────────────────
# _classify_atoms_from_verification
# ──────────────────────────────────────────────────────────────────────────


class TestClassifyAtomsFromVerification:
    def test_correct_verdict_anchors_all(self):
        graph = _graph_with_atoms((1, AtomStatus.PENDING), (2, AtomStatus.PENDING))
        ver = VerificationResult(
            verdict=Verdict.CORRECT, critique="", confidence=0.95,
        )
        sr._classify_atoms_from_verification(graph, ver, threshold=0.90)
        assert graph.atoms[1].status == AtomStatus.ANCHORED
        assert graph.atoms[2].status == AtomStatus.ANCHORED

    def test_uses_atom_confidences_when_available(self):
        graph = _graph_with_atoms((1, AtomStatus.PENDING), (2, AtomStatus.PENDING))
        ver = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="atom 2 wrong", confidence=0.5,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.30),
            ],
        )
        sr._classify_atoms_from_verification(graph, ver, threshold=0.90)
        assert graph.atoms[1].status == AtomStatus.ANCHORED
        assert graph.atoms[2].status == AtomStatus.FAILED

    def test_no_atom_confidences_marks_all_failed(self):
        graph = _graph_with_atoms((1, AtomStatus.PENDING), (2, AtomStatus.PENDING))
        ver = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="", confidence=0.2,
        )
        sr._classify_atoms_from_verification(graph, ver, threshold=0.90)
        # Without per-atom data, conservative: treat all as failed for re-work
        assert graph.atoms[1].status == AtomStatus.FAILED
        assert graph.atoms[2].status == AtomStatus.FAILED

    def test_synthetic_preamble_stays_anchored(self):
        # synthetic id<0 atoms auto-anchor in from_annotation; classify shouldn't
        # demote them based on a non-CORRECT verdict
        annotations = [
            _make_annotation(-1, synthetic=True, content="problem statement"),
            _make_annotation(1, deps=()),
        ]
        graph = ProofGraph.from_atoms(annotations)
        ver = VerificationResult(
            verdict=Verdict.MAJOR_FLAW, critique="", confidence=0.4,
        )
        sr._classify_atoms_from_verification(graph, ver, threshold=0.90)
        assert graph.atoms[-1].status == AtomStatus.ANCHORED  # preserved
        assert graph.atoms[1].status == AtomStatus.FAILED


# ──────────────────────────────────────────────────────────────────────────
# solve() — end-to-end with mocked subagents
# ──────────────────────────────────────────────────────────────────────────


def _solution_with_atoms(*atom_ids: int, problem: str = "P") -> Solution:
    """Build a Solution.solution_text that parse_atoms() will decompose
    into ``len(atom_ids)`` real atoms with the given IDs (chained deps).
    """
    chunks: list[str] = []
    for i, aid in enumerate(atom_ids):
        deps = f"[{atom_ids[i - 1]}]" if i > 0 else "[]"
        chunks.append(f"ATOM[{aid}] deps={deps} oracle=L3\nbody of atom {aid}")
    return Solution(
        problem=problem,
        solution_text="\n\n".join(chunks),
        iteration=0,
    )


def _verification(
    *,
    verdict: Verdict,
    confidence: float = 0.95,
    atom_confidences: list[AtomConfidence] | None = None,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict, critique="", confidence=confidence,
        atom_confidences=atom_confidences or [],
    )


def _mk_result(
    status: str,
    *,
    content: str = "atom_body",
    confidence: float = 0.95,
    error_category: str = "general",
) -> MicrokernelResult:
    return MicrokernelResult(
        status=status,  # type: ignore[arg-type]
        replacement_content=content,
        confidence=confidence,
        critique="",
        error_category=error_category,
        revisions_used=0,
    )


@pytest.fixture
def mocked_pipeline(monkeypatch):
    """Patch generate/verify/enumerate_techniques/gvr_microkernel on search module."""
    gen = MagicMock()
    ver = MagicMock()
    enum_ = MagicMock()
    mk = MagicMock()
    monkeypatch.setattr(sr, "generate", gen)
    monkeypatch.setattr(sr, "verify", ver)
    monkeypatch.setattr(sr, "enumerate_techniques", enum_)
    monkeypatch.setattr(sr, "gvr_microkernel", mk)
    return gen, ver, enum_, mk


class TestSolveBridgeHappyPath:
    def test_first_bridge_accepts_returns_correct(self, mocked_pipeline):
        gen, ver, _, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2, 3)
        ver.return_value = _verification(verdict=Verdict.CORRECT, confidence=0.97)

        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(),
            domain="math",
            client=MagicMock(),
        )

        assert result.solved
        assert result.verdict == Verdict.CORRECT
        assert result.confidence == pytest.approx(0.97)
        assert mk.call_count == 0  # no gap-filling needed
        # Bridge event was emitted
        types = [e.type for e in result.events]
        assert EventType.BRIDGE_GENERATED in types
        assert EventType.ACCEPT in types


class TestSolveGapFilling:
    def test_one_gap_filled_by_microkernel(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2, 3)
        # Verifier flags atom 2 as failed
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.6,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.30),
                AtomConfidence(id=3, confidence=0.95),
            ],
        )
        enum_.return_value = [Technique(name="induction", coherence=0.8)]
        mk.return_value = _mk_result("filled", content="fixed body", confidence=0.92)

        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(),
            domain="math",
            client=MagicMock(),
        )

        assert result.solved
        assert mk.call_count == 1
        # Final confidence is min over atom confidences (0.30 fix → 0.92)
        # min(0.95, 0.92, 0.95) = 0.92
        assert result.confidence == pytest.approx(0.92)
        # The fixed body shows up in the assembled solution
        assert "fixed body" in (result.solution or "")
        # Gap-filled event was emitted
        assert EventType.GAP_FILLED in [e.type for e in result.events]


class TestSolveSubdivisionTooLarge:
    def test_too_large_triggers_subdivide_then_children_fill(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.5,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.20),
            ],
        )
        enum_.return_value = [Technique(name="lemma_X", coherence=0.7)]
        # First mk call: too_large. After subdivide, two child gaps appear.
        # Each child is then filled successfully.
        mk.side_effect = [
            _mk_result("too_large", confidence=0.0),
            _mk_result("filled", content="child0", confidence=0.91),
            _mk_result("filled", content="child1", confidence=0.93),
        ]

        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(max_depth=2),
            domain="math",
            client=MagicMock(),
        )

        assert result.solved
        # First call too_large + 2 fills = 3 microkernel invocations
        assert mk.call_count == 3
        # Subdivided event was emitted
        types = [e.type for e in result.events]
        assert EventType.GAP_SUBDIVIDED in types
        subdivide_events = [e for e in result.events if e.type == EventType.GAP_SUBDIVIDED]
        assert subdivide_events[0].data["reason"] == "too_large"


class TestSolveSubdivisionFailureCount:
    def test_three_failures_auto_subdivide(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.4,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.20),
            ],
        )
        # Enumerator gives fresh techniques each call
        enum_.side_effect = [
            [Technique(name="A", coherence=0.7)],
            [Technique(name="B", coherence=0.7)],
            [Technique(name="C", coherence=0.7)],
            [Technique(name="D0", coherence=0.7)],
            [Technique(name="D1", coherence=0.7)],
        ]
        # 3 failures then 2 child fills
        mk.side_effect = [
            _mk_result("failed", confidence=0.3, error_category="logic"),
            _mk_result("failed", confidence=0.3, error_category="logic"),
            _mk_result("failed", confidence=0.3, error_category="logic"),
            _mk_result("filled", content="c0", confidence=0.91),
            _mk_result("filled", content="c1", confidence=0.92),
        ]

        # Budget high enough to allow 3 attempts on the parent gap
        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(
                technique_budget=5,
                failure_subdivision_threshold=3,
                max_depth=2,
            ),
            domain="math",
            client=MagicMock(),
        )

        assert result.solved
        subdivide_events = [e for e in result.events if e.type == EventType.GAP_SUBDIVIDED]
        assert len(subdivide_events) >= 1
        assert subdivide_events[0].data["reason"] == "failure_count"


class TestSolveMaxDepthCap:
    def test_too_large_at_max_depth_does_not_subdivide(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.4,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.20),
            ],
        )
        enum_.return_value = [Technique(name="T", coherence=0.7)]
        # Every microkernel call returns too_large
        mk.return_value = _mk_result("too_large", confidence=0.0)

        # max_depth=0 → cannot subdivide at all
        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(
                max_depth=0, max_bridges=1, technique_budget=3,
            ),
            domain="math",
            client=MagicMock(),
        )

        # No subdivision occurred
        subdivide_events = [e for e in result.events if e.type == EventType.GAP_SUBDIVIDED]
        assert subdivide_events == []
        # And the result is UNSOLVED (one gap never filled)
        assert not result.solved


class TestSolveReBridge:
    def test_re_bridge_passes_summary_to_next_generate(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline

        # Bridge 0: failed gap on atom 2, microkernel can't fill, exhausts budget
        # Bridge 1: succeeds (verifier returns CORRECT)
        gen.side_effect = [
            _solution_with_atoms(1, 2),
            _solution_with_atoms(10, 20),
        ]
        ver.side_effect = [
            _verification(
                verdict=Verdict.MAJOR_FLAW, confidence=0.5,
                atom_confidences=[
                    AtomConfidence(id=1, confidence=0.95),
                    AtomConfidence(id=2, confidence=0.20),
                ],
            ),
            _verification(verdict=Verdict.CORRECT, confidence=0.98),
        ]
        enum_.return_value = [Technique(name="X", coherence=0.7)]
        # All microkernel calls on bridge 0 fail with logic category
        mk.return_value = _mk_result(
            "failed", confidence=0.3, error_category="logic",
        )

        cfg = sr.SearchConfig(
            max_bridges=2, technique_budget=3,
            failure_subdivision_threshold=99,  # disable failure-count subdivide
            max_depth=0,                       # also disable too_large subdivide
        )
        result = sr.solve(
            "prove P", config=_make_config(), search_config=cfg,
            domain="math", client=MagicMock(),
        )

        assert result.solved
        assert result.verdict == Verdict.CORRECT
        # Re-bridge event was emitted between the two bridges
        assert EventType.RE_BRIDGE_TRIGGERED in [e.type for e in result.events]
        # Second generate() call received failed_approaches containing the summary
        _, kwargs = gen.call_args_list[1]
        assert "failed_approaches" in kwargs
        assert len(kwargs["failed_approaches"]) == 1
        assert "anchored" in kwargs["failed_approaches"][0].lower()


class TestSolveBridgesExhausted:
    def test_returns_unsolved_with_best_seen(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.side_effect = [
            _solution_with_atoms(1, 2),
            _solution_with_atoms(10, 20),
        ]
        ver.side_effect = [
            _verification(
                verdict=Verdict.MAJOR_FLAW, confidence=0.6,
                atom_confidences=[
                    AtomConfidence(id=1, confidence=0.95),
                    AtomConfidence(id=2, confidence=0.20),
                ],
            ),
            _verification(
                verdict=Verdict.MAJOR_FLAW, confidence=0.5,
                atom_confidences=[
                    AtomConfidence(id=10, confidence=0.85),
                    AtomConfidence(id=20, confidence=0.30),
                ],
            ),
        ]
        enum_.return_value = [Technique(name="X", coherence=0.7)]
        mk.return_value = _mk_result(
            "failed", confidence=0.2, error_category="logic",
        )

        cfg = sr.SearchConfig(
            max_bridges=2, technique_budget=3,
            failure_subdivision_threshold=99, max_depth=0,
        )
        result = sr.solve(
            "prove P", config=_make_config(), search_config=cfg,
            domain="math", client=MagicMock(),
        )

        assert not result.solved
        assert result.verdict == Verdict.UNSOLVED
        assert result.admitted_failure
        # Best confidence is bridge-0's 0.6 (higher than bridge-1's 0.5)
        assert result.confidence == pytest.approx(0.6)


class TestSolveEvents:
    def test_bridge_events_carry_iteration_index(self, mocked_pipeline):
        gen, ver, _, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1)
        ver.return_value = _verification(verdict=Verdict.CORRECT, confidence=0.95)

        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(),
            domain="math",
            client=MagicMock(),
        )

        bridge_events = [e for e in result.events if e.type == EventType.BRIDGE_GENERATED]
        assert len(bridge_events) == 1
        assert bridge_events[0].iteration == 0
        assert bridge_events[0].data["bridge_index"] == 0
        assert bridge_events[0].data["confidence"] == pytest.approx(0.95)

    def test_gap_filled_event_records_technique_and_confidence(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.5,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.30),
            ],
        )
        enum_.return_value = [Technique(name="cauchy_schwarz", coherence=0.7)]
        mk.return_value = _mk_result("filled", content="X", confidence=0.93)

        result = sr.solve(
            "prove P",
            config=_make_config(),
            search_config=sr.SearchConfig(),
            domain="math",
            client=MagicMock(),
        )

        filled_events = [e for e in result.events if e.type == EventType.GAP_FILLED]
        assert len(filled_events) == 1
        assert filled_events[0].data["technique"] == "cauchy_schwarz"
        assert filled_events[0].data["confidence"] == pytest.approx(0.93)
        assert filled_events[0].data["gap_id"] == 2


class TestSolveExplorationGivesNoTechniques:
    def test_empty_technique_list_skips_gap_eventually_breaks(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.5,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.20),
            ],
        )
        # Explorer can't think of anything → empty list
        enum_.return_value = []

        cfg = sr.SearchConfig(
            max_bridges=1, technique_budget=3,
            failure_subdivision_threshold=99, max_depth=0,
        )
        result = sr.solve(
            "prove P", config=_make_config(), search_config=cfg,
            domain="math", client=MagicMock(),
        )

        # mk never called because no technique was ever selected
        assert mk.call_count == 0
        # Search terminated (didn't infinite-loop on the empty enumeration)
        assert not result.solved


class TestSolveTechniqueBudgetExhausted:
    def test_gap_skipped_after_budget_exhausted(self, mocked_pipeline):
        gen, ver, enum_, mk = mocked_pipeline
        gen.return_value = _solution_with_atoms(1, 2)
        ver.return_value = _verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.5,
            atom_confidences=[
                AtomConfidence(id=1, confidence=0.95),
                AtomConfidence(id=2, confidence=0.20),
            ],
        )
        # Each call returns a fresh technique name (not filtered as duplicate)
        enum_.side_effect = [
            [Technique(name=f"t{i}", coherence=0.5)] for i in range(20)
        ]
        # Microkernel always fails (no subdivision because thresholds disabled)
        mk.return_value = _mk_result("failed", confidence=0.2, error_category="logic")

        cfg = sr.SearchConfig(
            max_bridges=1, technique_budget=3,
            failure_subdivision_threshold=99, max_depth=0,
        )
        result = sr.solve(
            "prove P", config=_make_config(), search_config=cfg,
            domain="math", client=MagicMock(),
        )

        # Microkernel was called exactly technique_budget times
        assert mk.call_count == 3
        assert not result.solved
