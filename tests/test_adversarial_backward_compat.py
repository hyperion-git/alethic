"""Adversarial backward-compatibility tests for Alethic.

Verifies that changes to subagents.py, cli.py, models.py, __init__.py,
and tools.py did NOT break any existing behavior. Each test targets a
specific contract that must remain stable across refactors.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig, Issue, Solution, Verdict, VerificationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str):
    """Create a mock Anthropic response object with a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# 1. MathAgent.solve() still uses math prompts (not physics prompts)
# ---------------------------------------------------------------------------


class TestMathAgentUsesDefaultMathPrompts:
    """MathAgent.solve() must pass GENERATOR_SYSTEM (math) — not
    PHYSICS_GENERATOR_SYSTEM — to the Claude API."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_system_prompt_is_math_not_physics(self, _mock_tools):
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=1,
            enable_code_execution=False,
            verbose=False,
        )

        solution_resp = _mock_response("Solution text")
        verify_resp = _mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
            "CRITIQUE:\nAll good.\n\nISSUES:\nNone"
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [solution_resp, verify_resp]

        agent = MathAgent(config=config)
        agent.client = mock_client
        agent.solve("Prove sqrt(2) is irrational")

        # The first API call is the generator.  Its system prompt must
        # contain the math identifier, never the physics one.
        first_call_kwargs = mock_client.messages.create.call_args_list[0]
        system_prompt = first_call_kwargs.kwargs.get(
            "system", first_call_kwargs[1].get("system", "")
        )

        assert "mathematical problem solver" in system_prompt.lower(), (
            "Generator system prompt should be the math prompt"
        )
        assert "physics" not in system_prompt.lower(), (
            "MathAgent must NOT use physics prompts"
        )


# ---------------------------------------------------------------------------
# 2. MathAgent.solve() calls generate() WITHOUT system_prompt/user_template
# ---------------------------------------------------------------------------


class TestMathAgentPromptKwargs:
    """MathAgent.solve() injects a system_prompt with tool guidance but does
    not inject a custom user_template — the default template is used."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    @patch("alethic.subagents.generate", wraps=None)
    def test_generate_called_with_tool_guidance_in_system_prompt(self, mock_generate, _mock_tools):
        from alethic.agent import MathAgent

        # Make mock_generate return a Solution and set up verify to approve
        mock_generate.return_value = Solution(
            problem="test", solution_text="answer", iteration=1
        )

        config = AgentConfig(
            max_iterations=1,
            enable_code_execution=False,
            verbose=False,
        )

        agent = MathAgent(config=config)
        agent.client = MagicMock()

        # Patch verify to return an acceptable result immediately
        with patch("alethic.agent.verify") as mock_verify:
            mock_verify.return_value = VerificationResult(
                verdict=Verdict.CORRECT,
                critique="OK",
                confidence=0.95,
            )
            with patch("alethic.agent.generate", mock_generate):
                agent.solve("test problem")

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args
        # MathAgent now builds a system_prompt with tool guidance appended
        system_prompt = call_kwargs.kwargs.get("system_prompt")
        assert system_prompt is not None, (
            "MathAgent should inject a system_prompt with tool guidance"
        )
        assert "mathematical problem solver" in system_prompt.lower(), (
            "system_prompt should start with the math GENERATOR_SYSTEM"
        )
        assert "SymPy" in system_prompt, (
            "system_prompt should include SymPy tool guidance"
        )
        assert "NumPy" in system_prompt, (
            "system_prompt should include NumPy tool guidance"
        )
        assert call_kwargs.kwargs.get("user_template") is None, (
            "MathAgent must NOT inject a custom user_template into generate()"
        )


# ---------------------------------------------------------------------------
# 3. generate() without kwargs uses GENERATOR_SYSTEM
# ---------------------------------------------------------------------------


