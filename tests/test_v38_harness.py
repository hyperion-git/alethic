"""Tests for run_benchmark search_mode pass-through (v3.8 integration)."""

from __future__ import annotations

import json
from unittest import mock

from alethic.models import AgentEvent, AgentResult, EventType, Verdict


def _bench_file(tmp_path):
    bench = {
        "name": "mini", "version": "1",
        "problems": [
            {"id": "p1", "domain": "math", "problem": "1+1?", "expected_solvable": True},
        ],
    }
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(bench))
    return str(path)


def _result_with_tree_events() -> AgentResult:
    events = [
        AgentEvent(type=EventType.BRIDGE_GENERATED, iteration=0, data={}),
        AgentEvent(type=EventType.GAP_FILLED, iteration=0, data={"gap_id": 2}),
        AgentEvent(type=EventType.GAP_FILLED, iteration=0, data={"gap_id": 3}),
        AgentEvent(type=EventType.ACCEPT, iteration=0, data={}),
    ]
    return AgentResult(
        problem="1+1?", solution="2", verdict=Verdict.CORRECT, confidence=0.99,
        iterations_used=1, total_revisions=0, admitted_failure=False, events=events,
    )


class TestRunBenchmarkSearchMode:
    def test_tree_mode_configures_agents_and_reports(self, tmp_path):
        from alethic.eval.harness import run_benchmark

        with mock.patch("alethic.eval.harness.MathAgent") as agent_cls:
            agent_cls.return_value.solve.return_value = _result_with_tree_events()
            report = run_benchmark(
                _bench_file(tmp_path), api_key="k", preset="default",
                search_mode="tree",
            )
        config = agent_cls.call_args.kwargs["config"]
        assert config.search_mode == "tree"
        assert config.search is not None
        assert report["search_mode"] == "tree"
        assert report["results"][0]["bridges_used"] == 1
        assert report["results"][0]["gaps_filled"] == 2

    def test_flat_mode_reports_null_tree_metrics(self, tmp_path):
        from alethic.eval.harness import run_benchmark

        with mock.patch("alethic.eval.harness.MathAgent") as agent_cls:
            agent_cls.return_value.solve.return_value = _result_with_tree_events()
            report = run_benchmark(_bench_file(tmp_path), api_key="k")
        config = agent_cls.call_args.kwargs["config"]
        assert config.search_mode == "flat"
        assert report["search_mode"] == "flat"
        assert report["results"][0]["bridges_used"] is None
        assert report["results"][0]["gaps_filled"] is None
