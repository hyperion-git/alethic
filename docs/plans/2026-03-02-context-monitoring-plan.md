# Context Window Monitoring & Checkpoint-Resume Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add context window monitoring, token tracking, and checkpoint-resume to the Alethic Python library and skill orchestrator.

**Architecture:** A `TokenLedger` accumulates per-call token usage from `response.usage`. Pre-flight estimates detect context exhaustion before API calls. On exhaustion, state is serialized to a session directory and the agent returns early with a checkpoint path. Resume reconstructs state from the checkpoint. The skill orchestrator gains `--resume`, capped `failed_approaches`, and a context-pressure heuristic.

**Tech Stack:** Python 3.13, Anthropic SDK, dataclasses, JSON serialization, pytest with unittest.mock

**Design doc:** `docs/plans/2026-03-02-context-monitoring-design.md`

---

### Task 1: Exception Hierarchy

**Files:**
- Create: `src/alethic/exceptions.py`
- Test: `tests/test_exceptions.py`

**Step 1: Write the test**

```python
"""Tests for alethic exception hierarchy."""

from alethic.exceptions import (
    AlethicError,
    CheckpointError,
    ContextExhaustedError,
    TruncatedResponseError,
)


def test_hierarchy():
    """All custom exceptions inherit from AlethicError."""
    assert issubclass(TruncatedResponseError, AlethicError)
    assert issubclass(ContextExhaustedError, AlethicError)
    assert issubclass(CheckpointError, AlethicError)
    assert issubclass(AlethicError, Exception)


def test_messages():
    """Exceptions carry descriptive messages."""
    e = ContextExhaustedError("estimated 180000 tokens, limit 200000")
    assert "180000" in str(e)

    e = TruncatedResponseError("stop_reason=max_tokens")
    assert "max_tokens" in str(e)

    e = CheckpointError("disk full")
    assert "disk full" in str(e)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_exceptions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alethic.exceptions'`

**Step 3: Write implementation**

```python
"""Custom exceptions for the Alethic agent."""


class AlethicError(Exception):
    """Base exception for all Alethic errors."""


class TruncatedResponseError(AlethicError):
    """Raised when an API response was truncated (stop_reason=max_tokens)."""


class ContextExhaustedError(AlethicError):
    """Raised when estimated input tokens approach the model's context limit."""


class CheckpointError(AlethicError):
    """Raised when checkpoint state cannot be written to disk."""
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_exceptions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/exceptions.py tests/test_exceptions.py
git commit -m "feat: add exception hierarchy for context monitoring"
```

---

### Task 2: TokenLedger and Model Changes

**Files:**
- Modify: `src/alethic/models.py`
- Test: `tests/test_token_ledger.py`

**Step 1: Write the test**

