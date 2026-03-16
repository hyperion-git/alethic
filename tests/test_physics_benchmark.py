"""Tests for physics benchmark format and domain dispatch."""
import json
from pathlib import Path

from alethic.eval.harness import load_benchmark

PHYSICS_BENCH = Path(__file__).parent.parent / "data" / "benchmarks" / "physics-sample.json"


class TestPhysicsBenchmarkFormat:
    def test_loads_without_error(self):
        bench = load_benchmark(str(PHYSICS_BENCH))
        assert "problems" in bench
        assert len(bench["problems"]) == 10

    def test_all_problems_are_physics_domain(self):
        bench = load_benchmark(str(PHYSICS_BENCH))
        for p in bench["problems"]:
            assert p["domain"] == "physics", f"{p['id']} should be physics domain"

    def test_has_false_claim(self):
        bench = load_benchmark(str(PHYSICS_BENCH))
        false_claims = [p for p in bench["problems"] if not p["expected_solvable"]]
        assert len(false_claims) >= 1, "Need at least one false-claim problem"

    def test_unique_ids(self):
        bench = load_benchmark(str(PHYSICS_BENCH))
        ids = [p["id"] for p in bench["problems"]]
        assert len(ids) == len(set(ids)), "Problem IDs must be unique"

    def test_required_fields(self):
        bench = load_benchmark(str(PHYSICS_BENCH))
        for p in bench["problems"]:
            assert "id" in p
            assert "domain" in p
            assert "problem" in p
            assert "expected_solvable" in p


class TestPhysicsDomainDispatch:
    def test_harness_dispatches_physics_agent(self, monkeypatch):
        """run_benchmark should use PhysicsAgent for physics domain problems."""
        from alethic.eval import harness as harness_module
        from alethic.models import AgentResult, Verdict

        dispatched_agents = []

        class MockAgent:
            def __init__(self, **kw):
                pass

            def solve(self, problem):
                return AgentResult(
                    problem=problem,
                    solution="mock",
                    verdict=Verdict.CORRECT,
                    confidence=0.95,
                    iterations_used=1,
                    total_revisions=0,
                    admitted_failure=False,
                    events=[],
                    failed_approaches=[],
                )

        class MockMathAgent(MockAgent):
            def __init__(self, **kw):
                super().__init__(**kw)
                dispatched_agents.append("math")

        class MockPhysicsAgent(MockAgent):
            def __init__(self, **kw):
                super().__init__(**kw)
                dispatched_agents.append("physics")

        monkeypatch.setattr(harness_module, "MathAgent", MockMathAgent)
        monkeypatch.setattr(harness_module, "PhysicsAgent", MockPhysicsAgent)

        import tempfile
        bench = {
            "name": "test",
            "version": "1.0",
            "problems": [{
                "id": "test-physics",
                "domain": "physics",
                "problem": "Derive F=ma",
                "expected_solvable": True,
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bench, f)
            f.flush()
            harness_module.run_benchmark(f.name, preset="quick")

        assert "physics" in dispatched_agents
