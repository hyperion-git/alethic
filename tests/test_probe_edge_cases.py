"""Probe B: Boundary conditions and edge cases in the main solve() orchestrator loop.

Agent B probes: boundary conditions and unusual config combinations in agent.py's
solve() orchestrator and supporting functions.

Probe points:
  B1 — All N candidates fail generation (empty candidate list handling)
  B2 — Zero-iteration solve (max_iterations=0 blocked by validation)
  B3 — All-UNSOLVED verdicts with N=3 (rank_candidates on zero-confidence pool)
  B4 — confidence_threshold=1.0 (never satisfied, should exhaust gracefully)
  B5 — max_revisions_per_cycle=0 + FIXABLE fallthrough (revision loop skipped)
  B6 — evidence_state initialization on checkpoint-resume (adaptive_compute safe)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from alethic.agent import MathAgent, rank_candidates
from alethic.models import (
    AgentConfig,
    Solution,
    Verdict,
    VerificationResult,
)


# ── Shared helpers ──────────────────────────────────────────────────────────


def _mock_response(text: str, stop_reason: str = "end_turn"):
    """Create a mock Anthropic response with explicit usage attributes."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 200
    return resp


CORRECT_095 = "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nPerfect.\n\nISSUES:\nNone"
MINOR_060 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.60\n\nCRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
)
MAJOR_020 = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.20\n\nCRITIQUE:\nWrong approach.\n\nISSUES:\n- Logic error"
)
UNSOLVED_000 = (
    "VERDICT: unsolved\nCONFIDENCE: 0.00\n\nCRITIQUE:\nCould not solve.\n\nISSUES:\nNone"
)
# FIXABLE with a well-formed CORRECTED SOLUTION block
FIXABLE_070_WITH_CORRECTION = (
    "VERDICT: fixable\nCONFIDENCE: 0.70\n\nCRITIQUE:\nSign error in step 3.\n\n"
    "ISSUES:\n- Sign error\n\n"
    "CORRECTED SOLUTION:\nFixed solution text here\n\nEND CORRECTED SOLUTION"
)


def _make_agent(config: AgentConfig) -> MathAgent:
    """Create a MathAgent with a mock client, bypassing real API calls."""
    agent = MathAgent(config=config, api_key="test-key")
    agent.client = MagicMock()
    return agent


def _write_checkpoint_dir(tmp_path: Path, current_iteration: int = 3) -> Path:
    """Write a minimal but valid checkpoint directory for resume tests."""
    session_dir = tmp_path / "test-session"
    session_dir.mkdir()
    (session_dir / "worklog").mkdir()

    session_data = {
        "schema_version": 1,
        "status": "checkpoint",
        "domain": "math",
        "problem": "prove sqrt(2) is irrational",
        "current_iteration": current_iteration,
        "best_confidence": 0.72,
        "failed_approaches": ["tried direct proof"],
        "stall_state": {
            "iterations_since_meaningful_improvement": 1,
            "iteration_final_verdicts": ["minor_issues"],
            "resets_used": 0,
            "reset_cooldown_remaining": 0,
        },
        "token_ledger": {"input_tokens": 5000, "output_tokens": 2000, "api_calls": 6},
        "config": {
            "max_iterations": 5,
            "confidence_threshold": 0.90,
            "best_of_n": 2,
            "context_threshold": 0.8,
        },
    }
    (session_dir / "session.json").write_text(json.dumps(session_data))
    (session_dir / "worklog" / "best_solution.md").write_text("Previous best solution")
    return session_dir


# ── Probe B1: All N candidates fail generation ──────────────────────────────


