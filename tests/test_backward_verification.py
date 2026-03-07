"""Tests for backward verification (feature 1.3)."""

from __future__ import annotations


class TestBackwardVerificationInPrompts:
    """Verifier prompts must contain backward verification instructions."""

    def test_math_verifier_system_contains_backward_check(self):
        from alethic.prompts import VERIFIER_SYSTEM

        text = VERIFIER_SYSTEM.lower()
        assert "backward" in text or "reconstruct" in text, (
            "VERIFIER_SYSTEM must contain backward verification instructions"
        )

    def test_physics_verifier_system_contains_backward_check(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM

        text = PHYSICS_VERIFIER_SYSTEM.lower()
        assert "backward" in text or "reconstruct" in text, (
            "PHYSICS_VERIFIER_SYSTEM must contain backward verification instructions"
        )

    def test_math_verifier_backward_check_failure_label(self):
        from alethic.prompts import VERIFIER_SYSTEM

        # Must instruct verifier to use this specific label for flagging
        assert "backward_check_failure" in VERIFIER_SYSTEM

    def test_physics_verifier_backward_check_failure_label(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM

        assert "backward_check_failure" in PHYSICS_VERIFIER_SYSTEM
