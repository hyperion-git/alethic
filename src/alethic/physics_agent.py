"""Physics derivation agent — thin subclass of MathAgent.

Swaps in physics-specific prompt templates while reusing the entire
Generate → Verify → Revise orchestrator loop.
"""

from __future__ import annotations

from alethic.agent import MathAgent
from alethic.physics_prompts import (
    BALANCED_PHYSICS_ADDENDUM,
    PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM,
    PHYSICS_DISPROOF_STRATEGY_ADDENDUM,
    PHYSICS_GENERATOR_SYSTEM,
    PHYSICS_GENERATOR_USER,
    PHYSICS_REVISER_SYSTEM,
    PHYSICS_REVISER_USER,
    PHYSICS_SATURATION_AWARENESS_ADDENDUM,
    PHYSICS_STRATEGY_RESET_ADDENDUM,
    PHYSICS_SURVEY_GENERATOR_GUIDANCE,
    PHYSICS_SURVEY_VERIFIER_GUIDANCE,
    PHYSICS_TOOL_GUIDANCE,
    PHYSICS_VERIFIER_SYSTEM,
    PHYSICS_VERIFIER_USER,
)


class PhysicsAgent(MathAgent):
    """Alethic physics derivation agent with configurable model backends.

    Thin subclass of MathAgent that injects physics-specific prompt templates
    into the Generate → Verify → Revise loop. All orchestrator logic is
    inherited from MathAgent — only the prompts differ.

    Usage:
        agent = PhysicsAgent()  # uses ANTHROPIC_API_KEY
        result = agent.solve("Derive the energy spectrum of the quantum harmonic oscillator.")
        print(result)
    """

    def _prompt_set(self) -> dict[str, str]:
        return {
            "generator_system": PHYSICS_GENERATOR_SYSTEM,
            "generator_user": PHYSICS_GENERATOR_USER,
            "balanced_addendum": BALANCED_PHYSICS_ADDENDUM,
            "verifier_system": PHYSICS_VERIFIER_SYSTEM,
            "verifier_user": PHYSICS_VERIFIER_USER,
            "reviser_system": PHYSICS_REVISER_SYSTEM,
            "reviser_user": PHYSICS_REVISER_USER,
        }

    def _domain(self) -> str:
        return "physics"

    def _get_tool_guidance_map(self) -> dict:
        return PHYSICS_TOOL_GUIDANCE

    def _reset_addendum(self) -> str:
        return PHYSICS_STRATEGY_RESET_ADDENDUM

    def _disproof_addendum(self) -> str:
        return PHYSICS_DISPROOF_STRATEGY_ADDENDUM

    def _saturation_addendum(self) -> str:
        return PHYSICS_SATURATION_AWARENESS_ADDENDUM

    def _survey_guidance(self, role: str) -> str:
        if role == "generator":
            return PHYSICS_SURVEY_GENERATOR_GUIDANCE
        if role == "verifier":
            return PHYSICS_SURVEY_VERIFIER_GUIDANCE
        return ""

    def _adversarial_addendum(self) -> str | None:
        if not self.config.adversarial_self_correction:
            return None
        return PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM

    def _log_header(self) -> str:
        return "ALETHIC PHYSICS DERIVATION AGENT"
