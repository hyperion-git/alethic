# Vibefeld-Inspired Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add failed approach tracking, issue-level severity, per-section confidence, and structured event logging to the Alethic GVR loop, with a RunState/EventLog restructure of the orchestrator.

**Architecture:** Replace loose local variables in `solve()` with `RunState` (mutable control-flow state) and `EventLog` (append-only audit). New `Issue`, `SectionConfidence`, and `AgentEvent` types extend the verification and result models. Generator gets failed approach summaries; Verifier outputs severity-tagged issues and per-section confidence; Reviser targets low-confidence sections. All changes preserve decoupled verification.

**Tech Stack:** Python 3.13, dataclasses, pytest (mocked API), ruff, mypy

**Run commands in:** `/home/xeal/.local/bin/micromamba run -n alethic <command>`

**Test command:** `pytest /home/xeal/dev/alethic -v --tb=short`

**Lint command:** `ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`

---

## Commit 1: New Types + RunState/EventLog Restructure

This commit introduces all new data types, restructures `agent.py` to use `RunState`/`EventLog`, and updates all existing tests to work with the new types.

### Task 1.1: Add IssueSeverity, Issue, SectionConfidence, EventType, AgentEvent to models.py

**Files:**
- Modify: `src/alethic/models.py:1-17` (add new enums and dataclasses after Verdict)

**Step 1: Write failing tests for new types**

Create file `tests/test_new_types.py`:

```python
"""Tests for new data types: Issue, SectionConfidence, AgentEvent."""

from __future__ import annotations

from alethic.models import (
    AgentEvent,
    EventType,
    Issue,
    IssueSeverity,
    SectionConfidence,
)


class TestIssueSeverity:
    def test_values(self):
        assert IssueSeverity.CRITICAL.value == "critical"
        assert IssueSeverity.MAJOR.value == "major"
        assert IssueSeverity.MINOR.value == "minor"


class TestIssue:
    def test_construction(self):
        issue = Issue(text="Sign error in step 3")
        assert issue.text == "Sign error in step 3"
        assert issue.severity == IssueSeverity.MAJOR  # default
        assert issue.addressed is False

    def test_explicit_severity(self):
        issue = Issue(text="Division by zero", severity=IssueSeverity.CRITICAL)
        assert issue.severity == IssueSeverity.CRITICAL

    def test_str_returns_text(self):
        issue = Issue(text="Missing justification")
        assert str(issue) == "Missing justification"

    def test_str_in_fstring(self):
        issue = Issue(text="Off by one")
        assert f"- {issue}" == "- Off by one"

    def test_frozen(self):
        import pytest
        issue = Issue(text="test")
        with pytest.raises(AttributeError):
            issue.text = "changed"  # type: ignore[misc]

    def test_equality(self):
        a = Issue(text="err", severity=IssueSeverity.MINOR)
        b = Issue(text="err", severity=IssueSeverity.MINOR)
        assert a == b

    def test_addressed_flag(self):
        from dataclasses import replace
        issue = Issue(text="err")
        fixed = replace(issue, addressed=True)
        assert fixed.addressed is True
        assert issue.addressed is False


class TestSectionConfidence:
    def test_construction(self):
        sc = SectionConfidence(section="base case", confidence=0.95)
        assert sc.section == "base case"
        assert sc.confidence == 0.95
        assert sc.note == ""

    def test_with_note(self):
        sc = SectionConfidence(section="induction step", confidence=0.55, note="gap in logic")
        assert sc.note == "gap in logic"


class TestEventType:
    def test_values(self):
        assert EventType.GENERATE.value == "generate"
        assert EventType.VERIFY.value == "verify"
        assert EventType.REVISE.value == "revise"
        assert EventType.ERROR.value == "error"
        assert EventType.ACCEPT.value == "accept"
        assert EventType.FAIL.value == "fail"


class TestAgentEvent:
    def test_construction(self):
        event = AgentEvent(type=EventType.GENERATE, iteration=1, data={"candidate": 1})
        assert event.type == EventType.GENERATE
        assert event.iteration == 1
        assert event.data == {"candidate": 1}

    def test_timestamp_auto(self):
        import time
        before = time.time()
        event = AgentEvent(type=EventType.VERIFY, iteration=1)
        after = time.time()
        assert before <= event.timestamp <= after

    def test_empty_data_default(self):
        event = AgentEvent(type=EventType.ERROR, iteration=2)
        assert event.data == {}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py -v`
