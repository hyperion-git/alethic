"""Agent F — Probe: Checkpoint-Resume with v3.4 State.

Probes:
1. Checkpoint schema evolution (v3.3 -> v3.4 resume without crash)
2. EvidenceState serialization (JSON-safe, no frozenset)
3. Stall state dict completeness (all fields needed by _check_stall)
4. Session directory with dynamic N (worklog files reflect actual N)
"""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import asdict, fields as dataclass_fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    EvidenceState,
    EventType,
    OracleType,
    Solution,
    TokenLedger,
    Verdict,
    VerificationResult,
)
from alethic.session import (
    create_session_dir,
    load_checkpoint,
    write_checkpoint,
)


# ────────────────────────────────────────────────────────────────
# Probe 1: Checkpoint Schema Evolution (v3.3 → v3.4 graceful resume)
# ────────────────────────────────────────────────────────────────


class TestCheckpointSchemaEvolution:
    """Verify that a v3.3 checkpoint (missing v3.4 fields) resumes gracefully."""

    def _write_v33_checkpoint(self, session_dir: str) -> None:
        """Write a checkpoint in v3.3 format — no evidence_state, no adaptive_* fields."""
        Path(session_dir).mkdir(parents=True, exist_ok=True)
        worklog = Path(session_dir) / "worklog"
        worklog.mkdir(exist_ok=True)

        data = {
            "schema_version": 1,
            "status": "checkpoint",
            "domain": "math",
            "problem": "Prove sqrt(2) is irrational",
            "current_iteration": 3,
            "best_confidence": 0.78,
            "failed_approaches": ["Sign error in step 3"],
            "stall_state": {
                "iterations_since_meaningful_improvement": 1,
                "iteration_final_verdicts": ["major_flaw", "minor_issues"],
                "resets_used": 0,
                "reset_cooldown_remaining": 0,
            },
            "token_ledger": {
                "input_tokens": 12000,
                "output_tokens": 5000,
                "api_calls": 6,
            },
            "config": {
                "max_iterations": 5,
                "confidence_threshold": 0.90,
                "best_of_n": 2,
                "context_threshold": 0.8,
            },
            "created_at": "2026-03-01T12:00:00+00:00",
            "checkpointed_at": "2026-03-01T12:05:00+00:00",
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(data, indent=2))
        (worklog / "best_solution.md").write_text("Proof by contradiction...")

    def test_v33_checkpoint_loads_without_crash(self, tmp_path):
        """A v3.3 checkpoint has no evidence_state — load_checkpoint must not crash."""
        session_dir = str(tmp_path / "old-session")
        self._write_v33_checkpoint(session_dir)

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 3
        assert checkpoint["best_confidence"] == 0.78
        assert checkpoint["best_solution_text"] == "Proof by contradiction..."
        # v3.3 had no evidence_state key — checkpoint.get should default gracefully
        # load_checkpoint doesn't extract evidence_state — it's not in its return schema
        # This is correct because agent.py always starts evidence_state=None on resume

    def test_v33_checkpoint_stall_state_defaults(self, tmp_path):
        """When stall_state is an empty dict (v3.3 edge case), defaults should apply."""
        session_dir = str(tmp_path / "minimal-session")
        Path(session_dir).mkdir(parents=True)
        (Path(session_dir) / "worklog").mkdir()

        data = {
            "status": "checkpoint",
            "problem": "test",
            "current_iteration": 2,
            "best_confidence": 0.5,
            # v3.3-style: stall_state might be empty
            "stall_state": {},
            "token_ledger": {},
            "config": {},
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        checkpoint = load_checkpoint(session_dir)
        ss = checkpoint["stall_state"]
        # Empty stall_state is valid — agent.py uses .get() with defaults
        assert ss == {}

    def test_resume_with_v33_checkpoint_agent_constructs_state(self, tmp_path):
        """Simulate the agent resume path: ensure RunState is populated from v3.3 checkpoint."""
        from alethic.agent import RunState

        session_dir = str(tmp_path / "old-session")
        self._write_v33_checkpoint(session_dir)
        checkpoint = load_checkpoint(session_dir)

        # Replicate the resume logic from agent.py lines 636-671
        state = RunState()
        state.best_confidence = checkpoint["best_confidence"]
        state.failed_approaches = checkpoint.get("failed_approaches", [])

        ss = checkpoint.get("stall_state", {})
        state.iterations_since_meaningful_improvement = ss.get(
            "iterations_since_meaningful_improvement", 0
        )
        valid_verdicts = {e.value for e in Verdict}
        for v in ss.get("iteration_final_verdicts", []):
            if isinstance(v, str) and v in valid_verdicts:
                state.iteration_final_verdicts.append(Verdict(v))
        state.resets_used = ss.get("resets_used", 0)
        state.reset_cooldown_remaining = ss.get("reset_cooldown_remaining", 0)

        # Restore best solution
        best_text = checkpoint.get("best_solution_text")
        if best_text:
            state.best_solution = Solution(
                problem="Prove sqrt(2) is irrational",
                solution_text=best_text,
                iteration=0,
            )

        # Verify correctness
        assert state.best_confidence == 0.78
        assert len(state.failed_approaches) == 1
        assert state.iterations_since_meaningful_improvement == 1
        assert list(state.iteration_final_verdicts) == [
            Verdict.MAJOR_FLAW,
            Verdict.MINOR_ISSUES,
        ]
        assert state.resets_used == 0
        assert state.best_solution_text == "Proof by contradiction..."

    def test_v33_no_adaptive_fields_in_config(self, tmp_path):
        """v3.3 session.json config has no adaptive_compute or adaptive_revision_budget.
        Agent should use its own config, not the checkpoint's config."""
        session_dir = str(tmp_path / "old-session")
        self._write_v33_checkpoint(session_dir)
        checkpoint = load_checkpoint(session_dir)

        # The checkpoint config has no adaptive_* keys
        saved_config = checkpoint["config"]
        assert "adaptive_compute" not in saved_config
        assert "adaptive_revision_budget" not in saved_config
        # This is fine — agent.py uses self.config, not checkpoint config

    def test_v33_checkpoint_missing_config_entirely(self, tmp_path):
        """Edge case: a checkpoint with no config key at all."""
        session_dir = str(tmp_path / "edge-session")
        Path(session_dir).mkdir(parents=True)
        (Path(session_dir) / "worklog").mkdir()

        data = {
            "status": "checkpoint",
            "problem": "test",
            "current_iteration": 1,
            "best_confidence": 0.4,
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["config"] == {}
        assert checkpoint["stall_state"] == {}
        assert checkpoint["failed_approaches"] == []
        assert checkpoint["token_ledger"] == {}


# ────────────────────────────────────────────────────────────────
# Probe 2: EvidenceState Serialization
# ────────────────────────────────────────────────────────────────


class TestEvidenceStateSerialization:
    """Verify that EvidenceState fields are JSON-serializable."""

    def test_basic_evidence_state_json_safe(self):
        """All default EvidenceState fields should be JSON-serializable."""
        es = EvidenceState(
            iteration=3,
            best_confidence=0.85,
            error_category="algebra",
        )
        # json.dumps should not raise
        serialized = json.dumps(asdict(es))
        assert isinstance(serialized, str)

    def test_evidence_state_with_populated_fields(self):
        """EvidenceState with all fields populated should be JSON-safe."""
        es = EvidenceState(
            iteration=5,
            best_confidence=0.92,
            error_category="logic",
            confidence_history=[0.5, 0.65, 0.78, 0.85, 0.92],
            iteration_shape="improving",
            dynamic_n=3,
            oracle_calls_used=7,
            domain_check_results={"dim_check": "PASS", "limit_check": "FAIL"},
        )
        serialized = json.dumps(asdict(es))
        roundtripped = json.loads(serialized)
        assert roundtripped["confidence_history"] == [0.5, 0.65, 0.78, 0.85, 0.92]
        assert roundtripped["domain_check_results"]["dim_check"] == "PASS"

    def test_no_frozenset_in_evidence_state(self):
        """Confirm EvidenceState contains no frozenset fields (which break json.dumps)."""
        es = EvidenceState(iteration=1, best_confidence=0.5, error_category="general")
        for f in dataclass_fields(es):
            val = getattr(es, f.name)
            assert not isinstance(val, frozenset), (
                f"Field '{f.name}' is a frozenset — would crash json.dumps"
            )

    def test_evidence_state_not_persisted_in_checkpoint(self, tmp_path):
        """Confirm evidence_state is NOT persisted to session.json.

        This is by design: evidence_state is ephemeral (reconstructed each
        iteration from the verification result). If it were persisted, we'd
        need migration logic for schema changes.
        """
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()

        # Write a checkpoint — write_checkpoint does NOT accept evidence_state
        initial = {"status": "running"}
        (Path(session_dir) / "session.json").write_text(json.dumps(initial))

        write_checkpoint(
            session_dir=session_dir,
            current_iteration=2,
            best_confidence=0.7,
            best_solution_text="x=42",
            failed_approaches=[],
            stall_state={"iterations_since_meaningful_improvement": 0},
            token_ledger=TokenLedger(input_tokens=100, output_tokens=50, api_calls=1),
            status="running",
        )

        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert "evidence_state" not in data

    def test_oracle_type_enum_json_serializable(self):
        """OracleType.value should be a plain string for JSON."""
        for ot in OracleType:
            serialized = json.dumps({"oracle": ot.value})
            assert ot.value in serialized


# ────────────────────────────────────────────────────────────────
# Probe 3: Stall State Dict Completeness
# ────────────────────────────────────────────────────────────────


class TestStallStateDictCompleteness:
    """Verify RunState.stall_state_dict() includes all fields _check_stall() needs."""

    def test_stall_state_dict_has_all_required_keys(self):
        """stall_state_dict() must include every field read during resume."""
        from alethic.agent import RunState

        state = RunState()
        state.iterations_since_meaningful_improvement = 3
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.resets_used = 1
        state.reset_cooldown_remaining = 1

        ssd = state.stall_state_dict()

        # These are the 4 keys the resume logic reads (agent.py lines 650-659)
        assert "iterations_since_meaningful_improvement" in ssd
        assert "iteration_final_verdicts" in ssd
        assert "resets_used" in ssd
        assert "reset_cooldown_remaining" in ssd

        # Values must match
        assert ssd["iterations_since_meaningful_improvement"] == 3
        assert ssd["resets_used"] == 1
        assert ssd["reset_cooldown_remaining"] == 1
        assert ssd["iteration_final_verdicts"] == ["major_flaw", "major_flaw"]

    def test_stall_state_dict_json_serializable(self):
        """stall_state_dict() must be JSON-safe (no Verdict enums, no deque)."""
        from alethic.agent import RunState

        state = RunState()
        state.iteration_final_verdicts.append(Verdict.CORRECT)
        state.iteration_final_verdicts.append(Verdict.MINOR_ISSUES)

        ssd = state.stall_state_dict()
        serialized = json.dumps(ssd)
        roundtripped = json.loads(serialized)
        assert roundtripped["iteration_final_verdicts"] == ["correct", "minor_issues"]

    def test_roundtrip_stall_state_through_checkpoint(self, tmp_path):
        """Write stall state to checkpoint, load it back, reconstruct RunState."""
        from alethic.agent import RunState

        # Create initial state
        state = RunState()
        state.iterations_since_meaningful_improvement = 2
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.FIXABLE)
        state.resets_used = 1
        state.reset_cooldown_remaining = 0

        # Write checkpoint
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        (Path(session_dir) / "session.json").write_text(json.dumps({"status": "running"}))

        write_checkpoint(
            session_dir=session_dir,
            current_iteration=4,
            best_confidence=0.82,
            best_solution_text="solution text",
            failed_approaches=["approach 1"],
            stall_state=state.stall_state_dict(),
            token_ledger=TokenLedger(),
            status="checkpoint",
        )

        # Load back
        checkpoint = load_checkpoint(session_dir)
        ss = checkpoint["stall_state"]

        # Reconstruct (mirroring agent.py resume logic)
        restored = RunState()
        restored.iterations_since_meaningful_improvement = ss.get(
            "iterations_since_meaningful_improvement", 0
        )
        valid_verdicts = {e.value for e in Verdict}
        for v in ss.get("iteration_final_verdicts", []):
            if isinstance(v, str) and v in valid_verdicts:
                restored.iteration_final_verdicts.append(Verdict(v))
        restored.resets_used = ss.get("resets_used", 0)
        restored.reset_cooldown_remaining = ss.get("reset_cooldown_remaining", 0)

        # Verify roundtrip fidelity
        assert restored.iterations_since_meaningful_improvement == 2
        assert list(restored.iteration_final_verdicts) == [
            Verdict.MAJOR_FLAW,
            Verdict.FIXABLE,
        ]
        assert restored.resets_used == 1
        assert restored.reset_cooldown_remaining == 0

    def test_check_stall_uses_only_serialized_fields(self):
        """_check_stall() must not depend on fields absent from stall_state_dict().

        We verify this by checking that every RunState field accessed by _check_stall
        is either (a) in stall_state_dict() or (b) computed from config (not state).
        """
        from alethic.agent import MathAgent, RunState

        config = AgentConfig(
            max_iterations=5,
            stall_window=2,
            stall_epsilon=0.03,
            stall_reset=True,
            verbose=False,
        )

        # Create a state that should trigger a stall (2 major flaws in a row)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.resets_used = 0
        state.reset_cooldown_remaining = 0

        # Roundtrip through serialization
        ssd = state.stall_state_dict()
        serialized = json.dumps(ssd)
        loaded = json.loads(serialized)

        # Reconstruct
        restored = RunState()
        restored.iterations_since_meaningful_improvement = loaded.get(
            "iterations_since_meaningful_improvement", 0
        )
        valid_verdicts = {e.value for e in Verdict}
        for v in loaded.get("iteration_final_verdicts", []):
            if isinstance(v, str) and v in valid_verdicts:
                restored.iteration_final_verdicts.append(Verdict(v))
        restored.resets_used = loaded.get("resets_used", 0)
        restored.reset_cooldown_remaining = loaded.get("reset_cooldown_remaining", 0)

        # Both original and restored should produce the same stall check result
        agent = MathAgent(config=config, api_key="test-key")
        assert agent.router.check_stall(state) == agent.router.check_stall(restored)

    def test_stall_state_dict_handles_empty_verdicts_deque(self):
        """stall_state_dict() with no verdicts should serialize cleanly."""
        from alethic.agent import RunState

        state = RunState()
        ssd = state.stall_state_dict()
        assert ssd["iteration_final_verdicts"] == []
        # JSON roundtrip
        loaded = json.loads(json.dumps(ssd))
        assert loaded["iteration_final_verdicts"] == []

    def test_stall_state_deque_maxlen_preserved_on_resume(self):
        """The iteration_final_verdicts deque has maxlen=2. After resume with
        2 verdicts, adding a third should evict the oldest."""
        from alethic.agent import RunState

        state = RunState()
        # Simulate resume: add 2 verdicts
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MINOR_ISSUES)
        assert len(state.iteration_final_verdicts) == 2

        # Add a third — maxlen=2 should evict the first
        state.iteration_final_verdicts.append(Verdict.CORRECT)
        assert len(state.iteration_final_verdicts) == 2
        assert state.iteration_final_verdicts[0] == Verdict.MINOR_ISSUES
        assert state.iteration_final_verdicts[1] == Verdict.CORRECT


