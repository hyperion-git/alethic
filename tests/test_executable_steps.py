"""Tests for executable intermediate steps (feature 1.6)."""

from __future__ import annotations


class TestGeneratorStepVerification:
    """Generator prompts must contain step-verification instructions."""

    def test_math_generator_system_contains_verify_step_instruction(self):
        from alethic.prompts import GENERATOR_SYSTEM

        text = GENERATOR_SYSTEM.lower()
        assert "verify_step" in text, (
            "GENERATOR_SYSTEM must instruct generator to write verify_step_N() functions"
        )

    def test_physics_generator_system_contains_verify_step_instruction(self):
        from alethic.physics_prompts import PHYSICS_GENERATOR_SYSTEM

        text = PHYSICS_GENERATOR_SYSTEM.lower()
        assert "verify_step" in text, (
            "PHYSICS_GENERATOR_SYSTEM must instruct generator to write verify_step_N() functions"
        )

    def test_math_generator_system_mentions_embedding_results(self):
        from alethic.prompts import GENERATOR_SYSTEM

        # Generator must embed results in the solution text (not just run them silently)
        assert "numerical check" in GENERATOR_SYSTEM.lower() or "embed" in GENERATOR_SYSTEM.lower() or "inline" in GENERATOR_SYSTEM.lower()

    def test_physics_generator_system_mentions_dimensional_check(self):
        from alethic.physics_prompts import PHYSICS_GENERATOR_SYSTEM

        text = PHYSICS_GENERATOR_SYSTEM.lower()
        assert "dimensional" in text or "dimension" in text, (
            "Physics generator must mention dimensional consistency checking in step functions"
        )


class TestVerifierStepAwareness:
    """Verifier prompts must acknowledge step-verified results."""

    def test_math_verifier_system_references_step_verification(self):
        from alethic.prompts import VERIFIER_SYSTEM

        text = VERIFIER_SYSTEM.lower()
        assert "verify_step" in text or "step-verified" in text or "numerical check" in text, (
            "VERIFIER_SYSTEM must tell verifier how to treat embedded step verification results"
        )

    def test_physics_verifier_system_references_step_verification(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM

        text = PHYSICS_VERIFIER_SYSTEM.lower()
        assert "verify_step" in text or "step-verified" in text or "numerical check" in text