Expected: ImportError (types don't exist yet)

**Step 3: Implement new types in models.py**

Add after the `Verdict` class (line 17), before `AgentConfig`:

```python
class IssueSeverity(enum.Enum):
    """Severity level for individual verification issues."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True)
class Issue:
    """A single issue found by the Verifier, with severity tracking."""

    text: str
    severity: IssueSeverity = IssueSeverity.MAJOR
    addressed: bool = False

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class SectionConfidence:
    """Per-section confidence from the Verifier."""

    section: str
    confidence: float
    note: str = ""


class EventType(enum.Enum):
    """Type of event in the agent's execution log."""

    GENERATE = "generate"
    VERIFY = "verify"
    REVISE = "revise"
    ERROR = "error"
    ACCEPT = "accept"
    FAIL = "fail"


@dataclass(frozen=True)
class AgentEvent:
    """A single event in the agent's execution log."""

    type: EventType
    iteration: int
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py -v`
Expected: All PASS

---

### Task 1.2: Update VerificationResult to use Issue and SectionConfidence

**Files:**
- Modify: `src/alethic/models.py:133-161` (VerificationResult)

**Step 1: Write failing tests**

Add to `tests/test_new_types.py`:

```python
from alethic.models import Verdict, VerificationResult


class TestVerificationResultIssueType:
    def test_issues_are_issue_objects(self):
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Problems",
            confidence=0.7,
            issues=[Issue(text="Sign error"), Issue(text="Missing case", severity=IssueSeverity.MINOR)],
        )
        assert len(vr.issues) == 2
        assert isinstance(vr.issues[0], Issue)
        assert vr.issues[0].text == "Sign error"
        assert vr.issues[1].severity == IssueSeverity.MINOR

    def test_is_acceptable_blocks_on_critical(self):
        vr = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="Mostly good",
            confidence=0.95,
            issues=[Issue(text="Fatal flaw", severity=IssueSeverity.CRITICAL)],
        )
        assert not vr.is_acceptable()  # CRITICAL blocks acceptance

    def test_is_acceptable_allows_non_critical(self):
        vr = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="Good",
            confidence=0.95,
            issues=[Issue(text="Minor typo", severity=IssueSeverity.MINOR)],
        )
        assert vr.is_acceptable()

    def test_section_confidences(self):
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="OK",
            confidence=0.8,
            section_confidences=[
                SectionConfidence(section="setup", confidence=0.95),
                SectionConfidence(section="induction step", confidence=0.55, note="gap"),
            ],
        )
        assert len(vr.section_confidences) == 2
        assert vr.section_confidences[1].confidence == 0.55

    def test_str_format_with_issue_objects(self):
        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="Bad",
            confidence=0.2,
            issues=[Issue(text="Division by zero")],
        )
        text = str(vr)
        assert "Division by zero" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_new_types.py::TestVerificationResultIssueType -v`
Expected: TypeError on construction (issues expects list[str])

**Step 3: Update VerificationResult in models.py**

Change `issues` type and add `section_confidences`. Update `is_acceptable`:

```python
@dataclass
class VerificationResult:
    """Result from the Verifier subagent."""

    verdict: Verdict
    critique: str
    confidence: float
    issues: list[Issue] = field(default_factory=list)
    reason: str = ""
    section_confidences: list[SectionConfidence] = field(default_factory=list)

    def is_acceptable(self, threshold: float = 0.90) -> bool:
        has_critical = any(
            getattr(issue, "severity", None) == IssueSeverity.CRITICAL
            for issue in self.issues
        )
        return (
            self.verdict == Verdict.CORRECT
            and self.confidence >= threshold
            and not has_critical
        )

    def needs_revision(self, threshold: float = 0.90) -> bool:
        return self.verdict in (Verdict.MINOR_ISSUES, Verdict.MAJOR_FLAW) or (
            self.verdict == Verdict.CORRECT and self.confidence < threshold
        )

    def __str__(self) -> str:
        lines = [
            f"Verdict: {self.verdict.value}",
            f"Confidence: {self.confidence:.0%}",
            f"Critique: {self.critique}",
        ]
        if self.issues:
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
        return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_new_types.py -v`
Expected: All PASS

---

### Task 1.3: Update AgentResult to use events + deprecated history property

**Files:**
- Modify: `src/alethic/models.py:174-214` (AgentResult)

**Step 1: Write failing tests**

Add to `tests/test_new_types.py`:

```python
import warnings


class TestAgentResultEvents:
    def test_events_field(self):
        from alethic.models import AgentResult
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=[
                AgentEvent(type=EventType.GENERATE, iteration=1, data={"candidate": 1, "solution_preview": "..."}),
                AgentEvent(type=EventType.VERIFY, iteration=1, data={"candidate": 1, "verdict": "correct", "confidence": 0.95, "num_issues": 0}),
            ],
        )
        assert len(result.events) == 2
        assert result.events[0].type == EventType.GENERATE

    def test_history_property_backward_compat(self):
        from alethic.models import AgentResult
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=[
                AgentEvent(type=EventType.GENERATE, iteration=1, data={"candidate": 1, "solution_preview": "abc"}),
                AgentEvent(type=EventType.VERIFY, iteration=1, data={"candidate": 1, "verdict": "correct", "confidence": 0.95, "num_issues": 0}),
            ],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            history = result.history
        assert isinstance(history, list)
        assert all(isinstance(h, dict) for h in history)
        assert history[0]["phase"] == "generate"
        assert history[0]["iteration"] == 1
        assert history[0]["candidate"] == 1

    def test_history_property_warns(self):
        from alethic.models import AgentResult
        result = AgentResult(
            problem="test", solution=None, verdict=Verdict.UNSOLVED,
            confidence=0.0, iterations_used=0, total_revisions=0,
            admitted_failure=True,
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = result.history
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "events" in str(w[0].message)

    def test_failed_approaches_field(self):
        from alethic.models import AgentResult
        result = AgentResult(
            problem="test", solution="answer", verdict=Verdict.CORRECT,
            confidence=0.95, iterations_used=2, total_revisions=1,
            admitted_failure=False,
            failed_approaches=["Tried induction on n, but base case fails for n=0"],
        )
        assert len(result.failed_approaches) == 1
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestAgentResultEvents -v`
Expected: TypeError (events kwarg doesn't exist)

**Step 3: Update AgentResult in models.py**

Replace the `history` field with `events`, add `failed_approaches`, add deprecated `history` property:

```python
@dataclass
class AgentResult:
    """Final result from the Alethic agent's solve() method."""

    problem: str
    solution: str | None
    verdict: Verdict
    confidence: float
    iterations_used: int
    total_revisions: int
    admitted_failure: bool
    events: list[AgentEvent] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    candidates_per_iteration: int = 1
    failed_approaches: list[str] = field(default_factory=list)

    @property
    def history(self) -> list[dict]:
        """Backward-compatible dict view of events. Deprecated: use .events instead."""
        import warnings
        warnings.warn(
            "AgentResult.history is deprecated; use AgentResult.events instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return [
            {"phase": e.type.value, "iteration": e.iteration, **e.data}
            for e in self.events
        ]

    @property
    def solved(self) -> bool:
        return self.verdict == Verdict.CORRECT and self.solution is not None

    def __str__(self) -> str:
        status = "SOLVED" if self.solved else "UNSOLVED"
        lines = [
            f"{'=' * 60}",
            f"Result: {status}",
            f"Confidence: {self.confidence:.0%}",
            f"Iterations: {self.iterations_used}",
            f"Total revisions: {self.total_revisions}",
        ]
        if self.candidates_per_iteration > 1:
            lines.append(f"Candidates per iteration: {self.candidates_per_iteration}")
        if self.failed_approaches:
            lines.append(f"Failed approaches: {len(self.failed_approaches)}")
        lines.extend([
            f"Time: {self.elapsed_seconds:.1f}s",
            f"{'=' * 60}",
        ])
        if self.solution:
            lines.append("")
            lines.append(self.solution)
        elif self.admitted_failure:
            lines.append("")
            lines.append("[Agent admitted failure — problem could not be solved reliably]")
        return "\n".join(lines)
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py -v`
Expected: All PASS

---

### Task 1.4: Update __init__.py exports

**Files:**
- Modify: `src/alethic/__init__.py`

**Step 1: Update exports**

Add new types to imports and `__all__`:

```python
from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    EventType,
    Issue,
    IssueSeverity,
    Revision,
    SectionConfidence,
    Solution,
    Verdict,
    VerificationResult,
)

__all__ = [
    "MathAgent",
    "PhysicsAgent",
    "AgentConfig",
    "AgentEvent",
    "AgentResult",
    "EventType",
    "Issue",
    "IssueSeverity",
    "Revision",
    "SectionConfidence",
    "Solution",
    "Verdict",
    "VerificationResult",
]

__version__ = "1.0.0"  # bumped to 2.0.0 in commit 4
```

**Step 2: Verify imports work**

Run: `python -c "from alethic import Issue, IssueSeverity, SectionConfidence, AgentEvent, EventType; print('OK')"`
Expected: `OK`

---

### Task 1.5: Add RunState and EventLog to agent.py, refactor solve()

**Files:**
- Modify: `src/alethic/agent.py` (full restructure of solve loop)

**Step 1: Write failing test for RunState/EventLog**

Add to `tests/test_new_types.py`:

```python
class TestRunStateAndEventLog:
    def test_run_state_defaults(self):
        from alethic.agent import RunState
        state = RunState()
        assert state.total_revisions == 0
        assert state.best_solution is None
        assert state.best_confidence == 0.0
        assert state.failed_approaches == []

    def test_event_log_emit(self):
        from alethic.agent import EventLog
        log = EventLog()
        log.emit(EventType.GENERATE, iteration=1, candidate=1, chars=500)
        assert len(log.events) == 1
        assert log.events[0].type == EventType.GENERATE
        assert log.events[0].data["candidate"] == 1
        assert log.events[0].data["chars"] == 500
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestRunStateAndEventLog -v`
Expected: ImportError

**Step 3: Add RunState and EventLog classes to agent.py**

Add after the imports, before `MathAgent`:

```python
from alethic.models import AgentConfig, AgentEvent, AgentResult, EventType, Solution, Verdict, VerificationResult


@dataclass
class RunState:
    """Mutable state accumulated across iterations of the GVR loop."""

    total_revisions: int = 0
    best_solution: Solution | None = None
    best_confidence: float = 0.0
    failed_approaches: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)


@dataclass
class EventLog:
    """Append-only event log for the GVR loop."""

    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type: EventType, iteration: int, **data) -> None:
        self.events.append(AgentEvent(type=type, iteration=iteration, data=data))
```

Add the `dataclass` and `field` imports at the top of agent.py:

```python
from dataclasses import dataclass, field
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py::TestRunStateAndEventLog -v`
Expected: PASS

**Step 5: Refactor solve() to use RunState and EventLog**

This is the largest change. Refactor `solve()` in `agent.py` to:
1. Create `state = RunState()` and `log = EventLog()` at the start
2. Replace all `history.append({...})` with `log.emit(...)` calls
3. Replace `total_revisions`, `best_solution`, `best_confidence` locals with `state.*`
4. Update `_run_revision_loop` signature to take `state` and `log`, return `AgentResult | None`
5. Update all `AgentResult(...)` construction to use `events=log.events` instead of `history=history`
6. Pass `start_time=state.start_time` where `start_time` was used

Key changes to `_run_revision_loop`:
- Remove `total_revisions`, `history`, `start_time`, `best_solution`, `best_confidence` parameters
- Add `state: RunState` and `log: EventLog` parameters
- Mutate `state` directly instead of returning tuples
- Return `AgentResult | None` instead of `AgentResult | tuple[int, Solution | None, float]`

Key changes to `_check_false_premise`:
- Remove `total_revisions`, `history`, `start_time` parameters
- Add `state: RunState` and `log: EventLog` parameters

**Step 6: Run full test suite**

Run: `pytest /home/xeal/dev/alethic -v --tb=short`
Expected: Several tests fail (tests that access `result.history` or construct `VerificationResult(issues=["str"])`)

---

### Task 1.6: Fix existing tests that break

**Files:**
- Modify: `tests/test_alethic.py`
- Modify: `tests/test_adversarial_prompts.py`
- Modify: `tests/test_adversarial_backward_compat.py`
- Modify: `tests/test_physics.py`
- Modify: `tests/test_best_of_n.py`

**Step 1: Fix `result.issues[0].lower()` patterns (3 locations)**

In `tests/test_alethic.py`:
- Line 304: `result.issues[0].lower()` → `result.issues[0].text.lower()`
- Line 343: `result.issues[0].lower()` → `result.issues[0].text.lower()`

In `tests/test_adversarial_prompts.py`:
- Line 872: `"Gap in step 3" in result.issues[0]` → `"Gap in step 3" in result.issues[0].text`

**Step 2: Fix `issues=["str"]` construction patterns (~14 locations)**

Wherever tests construct `VerificationResult(issues=["str"])`, change to `VerificationResult(issues=[Issue(text="str")])`.

Add the import `from alethic.models import Issue` to each affected test file.

Affected files and lines:
- `tests/test_adversarial_backward_compat.py:205`: `issues=["Step 2 is wrong"]` → `issues=[Issue(text="Step 2 is wrong")]`
- `tests/test_adversarial_prompts.py`: lines 153, 172, 233, 304, 518, 576, 642, 703, 734, 765, 788 — all `issues=["err"]` or similar → `issues=[Issue(text="err")]`
- `tests/test_physics.py:397`: `issues=["Bug"]` → `issues=[Issue(text="Bug")]`

**Step 3: Fix `result.history` access patterns (4 locations)**

In `tests/test_alethic.py:595`:
```python
# Old: error_entries = [h for h in result.history if h.get("phase") == "error"]
# New: use events directly
error_entries = [e for e in result.events if e.type == EventType.ERROR]
assert len(error_entries) == 1
assert error_entries[0].iteration == 1
```

In `tests/test_best_of_n.py`:
- Line 435-438: Replace `result.history` access with `result.events` access:
  ```python
  gen_entries = [e for e in result.events if e.type == EventType.GENERATE]
  assert len(gen_entries) == 2
  assert gen_entries[0].data["candidate"] == 1
  assert gen_entries[1].data["candidate"] == 2
  ```
- Line 441-444: Same pattern for verify entries
- Line 468-470: Same pattern for N=1 case

Add `from alethic.models import EventType` to affected test files.

**Step 4: Fix `issue[:100]` in agent.py:413**

Change: `self._log(f"  Issue: {issue[:100]}")` → `self._log(f"  Issue: {str(issue)[:100]}")`

**Step 5: Run full test suite**

Run: `pytest /home/xeal/dev/alethic -v --tb=short`
Expected: All 309+ tests PASS

**Step 6: Lint**

Run: `ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`
Expected: No errors

**Step 7: Commit**

```bash
git add src/alethic/models.py src/alethic/agent.py src/alethic/__init__.py tests/
git commit -m "Add Issue, SectionConfidence, AgentEvent types; restructure agent with RunState/EventLog

Introduce IssueSeverity enum + Issue dataclass replacing list[str] in
VerificationResult.issues. Add SectionConfidence for per-section
verification. Add EventType enum + AgentEvent replacing list[dict] in
AgentResult (history field deprecated with DeprecationWarning).

Restructure agent.py: replace loose local variables with RunState
(mutable control-flow state) and EventLog (append-only audit).
Simplify _run_revision_loop to return AgentResult | None, mutating
state directly.

BREAKING: VerificationResult.issues is now list[Issue], not list[str].
BREAKING: AgentResult.history renamed to .events (list[AgentEvent]).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Commit 2: Failed Approach Tracking

### Task 2.1: Add _summarize_failed_approach function

**Files:**
- Modify: `src/alethic/agent.py` (add helper function)

**Step 1: Write failing tests**

Add to `tests/test_new_types.py`:

```python
class TestSummarizeFailedApproach:
    def test_extracts_first_sentence_and_top_issue(self):
        from alethic.agent import _summarize_failed_approach
        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="The proof attempts to use induction on n. However, the base case fails because f(0) is undefined. The inductive step also has a gap.",
            confidence=0.15,
            issues=[Issue(text="Base case fails: f(0) undefined"), Issue(text="Gap in inductive step")],
        )
        summary = _summarize_failed_approach(vr)
        assert len(summary) <= 200
        assert "induction" in summary.lower()

    def test_handles_empty_issues(self):
        from alethic.agent import _summarize_failed_approach
        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="Fundamentally wrong approach.",
            confidence=0.1,
        )
        summary = _summarize_failed_approach(vr)
        assert len(summary) > 0
        assert len(summary) <= 200

    def test_handles_long_critique(self):
        from alethic.agent import _summarize_failed_approach
        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="A" * 500 + ". Second sentence.",
            confidence=0.1,
        )
        summary = _summarize_failed_approach(vr)
        assert len(summary) <= 200
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestSummarizeFailedApproach -v`
Expected: ImportError

**Step 3: Implement _summarize_failed_approach in agent.py**

Add as a module-level function before `MathAgent`:

```python
def _summarize_failed_approach(verification: VerificationResult) -> str:
    """Extract a one-line summary of a failed approach from a verification result."""
    # First sentence of critique
    critique = verification.critique.strip()
    first_sentence_end = critique.find(". ")
    if first_sentence_end > 0:
        summary = critique[: first_sentence_end + 1]
    else:
        summary = critique[:150]

    # Append top issue if available
    if verification.issues:
        top_issue = str(verification.issues[0])
        summary = f"{summary} Issue: {top_issue}"

    return summary[:200]
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py::TestSummarizeFailedApproach -v`
Expected: PASS

---

### Task 2.2: Wire failed approaches into generate() and the orchestrator

**Files:**
- Modify: `src/alethic/subagents.py:143-195` (generate function)
- Modify: `src/alethic/agent.py` (orchestrator loop)

**Step 1: Write failing tests**

Add to `tests/test_new_types.py`:

```python
class TestFailedApproachInGenerate:
    def test_generate_accepts_failed_approaches(self):
        """generate() should accept and use failed_approaches kwarg."""
        from unittest.mock import MagicMock, patch
        from alethic.subagents import generate

        config = AgentConfig(enable_code_execution=False)
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Solution text"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("alethic.subagents.process_tool_calls", return_value=[]):
            sol = generate(
                mock_client,
                problem="Prove P",
                config=config,
                iteration=2,
                failed_approaches=("Tried induction, base case fails",),
            )
        assert sol.solution_text == "Solution text"

        # Check the user message includes failed approaches
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Previously attempted" in user_msg
        assert "induction" in user_msg

    def test_generate_no_failed_approaches(self):
        """generate() with empty failed_approaches should not add the section."""
        from unittest.mock import MagicMock, patch
        from alethic.subagents import generate

        config = AgentConfig(enable_code_execution=False)
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Solution"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("alethic.subagents.process_tool_calls", return_value=[]):
            generate(mock_client, problem="Prove P", config=config, iteration=1)

        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Previously attempted" not in user_msg
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestFailedApproachInGenerate -v`
Expected: TypeError (unexpected kwarg `failed_approaches`)

**Step 3: Update generate() in subagents.py**

Add `failed_approaches` parameter:

```python
def generate(
    client,
    problem: str,
    config: AgentConfig,
    iteration: int,
    balanced: bool = True,
    *,
    failed_approaches: tuple[str, ...] = (),
    system_prompt: str | None = None,
    user_template: str | None = None,
    balanced_addendum: str | None = None,
) -> Solution:
```

Add before the `_call_model` call, after constructing `user_msg`:

```python
    if failed_approaches:
        approaches_text = "\n".join(f"- {a}" for a in failed_approaches)
        user_msg += (
            f"\n\n## Previously attempted strategies that did NOT work:\n"
            f"{approaches_text}\n"
            f"Avoid repeating these approaches. Try a fundamentally different strategy."
        )
