"""Tests for best-of-N sampling feature.

Covers configuration, CLI integration, backward compatibility, candidate
selection, revision, parallel execution, physics agent inheritance, and
history metadata.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alethic.models import (
    AgentConfig,
    AgentResult,
    EventType,
    Verdict,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_response(text: str):
    """Create a mock Anthropic response object with a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


CORRECT_HIGH = "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nPerfect.\n\nISSUES:\nNone"
CORRECT_MED = "VERDICT: correct\nCONFIDENCE: 0.88\n\nCRITIQUE:\nGood but unsure.\n\nISSUES:\nNone"
MINOR_ISSUES = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.6\n\nCRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
)
MAJOR_FLAW = "VERDICT: major_flaw\nCONFIDENCE: 0.2\n\nCRITIQUE:\nWrong.\n\nISSUES:\n- Logic error"


# ── Config tests ─────────────────────────────────────────────────────


class TestBestOfNConfig:
    def test_default_is_one(self):
        config = AgentConfig()
        assert config.best_of_n == 1

    def test_preset_values(self):
        assert AgentConfig.from_preset("quick").best_of_n == 1
        assert AgentConfig.from_preset("default").best_of_n == 2
        assert AgentConfig.from_preset("thorough").best_of_n == 3
        assert AgentConfig.from_preset("extreme").best_of_n == 5

    def test_from_preset_with_override(self):
        config = AgentConfig.from_preset("quick", best_of_n=4)
        assert config.best_of_n == 4
        assert config.max_iterations == 2  # from preset

    def test_validation_rejects_zero(self):
        with pytest.raises(ValueError, match="best_of_n must be >= 1"):
            AgentConfig(best_of_n=0)

    def test_validation_rejects_negative(self):
        with pytest.raises(ValueError, match="best_of_n must be >= 1"):
            AgentConfig(best_of_n=-1)

    def test_explicit_one_is_valid(self):
        config = AgentConfig(best_of_n=1)
        assert config.best_of_n == 1


# ── CLI tests ────────────────────────────────────────────────────────


class TestBestOfNCLI:
    def test_best_of_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--best-of", "3", "test problem"])
        config = _build_config(args)
        assert config.best_of_n == 3

    def test_best_of_short_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["-B", "5", "test problem"])
        config = _build_config(args)
        assert config.best_of_n == 5

    def test_preset_plus_override(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "thorough", "--best-of", "7", "test"])
        config = _build_config(args)
        assert config.best_of_n == 7  # explicit override
        assert config.confidence_threshold == 0.95  # from preset

    def test_preset_default_without_flag(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["--preset", "default", "test"])
        config = _build_config(args)
        assert config.best_of_n == 2  # from preset

    def test_no_flag_no_preset_is_one(self):
        from alethic.cli import _build_config, build_parser

        parser = build_parser()
        args = parser.parse_args(["test"])
        config = _build_config(args)
        assert config.best_of_n == 1  # dataclass default


# ── Backward compatibility tests ─────────────────────────────────────


class TestBestOfNBackwardCompat:
    """N=1 should produce identical API call count and result structure."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n1_same_api_calls(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Solution text"),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.candidates_per_iteration == 1
        # Exactly 2 API calls: 1 generate + 1 verify
        assert mock_client.messages.create.call_count == 2

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n1_result_structure(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Answer"),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test")

        assert isinstance(result, AgentResult)
        assert result.candidates_per_iteration == 1
        # candidates_per_iteration=1 should NOT appear in __str__
        result_str = str(result)
        assert "Candidates per iteration" not in result_str


# ── Candidate selection tests ────────────────────────────────────────


class TestBestOfNSelectsBest:
    """N=3 should produce 6 API calls (3 gen + 3 ver) and select the
    highest-confidence candidate."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_selects_highest_confidence(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        # 3 generate responses + 3 verify responses
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response("Candidate C"),
            # Verifications — B gets highest confidence
            _mock_response(MINOR_ISSUES),  # candidate A → 0.6
            _mock_response(CORRECT_HIGH),  # candidate B → 0.95
            _mock_response(CORRECT_MED),  # candidate C → 0.88
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.confidence == 0.95
        assert result.candidates_per_iteration == 3
        # 3 generate + 3 verify = 6 API calls
        assert mock_client.messages.create.call_count == 6