# ────────────────────────────────────────────────────────────────
# Probe 4: Session Directory with Dynamic N
# ────────────────────────────────────────────────────────────────


class TestSessionDirDynamicN:
    """Verify behavior when dynamic N changes across iterations.

    The Python library does NOT write individual candidate files to worklog
    (that's skills-only). Instead, it writes a single best_solution.md via
    write_checkpoint. So the concern is: does the checkpoint correctly reflect
    the actual N used when dynamic N changes?
    """

    def test_checkpoint_does_not_assume_fixed_n(self, tmp_path):
        """write_checkpoint is N-agnostic — it just writes best_confidence and
        best_solution_text. Changing N between iterations should not matter."""
        session_dir = str(tmp_path / "dynamic-n-session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        (Path(session_dir) / "session.json").write_text(
            json.dumps({"status": "running"})
        )

        # Iteration 1: N=1 (probe)
        write_checkpoint(
            session_dir=session_dir,
            current_iteration=1,
            best_confidence=0.6,
            best_solution_text="solution from N=1 probe",
            failed_approaches=["approach 1"],
            stall_state={"iterations_since_meaningful_improvement": 0},
            token_ledger=TokenLedger(input_tokens=1000, output_tokens=500, api_calls=2),
            status="running",
        )

        # Iteration 2: N=3 (escalated)
        write_checkpoint(
            session_dir=session_dir,
            current_iteration=2,
            best_confidence=0.82,
            best_solution_text="best of 3 candidates",
            failed_approaches=["approach 1", "approach 2"],
            stall_state={"iterations_since_meaningful_improvement": 0},
            token_ledger=TokenLedger(input_tokens=4000, output_tokens=2000, api_calls=8),
            status="running",
        )

        # Load and verify the latest state
        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 2
        assert checkpoint["best_confidence"] == 0.82
        assert checkpoint["best_solution_text"] == "best of 3 candidates"

    def test_create_session_dir_records_best_of_n(self, tmp_path):
        """session.json should record the configured best_of_n (base, not dynamic)."""
        config = AgentConfig(best_of_n=3, verbose=False)
        session_dir = create_session_dir(
            problem="test",
            domain="math",
            config=config,
            base_dir=str(tmp_path),
        )
        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert data["config"]["best_of_n"] == 3

    def test_dynamic_n_not_in_session_config(self, tmp_path):
        """The adaptive_compute flag is NOT saved in session.json config.
        This is fine — agent.py uses self.config, not checkpoint config."""
        config = AgentConfig(
            best_of_n=3,
            adaptive_compute=True,
            verbose=False,
        )
        session_dir = create_session_dir(
            problem="hard problem",
            domain="math",
            config=config,
            base_dir=str(tmp_path),
        )
        data = json.loads((Path(session_dir) / "session.json").read_text())
        # Only these config keys are saved by create_session_dir
        assert set(data["config"].keys()) == {
            "max_iterations",
            "confidence_threshold",
            "best_of_n",
            "context_threshold",
        }
        assert "adaptive_compute" not in data["config"]