class TestGenerateDefaultPrompt:
    """Calling generate() with no prompt kwargs must use GENERATOR_SYSTEM."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generate_uses_math_system_prompt(self, _mock_tools):
        from alethic.subagents import generate

        config = AgentConfig(enable_code_execution=False, verbose=False)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response("Solution")

        generate(mock_client, "test problem", config, iteration=1)

        call_kwargs = mock_client.messages.create.call_args
        system_prompt = call_kwargs.kwargs.get(
            "system", call_kwargs[1].get("system", "")
        )
        assert "mathematical problem solver" in system_prompt.lower()


# ---------------------------------------------------------------------------
# 4. verify() without kwargs uses VERIFIER_SYSTEM
# ---------------------------------------------------------------------------


class TestVerifyDefaultPrompt:
    """Calling verify() with no prompt kwargs must use VERIFIER_SYSTEM."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_verify_uses_math_verifier_prompt(self, _mock_tools):
        from alethic.subagents import verify

        config = AgentConfig(enable_code_execution=False, verbose=False)
        solution = Solution(problem="test", solution_text="answer", iteration=1)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            "VERDICT: correct\nCONFIDENCE: 0.9\n\nCRITIQUE:\nOK\n\nISSUES:\nNone"
        )

        verify(mock_client, "test problem", solution, config)

        call_kwargs = mock_client.messages.create.call_args
        system_prompt = call_kwargs.kwargs.get(
            "system", call_kwargs[1].get("system", "")
        )
        assert "mathematical proof verifier" in system_prompt.lower(), (
            f"Verifier system prompt should contain 'mathematical proof verifier', got: {system_prompt[:200]}"
        )


# ---------------------------------------------------------------------------
# 5. revise() without kwargs uses REVISER_SYSTEM
# ---------------------------------------------------------------------------


