"""Tests for the Alethic physics derivation agent.

Tests the physics-specific prompts, PhysicsAgent subclass, expanded sandbox
allowlist, and CLI derive subcommand with mocked API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig, Verdict
from alethic.physics_prompts import (
    BALANCED_PHYSICS_ADDENDUM,
    PHYSICS_GENERATOR_SYSTEM,
    PHYSICS_GENERATOR_USER,
    PHYSICS_REVISER_SYSTEM,
    PHYSICS_REVISER_USER,
    PHYSICS_VERIFIER_SYSTEM,
    PHYSICS_VERIFIER_USER,
)
from alethic.tools import _ALLOWED_MODULES  # noqa: F811

# ── Physics prompt content tests ─────────────────────────────────────


class TestPhysicsPrompts:
    def test_generator_role_identity(self):
        assert "theoretical physics" in PHYSICS_GENERATOR_SYSTEM.lower()
        assert "derivation" in PHYSICS_GENERATOR_SYSTEM.lower()

    def test_generator_has_physics_strategies(self):
        strategies = [
            "Lagrangian",
            "Hamiltonian",
            "Perturbation theory",
            "Separation of variables",
            "Noether",
            "Green's functions",
            "WKB",
            "Adiabatic",
            "Dimensional analysis",
            "Feynman diagrams",
            "Renormalization",
            "Path integral",
        ]
        for s in strategies:
            assert s in PHYSICS_GENERATOR_SYSTEM, f"Missing strategy: {s}"

    def test_generator_has_conclusion_marker(self):
        assert "CONCLUSION:" in PHYSICS_GENERATOR_SYSTEM

    def test_generator_user_template(self):
        msg = PHYSICS_GENERATOR_USER.format(problem="Test problem")
        assert "Test problem" in msg
        assert "Derive" in PHYSICS_GENERATOR_USER

    def test_verifier_role_identity(self):
        assert "physics derivation verifier" in PHYSICS_VERIFIER_SYSTEM.lower()

    def test_verifier_has_physics_errors(self):
        physics_errors = [
            "dimensional inconsistency",
            "unphysical limiting behavior",
            "conservation laws",
            "sign convention",
            "unjustified approximation",
            "boundary condition",
        ]
        text_lower = PHYSICS_VERIFIER_SYSTEM.lower()
        for err in physics_errors:
            assert err in text_lower, f"Missing physics error: {err}"

    def test_verifier_has_all_verdicts(self):
        for verdict in ["correct", "minor_issues", "major_flaw", "unsolved"]:
            assert verdict in PHYSICS_VERIFIER_SYSTEM

    def test_verifier_correct_definition(self):
        assert "physically and mathematically sound" in PHYSICS_VERIFIER_SYSTEM.lower()

    def test_verifier_is_decoupled(self):
        assert "You are independent" in PHYSICS_VERIFIER_SYSTEM
        assert "VERDICT:" in PHYSICS_VERIFIER_SYSTEM

    def test_verifier_user_template(self):
        msg = PHYSICS_VERIFIER_USER.format(problem="Test", solution="Answer")
        assert "Test" in msg
        assert "Answer" in msg

    def test_reviser_role_identity(self):
        assert "physics derivation reviser" in PHYSICS_REVISER_SYSTEM.lower()

    def test_reviser_references_derivation_approach(self):
        assert "derivation approach" in PHYSICS_REVISER_SYSTEM.lower()

    def test_reviser_has_standard_markers(self):
        assert "CHANGES MADE:" in PHYSICS_REVISER_SYSTEM
        assert "REVISED SOLUTION:" in PHYSICS_REVISER_SYSTEM

    def test_reviser_user_template(self):
        msg = PHYSICS_REVISER_USER.format(
            problem="P", solution="S", critique="C", issues="I"
        )
        assert "P" in msg
        assert "PREVIOUS DERIVATION:" in PHYSICS_REVISER_USER

    def test_balanced_addendum_physics(self):
        assert "dimensional" in BALANCED_PHYSICS_ADDENDUM.lower()
        assert "limiting case" in BALANCED_PHYSICS_ADDENDUM.lower()


# ── Expanded sandbox tests ───────────────────────────────────────────


class TestExpandedSandbox:
    def test_scipy_in_allowed_modules(self):
        assert "scipy" in _ALLOWED_MODULES

    def test_mpmath_in_allowed_modules(self):
        assert "mpmath" in _ALLOWED_MODULES

    def test_numpy_still_allowed(self):
        assert "numpy" in _ALLOWED_MODULES

    def test_sympy_still_allowed(self):
        assert "sympy" in _ALLOWED_MODULES


# ── PhysicsAgent instantiation tests ─────────────────────────────────


class TestPhysicsAgent:
    def test_physics_agent_instantiation(self):
        from alethic.physics_agent import PhysicsAgent

        agent = PhysicsAgent(api_key="test-key")
        assert agent.config is not None
        assert isinstance(agent.config, AgentConfig)

    def test_physics_agent_is_subclass(self):
        from alethic.agent import MathAgent
        from alethic.physics_agent import PhysicsAgent

        assert issubclass(PhysicsAgent, MathAgent)

    def test_physics_agent_with_preset(self):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig.from_preset("quick")
        agent = PhysicsAgent(config=config, api_key="test-key")
        assert agent.config.max_iterations == 2

    def test_physics_agent_exported(self):
        from alethic import PhysicsAgent

        assert PhysicsAgent is not None

    def test_physics_agent_prompt_set(self):
        """PhysicsAgent._prompt_set() should return all 7 physics prompt keys."""
        from alethic.physics_agent import PhysicsAgent

        agent = PhysicsAgent(api_key="test-key")
        prompts = agent._prompt_set()
        expected_keys = {
            "generator_system",
            "generator_user",
            "balanced_addendum",
            "verifier_system",
            "verifier_user",
            "reviser_system",
            "reviser_user",
        }
        assert set(prompts.keys()) == expected_keys
        # Verify prompts contain physics-specific content
        assert "theoretical physics" in prompts["generator_system"].lower()
        assert "physics derivation verifier" in prompts["verifier_system"].lower()

    def test_physics_agent_inherits_solve(self):
        """PhysicsAgent should not override solve() — it inherits MathAgent's."""
        from alethic.agent import MathAgent
        from alethic.physics_agent import PhysicsAgent

        assert "solve" not in PhysicsAgent.__dict__
        assert PhysicsAgent.solve is MathAgent.solve

    def test_physics_agent_log_header(self):
        from alethic.physics_agent import PhysicsAgent

        agent = PhysicsAgent(api_key="test-key")
        assert agent._log_header() == "ALETHIC PHYSICS DERIVATION AGENT"

    def test_math_agent_log_header(self):
        from alethic.agent import MathAgent

        agent = MathAgent(api_key="test-key")
        assert agent._log_header() == "ALETHIC MATH AGENT"

    def test_math_agent_prompt_set_empty(self):
        from alethic.agent import MathAgent

        agent = MathAgent(api_key="test-key")
        assert agent._prompt_set() == {}