class TestProbeB1AllCandidatesFail:
    """Probe B1: When _generate_candidates returns [] because all N futures raised,
    the orchestrator must skip the iteration gracefully and return admitted_failure=True
    after exhausting max_iterations.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.generate", side_effect=RuntimeError("simulated generate failure"))
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b1_all_candidates_fail_n2_no_crash(
        self, _mock_session, _mock_gen, _mock_tools
    ):
        """When all N=2 candidates fail in the thread pool, solve() skips the iteration
        and exhausts max_iterations without raising an exception.
        """
        config = AgentConfig(
            max_iterations=2,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)

        result = agent.solve("test problem")

        assert result is not None, "solve() must return a result, not raise"
        assert result.verdict == Verdict.UNSOLVED
        assert result.admitted_failure is True
        assert result.confidence == 0.0
        # No verify or revise calls: all iterations were skipped due to empty candidates
        agent.client.messages.create.assert_not_called()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.generate", side_effect=RuntimeError("simulated generate failure"))
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b1_error_events_logged_per_failed_iteration(
        self, _mock_session, _mock_gen, _mock_tools
    ):
        """Each iteration where all candidates fail should log exactly one ERROR event
        with 'all candidates failed' in the error field.
        """
        from alethic.models import EventType

        config = AgentConfig(
            max_iterations=3,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)

        result = agent.solve("test problem")

        error_events = [e for e in result.events if e.type == EventType.ERROR]
        assert len(error_events) == 3, (
            f"Expected 3 ERROR events (one per failed iteration), got {len(error_events)}"
        )
        for ev in error_events:
            assert "all candidates failed" in ev.data.get("error", ""), (
                f"ERROR event should mention 'all candidates failed': {ev.data}"
            )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b1_n1_api_error_is_caught_and_iteration_skipped(
        self, _mock_session, _mock_tools
    ):
        """For N=1, anthropic.APIError during generation is caught by the outer handler
        and the iteration is skipped — solve() still returns a valid AgentResult.
        """
        config = AgentConfig(
            max_iterations=2,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)
        agent.client.messages.create.side_effect = anthropic.APIError(
            message="simulated API error",
            request=MagicMock(),
            body=None,
        )

        result = agent.solve("test problem")

        assert result is not None
        assert result.admitted_failure is True
        assert result.verdict == Verdict.UNSOLVED


# ── Probe B2: Zero-iteration solve (validation) ─────────────────────────────


class TestProbeB2ZeroIterations:
    """Probe B2: max_iterations=0 is rejected by AgentConfig.__post_init__ validation
    before any loop logic runs. The for loop would be range(1,1)=empty, but
    validation pre-empts it with ValueError.
    """

    def test_probe_b2_zero_iterations_raises_value_error(self):
        """AgentConfig(max_iterations=0) raises ValueError at construction time."""
        with pytest.raises(ValueError, match="max_iterations must be >= 1"):
            AgentConfig(max_iterations=0)

    def test_probe_b2_negative_iterations_raises_value_error(self):
        """AgentConfig(max_iterations=-5) raises ValueError at construction time."""
        with pytest.raises(ValueError, match="max_iterations must be >= 1"):
            AgentConfig(max_iterations=-5)

    def test_probe_b2_one_is_minimum_valid_value(self):
        """max_iterations=1 must not raise — it is the minimum valid value."""
        config = AgentConfig(max_iterations=1, verbose=False)
        assert config.max_iterations == 1

    def test_probe_b2_no_preset_produces_zero_or_negative_iterations(self):
        """Every named preset must produce max_iterations >= 1."""
        for preset_name in ("quick", "default", "thorough", "extreme"):
            config = AgentConfig.from_preset(preset_name)
            assert config.max_iterations >= 1, (
                f"Preset '{preset_name}' produced max_iterations={config.max_iterations}"
            )


# ── Probe B3: All-UNSOLVED verdicts with N=3 ────────────────────────────────


class TestProbeB3AllUnsolvedN3:
    """Probe B3: When all N=3 candidates receive UNSOLVED verdicts (confidence=0.0),
    rank_candidates returns a valid index, needs_revision() returns False (preventing
    revision calls), and solve() exhausts iterations gracefully.
    """

    def test_probe_b3_rank_candidates_handles_all_zero_confidence(self):
        """rank_candidates must return a valid index in [0, N) when all confidences are 0.0."""
        verifications = [
            VerificationResult(verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0),
            VerificationResult(verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0),
            VerificationResult(verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0),
        ]
        idx = rank_candidates(verifications)
        assert isinstance(idx, int), "rank_candidates must return an int"
        assert 0 <= idx < len(verifications), (
            f"rank_candidates returned out-of-bounds index {idx} for list of length 3"
        )

    def test_probe_b3_unsolved_does_not_need_revision(self):
        """Verdict.UNSOLVED returns False from needs_revision() at any confidence threshold."""
        vr = VerificationResult(verdict=Verdict.UNSOLVED, critique="N/A", confidence=0.0)
        assert vr.needs_revision(threshold=0.90) is False
        assert vr.needs_revision(threshold=0.0) is False
        assert vr.needs_revision(threshold=1.0) is False

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b3_all_unsolved_exhausts_without_revision(self, _mock_session, _mock_tools):
        """solve() with N=3 and all UNSOLVED verifications: no revision calls,
        admitted_failure=True after max_iterations=1.
        """
        config = AgentConfig(
            max_iterations=1,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)

        # 3 parallel generates + 3 sequential verifies, all UNSOLVED
        agent.client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response("Candidate C"),
            _mock_response(UNSOLVED_000),
            _mock_response(UNSOLVED_000),
            _mock_response(UNSOLVED_000),
        ]

        result = agent.solve("test problem")

        assert result is not None
        assert result.verdict == Verdict.UNSOLVED
        assert result.admitted_failure is True
        assert result.total_revisions == 0, (
            "No revisions should occur when all candidates are UNSOLVED"
        )
        # Exactly 6 calls: 3 gen + 3 ver, no revise
        assert agent.client.messages.create.call_count == 6

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b3_single_unsolved_no_revision(self, _mock_session, _mock_tools):
        """N=1 with UNSOLVED verdict: exactly 2 API calls (gen + verify), 0 revisions."""
        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)
        agent.client.messages.create.side_effect = [
            _mock_response("candidate solution"),
            _mock_response(UNSOLVED_000),
        ]

        result = agent.solve("test problem")

        assert result.total_revisions == 0
        assert agent.client.messages.create.call_count == 2


# ── Probe B4: confidence_threshold=1.0 ──────────────────────────────────────


class TestProbeB4ConfidenceThresholdOne:
    """Probe B4: confidence_threshold=1.0 means is_acceptable() is never True for
    confidence < 1.0. The loop must exhaust all iterations (not infinite-loop)
    and return admitted_failure=True.
    """

    def test_probe_b4_threshold_one_is_valid_config(self):
        """confidence_threshold=1.0 is at the [0.0, 1.0] boundary — must not raise."""
        config = AgentConfig(confidence_threshold=1.0, verbose=False)
        assert config.confidence_threshold == 1.0

    def test_probe_b4_is_acceptable_false_at_0_99(self):
        """CORRECT verdict at 0.99 confidence fails is_acceptable(1.0)."""
        vr = VerificationResult(verdict=Verdict.CORRECT, critique="Almost", confidence=0.99)
        assert vr.is_acceptable(threshold=1.0) is False

    def test_probe_b4_is_acceptable_true_at_exactly_1_0(self):
        """CORRECT verdict at 1.0 confidence passes is_acceptable(1.0)."""
        vr = VerificationResult(verdict=Verdict.CORRECT, critique="Perfect", confidence=1.0)
        assert vr.is_acceptable(threshold=1.0) is True

    def test_probe_b4_correct_below_threshold_needs_revision(self):
        """CORRECT verdict with confidence < threshold returns True from needs_revision()."""
        vr = VerificationResult(verdict=Verdict.CORRECT, critique="Good", confidence=0.95)
        assert vr.needs_revision(threshold=1.0) is True

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b4_threshold_one_exhausts_all_iterations(self, _mock_session, _mock_tools):
        """With threshold=1.0 and verifier always returning 0.95, all iterations
        are exhausted and admitted_failure=True — no infinite loop.
        """
        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=1,
            confidence_threshold=1.0,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
            adaptive_revision_budget=False,
        )
        agent = _make_agent(config)

        # Each iteration: gen + verify(0.95 correct, needs_revision=True)
        #                  + revise + re-verify(0.95, still needs_revision)
        agent.client.messages.create.side_effect = [
            _mock_response("Solution v1"),
            _mock_response(CORRECT_095),
            _mock_response("CHANGES MADE:\nFixed.\n\nREVISED SOLUTION:\nRevised v1"),
            _mock_response(CORRECT_095),
            _mock_response("Solution v2"),
            _mock_response(CORRECT_095),
            _mock_response("CHANGES MADE:\nFixed.\n\nREVISED SOLUTION:\nRevised v2"),
            _mock_response(CORRECT_095),
        ]

        result = agent.solve("test problem")

        assert result is not None, "solve() must not raise with threshold=1.0"
        assert result.admitted_failure is True
        assert result.verdict == Verdict.UNSOLVED
        # Best confidence seen was 0.95 — never reached threshold of 1.0
        assert result.confidence == pytest.approx(0.95, abs=0.01)


# ── Probe B5: max_revisions_per_cycle=0 + FIXABLE fallthrough ───────────────


class TestProbeB5ZeroRevisionsFixableFallthrough:
    """Probe B5: When a FIXABLE verdict's corrected solution fails re-verification,
    the code falls through to _run_revision_loop. With max_revisions_per_cycle=0,
    range(1, 0+1) is empty — the for-loop body never executes, the else clause
    fires, and None is returned without any revise API call.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b5_fixable_fallthrough_zero_revisions_no_revise_call(
        self, _mock_session, _mock_tools
    ):
        """FIXABLE re-verification failure with max_revisions_per_cycle=0:
        exactly 3 API calls (gen + initial verify + re-verify of correction),
        zero revise calls, admitted_failure=True.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
            adaptive_revision_budget=False,
        )
        agent = _make_agent(config)

        agent.client.messages.create.side_effect = [
            _mock_response("original solution"),
            _mock_response(FIXABLE_070_WITH_CORRECTION),  # initial verify → FIXABLE
            _mock_response(MAJOR_020),                    # re-verify of corrected → fails
            # No 4th call — revision loop must be skipped
        ]

        result = agent.solve("test problem")

        assert result is not None
        assert result.total_revisions == 0, (
            "No revisions should occur with max_revisions_per_cycle=0"
        )
        assert agent.client.messages.create.call_count == 3, (
            f"Expected exactly 3 API calls (gen + verify + re-verify), "
            f"got {agent.client.messages.create.call_count}"
        )
        assert result.admitted_failure is True

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_b5_run_revision_loop_with_zero_max_revisions_returns_none_directly(
        self, _mock_tools
    ):
        """Direct unit test of _run_revision_loop: max_revisions=0 returns None immediately
        with zero API calls. The for-else path with empty range fires correctly.
        """
        from alethic.agent import EventLog, RunState
        from alethic.subagents import _parse_verification

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )
        agent = _make_agent(config)
        state = RunState()
        log = EventLog()
        solution = Solution(problem="test", solution_text="original solution", iteration=1)
        verification = _parse_verification(MINOR_060)

        result = agent._run_revision_loop(
            problem="test",
            solution=solution,
            verification=verification,
            prompts={},
            iteration=1,
            state=state,
            log=log,
            threshold=0.90,
            max_revisions=0,
        )

        assert result is None, (
            "_run_revision_loop with max_revisions=0 must return None (empty range)"
        )
        assert state.total_revisions == 0
        agent.client.messages.create.assert_not_called()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b5_fixable_without_corrected_solution_uses_normal_revision(
        self, _mock_session, _mock_tools
    ):
        """FIXABLE verdict without a CORRECTED SOLUTION block (has_correction=False)
        goes directly to the normal revision loop. With max_revisions_per_cycle=1,
        one revision call occurs and the result is solved.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
        )
        agent = _make_agent(config)

        # FIXABLE verdict without CORRECTED SOLUTION block → has_correction=False
        fixable_no_correction = (
            "VERDICT: fixable\nCONFIDENCE: 0.70\n\n"
            "CRITIQUE:\nSign error in derivation.\n\nISSUES:\n- Sign error"
        )
        agent.client.messages.create.side_effect = [
            _mock_response("original solution"),
            _mock_response(fixable_no_correction),
            _mock_response("CHANGES MADE:\nFixed sign.\n\nREVISED SOLUTION:\nCorrected"),
            _mock_response(CORRECT_095),
        ]

        result = agent.solve("test problem")

        assert result.solved is True
        assert result.total_revisions == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.create_session_dir", side_effect=OSError("no filesystem"))
    def test_probe_b5_config_level_zero_revisions_also_skips_for_minor_issues(
        self, _mock_session, _mock_tools
    ):
        """With config.max_revisions_per_cycle=0 (not explicit override), MINOR_ISSUES
        still enters the revision path but immediately returns None — 2 API calls total.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
            adaptive_revision_budget=False,
        )
        agent = _make_agent(config)

        agent.client.messages.create.side_effect = [
            _mock_response("original solution"),
            _mock_response(MINOR_060),
            # No revision call expected
        ]

        result = agent.solve("test problem")

        assert result.total_revisions == 0
        assert agent.client.messages.create.call_count == 2  # gen + verify only
        assert result.admitted_failure is True


# ── Probe B6: evidence_state initialization on checkpoint-resume ─────────────


class TestProbeB6EvidenceStateOnResume:
    """Probe B6: When solve() is called with resume_from, evidence_state is initialized
    to None (line 702 of agent.py) and is NOT restored from the checkpoint.
    Even with adaptive_compute=True and iteration > 1, the guard
    'evidence_state is not None' safely prevents _compute_dynamic_n from being called.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.write_checkpoint")
    def test_probe_b6_resume_with_adaptive_compute_does_not_crash(
        self, _mock_cp, _mock_tools, tmp_path
    ):
        """Resuming with adaptive_compute=True must not crash even though
        evidence_state is None on the first resumed iteration.

        If the guard 'evidence_state is not None' were missing, this would
        raise AttributeError: 'NoneType' object has no attribute 'error_category'.
        """
        session_dir = _write_checkpoint_dir(tmp_path, current_iteration=3)

        config = AgentConfig(
            max_iterations=4,   # only iteration 4 runs (resumed from iter 3)
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
            adaptive_compute=True,
        )
        agent = _make_agent(config)

        agent.client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response(CORRECT_095),
            _mock_response(MINOR_060),
        ]

        # Must not raise AttributeError on evidence_state.error_category
        result = agent.solve(
            "prove sqrt(2) is irrational",
            resume_from=str(session_dir),
        )

        assert result is not None
        assert isinstance(result.verdict, Verdict)

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.write_checkpoint")
    def test_probe_b6_first_resumed_iter_uses_config_best_of_n_not_dynamic_n(
        self, _mock_cp, _mock_tools, tmp_path
    ):
        """On the first resumed iteration, n_this_iter equals config.best_of_n
        (because evidence_state is None, _compute_dynamic_n is not called).

        Verified indirectly: config.best_of_n=2, max_iterations=4, checkpoint at
        iter 3 → only iter 4 runs → exactly 4 API calls (2 gen + 2 ver).
        """
        session_dir = _write_checkpoint_dir(tmp_path, current_iteration=3)

        config = AgentConfig(
            max_iterations=4,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            stall_reset=False,
            adaptive_compute=True,
        )
        agent = _make_agent(config)

        agent.client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response(CORRECT_095),
            _mock_response(MINOR_060),
        ]

        result = agent.solve(
            "prove sqrt(2) is irrational",
            resume_from=str(session_dir),
        )

        # N=2 used: 2 gen + 2 ver = 4 calls (not N=1 which would give 2 calls)
        assert agent.client.messages.create.call_count == 4, (
            f"Expected 4 API calls (N=2: 2 gen + 2 ver), "
            f"got {agent.client.messages.create.call_count}"
        )
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.agent.write_checkpoint")
    def test_probe_b6_stall_state_correctly_restored_from_checkpoint(
        self, _mock_cp, _mock_tools, tmp_path
    ):
        """Checkpoint stall state is correctly restored — iterations_since_meaningful_improvement
        and resets_used are not reset to zero on resume.
        """
        from alethic.agent import RunState

        session_dir = _write_checkpoint_dir(tmp_path, current_iteration=3)

        config = AgentConfig(
            max_iterations=4,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
            stall_reset=True,
            stall_window=2,
        )
        agent = MathAgent(config=config, api_key="test-key")
        agent.client = MagicMock()

        agent.client.messages.create.side_effect = [
            _mock_response("Solution"),
            _mock_response(CORRECT_095),
        ]

        # Capture stall state at first _check_stall invocation
        captured: dict = {}
        original_check_stall = agent._check_stall

        def _capturing_check_stall(state: RunState) -> bool:
            if "iterations_since_meaningful_improvement" not in captured:
                captured["iterations_since_meaningful_improvement"] = (
                    state.iterations_since_meaningful_improvement
                )
                captured["resets_used"] = state.resets_used
            return original_check_stall(state)

        agent._check_stall = _capturing_check_stall  # type: ignore[method-assign]

        agent.solve("prove sqrt(2) is irrational", resume_from=str(session_dir))

        # Checkpoint had iterations_since_meaningful_improvement=1, resets_used=0
        assert captured.get("iterations_since_meaningful_improvement") == 1, (
            f"Expected stall counter=1 from checkpoint, got {captured}"
        )
        assert captured.get("resets_used") == 0

    def test_probe_b6_load_checkpoint_rejects_completed_sessions(self, tmp_path):
        """load_checkpoint raises ValueError for 'solved' or 'unsolved' status.
        Resuming a completed session is not supported.
        """
        from alethic.session import load_checkpoint

        session_dir = tmp_path / "done-session"
        session_dir.mkdir()

        for bad_status in ("solved", "unsolved"):
            (session_dir / "session.json").write_text(
                json.dumps({"status": bad_status, "current_iteration": 5})
            )
            with pytest.raises(ValueError, match="already completed"):
                load_checkpoint(str(session_dir))

    def test_probe_b6_evidence_state_is_not_an_instance_attribute(self):
        """evidence_state must be a local variable in solve() — not an instance attribute.
        If it were an attribute, it would persist (stale) across repeated solve() calls,
        corrupting adaptive_compute behavior on the second call.
        """
        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
        )
        agent = MathAgent(config=config, api_key="test-key")

        assert not hasattr(agent, "evidence_state"), (
            "evidence_state is a local variable in solve() — storing it as an instance "
            "attribute would cause stale state across multiple solve() calls."
        )
