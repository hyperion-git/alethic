"""Adversarial stress tests for PhysicsAgent's orchestrator loop.

Tests cover edge cases, prompt injection verification, revision loop dynamics,
failure modes, and config propagation — all with mocked API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from alethic.models import AgentConfig, Verdict
from alethic.physics_prompts import (
    PHYSICS_REVISER_SYSTEM,
    PHYSICS_VERIFIER_SYSTEM,
)


def _mock_response(text: str):
    """Create a mock Anthropic response object with a single text block."""
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = text
    mock_resp = MagicMock()
    mock_resp.content = [mock_block]
    return mock_resp


# ---------------------------------------------------------------------------
# 1. PhysicsAgent uses physics prompts in the revision loop
# ---------------------------------------------------------------------------


class TestPhysicsPromptsInRevisionLoop:
    """Verify that BOTH the reviser AND the re-verifier calls inside the
    revision loop use physics-specific system prompts, not the math defaults."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revision_and_reverify_use_physics_prompts(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Generate
            _mock_response("Candidate derivation of Schrodinger equation"),
            # Verify -> minor_issues
            _mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.70\n\n"
                "CRITIQUE:\nSign error in the kinetic energy term.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Sign error in kinetic term"
            ),
            # Revise
            _mock_response(
                "CHANGES MADE:\nFixed sign.\n\n"
                "REVISED SOLUTION:\nCorrected derivation"
            ),
            # Re-verify -> correct
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
                "CRITIQUE:\nDerivation is now sound.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the time-independent Schrodinger equation")

        assert result.solved
        assert result.total_revisions == 1

        calls = mock_client.messages.create.call_args_list
        assert len(calls) == 4

        # Call 0: Generator — physics system prompt
        gen_system = calls[0].kwargs.get("system", "")
        assert "theoretical physics" in gen_system.lower()

        # Call 1: Verifier — physics verifier system prompt
        ver_system = calls[1].kwargs.get("system", "")
        assert "physics derivation verifier" in ver_system.lower()

        # Call 2: Reviser — physics reviser system prompt
        rev_system = calls[2].kwargs.get("system", "")
        assert "physics derivation reviser" in rev_system.lower()
        assert PHYSICS_REVISER_SYSTEM in rev_system

        # Call 3: Re-verifier — STILL physics verifier, not math verifier
        reverify_system = calls[3].kwargs.get("system", "")
        assert "physics derivation verifier" in reverify_system.lower()
        assert PHYSICS_VERIFIER_SYSTEM in reverify_system


# ---------------------------------------------------------------------------
# 2. False premise detection works with PhysicsAgent
# ---------------------------------------------------------------------------


class TestFalsePremisePhysicsAgent:
    """Verify that PhysicsAgent returns early with a reason when the verifier
    detects a false premise (e.g., violates conservation of energy)."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_false_premise_early_exit(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=5,
            enable_code_execution=False,
            verbose=False,
        )

        false_premise_reason = (
            "Violates conservation of energy. A perpetual motion machine "
            "of the first kind cannot exist."
        )

        responses = [
            # Generate
            _mock_response("Here is a derivation of perpetual motion..."),
            # Verify -> unsolved with REASON
            _mock_response(
                f"VERDICT: unsolved\nCONFIDENCE: 0.05\n\n"
                f"CRITIQUE:\nThe problem asks to derive a perpetual motion device, "
                f"which is physically impossible.\n\n"
                f"REASON: {false_premise_reason}\n\n"
                f"ISSUES:\n- Problem premise violates first law of thermodynamics"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive a perpetual motion machine")

        assert not result.solved
        assert result.verdict == Verdict.UNSOLVED
        assert result.admitted_failure is False  # early exit, not exhaustion
        assert result.iterations_used == 1
        assert "conservation of energy" in result.solution.lower()

        # Should only have called 2 API calls (generate + verify), not more
        assert mock_client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# 3. Correct but below threshold triggers revision with physics prompts
# ---------------------------------------------------------------------------


class TestCorrectBelowThreshold:
    """CORRECT verdict with confidence below the threshold should trigger
    revision, and that revision must use physics prompts."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_correct_low_confidence_triggers_physics_revision(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=1,
            enable_code_execution=False,
            verbose=False,
            confidence_threshold=0.90,
        )

        responses = [
            # Generate
            _mock_response("Derivation with uncertain steps"),
            # Verify -> correct but below threshold
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.75\n\n"
                "CRITIQUE:\nLooks right but some steps are hand-wavy.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Step 3 lacks rigorous justification"
            ),
            # Revise (should happen because 0.75 < 0.90)
            _mock_response(
                "CHANGES MADE:\nAdded rigorous justification.\n\n"
                "REVISED SOLUTION:\nImproved derivation with justification"
            ),
            # Re-verify -> now passes threshold
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.93\n\n"
                "CRITIQUE:\nNow rigorous.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the Euler-Lagrange equation")

        assert result.solved
        assert result.total_revisions == 1
        assert result.confidence == 0.93

        # The reviser call (index 2) should use physics reviser prompt
        calls = mock_client.messages.create.call_args_list
        assert len(calls) == 4
        rev_system = calls[2].kwargs.get("system", "")
        assert "physics derivation reviser" in rev_system.lower()