class TestReviseDefaultPrompt:
    """Calling revise() with no prompt kwargs must use REVISER_SYSTEM."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_revise_uses_math_reviser_prompt(self, _mock_tools):
        from alethic.subagents import revise

        config = AgentConfig(enable_code_execution=False, verbose=False)
        solution = Solution(problem="test", solution_text="answer", iteration=1)
        verification = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Needs work",
            confidence=0.6,
            issues=[Issue(text="Step 2 is wrong")],
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            "CHANGES MADE:\nFixed step 2\n\nREVISED SOLUTION:\nBetter answer"
        )

        revise(mock_client, "test problem", solution, verification, config, revision_number=1)

        call_kwargs = mock_client.messages.create.call_args
        system_prompt = call_kwargs.kwargs.get(
            "system", call_kwargs[1].get("system", "")
        )
        assert "mathematical solution reviser" in system_prompt.lower(), (
            f"Reviser system prompt should contain 'mathematical solution reviser', got: {system_prompt[:200]}"
        )


# ---------------------------------------------------------------------------
# 6. Old CLI invocations still work
# ---------------------------------------------------------------------------


class TestOldCLIInvocations:
    """Legacy CLI argument patterns must parse and execute without crashes."""

    @patch("alethic.agent.MathAgent")
    def test_iterations_no_code_flags(self, mock_agent):
        from alethic.cli import main

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.__str__ = lambda self: "OK"
        mock_agent.return_value.solve.return_value = mock_result

        exit_code = main(["--iterations", "3", "--no-code", "test problem"])
        assert exit_code == 0

        # Verify the agent was created with expected config
        call_kwargs = mock_agent.call_args
        config = call_kwargs.kwargs.get("config", call_kwargs[1].get("config"))
        assert config.max_iterations == 3
        assert config.enable_code_execution is False


# ---------------------------------------------------------------------------
# 7. CLI preset flag still works
# ---------------------------------------------------------------------------


class TestCLIPresetFlag:
    """--preset quick must produce AgentConfig with quick preset values."""

    @patch("alethic.agent.MathAgent")
    def test_preset_quick_via_cli(self, mock_agent):
        from alethic.cli import main

        mock_result = MagicMock()
        mock_result.solved = True
        mock_result.__str__ = lambda self: "OK"
        mock_agent.return_value.solve.return_value = mock_result

        exit_code = main(["--preset", "quick", "test"])
        assert exit_code == 0

        call_kwargs = mock_agent.call_args
        config = call_kwargs.kwargs.get("config", call_kwargs[1].get("config"))
        assert config.max_iterations == 2
        assert config.max_revisions_per_cycle == 1
        assert config.confidence_threshold == 0.85


# ---------------------------------------------------------------------------
# 8. All public exports still present
# ---------------------------------------------------------------------------


class TestPublicExports:
    """Every public name in __all__ must be importable from alethic."""

    def test_all_exports_importable(self):
        from alethic import (
            AgentConfig,
            AgentResult,
            MathAgent,
            PhysicsAgent,
            Revision,
            Solution,
            Verdict,
            VerificationResult,
        )

        # Verify they are the real classes, not None or stubs
        assert inspect.isclass(MathAgent)
        assert inspect.isclass(PhysicsAgent)
        assert inspect.isclass(AgentConfig)
        assert inspect.isclass(AgentResult)
        assert inspect.isclass(Solution)
        assert inspect.isclass(VerificationResult)
        assert inspect.isclass(Revision)
        assert issubclass(Verdict, type) or isinstance(Verdict, type(Verdict))

    def test_physics_agent_is_subclass_of_math_agent(self):
        from alethic import MathAgent, PhysicsAgent

        assert issubclass(PhysicsAgent, MathAgent)


# ---------------------------------------------------------------------------
# 9. Solution class unchanged behavior
# ---------------------------------------------------------------------------


class TestSolutionBackwardCompat:
    """Solution("problem", "text", 1) must still work as a positional
    constructor and produce the expected attributes."""

    def test_positional_construction(self):
        sol = Solution("my problem", "my solution text", 1)
        assert sol.problem == "my problem"
        assert sol.solution_text == "my solution text"
        assert sol.iteration == 1
        assert isinstance(sol.timestamp, float)

    def test_str_returns_solution_text(self):
        sol = Solution("p", "the answer is 42", 1)
        assert str(sol) == "the answer is 42"

    def test_timestamp_auto_populated(self):
        import time

        before = time.time()
        sol = Solution("p", "t", 1)
        after = time.time()
        assert before <= sol.timestamp <= after


# ---------------------------------------------------------------------------
# 10. AgentConfig presets unchanged
# ---------------------------------------------------------------------------


class TestAgentConfigPresetsUnchanged:
    """All 4 presets must return the exact values documented in CLAUDE.md."""

    def test_quick_preset(self):
        c = AgentConfig.from_preset("quick")
        assert c.max_iterations == 2
        assert c.max_revisions_per_cycle == 1
        assert c.confidence_threshold == 0.85
        assert c.extended_thinking is False
        assert c.max_tokens == 16384
        assert c.best_of_n == 1

    def test_default_preset(self):
        c = AgentConfig.from_preset("default")
        assert c.max_iterations == 5
        assert c.max_revisions_per_cycle == 3
        assert c.confidence_threshold == 0.90
        assert c.extended_thinking is False
        assert c.max_tokens == 16384
        assert c.best_of_n == 2

    def test_thorough_preset(self):
        c = AgentConfig.from_preset("thorough")
        assert c.max_iterations == 8
        assert c.max_revisions_per_cycle == 5
        assert c.confidence_threshold == 0.95
        assert c.extended_thinking is True
        assert c.thinking_budget == 15000
        assert c.max_tokens == 32768
        assert c.best_of_n == 3

    def test_extreme_preset(self):
        c = AgentConfig.from_preset("extreme")
        assert c.max_iterations == 12
        assert c.max_revisions_per_cycle == 5
        assert c.confidence_threshold == 0.97
        assert c.extended_thinking is True
        assert c.thinking_budget == 40000
        assert c.max_tokens == 65536
        assert c.best_of_n == 5

    def test_exactly_four_presets(self):
        assert set(AgentConfig.PRESETS.keys()) == {
            "quick", "default", "thorough", "extreme"
        }


# ---------------------------------------------------------------------------
# 11. _ALLOWED_MODULES is a superset of the old list
# ---------------------------------------------------------------------------


class TestAllowedModulesSuperset:
    """_ALLOWED_MODULES must contain every module from the original list."""

    def test_all_original_modules_present(self):
        from alethic.tools import _ALLOWED_MODULES

        original_modules = {
            "math", "cmath", "fractions", "decimal", "itertools",
            "functools", "collections", "operator", "random",
            "statistics", "re", "string", "textwrap", "numbers",
            "numpy", "sympy",
        }
        missing = original_modules - _ALLOWED_MODULES
        assert not missing, (
            f"These modules were in the original _ALLOWED_MODULES but are now missing: {missing}"
        )

    def test_allowed_modules_is_a_set(self):
        from alethic.tools import _ALLOWED_MODULES

        assert isinstance(_ALLOWED_MODULES, (set, frozenset))

    def test_scipy_and_mpmath_still_present(self):
        """scipy and mpmath were added later but must remain."""
        from alethic.tools import _ALLOWED_MODULES

        assert "scipy" in _ALLOWED_MODULES
        assert "mpmath" in _ALLOWED_MODULES