```

**Step 4: Update orchestrator to accumulate and pass failed approaches**

In `agent.py`, in `_generate_candidates`, add `failed_approaches` parameter and pass it to `generate()`:

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
) -> list[tuple[Solution, float]]:
```

Pass to `generate()` inside `_gen_one()`:

```python
        def _gen_one() -> tuple[Solution, float]:
            t0 = time.time()
            sol = generate(
                self.client,
                problem=problem,
                config=self.config,
                iteration=iteration,
                balanced=balanced,
                failed_approaches=failed_approaches,
                system_prompt=prompts.get("generator_system"),
                user_template=prompts.get("generator_user"),
                balanced_addendum=prompts.get("balanced_addendum"),
            )
            return sol, time.time() - t0
```

In `solve()`, at the `_generate_candidates` call site, pass the snapshot:

```python
                candidates = self._generate_candidates(
                    problem=problem,
                    iteration=iteration,
                    balanced=balanced,
                    prompts=prompts,
                    n=n,
                    failed_approaches=tuple(state.failed_approaches),
                )
```

At the end of a failed iteration (before `continue` to next iteration), accumulate:

```python
                # After revision loop exhausted or solution unsolvable:
                summary = _summarize_failed_approach(verification)
                state.failed_approaches.append(summary)
```