# ── PhysicsAgent integration test with mocked API ────────────────────


class TestPhysicsAgentIntegration:
    def _mock_response(self, text: str):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = text
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        return mock_resp

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_physics_solve_correct_on_first_try(self, mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=3,
            enable_code_execution=False,
            verbose=False,
        )

        solution_response = self._mock_response(
            "E_n = hbar*omega*(n + 1/2)\n\nCONCLUSION: E_n = hbar*omega*(n + 1/2)"
        )
        verification_response = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
            "CRITIQUE:\nDerivation is sound.\n\nISSUES:\nNone"
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            solution_response,
            verification_response,
        ]

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive the energy levels of the quantum harmonic oscillator")

        assert result.solved
        assert result.verdict == Verdict.CORRECT
        assert result.iterations_used == 1
        assert result.confidence == 0.95

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_physics_solve_uses_physics_prompts(self, mock_tools):
        """Verify that PhysicsAgent passes physics prompts to generate/verify/revise."""
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            enable_code_execution=False,
            verbose=False,
        )

        solution_response = self._mock_response("E = mc^2\n\nCONCLUSION: E = mc^2")
        verification_response = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.96\n\n"
            "CRITIQUE:\nCorrect.\n\nISSUES:\nNone"
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            solution_response,
            verification_response,
        ]

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive E=mc^2 from special relativity")
        assert result.solved

        # Check the system prompt passed to the Generator
        gen_call = mock_client.messages.create.call_args_list[0]
        gen_system = gen_call.kwargs.get("system", gen_call[1].get("system", ""))
        assert "theoretical physics" in gen_system.lower()

        # Check the system prompt passed to the Verifier
        ver_call = mock_client.messages.create.call_args_list[1]
        ver_system = ver_call.kwargs.get("system", ver_call[1].get("system", ""))
        assert "physics derivation verifier" in ver_system.lower()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_physics_admit_failure(self, mock_tools):
        from alethic.physics_agent import PhysicsAgent

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            self._mock_response("Wrong derivation"),
            self._mock_response(
                "VERDICT: major_flaw\nCONFIDENCE: 0.1\n\n"
                "CRITIQUE:\nFundamentally wrong.\n\nISSUES:\n- Wrong approach"
            ),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses

        agent = PhysicsAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Derive something impossible")
        assert not result.solved
        assert result.admitted_failure


