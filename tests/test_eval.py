"""Tests for eval harness (feature 2.3)."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch

from alethic.models import AgentEvent, EventType


def _make_benchmark(problems: list[dict]) -> str:
    """Write a benchmark JSON to a temp file and return the path."""
    data = {"name": "test-bench", "version": "1.0", "problems": problems}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(data, tmp)
        return tmp.name


class TestBenchmarkFormat:
    def test_load_benchmark_returns_problems(self):
        from alethic.eval.harness import load_benchmark

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "Is 2 prime?", "expected_solvable": True},
        ])
        bench = load_benchmark(path)
        assert bench["name"] == "test-bench"
        assert len(bench["problems"]) == 1

    def test_load_benchmark_raises_on_missing_fields(self):
        import pytest

        from alethic.eval.harness import load_benchmark

        # A problem missing required fields
        path = _make_benchmark([{"id": "p1"}])
        with pytest.raises(ValueError, match="missing required field"):
            load_benchmark(path)


class TestRunBenchmark:
    @patch("alethic.eval.harness.MathAgent")
    def test_run_benchmark_returns_score_report(self, mock_agent):
        from alethic.eval.harness import run_benchmark

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.confidence = 0.95
        mock_result.iterations_used = 2
        mock_result.verdict.value = "correct"
        mock_agent.return_value.solve.return_value = mock_result

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "Prove 2 is prime.", "expected_solvable": True},
            {"id": "p2", "domain": "math", "problem": "Prove 1+1=3.", "expected_solvable": False},
        ])
        report = run_benchmark(path, api_key="fake-key", preset="quick")

        assert "total" in report
        assert "solve_rate" in report
        assert "results" in report
        assert report["total"] == 2

    @patch("alethic.eval.harness.MathAgent")
    def test_run_benchmark_score_rate_is_fraction(self, mock_agent):
        from alethic.eval.harness import run_benchmark

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.confidence = 0.9
        mock_result.iterations_used = 1
        mock_result.verdict.value = "correct"
        mock_agent.return_value.solve.return_value = mock_result

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "p1", "expected_solvable": True},
        ])
        report = run_benchmark(path, api_key="fake-key", preset="quick")
        assert 0.0 <= report["solve_rate"] <= 1.0

    @patch("alethic.eval.harness.MathAgent")
    def test_report_carries_anchor_and_epoch(self, mock_agent):
        """A report without both is not comparable to any other report."""
        from alethic.eval.harness import GATE_EPOCH, anchor_sha256, load_benchmark, run_benchmark

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.confidence = 0.9
        mock_result.iterations_used = 1
        mock_result.verdict.value = "correct"
        mock_agent.return_value.solve.return_value = mock_result

        path = _make_benchmark([
            {"id": "p1", "domain": "math", "problem": "p1", "expected_solvable": True},
            {"id": "f1", "domain": "math", "problem": "f1", "expected_solvable": False},
        ])
        report = run_benchmark(path, api_key="fake-key", preset="quick")

        assert report["anchor_sha256"] == anchor_sha256(load_benchmark(path))
        assert report["gate_epoch"] == GATE_EPOCH
        # The mock "solves" everything, so the anchor is a false positive.
        assert report["solve_rate"] == 1.0
        assert report["false_claim_accept_rate"] == 1.0


class TestAnchorHash:
    """The digest must be stable against noise and sensitive to the anchor set."""

    def _bench(self, problems):
        return {"name": "b", "version": "1.0", "problems": problems}

    def test_invariant_to_problem_order(self):
        from alethic.eval.harness import anchor_sha256

        a = {"id": "a", "domain": "math", "problem": "P", "expected_solvable": True}
        b = {"id": "b", "domain": "physics", "problem": "Q", "expected_solvable": False}
        assert anchor_sha256(self._bench([a, b])) == anchor_sha256(self._bench([b, a]))

    def test_changes_when_problem_text_edited(self):
        from alethic.eval.harness import anchor_sha256

        base = [{"id": "a", "domain": "math", "problem": "P", "expected_solvable": True}]
        edited = [{"id": "a", "domain": "math", "problem": "P!", "expected_solvable": True}]
        assert anchor_sha256(self._bench(base)) != anchor_sha256(self._bench(edited))

    def test_changes_when_solvability_flips(self):
        """The flag partitions the two populations — flipping it must invalidate."""
        from alethic.eval.harness import anchor_sha256

        base = [{"id": "a", "domain": "math", "problem": "P", "expected_solvable": True}]
        flipped = [{"id": "a", "domain": "math", "problem": "P", "expected_solvable": False}]
        assert anchor_sha256(self._bench(base)) != anchor_sha256(self._bench(flipped))

    def test_changes_when_problem_added(self):
        from alethic.eval.harness import anchor_sha256

        base = [{"id": "a", "domain": "math", "problem": "P", "expected_solvable": True}]
        grown = base + [
            {"id": "b", "domain": "math", "problem": "Q", "expected_solvable": True}
        ]
        assert anchor_sha256(self._bench(base)) != anchor_sha256(self._bench(grown))

    def test_changes_when_domain_edited(self):
        """Domain selects the agent class, so it is part of what was measured."""
        from alethic.eval.harness import anchor_sha256

        base = [{"id": "a", "domain": "math", "problem": "P", "expected_solvable": True}]
        moved = [{"id": "a", "domain": "physics", "problem": "P", "expected_solvable": True}]
        assert anchor_sha256(self._bench(base)) != anchor_sha256(self._bench(moved))


class TestSplitMetrics:
    """solve_rate and the false-claim rates must not share a denominator."""

    def _r(self, pid, *, solvable, solved, verdict="correct", error=None):
        return {
            "id": pid,
            "expected_solvable": solvable,
            "solved": solved,
            "verdict": verdict,
            "error": error,
        }

    def test_solve_rate_excludes_false_claim_anchors(self):
        """2 solvable both solved + 1 anchor rejected => 1.0, not 2/3."""
        from alethic.eval.harness import split_metrics

        m = split_metrics([
            self._r("p1", solvable=True, solved=True),
            self._r("p2", solvable=True, solved=True),
            self._r("f1", solvable=False, solved=False, verdict="major_flaw"),
        ])
        assert m["solve_rate"] == 1.0
        assert m["n_solvable"] == 2
        assert m["n_false_claim"] == 1

    def test_accepted_false_claim_is_a_false_positive(self):
        from alethic.eval.harness import split_metrics

        m = split_metrics([
            self._r("f1", solvable=False, solved=True),
            self._r("f2", solvable=False, solved=False, verdict="major_flaw"),
        ])
        assert m["false_claims_accepted"] == 1
        assert m["false_claim_accept_rate"] == 0.5
        assert m["false_claim_reject_rate"] == 0.5

    def test_errored_anchor_is_not_counted_as_a_rejection(self):
        """An error yields no verdict — it must leave the anchor denominator."""
        from alethic.eval.harness import split_metrics

        m = split_metrics([
            self._r("f1", solvable=False, solved=True),
            self._r("f2", solvable=False, solved=False, verdict="error", error="boom"),
        ])
        assert m["n_false_claim"] == 2
        assert m["n_false_claim_scored"] == 1
        # Naive `not solved` would report 0.5 here and flatter the verifier.
        assert m["false_claim_accept_rate"] == 1.0
        assert m["false_claim_reject_rate"] == 0.0

    def test_errored_solvable_problem_counts_as_unsolved(self):
        """Dropping errored solvable problems would inflate solve_rate."""
        from alethic.eval.harness import split_metrics

        m = split_metrics([
            self._r("p1", solvable=True, solved=True),
            self._r("p2", solvable=True, solved=False, verdict="error", error="boom"),
        ])
        assert m["solve_rate"] == 0.5
        assert m["n_errors"] == 1

    def test_no_anchors_gives_none_not_zero(self):
        """0.0 would read as 'accepted nothing'; None says 'nothing measured'."""
        from alethic.eval.harness import split_metrics

        m = split_metrics([self._r("p1", solvable=True, solved=True)])
        assert m["false_claim_accept_rate"] is None
        assert m["false_claim_reject_rate"] is None

    def test_verdict_distribution_separates_rejection_from_exhaustion(self):
        """reject_rate alone cannot distinguish these two; the histogram can."""
        from alethic.eval.harness import split_metrics

        m = split_metrics([
            self._r("f1", solvable=False, solved=False, verdict="major_flaw"),
            self._r("f2", solvable=False, solved=False, verdict="unsolved"),
        ])
        assert m["false_claim_reject_rate"] == 1.0
        assert m["false_claim_verdicts"] == {"major_flaw": 1, "unsolved": 1}


class TestAtomMeasurement:
    def test_measure_atoms_returns_metrics(self):
        from alethic.eval.harness import measure_atoms

        events = [
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                data={
                    "candidate": 1,
                    "solution_preview": "ATOM[1] deps=[] oracle=L3\nStep 1...\nATOM[2] deps=[1] oracle=L2\nStep 2...",
                },
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"candidate": 1, "verdict": "correct", "confidence": 0.92},
            ),
            AgentEvent(
                type=EventType.GENERATE,
                iteration=2,
                data={"candidate": 1, "solution_preview": "Just a plain solution"},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=2,
                data={"candidate": 1, "verdict": "correct", "confidence": 0.95},
            ),
        ]

        metrics = measure_atoms(events, n_iterations=2)
        assert "annotation_rate" in metrics
        assert "atom_counts" in metrics
        assert metrics["annotation_rate"] == 0.5  # 1/2 iterations had atoms
        assert len(metrics["atom_counts"]) == 2
        assert metrics["atom_counts"][0] > 0  # iter 1 had atoms
        assert metrics["atom_counts"][1] == 0  # iter 2 had no atoms

    def test_measure_atoms_best_of_n_selects_winner(self):
        """When multiple candidates exist, measure_atoms picks the winner."""
        from alethic.eval.harness import measure_atoms

        events = [
            # Candidate 0: no atoms, lower confidence
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                data={"candidate": 0, "solution_preview": "Plain solution"},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"candidate": 0, "verdict": "correct", "confidence": 0.80},
            ),
            # Candidate 1: has atoms, higher confidence (winner)
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                data={
                    "candidate": 1,
                    "solution_preview": "ATOM[1] deps=[] oracle=L3\nStep 1...",
                },
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"candidate": 1, "verdict": "correct", "confidence": 0.95},
            ),
        ]

        metrics = measure_atoms(events, n_iterations=1)
        # Winner is candidate 1, which has atoms
        assert metrics["atom_counts"][0] > 0
        assert metrics["annotation_rate"] == 1.0

    def test_measure_atoms_empty_events(self):
        from alethic.eval.harness import measure_atoms

        metrics = measure_atoms([], n_iterations=0)
        assert metrics["annotation_rate"] == 0.0
        assert metrics["atom_counts"] == []
        assert metrics["mean_atom_count"] == 0.0

    def test_measure_atoms_mean_atom_count(self):
        from alethic.eval.harness import measure_atoms

        events = [
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                data={
                    "candidate": 1,
                    "solution_preview": "ATOM[1] deps=[] oracle=L3\nA\nATOM[2] deps=[1] oracle=L2\nB",
                },
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"candidate": 1, "verdict": "correct", "confidence": 0.9},
            ),
            AgentEvent(
                type=EventType.GENERATE,
                iteration=2,
                data={"candidate": 1, "solution_preview": "No atoms here"},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=2,
                data={"candidate": 1, "verdict": "correct", "confidence": 0.9},
            ),
        ]

        metrics = measure_atoms(events, n_iterations=2)
        # 2 atoms in iter 1 + 0 atoms in iter 2 = mean 1.0
        assert metrics["mean_atom_count"] == 1.0


class TestPuctComparison:
    def test_puct_with_two_approaches(self):
        from alethic.eval.harness import compute_puct_comparison

        events = [
            # Iter 1
            AgentEvent(type=EventType.GENERATE, iteration=1,
                       data={"candidate": 1, "solution_preview": "algebra approach"}),
            AgentEvent(type=EventType.GENERATE, iteration=1,
                       data={"candidate": 2, "solution_preview": "logic approach"}),
            AgentEvent(type=EventType.VERIFY, iteration=1,
                       data={"candidate": 1, "verdict": "major_flaw", "confidence": 0.70,
                             "error_category": "algebra"}),
            AgentEvent(type=EventType.VERIFY, iteration=1,
                       data={"candidate": 2, "verdict": "major_flaw", "confidence": 0.60,
                             "error_category": "logic"}),
            # Iter 2
            AgentEvent(type=EventType.GENERATE, iteration=2,
                       data={"candidate": 1, "solution_preview": "algebra approach v2"}),
            AgentEvent(type=EventType.GENERATE, iteration=2,
                       data={"candidate": 2, "solution_preview": "new approach"}),
            AgentEvent(type=EventType.VERIFY, iteration=2,
                       data={"candidate": 1, "verdict": "minor_issues", "confidence": 0.75,
                             "error_category": "algebra"}),
            AgentEvent(type=EventType.VERIFY, iteration=2,
                       data={"candidate": 2, "verdict": "major_flaw", "confidence": 0.65,
                             "error_category": "missing_case"}),
        ]

        result = compute_puct_comparison(events)
        assert "reordered_iterations" in result
        assert "total_iterations" in result
        assert "divergence_rate" in result
        assert 0.0 <= result["divergence_rate"] <= 1.0

    def test_puct_single_candidate_no_divergence(self):
        """With N=1 per iteration, PUCT can never reorder — divergence should be 0."""
        from alethic.eval.harness import compute_puct_comparison

        events = [
            AgentEvent(type=EventType.GENERATE, iteration=1,
                       data={"candidate": 1, "solution_preview": "sol"}),
            AgentEvent(type=EventType.VERIFY, iteration=1,
                       data={"candidate": 1, "verdict": "correct", "confidence": 0.9}),
        ]
        result = compute_puct_comparison(events)
        assert result["divergence_rate"] == 0.0


class TestGateBenchmark:
    def test_gate_benchmark_loads(self):
        """gate-v38.json loads successfully with all 100 problems."""
        from alethic.eval.harness import load_benchmark
        import os

        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "benchmarks",
            "gate-v38.json",
        )
        bench = load_benchmark(path)
        assert bench["name"] == "gate-v38"
        assert len(bench["problems"]) == 100

        # Domain split
        math_count = sum(1 for p in bench["problems"] if p["domain"] == "math")
        physics_count = sum(1 for p in bench["problems"] if p["domain"] == "physics")
        assert math_count == 50, f"Expected 50 math, got {math_count}"
        assert physics_count == 50, f"Expected 50 physics, got {physics_count}"

        # All IDs unique
        ids = [p["id"] for p in bench["problems"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

        # False claims count
        false_count = sum(1 for p in bench["problems"] if not p["expected_solvable"])
        assert false_count == 10, f"Expected 10 false claims, got {false_count}"
