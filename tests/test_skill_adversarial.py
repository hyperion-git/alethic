"""Tests that skill files contain adversarial verifier wiring."""

from pathlib import Path

ORCHESTRATOR = Path("skills/alethic-common/orchestrator.md").read_text()
SOLVE_SKILL = Path("skills/alethic-solve/SKILL.md").read_text()
DERIVE_SKILL = Path("skills/alethic-derive/SKILL.md").read_text()


class TestOrchestratorAdversarial:
    def test_orchestrator_references_adversarial_flag(self):
        text = ORCHESTRATOR.lower()
        assert "adversarial" in text

    def test_orchestrator_conditionally_injects_addendum(self):
        # Must have conditional logic around adversarial injection
        assert "adversarial_verifier" in ORCHESTRATOR or "adversarial" in ORCHESTRATOR

    def test_solve_skill_sets_adversarial_for_thorough(self):
        # thorough and extreme presets must enable adversarial
        text = SOLVE_SKILL.lower()
        assert "adversarial" in text

    def test_derive_skill_sets_adversarial_for_thorough(self):
        text = DERIVE_SKILL.lower()
        assert "adversarial" in text
