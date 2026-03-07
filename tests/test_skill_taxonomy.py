"""Tests that the skill orchestrator contains error taxonomy classification."""

from pathlib import Path

ORCHESTRATOR = Path("skills/alethic-common/orchestrator.md").read_text()


class TestOrchestratorTaxonomy:
    def test_orchestrator_mentions_algebra_category(self):
        assert "algebra" in ORCHESTRATOR.lower()

    def test_orchestrator_mentions_citation_category(self):
        assert "citation" in ORCHESTRATOR.lower()

    def test_orchestrator_mentions_taxonomy(self):
        assert (
            "taxonomy" in ORCHESTRATOR.lower()
            or "error category" in ORCHESTRATOR.lower()
            or "error_category" in ORCHESTRATOR.lower()
        )

    def test_orchestrator_injects_addendum_to_reviser(self):
        # After reading verification, orchestrator must pass a strategy hint to the reviser
        assert (
            "strategy" in ORCHESTRATOR.lower()
            or "addendum" in ORCHESTRATOR.lower()
            or "revision_strategy" in ORCHESTRATOR.lower()
        )