```python
"""Tests for TokenLedger and context-related model changes."""

from unittest.mock import MagicMock

import pytest

from alethic.models import MODEL_CONTEXT_LIMITS, AgentConfig, AgentResult, TokenLedger, Verdict


class TestTokenLedger:
    def test_initial_state(self):
        ledger = TokenLedger()
        assert ledger.input_tokens == 0
        assert ledger.output_tokens == 0
        assert ledger.api_calls == 0
        assert ledger.total_tokens == 0

    def test_record_usage(self):
        ledger = TokenLedger()
        usage = MagicMock()
        usage.input_tokens = 1500
        usage.output_tokens = 500
        ledger.record(usage)

        assert ledger.input_tokens == 1500
        assert ledger.output_tokens == 500
        assert ledger.api_calls == 1
        assert ledger.total_tokens == 2000

    def test_record_accumulates(self):
        ledger = TokenLedger()
        for i in range(3):
            usage = MagicMock()
            usage.input_tokens = 1000
            usage.output_tokens = 400
            ledger.record(usage)

        assert ledger.input_tokens == 3000
        assert ledger.output_tokens == 1200
        assert ledger.api_calls == 3
        assert ledger.total_tokens == 4200

    def test_to_dict(self):
        ledger = TokenLedger()
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50
        ledger.record(usage)
        d = ledger.to_dict()
        assert d == {"input_tokens": 100, "output_tokens": 50, "api_calls": 1}

    def test_from_dict(self):
        d = {"input_tokens": 2000, "output_tokens": 800, "api_calls": 5}
        ledger = TokenLedger.from_dict(d)
        assert ledger.input_tokens == 2000
        assert ledger.output_tokens == 800
        assert ledger.api_calls == 5

    def test_from_dict_empty(self):
        ledger = TokenLedger.from_dict({})
        assert ledger.input_tokens == 0
        assert ledger.output_tokens == 0
        assert ledger.api_calls == 0


class TestModelContextLimits:
    def test_known_models(self):
        assert MODEL_CONTEXT_LIMITS["claude-opus-4-6"] == 200_000
        assert MODEL_CONTEXT_LIMITS["claude-sonnet-4-6"] == 200_000
        assert MODEL_CONTEXT_LIMITS["claude-haiku-4-5-20251001"] == 200_000

    def test_default_fallback(self):
        assert MODEL_CONTEXT_LIMITS.get("unknown-model", 200_000) == 200_000


class TestAgentConfigContextThreshold:
    def test_default_threshold(self):
        config = AgentConfig()
        assert config.context_threshold == 0.8

    def test_custom_threshold(self):
        config = AgentConfig(context_threshold=0.9)
        assert config.context_threshold == 0.9

    def test_threshold_validation(self):
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=1.5)
        with pytest.raises(ValueError, match="context_threshold"):
            AgentConfig(context_threshold=-0.1)

    def test_preset_preserves_default(self):
        config = AgentConfig.from_preset("quick")
        assert config.context_threshold == 0.85  # preset overrides

    def test_explicit_override(self):
        config = AgentConfig.from_preset("quick", context_threshold=0.7)
        assert config.context_threshold == 0.7


class TestAgentResultNewFields:
    def test_token_ledger_default(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
        )
        assert result.token_ledger is None
        assert result.session_dir is None
        assert result.checkpoint_path is None

    def test_token_ledger_populated(self):
        ledger = TokenLedger(input_tokens=5000, output_tokens=2000, api_calls=3)
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            token_ledger=ledger,
            session_dir="/tmp/alethic-test/",
        )
        assert result.token_ledger.total_tokens == 7000
        assert result.session_dir == "/tmp/alethic-test/"

    def test_checkpoint_path(self):
        result = AgentResult(
            problem="test",
            solution=None,
            verdict=Verdict.UNSOLVED,
            confidence=0.7,
            iterations_used=3,
            total_revisions=2,
            admitted_failure=False,
            checkpoint_path="/tmp/alethic-test/",
        )
        assert result.checkpoint_path == "/tmp/alethic-test/"
        assert not result.admitted_failure
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_token_ledger.py -v`
Expected: FAIL — `ImportError: cannot import name 'TokenLedger'`

**Step 3: Write implementation**

Add to `src/alethic/models.py`:

1. `TokenLedger` dataclass (before `AgentConfig`):

```python
@dataclass
class TokenLedger:
    """Tracks cumulative token usage across API calls in a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, usage) -> None:
        """Record token usage from an Anthropic API response."""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.api_calls += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "api_calls": self.api_calls,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenLedger:
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            api_calls=d.get("api_calls", 0),
        )
```

2. `MODEL_CONTEXT_LIMITS` module-level dict:

```python
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
```

3. `context_threshold` field on `AgentConfig` (after `reset_n_boost`):

```python
context_threshold: float = 0.8
```

With validation in `__post_init__`:

```python
if not 0.0 < self.context_threshold <= 1.0:
    raise ValueError(f"context_threshold must be in (0.0, 1.0], got {self.context_threshold}")
```

4. New fields on `AgentResult`:

```python
token_ledger: TokenLedger | None = None
session_dir: str | None = None
checkpoint_path: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_token_ledger.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All 585+ tests pass (new fields have defaults, no behavioral change)

**Step 6: Commit**

```bash
git add src/alethic/models.py tests/test_token_ledger.py
git commit -m "feat: add TokenLedger, MODEL_CONTEXT_LIMITS, context_threshold"
```

---

### Task 3: Subagent Token Tracking and Safety

**Files:**
- Modify: `src/alethic/subagents.py`
- Test: `tests/test_context_safety.py`

**Step 1: Write the test**

```python
"""Tests for context window safety in subagents."""

from unittest.mock import MagicMock, patch

import pytest

from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
from alethic.models import AgentConfig, TokenLedger