# ── Revision tests ───────────────────────────────────────────────────


class TestBestOfNWithRevision:
    """N=2, best candidate needs revision: 2 gen + 2 ver + 1 rev + 1 re-ver."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revises_best_candidate(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=1,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            # Generate 2 candidates
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            # Verify both — best is minor_issues at 0.6
            _mock_response(MAJOR_FLAW),  # candidate A → 0.2
            _mock_response(MINOR_ISSUES),  # candidate B → 0.6
            # Revise best (candidate B)
            _mock_response("CHANGES MADE:\nFixed.\n\nREVISED SOLUTION:\nFixed B"),
            # Re-verify revision → correct
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        assert result.total_revisions == 1
        assert result.candidates_per_iteration == 2
        # 2 gen + 2 ver + 1 rev + 1 re-ver = 6
        assert mock_client.messages.create.call_count == 6


# ── Parallel execution tests ────────────────────────────────────────


class TestBestOfNParallel:
    """N>1 should use ThreadPoolExecutor; N=1 should not."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n3_uses_thread_pool(self, _mock_tools):
        from concurrent.futures import ThreadPoolExecutor as RealTPE

        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("A"),
            _mock_response("B"),
            _mock_response("C"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
            _mock_response(MINOR_ISSUES),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        with patch("alethic.agent.ThreadPoolExecutor", wraps=RealTPE) as mock_pool_cls:
            result = agent.solve("test")

        # ThreadPoolExecutor should have been instantiated with max_workers=3
        mock_pool_cls.assert_called_once_with(max_workers=3)
        assert result.solved
        # 3 gen + 3 ver = 6 API calls
        assert mock_client.messages.create.call_count == 6

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n1_does_not_use_thread_pool(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Solution"),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        with patch("alethic.agent.ThreadPoolExecutor") as mock_pool_cls:
            result = agent.solve("test")

        # ThreadPoolExecutor should never be instantiated for N=1
        mock_pool_cls.assert_not_called()
        assert result.solved


# ── PhysicsAgent inheritance tests ───────────────────────────────────


class TestBestOfNPhysicsAgent:
    """PhysicsAgent should inherit best-of-N and use physics prompts."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_physics_agent_best_of_n(self, _mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Derivation A"),
            _mock_response("Derivation B"),
            _mock_response(MINOR_ISSUES),
            _mock_response(CORRECT_HIGH),
        ]

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive E=mc^2")

        assert result.solved
        assert result.candidates_per_iteration == 2

        # Verify physics prompts were used (check first API call's system prompt)
        first_call = mock_client.messages.create.call_args_list[0]
        system_prompt = first_call.kwargs.get("system", first_call[1].get("system", ""))
        assert "physics" in system_prompt.lower() or "derivation" in system_prompt.lower()


# ── History metadata tests ───────────────────────────────────────────


class TestBestOfNHistory:
    """History entries should contain 'candidate' field."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_history_has_candidate_field(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("A"),
            _mock_response("B"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_ISSUES),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test")

        # Check generate events have candidate field
        gen_entries = [e for e in result.events if e.type == EventType.GENERATE]
        assert len(gen_entries) == 2
        assert gen_entries[0].data["candidate"] == 1
        assert gen_entries[1].data["candidate"] == 2

        # Check verify events have candidate field
        ver_entries = [e for e in result.events if e.type == EventType.VERIFY]
        assert len(ver_entries) == 2
        for entry in ver_entries:
            assert "candidate" in entry.data

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n1_history_has_candidate_one(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            best_of_n=1,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("Solution"),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test")

        gen_entries = [e for e in result.events if e.type == EventType.GENERATE]
        assert len(gen_entries) == 1
        assert gen_entries[0].data["candidate"] == 1


# ── AgentResult display tests ────────────────────────────────────────


class TestAgentResultDisplay:
    def test_str_shows_candidates_when_gt_1(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            candidates_per_iteration=3,
        )
        assert "Candidates per iteration: 3" in str(result)

    def test_str_hides_candidates_when_1(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            candidates_per_iteration=1,
        )
        assert "Candidates per iteration" not in str(result)


# ── Variant-B client tests ──────────────────────────────────────────


class TestVariantBClient:
    """Variant-B model diversity: client reuse, separate client creation, alternation."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_same_model_reuses_client(self, _mock_tools):
        """When variant_b model matches primary, same client is reused (no new Anthropic())."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # same as primary
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            # Generate 2 candidates
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            # Verify both
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client
        agent._api_key = "test-key"

        with patch("alethic.agent.get_client") as mock_anthropic_cls:
            result = agent.solve("test problem")

        # No new Anthropic() client should have been created
        mock_anthropic_cls.assert_not_called()
        assert result.solved
        # All generate + verify calls went through the same mock_client
        assert mock_client.messages.create.call_count == 4

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_different_model_creates_new_client(self, _mock_tools):
        """When variant_b model differs, a separate Anthropic client is created."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},  # different from primary
        )

        primary_client = MagicMock(name="primary_client")
        variant_client = MagicMock(name="variant_client")

        # Primary handles candidate 0 (even) gen + both verifications
        # Variant handles candidate 1 (odd) gen
        primary_client.messages.create.side_effect = [
            _mock_response("Candidate A (primary)"),
            # Verify both candidates (verification always uses primary client)
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
        ]
        variant_client.messages.create.side_effect = [
            _mock_response("Candidate B (variant)"),
        ]

        agent = MathAgent(config=config)
        agent.client = primary_client
        agent._api_key = "test-key"

        with patch("alethic.agent.get_client", return_value=variant_client) as mock_cls:
            result = agent.solve("test problem")

        # A new Anthropic client should have been created for the variant
        mock_cls.assert_called_once_with(api_key="test-key", config=config.build_variant_b_config())
        # Variant client should have been called for at least one generate
        assert variant_client.messages.create.call_count >= 1
        assert result.solved

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_odd_even_alternation(self, _mock_tools):
        """With best_of_n=4 and variant_b (same model), all 4 candidates complete."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=4,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # same model to simplify
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            # Generate 4 candidates
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response("Candidate C"),
            _mock_response("Candidate D"),
            # Verify 4 candidates
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
            _mock_response(MINOR_ISSUES),
            _mock_response(MAJOR_FLAW),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client
        agent._api_key = "test-key"

        result = agent.solve("test problem")

        assert result.solved
        assert result.candidates_per_iteration == 4
        # 4 gen + 4 ver = 8 API calls
        assert mock_client.messages.create.call_count == 8


# ── Large N with partial failures tests ─────────────────────────────


class TestLargeN:
    """Large best-of-N with partial generation failures."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n5_with_partial_failures(self, _mock_tools):
        """N=5 where 2 generate calls fail; remaining 3 produce a result."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=5,
            enable_code_execution=False,
            verbose=False,
        )

        # Track call count to fail on specific generate calls.
        # _generate_candidates uses ThreadPoolExecutor, so we can't rely on
        # strict ordering. Instead, we use a counter: the first 5 calls are
        # generates; calls 2 and 4 (1-indexed) raise RuntimeError.
        # The remaining calls are verify responses for the 3 survivors.
        call_counter = {"n": 0}

        def side_effect(*args, **kwargs):
            call_counter["n"] += 1
            n = call_counter["n"]
            if n <= 5:
                # Generate phase: fail calls 2 and 4
                if n in (2, 4):
                    raise RuntimeError(f"Simulated failure on generate call {n}")
                return _mock_response(f"Candidate from gen call {n}")
            else:
                # Verify phase: all succeed
                return _mock_response(CORRECT_HIGH)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        # 5 generate attempts (2 failed) + 3 verify calls = 8 total
        assert mock_client.messages.create.call_count == 8
