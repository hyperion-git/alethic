# Stochastic Reset Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add dual-trigger stall detection (confidence plateau + major-flaw streak) with structural reset actions to the GVR orchestrator loop.

**Architecture:** A lightweight monitoring layer in the `solve()` loop detects when confidence stops improving or when `MAJOR_FLAW` verdicts repeat. On trigger, the next iteration widens best-of-N, injects a strategy-reset prompt, and reduces revision budget to 1. All overrides are iteration-scoped (auto-revert). Detection is deterministic; stochasticity comes from the LLM response, not the trigger.

**Tech Stack:** Python 3.13, dataclasses, collections.deque, pytest with unittest.mock

**Design doc:** `docs/plans/2026-02-18-stochastic-reset-design.md`

---

### Task 1: Add `STALL_RESET` to `EventType` enum

**Files:**
- Modify: `src/alethic/models.py:49-57`
- Test: `tests/test_new_types.py:100-110`

**Step 1: Write the failing test**

In `tests/test_new_types.py`, update `TestEventType`:

```python
class TestEventType:
    def test_all_values(self):
        assert EventType.GENERATE.value == "generate"
        assert EventType.VERIFY.value == "verify"
        assert EventType.REVISE.value == "revise"
        assert EventType.ERROR.value == "error"
        assert EventType.ACCEPT.value == "accept"
        assert EventType.FAIL.value == "fail"
        assert EventType.STALL_RESET.value == "stall_reset"

    def test_exactly_seven_members(self):
        assert len(EventType) == 7
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py::TestEventType -v`
Expected: FAIL — `AttributeError: STALL_RESET`

**Step 3: Write minimal implementation**

In `src/alethic/models.py:49-57`, add the new member:

```python
class EventType(enum.Enum):
    """Type of event in the agent's execution log."""

    GENERATE = "generate"
    VERIFY = "verify"
    REVISE = "revise"
    ERROR = "error"
    ACCEPT = "accept"
    FAIL = "fail"
    STALL_RESET = "stall_reset"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py::TestEventType -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/models.py tests/test_new_types.py
git commit -m "feat: add STALL_RESET event type"
```

---

### Task 2: Add stall config fields to `AgentConfig`

**Files:**
- Modify: `src/alethic/models.py:70-169`
- Test: `tests/test_new_types.py` (new class), `tests/test_alethic.py:122-149`

**Step 1: Write the failing tests**

Add to `tests/test_new_types.py`:

```python
class TestStallResetConfig:
    def test_default_values(self):
        config = AgentConfig()
        assert config.stall_window == 2
        assert config.stall_epsilon == 0.03
        assert config.stall_reset is True
        assert config.reset_n_boost == 1

    def test_stall_reset_disabled(self):
        config = AgentConfig(stall_reset=False)
        assert config.stall_reset is False

    def test_validation_stall_window_positive(self):
        with pytest.raises(ValueError, match="stall_window must be >= 1"):
            AgentConfig(stall_window=0)

    def test_validation_stall_epsilon_nonneg(self):
        with pytest.raises(ValueError, match="stall_epsilon must be >= 0"):
            AgentConfig(stall_epsilon=-0.01)

    def test_validation_reset_n_boost_nonneg(self):
        with pytest.raises(ValueError, match="reset_n_boost must be >= 0"):
            AgentConfig(reset_n_boost=-1)
```

Add to `tests/test_alethic.py` class `TestPresets`:

```python
    def test_preset_stall_reset_values(self):
        quick = AgentConfig.from_preset("quick")
        assert quick.stall_reset is False
        assert quick.reset_n_boost == 0

        default = AgentConfig.from_preset("default")
        assert default.stall_reset is True
        assert default.stall_window == 2
        assert default.stall_epsilon == 0.03
        assert default.reset_n_boost == 1

        thorough = AgentConfig.from_preset("thorough")
        assert thorough.stall_window == 3
        assert thorough.stall_epsilon == 0.02
        assert thorough.reset_n_boost == 1

        extreme = AgentConfig.from_preset("extreme")
        assert extreme.stall_window == 3
        assert extreme.stall_epsilon == 0.02
        assert extreme.reset_n_boost == 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_new_types.py::TestStallResetConfig tests/test_alethic.py::TestPresets::test_preset_stall_reset_values -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'stall_window'`

**Step 3: Write minimal implementation**

In `src/alethic/models.py`, add 4 fields to `AgentConfig` after `verbose` (line 105):