def _mock_response(text: str, stop_reason: str = "end_turn", input_tokens: int = 500, output_tokens: int = 200):
    """Create a mock Anthropic response with usage and stop_reason."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


class TestTokenTracking:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_ledger_records_usage(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("hello", input_tokens=1000, output_tokens=300)
        config = AgentConfig(verbose=False, enable_code_execution=False)
        ledger = TokenLedger()

        _call_model(client, system="sys", user_message="hi", config=config,
                     temperature=1.0, ledger=ledger)

        assert ledger.input_tokens == 1000
        assert ledger.output_tokens == 300
        assert ledger.api_calls == 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_no_ledger_still_works(self, _ptc):
        """Backward compat: ledger=None means no tracking, no errors."""
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("hello")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(client, system="sys", user_message="hi",
                             config=config, temperature=1.0)
        assert result == "hello"


class TestTruncatedResponseDetection:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_max_tokens_raises(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "partial output", stop_reason="max_tokens"
        )
        config = AgentConfig(verbose=False, enable_code_execution=False)

        with pytest.raises(TruncatedResponseError, match="max_tokens"):
            _call_model(client, system="sys", user_message="hi",
                        config=config, temperature=1.0)

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_end_turn_does_not_raise(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("full output", stop_reason="end_turn")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(client, system="sys", user_message="hi",
                             config=config, temperature=1.0)
        assert result == "full output"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_truncation_still_records_ledger(self, _ptc):
        """Even on truncation, the ledger should record the usage."""
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response(
            "partial", stop_reason="max_tokens", input_tokens=5000, output_tokens=16384
        )
        config = AgentConfig(verbose=False, enable_code_execution=False)
        ledger = TokenLedger()

        with pytest.raises(TruncatedResponseError):
            _call_model(client, system="sys", user_message="hi",
                        config=config, temperature=1.0, ledger=ledger)

        assert ledger.input_tokens == 5000
        assert ledger.api_calls == 1


class TestPreFlightEstimate:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_context_exhausted_before_call(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        config = AgentConfig(verbose=False, enable_code_execution=False)

        # 800K chars / 4 = 200K tokens estimate, exceeds 0.8 * 200K = 160K
        big_message = "x" * 800_000

        with pytest.raises(ContextExhaustedError, match="estimated"):
            _call_model(client, system="sys", user_message=big_message,
                        config=config, temperature=1.0, context_limit=200_000,
                        context_threshold=0.8)

        # API was never called
        client.messages.create.assert_not_called()

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_within_limit_proceeds(self, _ptc):
        from alethic.subagents import _call_model

        client = MagicMock()
        client.messages.create.return_value = _mock_response("ok")
        config = AgentConfig(verbose=False, enable_code_execution=False)

        result = _call_model(client, system="sys", user_message="short",
                             config=config, temperature=1.0, context_limit=200_000,
                             context_threshold=0.8)
        assert result == "ok"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_safety.py -v`
Expected: FAIL — `_call_model()` doesn't accept `ledger`/`context_limit`/`context_threshold` yet

**Step 3: Write implementation**

Modify `_call_model()` in `src/alethic/subagents.py`:

1. Add imports at top of file:
```python
from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
```

2. Change `_call_model` signature:
```python
def _call_model(
    client,
    *,
    system: str,
    user_message: str,
    config: AgentConfig,
    temperature: float,
    tools: list[dict] | None = None,
    ledger: TokenLedger | None = None,
    context_limit: int = 200_000,
    context_threshold: float = 0.8,
) -> str:
```

3. Add pre-flight estimate before building kwargs:
```python
    estimated_input = len(system + user_message) // 4
    if estimated_input > context_threshold * context_limit:
        raise ContextExhaustedError(
            f"Pre-flight estimate: ~{estimated_input} tokens "
            f"(threshold: {int(context_threshold * context_limit)} of {context_limit})"
        )
```

4. After each `response = _create_with_retry(client, kwargs)`, add:
```python
        if ledger is not None:
            ledger.record(response.usage)
```

5. Before extracting text (when `not tool_results`), check stop_reason:
```python
        if not tool_results:
            if getattr(response, "stop_reason", None) == "max_tokens":
                raise TruncatedResponseError(
                    f"Response truncated (stop_reason=max_tokens) after "
                    f"{ledger.api_calls if ledger else '?'} calls"
                )
            return _extract_text(response)
```

6. After appending tool results to messages (tool-use loop), re-estimate:
```python
        # Re-estimate context after tool round
        total_chars = sum(
            len(str(m.get("content", ""))) for m in messages
        ) + len(system)
        re_estimated = total_chars // 4
        if re_estimated > context_threshold * context_limit:
            raise ContextExhaustedError(
                f"Tool-use loop estimate: ~{re_estimated} tokens "
                f"(threshold: {int(context_threshold * context_limit)} of {context_limit})"
            )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_safety.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All existing tests pass. The existing `_mock_response()` helpers return `MagicMock()` objects where `.usage` and `.stop_reason` are auto-MagicMock attributes — `ledger.record()` is only called when `ledger` is not None (default), and `stop_reason` auto-attributes won't equal the string `"max_tokens"`, so no existing tests break.

**Step 6: Commit**

```bash
git add src/alethic/subagents.py tests/test_context_safety.py
git commit -m "feat: add token tracking, truncation detection, and pre-flight context estimate to _call_model"
```

---

### Task 4: Session Persistence Module

**Files:**
- Create: `src/alethic/session.py`
- Test: `tests/test_session.py`

**Step 1: Write the test**

```python
"""Tests for session directory creation, checkpoint write/load."""

import json
from pathlib import Path

import pytest

from alethic.exceptions import CheckpointError
from alethic.models import AgentConfig, TokenLedger, Verdict
from alethic.session import (
    create_session_dir,
    load_checkpoint,
    scan_incomplete_sessions,
    write_checkpoint,
)


class TestCreateSessionDir:
    def test_creates_directory_structure(self, tmp_path):
        session_dir = create_session_dir(
            problem="Prove sqrt(2) is irrational",
            domain="math",
            config=AgentConfig(max_iterations=2, verbose=False),
            base_dir=str(tmp_path),
        )
        p = Path(session_dir)
        assert p.exists()
        assert (p / "worklog").is_dir()
        assert (p / "problem.md").exists()
        assert (p / "session.json").exists()

    def test_problem_wrapped_in_tags(self, tmp_path):
        session_dir = create_session_dir(
            problem="Is 17 prime?",
            domain="math",
            config=AgentConfig(verbose=False),
            base_dir=str(tmp_path),
        )
        content = (Path(session_dir) / "problem.md").read_text()
        assert "<problem_statement>" in content
        assert "Is 17 prime?" in content
        assert "</problem_statement>" in content

    def test_session_json_fields(self, tmp_path):
        config = AgentConfig(max_iterations=5, confidence_threshold=0.9, verbose=False)
        session_dir = create_session_dir(
            problem="test problem",
            domain="physics",
            config=config,
            base_dir=str(tmp_path),
        )
        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert data["status"] == "running"
        assert data["domain"] == "physics"
        assert data["current_iteration"] == 0
        assert data["best_confidence"] == 0.0
        assert data["config"]["max_iterations"] == 5

    def test_slug_generation(self, tmp_path):
        session_dir = create_session_dir(
            problem="Prove that sqrt(2) is irrational!!!",
            domain="math",
            config=AgentConfig(verbose=False),
            base_dir=str(tmp_path),
        )
        dirname = Path(session_dir).name
        assert dirname.startswith("prove-that-sqrt-2-is-irrational")
        assert not dirname.startswith("-")


class TestWriteCheckpoint:
    def test_writes_session_json(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        session_json = {
            "status": "running",
            "current_iteration": 2,
            "best_confidence": 0.75,
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(session_json))

        write_checkpoint(
            session_dir=session_dir,
            current_iteration=3,
            best_confidence=0.85,
            best_solution_text="My solution",
            failed_approaches=["approach 1", "approach 2"],
            stall_state={"iterations_since_meaningful_improvement": 1},
            token_ledger=TokenLedger(input_tokens=5000, output_tokens=2000, api_calls=4),
            status="checkpoint",
        )

        data = json.loads((Path(session_dir) / "session.json").read_text())
        assert data["status"] == "checkpoint"
        assert data["current_iteration"] == 3
        assert data["best_confidence"] == 0.85
        assert data["token_ledger"]["api_calls"] == 4

        best = (Path(session_dir) / "worklog" / "best_solution.md").read_text()
        assert best == "My solution"

    def test_checkpoint_error_on_failure(self, tmp_path):
        with pytest.raises(CheckpointError):
            write_checkpoint(
                session_dir=str(tmp_path / "nonexistent" / "deep" / "path"),
                current_iteration=1,
                best_confidence=0.5,
                best_solution_text=None,
                failed_approaches=[],
                stall_state={},
                token_ledger=TokenLedger(),
                status="checkpoint",
            )


class TestLoadCheckpoint:
    def test_load_running_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        data = {
            "status": "running",
            "problem": "test problem",
            "current_iteration": 2,
            "best_confidence": 0.8,
            "failed_approaches": ["approach 1"],
            "stall_state": {
                "iterations_since_meaningful_improvement": 1,
                "iteration_final_verdicts": ["major_flaw"],
                "resets_used": 0,
                "reset_cooldown_remaining": 0,
            },
            "token_ledger": {"input_tokens": 3000, "output_tokens": 1000, "api_calls": 3},
            "config": {"max_iterations": 5, "confidence_threshold": 0.9},
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(data))
        (Path(session_dir) / "worklog" / "best_solution.md").write_text("best so far")

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 2
        assert checkpoint["best_confidence"] == 0.8
        assert checkpoint["best_solution_text"] == "best so far"
        assert len(checkpoint["failed_approaches"]) == 1

    def test_load_checkpoint_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        (Path(session_dir) / "worklog").mkdir()
        data = {"status": "checkpoint", "current_iteration": 4, "best_confidence": 0.7,
                "failed_approaches": [], "stall_state": {}, "token_ledger": {},
                "config": {}, "problem": "test"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        checkpoint = load_checkpoint(session_dir)
        assert checkpoint["current_iteration"] == 4

    def test_reject_solved_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        data = {"status": "solved"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        with pytest.raises(ValueError, match="already completed"):
            load_checkpoint(session_dir)

    def test_reject_unsolved_session(self, tmp_path):
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir()
        data = {"status": "unsolved"}
        (Path(session_dir) / "session.json").write_text(json.dumps(data))

        with pytest.raises(ValueError, match="already completed"):
            load_checkpoint(session_dir)

    def test_reject_missing_session_json(self, tmp_path):
        with pytest.raises(ValueError, match="session.json"):
            load_checkpoint(str(tmp_path))


class TestScanIncompleteSessions:
    def test_finds_running_session(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "test-20260302-ab12"
        session_dir.mkdir()
        data = {"status": "running", "problem": "Is 17 prime?", "current_iteration": 2,
                "best_confidence": 0.7, "config": {"max_iterations": 5}}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 1
        assert results[0]["session_dir"] == str(session_dir)
        assert results[0]["problem"] == "Is 17 prime?"

    def test_finds_checkpoint_session(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "test-20260302-cd34"
        session_dir.mkdir()
        data = {"status": "checkpoint", "problem": "Prove X", "current_iteration": 5,
                "best_confidence": 0.88, "config": {"max_iterations": 8}}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 1

    def test_ignores_solved_sessions(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        session_dir = alethic_dir / "done-20260302-ef56"
        session_dir.mkdir()
        data = {"status": "solved", "problem": "solved one"}
        (session_dir / "session.json").write_text(json.dumps(data))

        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 0

    def test_empty_alethic_dir(self, tmp_path):
        alethic_dir = tmp_path / ".alethic"
        alethic_dir.mkdir()
        results = scan_incomplete_sessions(str(alethic_dir))
        assert len(results) == 0

    def test_nonexistent_dir(self, tmp_path):
        results = scan_incomplete_sessions(str(tmp_path / "nope"))
        assert len(results) == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alethic.session'`

**Step 3: Write implementation**

Create `src/alethic/session.py`. Key functions:

- `create_session_dir(problem, domain, config, base_dir=None) -> str`: Creates `.alethic/{slug}-{date}-{hex}/` with `problem.md`, `session.json`, `worklog/`. Uses `subprocess.run(["git", "rev-parse", ...])` for git detection when `base_dir` is None, falls back to `/tmp/`.
- `write_checkpoint(session_dir, current_iteration, best_confidence, best_solution_text, failed_approaches, stall_state, token_ledger, status) -> None`: Updates `session.json` and writes `worklog/best_solution.md`. Raises `CheckpointError` on `OSError`.
- `load_checkpoint(session_dir) -> dict`: Reads `session.json`, validates status is `running`/`checkpoint`, reads `worklog/best_solution.md`, returns dict with all fields needed to reconstruct `RunState`.
- `scan_incomplete_sessions(alethic_dir) -> list[dict]`: Scans for subdirectories with `session.json` where `status` is `running` or `checkpoint`. Returns list of summary dicts.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_session.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/session.py tests/test_session.py
git commit -m "feat: add session persistence with checkpoint write/load/scan"
```

---

### Task 5: Wire Token Ledger and Session into Agent

**Files:**
- Modify: `src/alethic/agent.py`
- Test: `tests/test_context_agent.py`

This is the integration task — it wires the ledger through `solve()`, creates session directories, handles `ContextExhaustedError`, and supports `resume_from`.

**Step 1: Write the test**

```python
"""Tests for context monitoring integration in MathAgent."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alethic.agent import MathAgent
from alethic.exceptions import ContextExhaustedError, TruncatedResponseError
from alethic.models import AgentConfig, Verdict