In `AgentResult` construction at failure admission, include:
```python
            failed_approaches=state.failed_approaches,
```

**Step 5: Run full test suite**

Run: `pytest /home/xeal/dev/alethic -v --tb=short`
Expected: All PASS

**Step 6: Lint**

Run: `ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`

**Step 7: Commit**

```bash
git add src/alethic/agent.py src/alethic/subagents.py tests/
git commit -m "Add failed approach tracking across GVR iterations

Generator receives one-line summaries of previously failed strategies,
preventing repeated dead-end approaches. Summaries are extracted from
the Verifier critique (first sentence + top issue) and accumulated in
RunState.failed_approaches. Passed as an immutable tuple snapshot to
_generate_candidates to preserve the ThreadPoolExecutor concurrency
boundary.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Commit 3: Issue Severity + Per-Section Confidence

### Task 3.1: Update Verifier prompts to request severity tags and section confidences

**Files:**
- Modify: `src/alethic/prompts.py:76-104` (VERIFIER_SYSTEM)
- Modify: `src/alethic/physics_prompts.py:112-140` (PHYSICS_VERIFIER_SYSTEM)

**Step 1: Write failing tests**

Add to `tests/test_new_types.py`:

```python
class TestVerifierPromptSeverity:
    def test_math_verifier_requests_severity_tags(self):
        from alethic.prompts import VERIFIER_SYSTEM
        assert "[CRITICAL]" in VERIFIER_SYSTEM
        assert "[MAJOR]" in VERIFIER_SYSTEM
        assert "[MINOR]" in VERIFIER_SYSTEM

    def test_physics_verifier_requests_severity_tags(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM
        assert "[CRITICAL]" in PHYSICS_VERIFIER_SYSTEM
        assert "[MAJOR]" in PHYSICS_VERIFIER_SYSTEM
        assert "[MINOR]" in PHYSICS_VERIFIER_SYSTEM

    def test_math_verifier_requests_section_confidences(self):
        from alethic.prompts import VERIFIER_SYSTEM
        assert "SECTION CONFIDENCES:" in VERIFIER_SYSTEM

    def test_physics_verifier_requests_section_confidences(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM
        assert "SECTION CONFIDENCES:" in PHYSICS_VERIFIER_SYSTEM
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestVerifierPromptSeverity -v`
Expected: AssertionError

**Step 3: Update prompts**

In both `VERIFIER_SYSTEM` and `PHYSICS_VERIFIER_SYSTEM`, replace the ISSUES format section with:

```
ISSUES:
- [CRITICAL] Issue requiring fundamental rework
- [MAJOR] Serious gap or error
- [MINOR] Small imprecision or stylistic concern
(Tag each issue with severity. Write "None" if there are no issues)

SECTION CONFIDENCES:
- [section name]: [0.0-1.0] [optional note]
(Omit this section if the solution is too short to decompose into sections)
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py::TestVerifierPromptSeverity -v`
Expected: PASS

---

### Task 3.2: Update _parse_verification() for hybrid severity and section confidences

**Files:**
- Modify: `src/alethic/subagents.py:211-283` (_parse_verification)

**Step 1: Write failing tests for severity parsing**

Add to `tests/test_new_types.py`:

```python
from alethic.subagents import _parse_verification


class TestSeverityParsing:
    def test_tagged_issues(self):
        text = (
            "VERDICT: major_flaw\nCONFIDENCE: 0.2\n\n"
            "CRITIQUE:\nBad proof.\n\n"
            "ISSUES:\n"
            "- [CRITICAL] Division by zero in step 3\n"
            "- [MINOR] Missing parentheses in notation\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 2
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[0].text == "Division by zero in step 3"
        assert result.issues[1].severity == IssueSeverity.MINOR

    def test_untagged_issues_default_major(self):
        text = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.7\n\n"
            "CRITIQUE:\nAlmost.\n\n"
            "ISSUES:\n- Step 2 needs justification\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 1
        assert result.issues[0].severity == IssueSeverity.MAJOR  # default
        assert result.issues[0].text == "Step 2 needs justification"

    def test_mixed_tagged_and_untagged(self):
        text = (
            "VERDICT: major_flaw\nCONFIDENCE: 0.3\n\n"
            "CRITIQUE:\nProblems.\n\n"
            "ISSUES:\n"
            "- [CRITICAL] Logic error\n"
            "- Missing edge case\n"
            "- [MINOR] Typo\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 3
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[1].severity == IssueSeverity.MAJOR
        assert result.issues[2].severity == IssueSeverity.MINOR

    def test_case_insensitive_tags(self):
        text = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.6\n\n"
            "CRITIQUE:\nOK.\n\n"
            "ISSUES:\n- [critical] Bad step\n- [Minor] Typo\n"
        )
        result = _parse_verification(text)
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[1].severity == IssueSeverity.MINOR

    def test_unknown_tag_defaults_major(self):
        text = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.6\n\n"
            "CRITIQUE:\nOK.\n\n"
            "ISSUES:\n- [WARNING] Something odd\n"
        )
        result = _parse_verification(text)
        assert result.issues[0].severity == IssueSeverity.MAJOR
        assert result.issues[0].text == "Something odd"

    def test_none_issues_still_empty(self):
        text = "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert len(result.issues) == 0


class TestSectionConfidenceParsing:
    def test_section_confidences_parsed(self):
        text = (
            "VERDICT: minor_issues\nCONFIDENCE: 0.75\n\n"
            "CRITIQUE:\nMostly good.\n\n"
            "ISSUES:\n- [MINOR] Typo\n\n"
            "SECTION CONFIDENCES:\n"
            "- Setup: 0.95\n"
            "- Induction step: 0.55 gap in logic\n"
            "- Conclusion: 0.90\n"
        )
        result = _parse_verification(text)
        assert len(result.section_confidences) == 3
        assert result.section_confidences[0].section == "Setup"
        assert result.section_confidences[0].confidence == 0.95
        assert result.section_confidences[1].confidence == 0.55
        assert result.section_confidences[1].note == "gap in logic"

    def test_missing_section_confidences(self):
        text = "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nGood.\n\nISSUES:\nNone"
        result = _parse_verification(text)
        assert len(result.section_confidences) == 0

    def test_malformed_section_confidence_skipped(self):
        text = (
            "VERDICT: correct\nCONFIDENCE: 0.9\n\n"
            "CRITIQUE:\nOK.\n\nISSUES:\nNone\n\n"
            "SECTION CONFIDENCES:\n"
            "- Valid section: 0.95\n"
            "- Malformed line without colon\n"
            "- Another valid: 0.80 note here\n"
        )
        result = _parse_verification(text)
        assert len(result.section_confidences) == 2
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestSeverityParsing tests/test_new_types.py::TestSectionConfidenceParsing -v`
Expected: AssertionError (issues are still strings, no section_confidences)

**Step 3: Update _parse_verification() in subagents.py**

Add `_parse_section_confidences` helper and update the issue parsing loop:

```python
def _parse_section_confidences(text: str) -> list:
    """Parse SECTION CONFIDENCES block from verifier output."""
    from alethic.models import SectionConfidence

    match = re.search(
        r"SECTION CONFIDENCES:\s*\n(.*?)(?=\n[A-Z]+:|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if not match:
        return []

    results = []
    for line in match.group(1).strip().split("\n"):
        cleaned = line.strip().lstrip("- ").strip()
        if not cleaned:
            continue
        # Pattern: "section name: 0.85 optional note"
        sc_match = re.match(r"(.+?):\s*([\d.]+)\s*(.*)", cleaned)
        if sc_match:
            section = sc_match.group(1).strip()
            try:
                conf = float(sc_match.group(2))
                conf = max(0.0, min(1.0, conf))
            except ValueError:
                continue
            note = sc_match.group(3).strip()
            results.append(SectionConfidence(section=section, confidence=conf, note=note))
    return results
```

In `_parse_verification()`, update the issues parsing block. Replace lines 260-268 with:

```python
    issues: list = []
    if issues_match:
        raw_issues = issues_match.group(1).strip()
        if raw_issues.lower() != "none":
            for line in raw_issues.split("\n"):
                cleaned = line.strip().lstrip("- ").strip()
                if not cleaned:
                    continue
                # Try to parse severity tag: [CRITICAL], [MAJOR], [MINOR]
                severity_tag_match = re.match(r"\[(\w+)\]\s*(.*)", cleaned)
                if severity_tag_match:
                    tag = severity_tag_match.group(1).upper()
                    issue_text = severity_tag_match.group(2).strip()
                    severity = _SEVERITY_MAP.get(tag, IssueSeverity.MAJOR)
                else:
                    issue_text = cleaned
                    severity = IssueSeverity.MAJOR
                if issue_text:
                    issues.append(Issue(text=issue_text, severity=severity))

    section_confidences = _parse_section_confidences(text)
```

Add at module level:

```python
from alethic.models import Issue, IssueSeverity, SectionConfidence

_SEVERITY_MAP: dict[str, IssueSeverity] = {
    "CRITICAL": IssueSeverity.CRITICAL,
    "MAJOR": IssueSeverity.MAJOR,
    "MINOR": IssueSeverity.MINOR,
}
```

Update the return statement to include `section_confidences`:

```python
    return VerificationResult(
        verdict=verdict,
        critique=critique,
        confidence=confidence,
        issues=issues,
        reason=reason,
        section_confidences=section_confidences,
    )
```

Also update the ISSUES regex to stop at SECTION CONFIDENCES:

```python
    issues_match = re.search(
        r"ISSUES:\s*\n(.*?)(?=\nREASON:|\nSECTION CONFIDENCES:|\Z)", text, re.DOTALL | re.IGNORECASE
    )
```

And update the CRITIQUE regex similarly:

```python
    critique_match = re.search(
        r"CRITIQUE:\s*\n(.*?)(?=\nREASON:|\nISSUES:|\nSECTION CONFIDENCES:|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py::TestSeverityParsing tests/test_new_types.py::TestSectionConfidenceParsing -v`
Expected: PASS

---

### Task 3.3: Update Reviser to target low-confidence sections

**Files:**
- Modify: `src/alethic/subagents.py:370-434` (revise function)

**Step 1: Write failing test**

Add to `tests/test_new_types.py`:

```python
class TestReviserSectionTargeting:
    def test_reviser_includes_low_confidence_sections(self):
        from unittest.mock import MagicMock, patch
        from alethic.subagents import revise

        config = AgentConfig(enable_code_execution=False)
        solution = Solution(problem="P", solution_text="text", iteration=1)
        verification = VerificationResult(
            verdict=Verdict.MINOR_ISSUES, critique="Needs work", confidence=0.7,
            issues=[Issue(text="gap")],
            section_confidences=[
                SectionConfidence(section="Setup", confidence=0.95),
                SectionConfidence(section="Induction step", confidence=0.45, note="gap in argument"),
            ],
        )

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "CHANGES MADE:\nFixed\n\nREVISED SOLUTION:\nBetter"
        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        with patch("alethic.subagents.process_tool_calls", return_value=[]):
            revise(mock_client, "P", solution, verification, config, 1)

        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Low-confidence sections" in user_msg
        assert "Induction step" in user_msg
        assert "0.45" in user_msg
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_new_types.py::TestReviserSectionTargeting -v`
Expected: AssertionError (section info not in prompt)

**Step 3: Update revise() to include section confidence info**

In `revise()` in `subagents.py`, after constructing `user_msg` (around line 404), add:

```python
    # Add low-confidence section targeting if available
    low_conf_sections = [
        sc for sc in verification.section_confidences if sc.confidence < 0.70
    ]
    if low_conf_sections:
        sections_text = "\n".join(
            f"- {sc.section}: {sc.confidence:.2f}" + (f" ({sc.note})" if sc.note else "")
            for sc in low_conf_sections
        )
        user_msg += (
            f"\n\n## Low-confidence sections (focus revision here):\n{sections_text}"
        )
```

**Step 4: Run to verify pass**

Run: `pytest tests/test_new_types.py::TestReviserSectionTargeting -v`
Expected: PASS

**Step 5: Run full test suite + lint**

Run: `pytest /home/xeal/dev/alethic -v --tb=short && ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`
Expected: All PASS, no lint errors

**Step 6: Commit**

```bash
git add src/alethic/subagents.py src/alethic/prompts.py src/alethic/physics_prompts.py tests/
git commit -m "Add issue severity (hybrid parsing) and per-section confidence

Verifier prompt now requests severity tags ([CRITICAL]/[MAJOR]/[MINOR])
on each issue and per-section confidence scores. Parser uses hybrid
approach: extracts tags when present, defaults to MAJOR for untagged
issues.

CRITICAL issues block acceptance in is_acceptable() regardless of
confidence score. Reviser receives low-confidence sections (< 0.70)
as targeted revision guidance.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Commit 4: Event Log + Version Bump

### Task 4.1: Update CLI JSON output to include events

**Files:**
- Modify: `src/alethic/cli.py:260-273`

**Step 1: Write failing test**

Add to `tests/test_new_types.py`:

```python
class TestCLIEventOutput:
    def test_json_output_includes_events(self):
        import json
        from alethic.models import AgentResult
        result = AgentResult(
            problem="test", solution="answer", verdict=Verdict.CORRECT,
            confidence=0.95, iterations_used=1, total_revisions=0,
            admitted_failure=False,
            events=[AgentEvent(type=EventType.GENERATE, iteration=1, timestamp=1.0, data={"candidate": 1})],
            failed_approaches=["Tried X"],
        )
        # Simulate CLI JSON serialization
        output = {
            "problem": result.problem,
            "solved": result.solved,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "iterations_used": result.iterations_used,
            "total_revisions": result.total_revisions,
            "candidates_per_iteration": result.candidates_per_iteration,
            "admitted_failure": result.admitted_failure,
            "elapsed_seconds": result.elapsed_seconds,
            "solution": result.solution,
            "failed_approaches": result.failed_approaches,
            "events": [
                {"type": e.type.value, "iteration": e.iteration, "timestamp": e.timestamp, **e.data}
                for e in result.events
            ],
        }
        serialized = json.dumps(output)
        parsed = json.loads(serialized)
        assert parsed["failed_approaches"] == ["Tried X"]
        assert parsed["events"][0]["type"] == "generate"
```

**Step 2: Run to verify pass (this is a schema test, should pass)**

Run: `pytest tests/test_new_types.py::TestCLIEventOutput -v`

**Step 3: Update cli.py JSON output**

In `cli.py`, update the JSON output dict to include new fields:

```python
        output = {
            "problem": result.problem,
            "solved": result.solved,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "iterations_used": result.iterations_used,
            "total_revisions": result.total_revisions,
            "candidates_per_iteration": result.candidates_per_iteration,
            "admitted_failure": result.admitted_failure,
            "elapsed_seconds": result.elapsed_seconds,
            "solution": result.solution,
            "failed_approaches": result.failed_approaches,
            "events": [
                {"type": e.type.value, "iteration": e.iteration, "timestamp": e.timestamp, **e.data}
                for e in result.events
            ],
        }
```

---

### Task 4.2: Version bump and documentation updates

**Files:**
- Modify: `pyproject.toml:7` (version)
- Modify: `src/alethic/__init__.py:53` (__version__)
- Modify: `CLAUDE.md` (module map)
- Modify: `README.md` (module reference, known limitations)

**Step 1: Bump version**

In `pyproject.toml`:
```
version = "2.0.0"
```

In `src/alethic/__init__.py`:
```python
__version__ = "2.0.0"
```

**Step 2: Update CLAUDE.md module map**

Add to the module table:

```
| `models.py` | Dataclasses: `AgentConfig` (with `PRESETS`, `from_preset()`, and `best_of_n` field), `Solution`, `VerificationResult` (with `Issue`, `SectionConfidence`, severity-aware `is_acceptable()`), `Revision`, `AgentResult` (with `AgentEvent` list, `failed_approaches`), `Verdict` enum, `IssueSeverity` enum, `EventType` enum |
| `agent.py` | `MathAgent` orchestrator — runs the Generate N → Verify all → Select best → Revise loop with best-of-N sampling (parallel via `ThreadPoolExecutor`), false-premise detection, candidate ranking, **failed approach tracking** via `RunState`, and **structured event logging** via `EventLog` |
```

**Step 3: Update README.md**

Update the `AgentResult` properties table to include:

```
| `events` | `list[AgentEvent]` | Structured event log for debugging and analysis |
| `failed_approaches` | `list[str]` | One-line summaries of strategies that failed |
| `history` | `list[dict]` | *Deprecated* — backward-compatible dict view of events |
```

Add to Known Limitations:
```
**Issue severity depends on prompt compliance.** The Verifier is prompted to tag issues with severity levels ([CRITICAL], [MAJOR], [MINOR]). When the model does not produce tags, issues default to MAJOR severity. Critical issues block solution acceptance regardless of confidence score.
```

**Step 4: Run full test suite**

Run: `pytest /home/xeal/dev/alethic -v --tb=short`
Expected: All PASS

**Step 5: Lint**

Run: `ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`
Expected: No errors

**Step 6: Commit**

```bash
git add pyproject.toml src/alethic/__init__.py src/alethic/cli.py CLAUDE.md README.md tests/
git commit -m "Add event log to CLI output, bump version to 2.0.0

CLI JSON output now includes structured events and failed_approaches.
Version bumped to 2.0.0 for breaking changes: VerificationResult.issues
type (str -> Issue), AgentResult.history renamed to .events.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Post-Implementation Checklist

After all 4 commits:

1. Run full test suite: `pytest /home/xeal/dev/alethic -v --tb=short`
2. Run with coverage: `pytest /home/xeal/dev/alethic --cov=alethic`
3. Lint: `ruff check /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`
4. Format: `ruff format /home/xeal/dev/alethic/src /home/xeal/dev/alethic/tests`
5. Type check: `mypy /home/xeal/dev/alethic/src/alethic`
6. Verify imports: `python -c "from alethic import Issue, IssueSeverity, SectionConfidence, AgentEvent, EventType; print('OK')"`
7. Verify git log shows 4 clean commits