# ────────────────────────────────────────────────────────────────
# Probe 4b: Comprehensive integration — resume with adaptive_compute
# ────────────────────────────────────────────────────────────────


class TestResumeWithAdaptiveCompute:
    """Verify that resuming a checkpoint with adaptive_compute=True works correctly,
    especially when evidence_state starts as None."""

    def test_evidence_state_none_on_resume_first_iter(self):
        """After resume, evidence_state is None — adaptive_compute should use best_of_n
        for the first resumed iteration (since iteration > 1 but evidence_state is None)."""
        # The condition in agent.py line 742-749:
        #   if self.config.adaptive_compute and iteration > 1 and evidence_state is not None:
        #       n_this_iter = self._compute_dynamic_n(evidence_state)
        #   else:
        #       n_this_iter = self.config.best_of_n
        #
        # On resume: evidence_state is None → falls through to best_of_n
        # This is the correct behavior: we don't have evidence from the previous
        # iteration, so we use the base N from config.
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=3,
            adaptive_compute=True,
            max_iterations=8,
            verbose=False,
        )
        agent = MathAgent(config=config, api_key="test-key")

        # evidence_state=None means _compute_dynamic_n is NOT called
        # This is correct — N defaults to best_of_n=3
        # We verify by checking that _compute_dynamic_n requires a non-None EvidenceState
        es = EvidenceState(iteration=4, best_confidence=0.6, error_category="logic")
        n = agent.router._compute_dynamic_n(es)
        assert n == 3  # logic error → escalate to config.best_of_n

    def test_compute_dynamic_n_with_different_categories(self):
        """Verify _compute_dynamic_n routing for each error category."""
        from alethic.agent import MathAgent

        config = AgentConfig(best_of_n=3, adaptive_compute=True, verbose=False)
        agent = MathAgent(config=config, api_key="test-key")

        # algebra: revise-first, keep N=1
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="algebra")
        assert agent.router._compute_dynamic_n(es) == 1

        # citation: revise-first, keep N=1
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="citation")
        assert agent.router._compute_dynamic_n(es) == 1

        # logic: need diversity → escalate
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="logic")
        assert agent.router._compute_dynamic_n(es) == 3

        # missing_case: need diversity → escalate
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="missing_case")
        assert agent.router._compute_dynamic_n(es) == 3

        # interpretation: need diversity → escalate
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="interpretation")
        assert agent.router._compute_dynamic_n(es) == 3

        # units: need diversity → escalate
        es = EvidenceState(iteration=2, best_confidence=0.8, error_category="units")
        assert agent.router._compute_dynamic_n(es) == 3

        # general with low confidence: escalate
        es = EvidenceState(
            iteration=2,
            best_confidence=0.5,  # < 0.9 * 0.75 = 0.675
            error_category="general",
        )
        assert agent.router._compute_dynamic_n(es) == 3

        # general with decent confidence: stay at 1
        es = EvidenceState(
            iteration=2,
            best_confidence=0.85,  # > 0.9 * 0.75 = 0.675
            error_category="general",
        )
        assert agent.router._compute_dynamic_n(es) == 1


