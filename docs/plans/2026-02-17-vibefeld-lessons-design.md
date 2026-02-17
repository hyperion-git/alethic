# Design: Vibefeld-Inspired Improvements

Four improvements to Alethic's GVR loop, inspired by architectural lessons from Vibefeld's adversarial proof framework. All changes preserve Alethic's decoupled verification architecture.

## Delivery

Single PR, 4 commits. Version bump from 1.0.0 to 2.0.0 (breaking changes to `VerificationResult.issues` type and `AgentResult.history` field name).

---

## 1. New Types (`models.py`)

### IssueSeverity Enum

```python
class IssueSeverity(enum.Enum):
    CRITICAL = "critical"   # Blocks acceptance regardless of confidence
    MAJOR = "major"         # Serious flaw
    MINOR = "minor"         # Advisory
```

### Issue (replaces `str` in `VerificationResult.issues`)

```python
@dataclass(frozen=True)
class Issue:
    text: str
    severity: IssueSeverity = IssueSeverity.MAJOR
    addressed: bool = False

    def __str__(self) -> str:
        return self.text
```

Default severity is MAJOR (matches existing behavior where issues contribute to MINOR_ISSUES/MAJOR_FLAW verdicts). `__str__` returns `self.text` for backward-compatible prompt formatting and logging.

### SectionConfidence

```python
@dataclass(frozen=True)
class SectionConfidence:
    section: str           # e.g. "base case", "induction step"
    confidence: float
    note: str = ""
```

Lives on `VerificationResult` (per-verification data, not per-run).

### EventType Enum + AgentEvent

```python
class EventType(enum.Enum):
    GENERATE = "generate"
    VERIFY = "verify"
    REVISE = "revise"
    ERROR = "error"
    ACCEPT = "accept"
    FAIL = "fail"

@dataclass(frozen=True)
class AgentEvent:
    type: EventType
    iteration: int
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
```

Named `AgentEvent` (not `Event`) to avoid namespace collision. `EventType` enum (not string) for type safety and autocomplete.

### VerificationResult Changes

- `issues: list[str]` becomes `issues: list[Issue]`
- Add `section_confidences: list[SectionConfidence] = field(default_factory=list)`
- `is_acceptable()` gains CRITICAL guard:
  ```python
  def is_acceptable(self, threshold: float = 0.90) -> bool:
      has_critical = any(
          getattr(issue, "severity", None) == IssueSeverity.CRITICAL
          for issue in self.issues
      )
      return self.verdict == Verdict.CORRECT and self.confidence >= threshold and not has_critical
  ```
  Uses `getattr` guard for safety against legacy `str` issues.

### AgentResult Changes

- `history: list[dict]` renamed to `events: list[AgentEvent]`
- Deprecated `@property history` returns `list[dict]` with `DeprecationWarning`
- Add `failed_approaches: list[str] = field(default_factory=list)`

---

## 2. RunState + EventLog (`agent.py`)

Replace the loose local variables in `solve()` with two internal objects:

```python
@dataclass
class RunState:
    total_revisions: int = 0
    best_solution: Solution | None = None
    best_confidence: float = 0.0
    failed_approaches: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

@dataclass
class EventLog:
    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type: EventType, iteration: int, **data: Any) -> None:
        self.events.append(AgentEvent(type=type, iteration=iteration, data=data))
```

### Rationale for the split

- **RunState**: Mutable, accumulates across iterations. Informs control-flow decisions. Consumer: orchestrator.
- **EventLog**: Append-only audit records. No influence on control flow. Consumer: caller after `solve()` returns.

Separation prevents reading events to make decisions (layering violation) and allows `EventLog.emit()` to be made thread-safe independently.

### Concurrency boundary

`_generate_candidates()` receives `failed_approaches=tuple(state.failed_approaches)` as an **immutable snapshot**. Never receives a reference to `RunState`. The parallel `ThreadPoolExecutor` section stays pure. Mutation happens only in sequential orchestrator code.

### _run_revision_loop simplification

Signature changes from:
```
-> AgentResult | tuple[int, Solution | None, float]
```
to:
```
-> AgentResult | None  # mutates state + log directly
```

Caller simplifies from tuple unpacking to a None check.

---

## 3. Failed Approach Tracking

### Summary extraction

New function:
```python
def _summarize_failed_approach(verification: VerificationResult) -> str
```
Produces a one-liner from the critique (first sentence + top issue). Testable in isolation.

### Accumulation

`state.failed_approaches.append(summary)` at the end of each failed iteration (when loop restarts from Generator).

### Generator prompt injection