def _mock_response(text: str, stop_reason: str = "end_turn",
                   input_tokens: int = 500, output_tokens: int = 200):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


CORRECT_HIGH = (
    "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
    "CRITIQUE:\nPerfect.\n\nISSUES:\nNone"
)


class TestTokenLedgerIntegration:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_solve_populates_ledger(self, _ptc, tmp_path):
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("solution", input_tokens=2000, output_tokens=1000),
            _mock_response(CORRECT_HIGH, input_tokens=3000, output_tokens=800),
        ]

        with patch("alethic.session.create_session_dir", return_value=str(tmp_path / "session")):
            Path(tmp_path / "session" / "worklog").mkdir(parents=True)
            result = agent.solve("test problem")

        assert result.token_ledger is not None
        assert result.token_ledger.api_calls == 2
        assert result.token_ledger.input_tokens == 5000
        assert result.token_ledger.output_tokens == 1800

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_session_dir_populated(self, _ptc, tmp_path):
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("solution"),
            _mock_response(CORRECT_HIGH),
        ]

        session_dir = str(tmp_path / "session")
        with patch("alethic.session.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            result = agent.solve("test problem")

        assert result.session_dir == session_dir


class TestContextExhaustedCheckpoint:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_checkpoint_on_context_exhaustion(self, _ptc, tmp_path):
        """When ContextExhaustedError fires, agent checkpoints and returns."""
        config = AgentConfig(
            max_iterations=5, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()

        # Iter 1: generate succeeds, verify returns minor_issues
        # Iter 2: generate raises ContextExhaustedError
        minor = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.7\n\n"
            "CRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
        )
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response("solution v1")
            elif call_count == 2:
                return _mock_response(minor)
            else:
                raise ContextExhaustedError("context full")

        agent.client.messages.create.side_effect = side_effect

        session_dir = str(tmp_path / "session")
        with patch("alethic.session.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            with patch("alethic.session.write_checkpoint") as mock_cp:
                result = agent.solve("test problem")

        assert result.verdict == Verdict.UNSOLVED
        assert not result.admitted_failure
        assert result.checkpoint_path is not None
        mock_cp.assert_called_once()


class TestTruncatedResponseHandling:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_generator_truncation_skips_candidate(self, _ptc, tmp_path):
        """A truncated generator response should skip that candidate, not crash."""
        config = AgentConfig(
            max_iterations=1, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.return_value = _mock_response(
            "partial", stop_reason="max_tokens"
        )

        session_dir = str(tmp_path / "session")
        with patch("alethic.session.create_session_dir", return_value=session_dir):
            Path(session_dir).mkdir(parents=True)
            (Path(session_dir) / "worklog").mkdir()
            result = agent.solve("test problem")

        # Agent should fail gracefully, not crash
        assert result.verdict == Verdict.UNSOLVED


class TestResumeFromCheckpoint:
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_resume_starts_from_saved_iteration(self, _ptc, tmp_path):
        """Resume should start from current_iteration + 1."""
        config = AgentConfig(
            max_iterations=5, best_of_n=1,
            enable_code_execution=False, verbose=False,
        )
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        agent.client.messages.create.side_effect = [
            _mock_response("resumed solution"),
            _mock_response(CORRECT_HIGH),
        ]

        # Create a checkpoint to resume from
        session_dir = str(tmp_path / "session")
        Path(session_dir).mkdir(parents=True)
        (Path(session_dir) / "worklog").mkdir()
        checkpoint_data = {
            "status": "checkpoint",
            "problem": "test problem",
            "current_iteration": 3,
            "best_confidence": 0.7,
            "failed_approaches": ["first try failed"],
            "stall_state": {
                "iterations_since_meaningful_improvement": 1,
                "iteration_final_verdicts": ["major_flaw"],
                "resets_used": 0,
                "reset_cooldown_remaining": 0,
            },
            "token_ledger": {"input_tokens": 10000, "output_tokens": 5000, "api_calls": 8},
            "config": {"max_iterations": 5, "confidence_threshold": 0.9},
        }
        (Path(session_dir) / "session.json").write_text(json.dumps(checkpoint_data))
        (Path(session_dir) / "worklog" / "best_solution.md").write_text("old best")

        result = agent.solve("test problem", resume_from=session_dir)

        assert result.solved
        assert result.iterations_used == 4  # resumed at iter 4 (3+1)
        assert len(result.failed_approaches) >= 1  # inherited from checkpoint
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_agent.py -v`
Expected: FAIL — `solve()` doesn't accept `resume_from`, no session dir creation

**Step 3: Write implementation**

Modify `src/alethic/agent.py`. Key changes:

1. Add imports: `from alethic.exceptions import ContextExhaustedError, TruncatedResponseError`, `from alethic.models import TokenLedger, MODEL_CONTEXT_LIMITS`, `from alethic.session import create_session_dir, write_checkpoint, load_checkpoint`

2. Add `_domain()` method to `MathAgent` (returns `"math"`, overridden in `PhysicsAgent` to return `"physics"`)

3. In `solve()`:
   - Create session dir at start
   - Create `TokenLedger` instance
   - Resolve `context_limit` from `MODEL_CONTEXT_LIMITS.get(config.model, 200_000)`
   - Pass `ledger`, `context_limit`, `config.context_threshold` through to all `generate()`, `verify()`, `revise()` calls (which pass them to `_call_model()`)
   - If `resume_from` is provided, call `load_checkpoint()` and reconstruct `RunState`, start loop from `current_iteration + 1`
   - Catch `ContextExhaustedError` in the main iteration loop: call `write_checkpoint()` with `status="checkpoint"`, return `AgentResult` with `checkpoint_path`
   - Catch `TruncatedResponseError` per-role (generator: skip candidate, verifier: treat as unsolved, reviser: break revision loop)
   - After each iteration, call `write_checkpoint()` with `status="running"` (best-effort, catch `OSError`)
   - Include `token_ledger` and `session_dir` in the returned `AgentResult`

4. Thread `ledger`, `context_limit`, `context_threshold` through `generate()`, `verify()`, `revise()` in `subagents.py` (add optional kwargs, pass to `_call_model()`).

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_agent.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass. Existing tests don't use `resume_from` and the session/ledger code is additive with defaults.

**Step 6: Commit**

```bash
git add src/alethic/agent.py src/alethic/subagents.py tests/test_context_agent.py
git commit -m "feat: wire token ledger, checkpoint-resume, and context safety into MathAgent.solve()"
```

---

### Task 6: CLI Flags

**Files:**
- Modify: `src/alethic/cli.py`
- Test: `tests/test_context_cli.py`

**Step 1: Write the test**

```python
"""Tests for context monitoring CLI flags."""

from alethic.cli import _build_config, build_parser


def _parse(args_str: str):
    parser = build_parser()
    return parser.parse_args(args_str.split())


class TestContextThresholdFlag:
    def test_default(self):
        args = _parse("test problem")
        config = _build_config(args)
        assert config.context_threshold == 0.8

    def test_explicit(self):
        args = _parse("--context-threshold 0.9 test problem")
        config = _build_config(args)
        assert config.context_threshold == 0.9

    def test_with_preset(self):
        args = _parse("--preset quick --context-threshold 0.7 test problem")
        config = _build_config(args)
        assert config.context_threshold == 0.7


class TestResumeFlag:
    def test_resume_flag_parsed(self):
        args = _parse("--resume /tmp/session test problem")
        assert args.resume == "/tmp/session"

    def test_resume_default_none(self):
        args = _parse("test problem")
        assert args.resume is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_cli.py -v`
Expected: FAIL — `--context-threshold` and `--resume` flags don't exist

**Step 3: Write implementation**

Add to `build_parser()` in `cli.py`:

```python
parser.add_argument(
    "--context-threshold",
    type=float,
    default=None,
    help="Context window utilization threshold before checkpoint (default: 0.8)",
)
parser.add_argument(
    "--resume",
    default=None,
    help="Resume from a checkpoint session directory",
)
```

Add `"context_threshold": "context_threshold"` to `_FLAG_TO_CONFIG`.

In `main()`, pass `resume_from=args.resume` to `agent.solve()`.

Add `token_ledger` to JSON output:

```python
if result.token_ledger:
    output["token_usage"] = result.token_ledger.to_dict()
if result.session_dir:
    output["session_dir"] = result.session_dir
if result.checkpoint_path:
    output["checkpoint_path"] = result.checkpoint_path
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_cli.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

**Step 6: Commit**

```bash
git add src/alethic/cli.py tests/test_context_cli.py
git commit -m "feat: add --resume and --context-threshold CLI flags with token usage in JSON output"
```

---

### Task 7: Exports and Backward Compatibility

**Files:**
- Modify: `src/alethic/__init__.py`
- Test: Run existing `tests/test_adversarial_backward_compat.py`

**Step 1: Update exports**

Add to `__init__.py` imports:

```python
from alethic.exceptions import (
    AlethicError,
    CheckpointError,
    ContextExhaustedError,
    TruncatedResponseError,
)
from alethic.models import TokenLedger
```

Add to `__all__`:

```python
"AlethicError",
"CheckpointError",
"ContextExhaustedError",
"TokenLedger",
"TruncatedResponseError",
```

**Step 2: Run backward compat tests**

Run: `pytest tests/test_adversarial_backward_compat.py -v`
Expected: PASS (new exports are additive)

**Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/alethic/__init__.py
git commit -m "feat: export TokenLedger and exception types from alethic package"
```

---

### Task 8: Skill Orchestrator Changes

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`
- Modify: `skills/alethic-solve/SKILL.md`
- Modify: `skills/alethic-derive/SKILL.md`

**Step 1: Cap `failed_approaches` in orchestrator.md**

In Step 2a (Generate), find the line about "Previous attempts:" and add:

> When constructing the "Previous attempts:" block, include only the **last 5 entries** from the `failed_approaches` list. Older entries remain in `session.json` for post-hoc analysis but are not inlined into the Generator prompt.

**Step 2: Add `--resume` flag to orchestrator.md**

In the Argument Parsing table, add:

| `--resume` | — | — | Resume from an incomplete session directory |

In Step 1 (Setup), add a new sub-step before slug generation:

> **1b. Resume check**: If `--resume PATH` is provided:
> 1. Read `{PATH}/session.json`. Validate `status` is `"running"` or `"checkpoint"`.
> 2. Extract `current_iteration`, `best_confidence`, `best_solution_path`, `failed_approaches`, `stall_state`, `config`, and the problem text.
> 3. Set `{session_dir} = PATH`. Skip slug generation, directory creation, and `problem.md` writing.
> 4. Set `start_iteration = current_iteration + 1`. The main loop (Step 2) starts from `start_iteration` instead of 1.
> 5. Restore all state variables from the saved values.
> 6. Print: `[RESUME] Resuming session {session_id} from iteration {start_iteration}`
>
> **1c. Auto-detect** (when `--resume` is NOT provided and a git root exists):
> 1. Scan `.alethic/` for subdirectories containing `session.json` where `status` is `"running"` or `"checkpoint"`.
> 2. If any are found, print a summary for each:
>    `Found incomplete session: .alethic/{id}/ (iter {N}/{max}, conf {best}, {status})`
> 3. Do NOT auto-resume — just inform the user. They must explicitly use `--resume` to continue.

**Step 3: Add context-pressure heuristic to orchestrator.md**

In the "Orchestrator Context Management" section, add:

> **Context-pressure checkpoint**: If you are past iteration 6 and notice that:
> - Your responses are becoming slower or shorter than earlier iterations
> - Auto-compression messages appear in the conversation
> - You are having difficulty recalling earlier iteration details
>
> Then checkpoint immediately: update `session.json` with `"status": "checkpoint"` and `"completed_at"` timestamp. Present whatever results exist with:
> ```
> [CHECKPOINT] Context pressure detected at iteration {N}.
> Best confidence: {best_confidence}
> Session saved to: .alethic/{session_id}/
> Resume with: /{command} --resume .alethic/{session_id}/ "{problem first 80 chars}..."
> ```

**Step 4: Update thin SKILL.md files**

In both `skills/alethic-solve/SKILL.md` and `skills/alethic-derive/SKILL.md`, add `--resume` to the flag table:

| `--resume` | — | — | Resume from an incomplete session directory |

**Step 5: Commit**

```bash
git add skills/alethic-common/orchestrator.md skills/alethic-solve/SKILL.md skills/alethic-derive/SKILL.md
git commit -m "feat: add --resume flag, cap failed_approaches, and context-pressure heuristic to skill orchestrator"
```

---

### Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Document new features**

Add to the Dev Commands section:

```bash
# Resume from checkpoint
alethic --resume .alethic/session-id/ "original problem"
alethic derive --resume .alethic/session-id/ "original problem"

# Custom context threshold
alethic --context-threshold 0.9 "Prove the Cayley-Hamilton theorem"
```

Add to the Module Map table:

| `exceptions.py` | `AlethicError`, `TruncatedResponseError`, `ContextExhaustedError`, `CheckpointError` |
| `session.py` | Session directory creation, checkpoint write/load, incomplete session scanning |

Update `AgentConfig` description to mention `context_threshold`.

Update `AgentResult` description to mention `token_ledger`, `session_dir`, `checkpoint_path`.

Update the Key Design Decisions section with a new entry:

> 18. **Context window monitoring with checkpoint-resume**: A `TokenLedger` tracks cumulative token usage from `response.usage` across all API calls. Before each call, a chars/4 heuristic estimates input tokens; if the estimate exceeds `context_threshold * model_context_limit` (default 80% of 200K), the agent checkpoints state to a session directory and returns early. `stop_reason == "max_tokens"` detection catches truncated responses. `solve()` accepts `resume_from` to continue from a checkpoint. Session directories are auto-created (`.alethic/` in git repos, `/tmp/alethic-*/` otherwise), matching the skill's layout. The skill orchestrator gains `--resume`, caps `failed_approaches` to last 5 in Generator prompts, and includes a soft context-pressure heuristic.

Update the CLI flags in the `cli.py` module map entry to include `--resume` and `--context-threshold`.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document context monitoring, checkpoint-resume, and new CLI flags"
```

---

### Task 10: Final Verification

**Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass (585 original + ~30 new)

**Step 2: Run linting**

Run: `ruff check src tests`
Expected: No errors

**Step 3: Run type checking**

Run: `mypy src/alethic`
Expected: No new errors

**Step 4: Run formatting**

Run: `ruff format src tests`

**Step 5: Manual smoke test**

Run (requires API key):
```bash
alethic --preset quick "Is 17 prime?"
```
Verify: session directory created in `.alethic/`, `session.json` contains `token_ledger` with nonzero values, result printed normally.

**Step 6: Final commit (if formatting changed anything)**

```bash
git add -A
git commit -m "chore: format and lint cleanup"
```