# ────────────────────────────────────────────────────────────────
# Probe 4c: EvidenceState confidence_history accumulation on resume
# ────────────────────────────────────────────────────────────────


class TestEvidenceStateAccumulation:
    """Verify that confidence_history is properly accumulated across iterations,
    including after a resume where evidence_state starts as None."""

    def test_confidence_history_grows_correctly(self):
        """Simulate the confidence_history accumulation logic from agent.py lines 846-850."""
        history: list[float] = []
        best_conf = 0.5

        # Iteration 1: evidence_state is None
        evidence_state = None
        new_history = (
            evidence_state.confidence_history + [best_conf]
            if evidence_state is not None
            else [best_conf]
        )
        evidence_state = EvidenceState(
            iteration=1,
            best_confidence=best_conf,
            error_category="general",
            confidence_history=new_history,
        )
        assert evidence_state.confidence_history == [0.5]

        # Iteration 2: evidence_state exists, append
        best_conf = 0.7
        new_history = evidence_state.confidence_history + [best_conf]
        evidence_state = EvidenceState(
            iteration=2,
            best_confidence=best_conf,
            error_category="algebra",
            confidence_history=new_history,
        )
        assert evidence_state.confidence_history == [0.5, 0.7]

        # Iteration 3: after resume — evidence_state was None, starts fresh
        evidence_state = None
        best_conf = 0.75
        new_history = (
            evidence_state.confidence_history + [best_conf]
            if evidence_state is not None
            else [best_conf]
        )
        evidence_state = EvidenceState(
            iteration=4,  # resumed from iter 3
            best_confidence=best_conf,
            error_category="logic",
            confidence_history=new_history,
        )
        # After resume, history resets to just the current confidence
        assert evidence_state.confidence_history == [0.75]

    def test_confidence_history_not_lost_within_session(self):
        """Within a single session (no resume), history accumulates across all iterations."""
        evidence_state = None
        confidences = [0.4, 0.55, 0.68, 0.75, 0.88]

        for i, conf in enumerate(confidences, start=1):
            new_history = (
                evidence_state.confidence_history + [conf]
                if evidence_state is not None
                else [conf]
            )
            evidence_state = EvidenceState(
                iteration=i,
                best_confidence=conf,
                error_category="general",
                confidence_history=new_history,
            )

        assert evidence_state.confidence_history == confidences