# ── Prompt injection via subagent kwargs tests ───────────────────────


class TestPromptKwargs:
    """Test that generate/verify/revise accept and use custom prompt kwargs."""

    def _mock_response(self, text: str):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = text
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        return mock_resp

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_custom_prompts(self, mock_tools):
        from alethic.subagents import generate

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response("Solution text")

        config = AgentConfig(enable_code_execution=False, verbose=False)

        result = generate(
            mock_client,
            problem="test",
            config=config,
            iteration=1,
            balanced=True,
            system_prompt="Custom system",
            user_template="Custom user: {problem}",
            balanced_addendum="\nCustom addendum",
        )

        call_kwargs = mock_client.messages.create.call_args
        assert "Custom system" in call_kwargs.kwargs.get("system", call_kwargs[1].get("system", ""))
        assert result.solution_text == "Solution text"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_custom_prompts(self, mock_tools):
        from alethic.models import Solution
        from alethic.subagents import verify

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        )

        config = AgentConfig(enable_code_execution=False, verbose=False)
        sol = Solution(problem="test", solution_text="answer", iteration=1)

        result = verify(
            mock_client,
            problem="test",
            solution=sol,
            config=config,
            system_prompt="Custom verifier",
            user_template="Custom verify: {problem}\n{solution}",
        )

        call_kwargs = mock_client.messages.create.call_args
        sys_prompt = call_kwargs.kwargs.get("system", call_kwargs[1].get("system", ""))
        assert sys_prompt == "Custom verifier"
        assert result.verdict == Verdict.CORRECT

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_custom_prompts(self, mock_tools):
        from alethic.models import Solution, VerificationResult
        from alethic.subagents import revise

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(
            "CHANGES MADE:\nFixed\n\nREVISED SOLUTION:\nBetter answer"
        )

        config = AgentConfig(enable_code_execution=False, verbose=False)
        sol = Solution(problem="test", solution_text="old answer", iteration=1)
        ver = VerificationResult(
            verdict=Verdict.MINOR_ISSUES, critique="Fix it", confidence=0.7, issues=["Bug"]
        )

        result = revise(
            mock_client,
            problem="test",
            solution=sol,
            verification=ver,
            config=config,
            revision_number=1,
            system_prompt="Custom reviser",
            user_template="Custom revise: {problem}\n{solution}\n{critique}\n{issues}",
        )

        call_kwargs = mock_client.messages.create.call_args
        sys_prompt = call_kwargs.kwargs.get("system", call_kwargs[1].get("system", ""))
        assert sys_prompt == "Custom reviser"
        assert result.solution_text is not None


# ── CLI derive subcommand tests ──────────────────────────────────────


class TestCLIDerive:
    def test_detect_derive_subcommand(self):
        from alethic.cli import _detect_subcommand

        cmd, remaining = _detect_subcommand(["derive", "Derive E=mc^2"])
        assert cmd == "derive"
        assert remaining == ["Derive E=mc^2"]

    def test_detect_solve_subcommand(self):
        from alethic.cli import _detect_subcommand

        cmd, remaining = _detect_subcommand(["solve", "Prove sqrt(2) is irrational"])
        assert cmd == "solve"
        assert remaining == ["Prove sqrt(2) is irrational"]

    def test_detect_no_subcommand(self):
        from alethic.cli import _detect_subcommand

        cmd, remaining = _detect_subcommand(["Prove sqrt(2) is irrational"])
        assert cmd is None
        assert remaining == ["Prove sqrt(2) is irrational"]

    def test_detect_derive_with_flags(self):
        from alethic.cli import _detect_subcommand

        cmd, remaining = _detect_subcommand(["derive", "--preset", "quick", "test"])
        assert cmd == "derive"
        assert remaining == ["--preset", "quick", "test"]

    def test_backward_compat_with_flags(self):
        from alethic.cli import _detect_subcommand

        cmd, remaining = _detect_subcommand(["--preset", "quick", "test problem"])
        assert cmd is None
        assert remaining == ["--preset", "quick", "test problem"]

    def test_derive_args_parse_after_detection(self):
        from alethic.cli import _detect_subcommand, build_parser

        cmd, remaining = _detect_subcommand(["derive", "--iterations", "2", "test"])
        assert cmd == "derive"

        parser = build_parser()
        args = parser.parse_args(remaining)
        assert args.iterations == 2
        assert args.problem == "test"
