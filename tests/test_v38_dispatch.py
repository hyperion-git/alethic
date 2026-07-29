"""Tests for the v3.8 tree-mode dispatch in MathAgent/PhysicsAgent."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from alethic.agent import MathAgent
from alethic.exceptions import CheckpointError
from alethic.models import AgentConfig, AgentResult, SearchConfig, Verdict
from alethic.physics_agent import PhysicsAgent


def _tree_result(**overrides) -> AgentResult:
    kwargs = dict(
        problem="p", solution="s", verdict=Verdict.CORRECT, confidence=0.95,
        iterations_used=1, total_revisions=0, admitted_failure=False,
    )
    kwargs.update(overrides)
    return AgentResult(**kwargs)


@pytest.fixture
def tree_config():
    return AgentConfig(search_mode="tree", verbose=False)


class TestTreeDispatch:
    def test_tree_mode_routes_to_search_solve(self, tree_config):
        agent = MathAgent(config=tree_config, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()) as spy:
            result = agent.solve("p", create_session=False)
        assert result.solved
        spy.assert_called_once()
        assert spy.call_args.kwargs["domain"] == "math"
        assert spy.call_args.kwargs["config"] is tree_config
        assert spy.call_args.kwargs["client"] is agent.client
        assert spy.call_args.kwargs["ledger"] is not None

    def test_default_search_config_used_when_none(self, tree_config):
        agent = MathAgent(config=tree_config, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()) as spy:
            agent.solve("p", create_session=False)
        assert spy.call_args.kwargs["search_config"] == SearchConfig()

    def test_explicit_search_config_passed_through(self):
        sc = SearchConfig.from_preset("extreme")
        cfg = AgentConfig(search_mode="tree", search=sc, verbose=False)
        agent = MathAgent(config=cfg, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()) as spy:
            agent.solve("p", create_session=False)
        assert spy.call_args.kwargs["search_config"] is sc

    def test_physics_agent_passes_physics_domain(self):
        cfg = AgentConfig(search_mode="tree", verbose=False)
        agent = PhysicsAgent(config=cfg, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()) as spy:
            agent.solve("p", create_session=False)
        assert spy.call_args.kwargs["domain"] == "physics"

    def test_flat_mode_never_calls_search(self):
        agent = MathAgent(config=AgentConfig(verbose=False, max_iterations=1),
                          api_key="test-key")
        with mock.patch("alethic.search.solve") as spy, \
             mock.patch.object(agent, "_generate_candidates",
                               side_effect=RuntimeError("flat path reached")), \
             pytest.raises(RuntimeError, match="flat path reached"):
            agent.solve("p", create_session=False)
        spy.assert_not_called()

    def test_session_dir_attached_and_finalized(self, tree_config, tmp_path):
        agent = MathAgent(config=tree_config, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()), \
             mock.patch("alethic.agent.create_session_dir", return_value=str(tmp_path)):
            (tmp_path / "session.json").write_text(json.dumps({"status": "running"}))
            result = agent.solve("p")
        assert result.session_dir == str(tmp_path)
        session = json.loads((tmp_path / "session.json").read_text())
        assert session["status"] == "solved"
        assert session["mode"] == "tree"

    def test_unsolved_result_finalizes_status_unsolved(self, tree_config, tmp_path):
        agent = MathAgent(config=tree_config, api_key="test-key")
        unsolved = _tree_result(
            solution=None, verdict=Verdict.UNSOLVED, confidence=0.4,
            admitted_failure=True,
        )
        with mock.patch("alethic.search.solve", return_value=unsolved), \
             mock.patch("alethic.agent.create_session_dir", return_value=str(tmp_path)):
            (tmp_path / "session.json").write_text(json.dumps({"status": "running"}))
            agent.solve("p")
        session = json.loads((tmp_path / "session.json").read_text())
        assert session["status"] == "unsolved"

    def test_finalization_skipped_when_search_already_checkpointed(
        self, tree_config, tmp_path,
    ):
        agent = MathAgent(config=tree_config, api_key="test-key")
        already = _tree_result(checkpoint_path=str(tmp_path / "tree_state.json"))
        with mock.patch("alethic.search.solve", return_value=already), \
             mock.patch("alethic.agent.create_session_dir", return_value=str(tmp_path)), \
             mock.patch("alethic.agent.write_tree_checkpoint") as wtc:
            agent.solve("p")
        wtc.assert_not_called()


class TestResumeModeGuards:
    def test_tree_resume_of_flat_checkpoint_raises(self, tree_config, tmp_path):
        (tmp_path / "session.json").write_text(json.dumps({"status": "checkpoint"}))
        agent = MathAgent(config=tree_config, api_key="test-key")
        with pytest.raises(CheckpointError, match="tree_state.json"):
            agent.solve("p", resume_from=str(tmp_path))

    def test_flat_resume_of_tree_checkpoint_raises(self, tmp_path):
        (tmp_path / "session.json").write_text(json.dumps({"status": "checkpoint"}))
        (tmp_path / "tree_state.json").write_text("{}")
        agent = MathAgent(config=AgentConfig(verbose=False), api_key="test-key")
        with pytest.raises(CheckpointError, match="tree-mode"):
            agent.solve("p", resume_from=str(tmp_path))

    def test_tree_resume_forwards_session_dir_and_resume_from(self, tree_config, tmp_path):
        (tmp_path / "session.json").write_text(json.dumps({"status": "checkpoint"}))
        (tmp_path / "tree_state.json").write_text("{}")
        agent = MathAgent(config=tree_config, api_key="test-key")
        with mock.patch("alethic.search.solve", return_value=_tree_result()) as spy:
            agent.solve("p", resume_from=str(tmp_path))
        assert spy.call_args.kwargs["session_dir"] == str(tmp_path)
        assert spy.call_args.kwargs["resume_from"] == str(tmp_path)