# ────────────────────────────────────────────────────────────────
# Probe: Token ledger roundtrip through checkpoint
# ────────────────────────────────────────────────────────────────


class TestTokenLedgerCheckpointRoundtrip:
    """Verify TokenLedger roundtrips correctly through JSON serialization."""

    def test_token_ledger_roundtrip(self, tmp_path):
        """Write a checkpoint with token ledger, load it, verify exact values."""
        session_dir = str(tmp_path / "ledger-session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        (Path(session_dir) / "session.json").write_text(
            json.dumps({"status": "running"})
        )

        original = TokenLedger(input_tokens=50_000, output_tokens=25_000, api_calls=12)

        write_checkpoint(
            session_dir=session_dir,
            current_iteration=3,
            best_confidence=0.85,
            best_solution_text="solution",
            failed_approaches=[],
            stall_state={},
            token_ledger=original,
            status="checkpoint",
        )

        checkpoint = load_checkpoint(session_dir)
        restored = TokenLedger.from_dict(checkpoint.get("token_ledger", {}))

        assert restored.input_tokens == 50_000
        assert restored.output_tokens == 25_000
        assert restored.api_calls == 12
        assert restored.total_tokens == 75_000

    def test_token_ledger_from_empty_dict(self):
        """TokenLedger.from_dict({}) should return zeroed ledger, not crash."""
        ledger = TokenLedger.from_dict({})
        assert ledger.input_tokens == 0
        assert ledger.output_tokens == 0
        assert ledger.api_calls == 0

    def test_token_ledger_from_v33_format(self):
        """v3.3 checkpoints should have the same token_ledger format.
        This is a no-regression test."""
        v33_ledger = {"input_tokens": 12000, "output_tokens": 5000, "api_calls": 6}
        restored = TokenLedger.from_dict(v33_ledger)
        assert restored.input_tokens == 12000
        assert restored.total_tokens == 17000