```python
    verbose: bool = True
    stall_window: int = 2
    stall_epsilon: float = 0.03
    stall_reset: bool = True
    reset_n_boost: int = 1
```

Add validation in `__post_init__` after the `thinking_budget` check (line 132):

```python
        if self.stall_window < 1:
            raise ValueError(f"stall_window must be >= 1, got {self.stall_window}")
        if self.stall_epsilon < 0:
            raise ValueError(f"stall_epsilon must be >= 0, got {self.stall_epsilon}")
        if self.reset_n_boost < 0:
            raise ValueError(f"reset_n_boost must be >= 0, got {self.reset_n_boost}")
```

Update `PRESETS` dict:

```python
    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "quick": {
            "max_iterations": 2,
            "max_revisions_per_cycle": 1,
            "confidence_threshold": 0.85,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 1,
            "stall_reset": False,
            "reset_n_boost": 0,
        },
        "default": {
            "max_iterations": 5,
            "max_revisions_per_cycle": 3,
            "confidence_threshold": 0.90,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 2,
            "stall_window": 2,
            "stall_epsilon": 0.03,
            "stall_reset": True,
            "reset_n_boost": 1,
        },
        "thorough": {
            "max_iterations": 8,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.95,
            "extended_thinking": True,
            "thinking_budget": 15000,
            "max_tokens": 32768,
            "best_of_n": 3,
            "stall_window": 3,
            "stall_epsilon": 0.02,
            "stall_reset": True,
            "reset_n_boost": 1,
        },
        "extreme": {
            "max_iterations": 12,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.97,
            "extended_thinking": True,
            "thinking_budget": 40000,
            "max_tokens": 65536,
            "best_of_n": 5,
            "stall_window": 3,
            "stall_epsilon": 0.02,
            "stall_reset": True,
            "reset_n_boost": 2,
        },
    }
```