New optional parameter on `generate()`:
```python
def generate(..., *, failed_approaches: tuple[str, ...] = (), ...) -> Solution:
```

Conditional prompt section (appended only when non-empty):
```
## Previously attempted strategies that did NOT work:
{approaches}
Avoid repeating these approaches. Try a fundamentally different strategy.
```

Both `prompts.py` and `physics_prompts.py` get this addition. Decoupling is preserved: the Verifier never sees failed approach summaries.

### Exposed on result

`AgentResult.failed_approaches: list[str]` for post-hoc analysis.

---

## 4. Issue Severity (Hybrid Parsing)

### Verifier prompt change

Add to ISSUES format instructions in both math and physics verifier system prompts:
```
ISSUES:
- [CRITICAL] Issue requiring fundamental rework
- [MAJOR] Serious gap or error
- [MINOR] Small imprecision or stylistic concern
(Tag each issue with severity. Write "None" if no issues)
```

### Parser change

In `_parse_verification()`, per issue line:
```python
severity_match = re.match(r"\[(\w+)\]\s*(.*)", cleaned_line)
if severity_match:
    tag = severity_match.group(1).upper()
    text = severity_match.group(2)
    severity = {"CRITICAL": ..., "MAJOR": ..., "MINOR": ...}.get(tag, IssueSeverity.MAJOR)
else:
    severity = IssueSeverity.MAJOR  # hybrid fallback
    text = cleaned_line
```

Hybrid: tries prompt-based tags first, falls back to MAJOR for untagged issues.

---

## 5. Per-Section Confidence

### Verifier prompt change

New section after ISSUES in both math and physics verifier system prompts:
```
SECTION CONFIDENCES:
- [section name]: [0.0-1.0] [optional note]
(Omit this section if the solution is too short to decompose)
```

### Parser

New `_parse_section_confidences()` called by `_parse_verification()`. Regex: `r"- (.+?):\s*([\d.]+)\s*(.*)"`. Populates `VerificationResult.section_confidences`.

### Reviser prompt enhancement

When low-confidence sections exist, append to reviser user message:
```
## Low-confidence sections (focus revision here):
{sections}
```

---

## 6. Event Log

### Library

Events accumulate on `EventLog` during `solve()`, then transferred to `AgentResult.events`. The `AgentResult.history` property provides backward-compatible `list[dict]` access with deprecation warning.

### Skills

Write events to `{session}/worklog/events.jsonl` after each Task call. One JSON line per event:
```jsonl
{"type":"generate","iteration":1,"timestamp":1708000000.0,"candidate":1,"chars":3400,"elapsed":12.0}
{"type":"verify","iteration":1,"timestamp":1708000012.0,"candidate":1,"verdict":"minor_issues","confidence":0.82}
```

Skill SKILL.md files get updated instructions.

---

## 7. Version Bump + Migration

- `pyproject.toml`: version 1.0.0 -> 2.0.0
- Breaking: `VerificationResult.issues` type (`str` -> `Issue`), `AgentResult.history` field renamed to `events`
- Deprecated: `AgentResult.history` property with `DeprecationWarning`
- Update: `CLAUDE.md`, `README.md` module maps

---

## Commit Plan

| # | Commit | Files |
|---|--------|-------|
| 1 | New types + RunState/EventLog restructure | `models.py`, `agent.py`, `tests/` |
| 2 | Failed approach tracking | `agent.py`, `subagents.py`, `prompts.py`, `physics_prompts.py`, `tests/` |
| 3 | Issue severity + per-section confidence | `subagents.py`, `prompts.py`, `physics_prompts.py`, `models.py`, `tests/` |
| 4 | Event log + version bump | `agent.py`, `cli.py`, `skills/`, `pyproject.toml`, `README.md`, `CLAUDE.md` |

---

## Expert Panel Notes

Design validated by 5-agent panel (API design, backward compat, performance, testing, architecture). Key decisions:

- **`Issue` as dataclass** (not str subclass) with `__str__` returning text. Clean break, 2.0.0 semver.
- **`AgentEvent`** (not `Event`) with `EventType` enum (not string). Avoids namespace collision.
- **Split RunState/EventLog**. Different lifetimes, different concurrency needs.
- **Parallel section stays pure**. Snapshot `tuple(state.failed_approaches)` passed to `_generate_candidates`.
- **Section confidences on `VerificationResult`**, not RunState. Per-verification data.
- **Performance is a non-issue**. Total overhead under 0.1ms against minutes of API calls.
- **~20 existing tests need updating, ~50 new tests needed**. Suite grows from ~309 to ~360.