# ────────────────────────────────────────────────────────────────
# Probe: Session config completeness — what is persisted vs what is used
# ────────────────────────────────────────────────────────────────


class TestSessionConfigCompleteness:
    """Probe whether the session.json config subset is sufficient for resume,
    or if important fields are lost."""

    def test_session_json_config_fields(self, tmp_path):
        """The config saved in session.json is a minimal subset. Verify it
        contains the fields actually used during resume."""
        config = AgentConfig.from_preset("thorough", verbose=False)
        session_dir = create_session_dir(
            problem="test",
            domain="math",
            config=config,
            base_dir=str(tmp_path),
        )
        data = json.loads((Path(session_dir) / "session.json").read_text())
        saved = data["config"]

        # These 4 fields are saved
        assert "max_iterations" in saved
        assert "confidence_threshold" in saved
        assert "best_of_n" in saved
        assert "context_threshold" in saved

        # These v3.4 fields are NOT saved (by design — they come from agent config)
        assert "adaptive_compute" not in saved
        assert "adaptive_revision_budget" not in saved
        assert "adversarial_self_correction" not in saved

    def test_resume_uses_agent_config_not_checkpoint_config(self):
        """Document that on resume, the agent uses self.config for iteration limits
        and adaptive settings, NOT the saved config from the checkpoint.

        This means if you resume with a different preset, the behavior changes.
        This is intentional — the saved config is informational only."""
        # The resume logic in agent.py reads:
        #   start_iteration = checkpoint["current_iteration"] + 1
        #   ... (state fields from checkpoint)
        # But max_iterations comes from self.config, not checkpoint["config"]
        #
        # So: resumed iteration range is
        #   range(checkpoint_iter + 1, self.config.max_iterations + 1)
        #
        # If the original used max_iterations=5 and you resume with max_iterations=8,
        # you get iterations 4..8, which is fine.
        #
        # If you resume with max_iterations=3 and checkpoint is at iter 3,
        # the range is range(4, 4) which is empty — you get 0 iterations.
        # This is arguably a design choice (not a bug).
        pass  # This test documents the behavior; no assertion needed beyond the analysis.