Update `AgentConfig` docstring to add the 4 new attributes.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_new_types.py::TestStallResetConfig tests/test_alethic.py::TestPresets -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/models.py tests/test_new_types.py tests/test_alethic.py
git commit -m "feat: add stall detection config fields and preset defaults"
```

---

### Task 3: Add stall tracking fields to `RunState`

**Files:**
- Modify: `src/alethic/agent.py:56-64`
- Test: `tests/test_new_types.py:303-312`

**Step 1: Write the failing test**

Update `TestRunStateAndEventLog.test_run_state_defaults` in `tests/test_new_types.py`:

```python
    def test_run_state_defaults(self):
        from alethic.agent import RunState

        state = RunState()
        assert state.total_revisions == 0
        assert state.best_solution is None
        assert state.best_confidence == 0.0
        assert state.failed_approaches == []
        assert isinstance(state.start_time, float)
        # New stall tracking fields
        assert state.iterations_since_meaningful_improvement == 0
        assert len(state.iteration_final_verdicts) == 0
        assert state.resets_used == 0
        assert state.reset_cooldown_remaining == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py::TestRunStateAndEventLog::test_run_state_defaults -v`
Expected: FAIL — `AttributeError`

**Step 3: Write minimal implementation**

In `src/alethic/agent.py`, add import at top:

```python
from collections import deque
```

Update `RunState` (line 56-64):

```python
@dataclass
class RunState:
    """Mutable state accumulated across iterations of the GVR loop."""

    total_revisions: int = 0
    best_solution: Solution | None = None
    best_confidence: float = 0.0
    failed_approaches: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    # Stall detection state
    iterations_since_meaningful_improvement: int = 0
    iteration_final_verdicts: deque = field(default_factory=lambda: deque(maxlen=3))
    resets_used: int = 0
    reset_cooldown_remaining: int = 0
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py::TestRunStateAndEventLog -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/agent.py tests/test_new_types.py
git commit -m "feat: add stall tracking fields to RunState"
```

---

### Task 4: Add strategy reset prompt templates

**Files:**
- Modify: `src/alethic/prompts.py` (append after BALANCED_GENERATOR_ADDENDUM)
- Modify: `src/alethic/physics_prompts.py` (append after BALANCED_PHYSICS_ADDENDUM)
- Test: `tests/test_new_types.py` (new class)

**Step 1: Write the failing test**

Add to `tests/test_new_types.py`:

```python
class TestStrategyResetPrompts:
    def test_math_reset_addendum_exists(self):
        from alethic.prompts import STRATEGY_RESET_ADDENDUM
        assert "STRATEGY RESET" in STRATEGY_RESET_ADDENDUM
        assert "MUST" in STRATEGY_RESET_ADDENDUM
        assert "{failed_approaches}" in STRATEGY_RESET_ADDENDUM

    def test_physics_reset_addendum_exists(self):
        from alethic.physics_prompts import PHYSICS_STRATEGY_RESET_ADDENDUM
        assert "STRATEGY RESET" in PHYSICS_STRATEGY_RESET_ADDENDUM
        assert "MUST" in PHYSICS_STRATEGY_RESET_ADDENDUM
        assert "{failed_approaches}" in PHYSICS_STRATEGY_RESET_ADDENDUM
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py::TestStrategyResetPrompts -v`
Expected: FAIL — `ImportError: cannot import name 'STRATEGY_RESET_ADDENDUM'`

**Step 3: Write minimal implementation**

Append to `src/alethic/prompts.py` after `BALANCED_GENERATOR_ADDENDUM` (after line 189):

```python
STRATEGY_RESET_ADDENDUM = """

## STRATEGY RESET — Previous approaches exhausted

The following high-level strategies have been tried and failed:
{failed_approaches}

You MUST use a categorically different proof technique.
Do NOT refine, extend, or repair any previous approach.
Start from a completely different mathematical foundation.
Consider approaches from a different branch of mathematics entirely.
"""
```

Append to `src/alethic/physics_prompts.py` after `BALANCED_PHYSICS_ADDENDUM` (after line 226):

```python
PHYSICS_STRATEGY_RESET_ADDENDUM = """

## STRATEGY RESET — Previous approaches exhausted

The following high-level derivation strategies have been tried and failed:
{failed_approaches}

You MUST use a categorically different derivation technique.
Do NOT refine, extend, or repair any previous approach.
Start from a completely different physical or mathematical foundation.
Consider approaches from a different formalism entirely (e.g., if \
Lagrangian methods failed, try Hamiltonian; if perturbation theory \
failed, try exact methods or symmetry arguments).
"""
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py::TestStrategyResetPrompts -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/prompts.py src/alethic/physics_prompts.py tests/test_new_types.py
git commit -m "feat: add strategy reset prompt templates for math and physics"
```

---

### Task 5: Add `reset_context` parameter to `generate()` in subagents

**Files:**
- Modify: `src/alethic/subagents.py:153-195`
- Test: `tests/test_new_types.py` (extend `TestFailedApproachInGenerate`)

**Step 1: Write the failing test**

Add to `tests/test_new_types.py` class `TestFailedApproachInGenerate`:

```python
    def test_generate_with_reset_context(self):
        from alethic.subagents import generate

        client = self._make_mock_client()
        config = AgentConfig(enable_code_execution=False, verbose=False)

        generate(
            client,
            problem="Prove sqrt(2) is irrational",
            config=config,
            iteration=3,
            balanced=False,
            failed_approaches=("Tried induction", "Tried contradiction"),
            reset_context="## STRATEGY RESET\nUse a different approach.",
        )

        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[0]["content"]
        # Reset context should appear instead of standard failed_approaches
        assert "STRATEGY RESET" in user_msg
        # Standard "Previously attempted" block should NOT appear
        assert "Previously attempted" not in user_msg

    def test_generate_reset_context_none_uses_standard(self):
        from alethic.subagents import generate

        client = self._make_mock_client()
        config = AgentConfig(enable_code_execution=False, verbose=False)

        generate(
            client,
            problem="Prove sqrt(2) is irrational",
            config=config,
            iteration=2,
            balanced=False,
            failed_approaches=("Tried induction",),
            reset_context=None,
        )

        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Previously attempted" in user_msg
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py::TestFailedApproachInGenerate::test_generate_with_reset_context -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'reset_context'`

**Step 3: Write minimal implementation**

In `src/alethic/subagents.py`, update `generate()` signature (line 153-164):

```python
def generate(
    client,
    problem: str,
    config: AgentConfig,
    iteration: int,
    balanced: bool = True,
    *,
    failed_approaches: tuple[str, ...] = (),
    reset_context: str | None = None,
    system_prompt: str | None = None,
    user_template: str | None = None,
    balanced_addendum: str | None = None,
) -> Solution:
```

Update the failed_approaches injection block (line 189-195):

```python
    if reset_context is not None:
        user_msg += f"\n\n{reset_context}"
    elif failed_approaches:
        approaches_text = "\n".join(f"- {a}" for a in failed_approaches)
        user_msg += (
            f"\n\n## Previously attempted strategies that did NOT work:\n"
            f"{approaches_text}\n"
            f"Avoid repeating these approaches. Try a fundamentally different strategy."
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py::TestFailedApproachInGenerate -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/subagents.py tests/test_new_types.py
git commit -m "feat: add reset_context parameter to generate() subagent"
```

---

### Task 6: Add `max_revisions` parameter to `_run_revision_loop`

**Files:**
- Modify: `src/alethic/agent.py:296-392`
- Test: `tests/test_stall_reset.py` (new file, first test)

**Step 1: Write the failing test**

Create `tests/test_stall_reset.py`:

```python
"""Tests for stochastic reset / stall detection feature."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alethic.models import AgentConfig, EventType, Verdict


def _mock_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


CORRECT_HIGH = (
    "VERDICT: correct\nCONFIDENCE: 0.95\n\n"
    "CRITIQUE:\nPerfect.\n\nISSUES:\nNone"
)
MINOR_060 = (
    "VERDICT: minor_issues\nCONFIDENCE: 0.60\n\n"
    "CRITIQUE:\nSmall error.\n\nISSUES:\n- Sign error"
)
MAJOR_020 = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.20\n\n"
    "CRITIQUE:\nWrong approach.\n\nISSUES:\n- Logic error"
)


class TestRevisionLoopMaxRevisions:
    """_run_revision_loop should respect max_revisions parameter."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_max_revisions_override_limits_revisions(self, _mock_tools):
        from alethic.agent import EventLog, MathAgent, RunState

        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=3,
            enable_code_execution=False,
            verbose=False,
        )
        agent = MathAgent(config=config)

        mock_client = MagicMock()
        # Only 1 revision + 1 re-verify should happen (not 3)
        mock_client.messages.create.side_effect = [
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nFixed"),
            _mock_response(CORRECT_HIGH),
        ]
        agent.client = mock_client

        from alethic.models import Solution, VerificationResult
        from alethic.subagents import _parse_verification

        state = RunState()
        log = EventLog()
        solution = Solution(problem="test", solution_text="original", iteration=1)
        verification = _parse_verification(MINOR_060)

        result = agent._run_revision_loop(
            problem="test",
            solution=solution,
            verification=verification,
            prompts={},
            iteration=1,
            state=state,
            log=log,
            threshold=0.90,
            max_revisions=1,
        )

        assert result is not None
        assert result.solved
        assert state.total_revisions == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_stall_reset.py::TestRevisionLoopMaxRevisions -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'max_revisions'`

**Step 3: Write minimal implementation**

In `src/alethic/agent.py`, update `_run_revision_loop` signature (line 296-307):

```python
    def _run_revision_loop(
        self,
        *,
        problem: str,
        solution: Solution,
        verification: VerificationResult,
        prompts: dict[str, str],
        iteration: int,
        state: RunState,
        log: EventLog,
        threshold: float,
        max_revisions: int | None = None,
    ) -> AgentResult | None:
```

Update the loop range (line 311):

```python
        effective_max_revisions = max_revisions if max_revisions is not None else self.config.max_revisions_per_cycle
        for rev_num in range(1, effective_max_revisions + 1):
```

Update the log line (line 312) to use `effective_max_revisions`:

```python
            self._log(f"[REVISE] Revision {rev_num}/{effective_max_revisions}...")
```

Update the exhaustion log (line 390) similarly:

```python
            self._log(f"[REVISE] Exhausted revision attempts for iteration {iteration}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_stall_reset.py::TestRevisionLoopMaxRevisions -v`
Expected: PASS

**Step 5: Run all existing tests to ensure no regressions**

Run: `pytest tests/ -v`
Expected: All PASS (the new parameter defaults to None, preserving existing behavior)

**Step 6: Commit**

```bash
git add src/alethic/agent.py tests/test_stall_reset.py
git commit -m "feat: add max_revisions parameter to _run_revision_loop"
```

---

### Task 7: Add `_check_stall` method and `_build_reset_context`

**Files:**
- Modify: `src/alethic/agent.py` (new methods on `MathAgent`)
- Test: `tests/test_stall_reset.py` (new classes)

**Step 1: Write the failing tests**

Add to `tests/test_stall_reset.py`:

```python
class TestCheckStall:
    """Unit tests for _check_stall detection logic."""

    def _make_agent(self, **kwargs):
        from alethic.agent import MathAgent
        config = AgentConfig(enable_code_execution=False, verbose=False, **kwargs)
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        return agent

    def test_no_stall_when_disabled(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_reset=False)
        state = RunState()
        state.iterations_since_meaningful_improvement = 10
        assert agent._check_stall(state) is False

    def test_no_stall_on_cooldown(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.reset_cooldown_remaining = 1
        assert agent._check_stall(state) is False

    def test_no_stall_max_resets_exhausted(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=2, max_iterations=5)
        state = RunState()
        state.iterations_since_meaningful_improvement = 5
        state.resets_used = 1  # max(1, 5//4) = 1
        assert agent._check_stall(state) is False

    def test_stall_detected_no_progress(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=2)
        state = RunState()
        state.iterations_since_meaningful_improvement = 2
        assert agent._check_stall(state) is True

    def test_stall_detected_major_flaw_streak(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=10)  # high window, shouldn't trigger
        state = RunState()
        state.iterations_since_meaningful_improvement = 0  # no plateau
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent._check_stall(state) is True

    def test_no_stall_single_major_flaw(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        assert agent._check_stall(state) is False

    def test_no_stall_major_then_minor(self):
        from alethic.agent import RunState
        agent = self._make_agent(stall_window=10)
        state = RunState()
        state.iteration_final_verdicts.append(Verdict.MAJOR_FLAW)
        state.iteration_final_verdicts.append(Verdict.MINOR_ISSUES)
        assert agent._check_stall(state) is False


class TestBuildResetContext:
    """Unit tests for _build_reset_context prompt construction."""

    def _make_agent(self, **kwargs):
        from alethic.agent import MathAgent
        config = AgentConfig(enable_code_execution=False, verbose=False, **kwargs)
        agent = MathAgent(config=config)
        agent.client = MagicMock()
        return agent

    def test_builds_context_with_last_two_approaches(self):
        agent = self._make_agent()
        approaches = ["Tried induction", "Tried contradiction", "Tried generating functions"]
        context = agent._build_reset_context(approaches)
        assert "STRATEGY RESET" in context
        # Should only include last 2
        assert "Tried induction" not in context
        assert "Tried contradiction" in context
        assert "Tried generating functions" in context

    def test_builds_context_with_fewer_than_two(self):
        agent = self._make_agent()
        context = agent._build_reset_context(["Only one"])
        assert "STRATEGY RESET" in context
        assert "Only one" in context

    def test_builds_context_empty_approaches(self):
        agent = self._make_agent()
        context = agent._build_reset_context([])
        assert "STRATEGY RESET" in context
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stall_reset.py::TestCheckStall tests/test_stall_reset.py::TestBuildResetContext -v`
Expected: FAIL — `AttributeError: 'MathAgent' object has no attribute '_check_stall'`

**Step 3: Write minimal implementation**

Add to `src/alethic/agent.py` class `MathAgent`, after `_log_header` (after line 153):

```python
    def _reset_addendum(self) -> str:
        """Return the strategy reset prompt template for this domain.

        Override in subclasses to use domain-specific reset prompts.
        """
        from alethic.prompts import STRATEGY_RESET_ADDENDUM
        return STRATEGY_RESET_ADDENDUM

    def _check_stall(self, state: RunState) -> bool:
        """Check whether a stall-triggered reset should fire this iteration."""
        if not self.config.stall_reset:
            return False
        if state.reset_cooldown_remaining > 0:
            return False
        max_resets = max(1, self.config.max_iterations // 4)
        if state.resets_used >= max_resets:
            return False

        # Detector 1: no meaningful progress for stall_window iterations
        if state.iterations_since_meaningful_improvement >= self.config.stall_window:
            return True

        # Detector 2: last 2 iteration-final verdicts are both MAJOR_FLAW
        verdicts = state.iteration_final_verdicts
        if len(verdicts) >= 2 and verdicts[-1] == Verdict.MAJOR_FLAW and verdicts[-2] == Verdict.MAJOR_FLAW:
            return True

        return False

    def _build_reset_context(self, failed_approaches: list[str]) -> str:
        """Build the strategy-reset prompt overlay for a reset iteration."""
        recent = failed_approaches[-2:] if len(failed_approaches) > 2 else failed_approaches
        approaches_text = "\n".join(f"- {a}" for a in recent) if recent else "- (none recorded)"
        return self._reset_addendum().format(failed_approaches=approaches_text)
```

Add `_reset_addendum` override to `PhysicsAgent` in `src/alethic/physics_agent.py`:

```python
    def _reset_addendum(self) -> str:
        from alethic.physics_prompts import PHYSICS_STRATEGY_RESET_ADDENDUM
        return PHYSICS_STRATEGY_RESET_ADDENDUM
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stall_reset.py::TestCheckStall tests/test_stall_reset.py::TestBuildResetContext -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alethic/agent.py src/alethic/physics_agent.py tests/test_stall_reset.py
git commit -m "feat: add _check_stall and _build_reset_context methods"
```

---

### Task 8: Wire stall detection into `solve()` loop + bugfix

This is the core integration task. It modifies the main loop to:
1. Check for stall at the top of each iteration
2. Override N and revision budget on reset
3. Pass reset_context to _generate_candidates → generate()
4. Update stall tracking state at the end of each iteration
5. **Bugfix:** move failed_approaches capture to iteration-final verification

**Files:**
- Modify: `src/alethic/agent.py:211-597` (solve loop, _generate_candidates)
- Test: `tests/test_stall_reset.py` (integration tests)

**Step 1: Write the failing integration tests**

Add to `tests/test_stall_reset.py`:

```python
class TestStallResetIntegration:
    """Integration tests: full solve() loop with mocked confidence trajectories."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_plateau_triggers_reset(self, _mock_tools):
        """Confidence plateau (0.6, 0.6) should trigger reset on iteration 3."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=4,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_window=2,
            stall_epsilon=0.03,
            stall_reset=True,
            reset_n_boost=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iter 1: gen -> verify (0.6 minor) -> revise -> re-verify (0.6 minor)
            _mock_response("Attempt 1"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV1"),
            _mock_response(MINOR_060),
            # Iter 2: gen -> verify (0.6 minor) -> revise -> re-verify (0.6 minor)
            _mock_response("Attempt 2"),
            _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV2"),
            _mock_response(MINOR_060),
            # Iter 3 (RESET): gen x2 (N=1+1=2) -> verify x2 -> 1 revision -> re-verify -> correct
            _mock_response("Fresh attempt A"),
            _mock_response("Fresh attempt B"),
            _mock_response(MINOR_060),
            _mock_response(CORRECT_HIGH),  # candidate B nails it
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        # Should have a STALL_RESET event
        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 1
        assert reset_events[0].iteration == 3

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_major_flaw_streak_triggers_reset(self, _mock_tools):
        """Two consecutive MAJOR_FLAW should trigger reset."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=4,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_window=10,  # High — so only major-flaw detector fires
            stall_reset=True,
            reset_n_boost=1,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            # Iter 1: gen -> verify (major) -> revise -> re-verify (major) -> break
            _mock_response("Bad 1"),
            _mock_response(MAJOR_020),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nStill bad"),
            _mock_response(MAJOR_020),
            # Iter 2: gen -> verify (major) -> revise -> re-verify (major) -> break
            _mock_response("Bad 2"),
            _mock_response(MAJOR_020),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nStill bad"),
            _mock_response(MAJOR_020),
            # Iter 3 (RESET — major flaw streak): gen x2 -> verify x2 -> correct
            _mock_response("Fresh A"),
            _mock_response("Fresh B"),
            _mock_response(CORRECT_HIGH),
            _mock_response(MINOR_060),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test problem")

        assert result.solved
        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 1
        assert reset_events[0].data["reason"] == "major_flaw_streak"

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_disabled_stall_reset_no_trigger(self, _mock_tools):
        """With stall_reset=False, no STALL_RESET events should appear."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            max_iterations=3,
            max_revisions_per_cycle=1,
            best_of_n=1,
            stall_reset=False,
            enable_code_execution=False,
            verbose=False,
        )

        responses = [
            _mock_response("A1"), _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV1"), _mock_response(MINOR_060),
            _mock_response("A2"), _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV2"), _mock_response(MINOR_060),
            _mock_response("A3"), _mock_response(MINOR_060),
            _mock_response("CHANGES MADE:\nFix\n\nREVISED SOLUTION:\nV3"), _mock_response(MINOR_060),
        ]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("test")

        reset_events = [e for e in result.events if e.type == EventType.STALL_RESET]
        assert len(reset_events) == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stall_reset.py::TestStallResetIntegration -v`
Expected: FAIL — no STALL_RESET events emitted

**Step 3: Write the implementation**

This is the largest change. Modify `src/alethic/agent.py`:

**3a. Update `_generate_candidates` to accept `reset_context`:**

Add `reset_context: str | None = None` parameter to `_generate_candidates` (line 211-220). Thread it through to `generate()` in `_gen_one()`:

```python
    def _generate_candidates(
        self,
        *,
        problem: str,
        iteration: int,
        balanced: bool,
        prompts: dict[str, str],
        n: int,
        failed_approaches: tuple[str, ...] = (),
        reset_context: str | None = None,
    ) -> list[tuple[Solution, float]]:
        """Generate N candidates. Parallel (ThreadPoolExecutor) when N>1, sequential when N=1."""

        def _gen_one() -> tuple[Solution, float]:
            t0 = time.time()
            sol = generate(
                self.client,
                problem=problem,
                config=self.config,
                iteration=iteration,
                balanced=balanced,
                failed_approaches=failed_approaches,
                reset_context=reset_context,
                system_prompt=prompts.get("generator_system"),
                user_template=prompts.get("generator_user"),
                balanced_addendum=prompts.get("balanced_addendum"),
            )
            return sol, time.time() - t0
```

**3b. Update `solve()` loop:**

Replace the fixed `n = self.config.best_of_n` (line 421) and wire in stall detection. The key changes are:

1. Move `n` computation inside the loop
2. Add stall check at iteration top
3. Track `iteration_final_verdict` after each iteration
4. Update `iterations_since_meaningful_improvement` at end of each iteration
5. Pass `reset_context` and `max_revisions` on reset iterations
6. **Bugfix:** track the iteration-final verification and use it for `_summarize_failed_approach`

The full `solve()` loop body (lines 435-572) should be updated. Key structural changes:

At loop top (after line 438):
```python
            # ── STALL CHECK ──
            is_reset = self._check_stall(state)
            if is_reset:
                reason = "major_flaw_streak" if (
                    len(state.iteration_final_verdicts) >= 2
                    and state.iteration_final_verdicts[-1] == Verdict.MAJOR_FLAW
                    and state.iteration_final_verdicts[-2] == Verdict.MAJOR_FLAW
                ) else "no_progress"
                state.resets_used += 1
                state.reset_cooldown_remaining = 1
                n_this_iter = self.config.best_of_n + self.config.reset_n_boost
                reset_context = self._build_reset_context(state.failed_approaches)
                self._log(f"[STALL RESET] Triggered (reason: {reason}) — "
                          f"N={n_this_iter}, max_revisions=1")
                log.emit(
                    EventType.STALL_RESET,
                    iteration,
                    reason=reason,
                    n_override=n_this_iter,
                    max_revisions_override=1,
                    resets_used=state.resets_used,
                    stall_counter=state.iterations_since_meaningful_improvement,
                )
            else:
                n_this_iter = self.config.best_of_n
                reset_context = None
                if state.reset_cooldown_remaining > 0:
                    state.reset_cooldown_remaining -= 1
```

Use `n_this_iter` and `reset_context` in the generation call:
```python
                candidates = self._generate_candidates(
                    problem=problem,
                    iteration=iteration,
                    balanced=balanced,
                    prompts=prompts,
                    n=n_this_iter,
                    failed_approaches=tuple(state.failed_approaches),
                    reset_context=reset_context,
                )
```

Pass `max_revisions=1` on reset iterations to `_run_revision_loop`:
```python
                if verification.needs_revision(threshold):
                    result = self._run_revision_loop(
                        problem=problem,
                        solution=solution,
                        verification=verification,
                        prompts=prompts,
                        iteration=iteration,
                        state=state,
                        log=log,
                        threshold=threshold,
                        max_revisions=1 if is_reset else None,
                    )
```

At the bottom of each iteration (before the `except`), track the iteration-final state:
```python
                # ── UPDATE STALL TRACKING ──
                # Use the last verification we performed this iteration
                state.iteration_final_verdicts.append(verification.verdict)

                # Track whether best_confidence improved meaningfully
                pre_iter_best = state.best_confidence
                # (best_confidence is already updated above)
                if state.best_confidence >= pre_iter_best + self.config.stall_epsilon:
                    state.iterations_since_meaningful_improvement = 0
                else:
                    state.iterations_since_meaningful_improvement += 1

                # ── ACCUMULATE FAILED APPROACH (bugfix: use iteration-final verification) ──
                summary = _summarize_failed_approach(verification)
                state.failed_approaches.append(summary)
```

Note: We need to track `pre_iter_best` at the start of the iteration. Add at line ~440:
```python
            pre_iter_best = state.best_confidence
```

And move the stall improvement check to use it:
```python
                if state.best_confidence > pre_iter_best + self.config.stall_epsilon:
                    state.iterations_since_meaningful_improvement = 0
                else:
                    state.iterations_since_meaningful_improvement += 1
```

Also remove the old `_summarize_failed_approach` call at the original location (old line 566-567) and place it after the stall tracking.

Update the header logging to not show fixed N:
```python
        if self.config.best_of_n > 1:
            self._log(f"Best-of-N: {self.config.best_of_n} candidates per iteration (parallel)")
        if self.config.stall_reset:
            self._log(f"Stall reset: enabled (window={self.config.stall_window}, "
                       f"epsilon={self.config.stall_epsilon})")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stall_reset.py -v`
Expected: PASS

**Step 5: Run full test suite to check regressions**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/alethic/agent.py tests/test_stall_reset.py
git commit -m "feat: wire stall detection into solve() loop with bugfix"
```

---

### Task 9: Add CLI flags for stall reset

**Files:**
- Modify: `src/alethic/cli.py:24-227`
- Test: `tests/test_alethic.py` (new CLI tests)

**Step 1: Write the failing test**

Add to `tests/test_alethic.py` class `TestCLI`:

```python
    def test_cli_no_stall_reset_flag(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args(["--no-stall-reset", "test"])
        config = _build_config(args)
        assert config.stall_reset is False

    def test_cli_stall_window_flag(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args(["--stall-window", "4", "test"])
        config = _build_config(args)
        assert config.stall_window == 4

    def test_cli_stall_epsilon_flag(self):
        from alethic.cli import _build_config, build_parser
        parser = build_parser()
        args = parser.parse_args(["--stall-epsilon", "0.05", "test"])
        config = _build_config(args)
        assert config.stall_epsilon == 0.05
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alethic.py::TestCLI::test_cli_no_stall_reset_flag -v`
Expected: FAIL — `error: unrecognized arguments: --no-stall-reset`

**Step 3: Write minimal implementation**

In `src/alethic/cli.py`, add argument definitions after `--tools` (after line 158):

```python
    parser.add_argument(
        "--no-stall-reset",
        action="store_true",
        help="Disable stall-triggered strategy reset",
    )
    parser.add_argument(
        "--stall-window",
        type=int,
        default=None,
        help="Iterations without improvement before triggering reset (default: 2)",
    )
    parser.add_argument(
        "--stall-epsilon",
        type=float,
        default=None,
        help="Minimum confidence improvement to count as progress (default: 0.03)",
    )
```

Add to `_FLAG_TO_CONFIG` (line 163-174):

```python
    "stall_window": "stall_window",
    "stall_epsilon": "stall_epsilon",
```

Add to `_build_config` (after the `tool_guidance` block, line 194):

```python
    if args.no_stall_reset:
        overrides["stall_reset"] = False
```

Add to `_FLAGS_WITH_VALUE` (line 212-227):

```python
    "--stall-window",
    "--stall-epsilon",
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alethic.py::TestCLI -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/alethic/cli.py tests/test_alethic.py
git commit -m "feat: add CLI flags for stall reset configuration"
```

---

### Task 10: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update the CLAUDE.md**

Add the new CLI flags to the Dev Commands section:

```
alethic --no-stall-reset "Is 17 prime?"                    # disable stall detection
alethic --stall-window 3 --stall-epsilon 0.05 "..."        # custom stall parameters
```

Update the Module Map table entry for `agent.py` to mention stall detection.

Update the Module Map table entry for `models.py` to mention `stall_window`, `stall_epsilon`, `stall_reset`, `reset_n_boost`.

Add a row to the Presets table for the new stall fields.

Update Key Design Decisions with a new item about stall detection.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect stall reset feature"
```

---

### Task 11: Final full test run and verify

**Step 1: Run full test suite with coverage**

Run: `pytest tests/ --cov=alethic -v`
Expected: All PASS, coverage should include the new stall detection code paths

**Step 2: Run linter**

Run: `ruff check src tests`
Expected: No errors

**Step 3: Run type checker**

Run: `mypy src/alethic`
Expected: No errors (or only pre-existing ones)