# ---------------------------------------------------------------------------
# 4. Max iterations exhaustion
# ---------------------------------------------------------------------------


class TestMaxIterationsExhaustion:
    """Set max_iterations=2, max_revisions=0. Both iterations get major_flaw.
    Verify admitted_failure=True and best solution is tracked."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_exhaustion_with_no_revisions(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iteration 1: generate
            _mock_response("First attempt derivation"),
            # Iteration 1: verify -> major_flaw (confidence 0.15)
            _mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.15\n\n"
                "CRITIQUE:\nFundamentally wrong approach.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Wrong Lagrangian"
            ),
            # Iteration 2: generate
            _mock_response("Second attempt derivation"),
            # Iteration 2: verify -> major_flaw (confidence 0.25)
            _mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.25\n\n"
                "CRITIQUE:\nBetter but still flawed.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Boundary condition error"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive something difficult")

        assert not result.solved
        assert result.admitted_failure is True
        assert result.verdict == Verdict.UNSOLVED
        assert result.iterations_used == 2
        assert result.total_revisions == 0

        # Best confidence should track the higher of the two (0.25)
        assert result.confidence == 0.25

        # Best solution should be from iteration 2 (higher confidence)
        assert result.solution == "Second attempt derivation"

        # Only 4 API calls: 2x (generate + verify), no revisions
        assert mock_client.messages.create.call_count == 4


# ---------------------------------------------------------------------------
# 5. Revision loop with major_flaw breaks out
# ---------------------------------------------------------------------------


class TestRevisionMajorFlawBreaksOut:
    """First solution gets minor_issues, revision gets major_flaw.
    Agent should break out of revision loop and start a new iteration."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_major_flaw_in_revision_restarts_iteration(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=3,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iteration 1: generate
            _mock_response("Iteration 1 derivation"),
            # Iteration 1: verify -> minor_issues
            _mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.65\n\n"
                "CRITIQUE:\nSmall gap in step 3.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Missing justification in step 3"
            ),
            # Iteration 1, revision 1: revise
            _mock_response(
                "CHANGES MADE:\nAttempted fix but introduced new error.\n\n"
                "REVISED SOLUTION:\nRevised but worse derivation"
            ),
            # Iteration 1, revision 1: re-verify -> major_flaw (should break)
            _mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.10\n\n"
                "CRITIQUE:\nRevision introduced a fundamental error.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Circular reasoning in revised step 3"
            ),
            # Iteration 2: generate (fresh start)
            _mock_response("Iteration 2 fresh derivation"),
            # Iteration 2: verify -> correct
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.92\n\n"
                "CRITIQUE:\nDerivation is sound.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the Dirac equation")

        assert result.solved
        assert result.iterations_used == 2
        # Only 1 revision was made (broke out after major_flaw)
        assert result.total_revisions == 1
        # Final solution is from iteration 2
        assert result.solution == "Iteration 2 fresh derivation"

        # 6 total API calls (not 8+ that would happen without break)
        assert mock_client.messages.create.call_count == 6


# ---------------------------------------------------------------------------
# 6. PhysicsAgent verbose output says "DERIVATION" not "SOLUTION"
# ---------------------------------------------------------------------------


class TestPhysicsVerboseLanguage:
    """Check that PhysicsAgent verbose logs use physics-appropriate language
    like 'derivation' rather than generic 'solution' or 'mathematical'."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verbose_uses_derivation_language(self, _mock_tools, capsys):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=True,
        )

        responses = [
            _mock_response("Some derivation text"),
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
                "CRITIQUE:\nGood.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        agent.solve("Derive the Klein-Gordon equation")

        captured = capsys.readouterr().out
        # Physics agent should say "PHYSICS DERIVATION AGENT" not "MATH AGENT"
        assert "PHYSICS DERIVATION AGENT" in captured
        assert "MATH AGENT" not in captured
        # Should refer to "derivation" in progress logs
        assert "derivation" in captured.lower()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verbose_failure_says_derivation(self, _mock_tools, capsys):
        """Admitted failure message should mention 'derivation' not 'solution'."""
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=True,
        )

        responses = [
            _mock_response("Wrong derivation"),
            _mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.1\n\n"
                "CRITIQUE:\nWrong.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Everything wrong"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        agent.solve("Derive something")

        captured = capsys.readouterr().out
        assert "verified derivation" in captured.lower()


# ---------------------------------------------------------------------------
# 7. PhysicsAgent preserves all MathAgent config
# ---------------------------------------------------------------------------


class TestPhysicsConfigPreservation:
    """Create PhysicsAgent with custom AgentConfig and verify the config
    propagates correctly through the solve loop."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_custom_config_propagates(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=7,
            max_revisions_per_cycle=4,
            confidence_threshold=0.85,
            enable_code_execution=False,
            verbose=False,
            temperature_generator=0.8,
            temperature_verifier=0.15,
            temperature_reviser=0.6,
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
        )

        agent = PhysicsAgent(config=config)

        # Verify config fields are preserved
        assert agent.config.max_iterations == 7
        assert agent.config.max_revisions_per_cycle == 4
        assert agent.config.confidence_threshold == 0.85
        assert agent.config.temperature_generator == 0.8
        assert agent.config.temperature_verifier == 0.15
        assert agent.config.temperature_reviser == 0.6
        assert agent.config.model == "claude-sonnet-4-20250514"
        assert agent.config.max_tokens == 8192

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_custom_threshold_affects_solve_loop(self, _mock_tools):
        """A lower threshold (0.85) should accept a 0.87 confidence that
        the default (0.90) would reject."""
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=1,
            confidence_threshold=0.85,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            _mock_response("Derivation text"),
            # CORRECT with confidence 0.87 — above 0.85 threshold
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.87\n\n"
                "CRITIQUE:\nLooks good.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the wave equation")

        # Should be accepted because 0.87 >= 0.85
        assert result.solved
        assert result.confidence == 0.87
        assert result.total_revisions == 0

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_custom_model_used_in_api_calls(self, _mock_tools):
        """Verify that the custom model name is passed to all API calls."""
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
            model="claude-sonnet-4-20250514",
        )

        responses = [
            _mock_response("Derivation"),
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
                "CRITIQUE:\nGood.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        agent.solve("Derive something")

        for api_call in mock_client.messages.create.call_args_list:
            assert api_call.kwargs.get("model") == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# 8. Zero revisions config
# ---------------------------------------------------------------------------


class TestZeroRevisionsConfig:
    """Set max_revisions_per_cycle=0. Verify no revise() calls are made even
    when the verdict is minor_issues."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_no_revisions_when_zero_max(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=2,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iteration 1: generate
            _mock_response("First derivation"),
            # Iteration 1: verify -> minor_issues (normally triggers revision)
            _mock_response(
                "VERDICT: minor_issues\nCONFIDENCE: 0.70\n\n"
                "CRITIQUE:\nSmall gap.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Missing step"
            ),
            # Iteration 2: generate (should go straight to new iteration)
            _mock_response("Second derivation"),
            # Iteration 2: verify -> correct
            _mock_response(
                "VERDICT: correct\nCONFIDENCE: 0.92\n\n"
                "CRITIQUE:\nNow sound.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\nNone"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the Boltzmann distribution")

        assert result.solved
        assert result.total_revisions == 0
        assert result.iterations_used == 2

        # Exactly 4 API calls: 2x (generate + verify), NO revise calls
        assert mock_client.messages.create.call_count == 4

        # Verify no reviser prompt was ever passed
        for api_call in mock_client.messages.create.call_args_list:
            system = api_call.kwargs.get("system", "")
            assert "reviser" not in system.lower()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_zero_revisions_with_major_flaw_still_no_revision(self, _mock_tools):
        """Even major_flaw should not trigger revision when max_revisions=0."""
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            _mock_response("Bad derivation"),
            _mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.10\n\n"
                "CRITIQUE:\nWrong.\n\n"
                "REASON: N/A\n\n"
                "ISSUES:\n- Totally wrong"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive something")

        assert not result.solved
        assert result.admitted_failure is True
        assert result.total_revisions == 0
        assert mock_client.messages.create.call_count == 2
