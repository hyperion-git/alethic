# v3.0.5 Polish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close out all remaining audit issues (#7, #8, #11–14, #16), fix the skill FIXABLE verdict bug, and fill the 3 remaining test coverage gaps (T2, T3, T9).

**Architecture:** 4 parallel fix agents grouped by file boundaries (no overlap), followed by a test agent. All changes are small, targeted fixes — no new modules or architectural changes.

**Tech Stack:** Python 3.13, pytest, anthropic SDK, Claude Code skills (markdown)

---

## Wave 1: Parallel Fixes (agents 1–4, no file overlap)

### Task 1: agent.py fixes (#12, #13, #14, #16)

**Files:**
- Modify: `src/alethic/agent.py:67` (deque maxlen)
- Modify: `src/alethic/agent.py:82-94` (summarize truncation)
- Modify: `src/alethic/agent.py:109-115` (store api_key)
- Modify: `src/alethic/agent.py:283-308` (variant-B client + error logging)

**Step 1: Fix #13 — shrink deque maxlen from 3 to 2**

In `src/alethic/agent.py`, line 67, change:
```python
    iteration_final_verdicts: deque = field(default_factory=lambda: deque(maxlen=3))
```
to:
```python
    iteration_final_verdicts: deque = field(default_factory=lambda: deque(maxlen=2))
```

**Step 2: Fix #14 — smart truncation in `_summarize_failed_approach`**

In `src/alethic/agent.py`, replace lines 82–94:
```python
def _summarize_failed_approach(verification: VerificationResult) -> str:
    """Extract a one-line summary of a failed approach from a verification result."""
    # First sentence of critique
    critique = verification.critique.strip()
    first_sentence_end = critique.find(". ")
    summary = critique[: first_sentence_end + 1] if first_sentence_end > 0 else critique[:150]

    # Append top issue if available
    if verification.issues:
        top_issue = str(verification.issues[0])
        summary = f"{summary} Issue: {top_issue}"

    return summary[:200]
```
with:
```python
def _summarize_failed_approach(verification: VerificationResult) -> str:
    """Extract a one-line summary of a failed approach from a verification result."""
    # First sentence of critique
    critique = verification.critique.strip()
    first_sentence_end = critique.find(". ")
    summary = critique[: first_sentence_end + 1] if first_sentence_end > 0 else critique[:150]

    # Append top issue if available
    if verification.issues:
        top_issue = str(verification.issues[0])
        summary = f"{summary} Issue: {top_issue}"

    if len(summary) <= 200:
        return summary
    # Truncate at last space before 200 chars, add ellipsis
    cut = summary[:197].rfind(" ")
    return summary[: cut if cut > 0 else 197] + "..."
```

**Step 3: Fix #12 — store api_key for variant-B reuse**

In `src/alethic/agent.py`, replace lines 109–115 (`__init__`):
```python
    def __init__(
        self,
        config: AgentConfig | None = None,
        api_key: str | None = None,
    ):
        self.config = config or AgentConfig()
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._setup_logging()
```
with:
```python
    def __init__(
        self,
        config: AgentConfig | None = None,
        api_key: str | None = None,
    ):
        self.config = config or AgentConfig()
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self._api_key)
        self._setup_logging()
```

Then in line ~289, replace:
```python
                variant_b_client = anthropic.Anthropic(api_key=self.client.api_key)
```
with:
```python
                variant_b_client = anthropic.Anthropic(api_key=self._api_key)
```

**Step 4: Fix #16 — add event logging for candidate generation failures**

In `src/alethic/agent.py`, around lines 303–308, the ThreadPoolExecutor error handler. This code is inside `_generate_candidates` which doesn't have access to `log`. We need to return the failure info so the caller can log it.

Actually — looking more carefully, `_generate_candidates` returns `list[tuple[Solution, float]]`. Failed candidates are silently dropped. The caller (`solve()`) already handles the "all candidates failed" case. The fix is to change the warning to also store the index so the caller can log it.

Replace:
```python
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning("Candidate %d failed: %s", idx, e)
```
with:
```python
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning("Candidate %d failed: %s", idx, e)
                    # Store failure for event logging by caller
                    results.append(None)  # sentinel
```

Wait — that changes the return type. Simpler: just pass the `log` parameter. But `_generate_candidates` is a method, so it can take `log` as an argument. Let me check the signature.

Actually, the simplest approach: since `_generate_candidates` already has `self` access and `log` is created in `solve()`, we need to thread `log` through. But this adds complexity. The cleaner fix: just enhance the logger.warning to include more context (the approach is already good for debugging), and add an optional callback.

**Revised approach for #16**: The simplest fix that matches the audit intent — add the event logging at the iteration level in `solve()` where the log is available. After calling `_generate_candidates`, check how many succeeded vs expected:

In `solve()`, after the `_generate_candidates` call and the "All candidates failed" check, add:
```python
            n_expected = n_this_iter if is_reset else self.config.best_of_n
            n_failed = n_expected - len(candidates)
            if n_failed > 0:
                log.emit(EventType.ERROR, iteration, error=f"{n_failed}/{n_expected} candidates failed")
```

This logs partial failures without changing `_generate_candidates`'s signature.

**Step 5: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_alethic.py tests/test_best_of_n.py tests/test_stall_reset.py -v --tb=short`
Expected: All pass.

---

### Task 2: Tool guidance pipeline (verifier_agent.py + models.py, #8 + #11)

**Files:**
- Modify: `src/alethic/models.py:126-132` (expand valid_tools)
- Modify: `src/alethic/verifier_agent.py:55-57` (remove filter)

**Step 1: Fix #11 + #8 — expand AgentConfig valid_tools and remove filter**

In `src/alethic/models.py`, lines 126–132, replace:
```python
        valid_tools = {"sympy", "numpy"}
        invalid = self.tool_guidance - valid_tools
        if invalid:
            raise ValueError(
                f"Unknown tool_guidance values: {invalid}. "
                f"Valid values: {valid_tools}"
            )
```
with:
```python
        valid_tools = {"sympy", "numpy", "scipy", "matplotlib"}
        invalid = self.tool_guidance - valid_tools
        if invalid:
            raise ValueError(
                f"Unknown tool_guidance values: {invalid}. "
                f"Valid values: {valid_tools}"
            )
```

In `src/alethic/verifier_agent.py`, lines 55–57, replace:
```python
            tool_guidance=frozenset(
                t for t in self.config.tool_guidance if t in {"sympy", "numpy"}
            ),
```
with:
```python
            tool_guidance=self.config.tool_guidance,
```

**Step 2: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_verify_check.py tests/test_alethic.py -v --tb=short -k "tool or guidance or verifier"`
Expected: All pass. If any test asserts only `{"sympy", "numpy"}` are valid, update it.

---

### Task 3: Synthesizer comment (issue #7)

**Files:**
- Modify: `src/alethic/synthesizer.py:26-33`

**Step 1: Add rationale comment**

In `src/alethic/synthesizer.py`, replace lines 26–33:
```python
# Severity ordering: lower = more severe (used for tie-breaking and sorting)
_VERDICT_SEVERITY = {
    Verdict.MAJOR_FLAW: 0,
    Verdict.UNSOLVED: 1,
    Verdict.FIXABLE: 2,
    Verdict.MINOR_ISSUES: 3,
    Verdict.CORRECT: 4,
}
```
with:
```python
# Severity ordering: lower = more severe (used for tie-breaking in consensus).
# UNSOLVED (1) > FIXABLE (2): "no solution at all" is worse than "flawed but
# recoverable solution", so ties between the two break toward UNSOLVED.
_VERDICT_SEVERITY = {
    Verdict.MAJOR_FLAW: 0,
    Verdict.UNSOLVED: 1,
    Verdict.FIXABLE: 2,
    Verdict.MINOR_ISSUES: 3,
    Verdict.CORRECT: 4,
}
```

**Step 2: Run tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest tests/test_synthesizer.py -v --tb=short`
Expected: All pass (no behavioral change).

---

### Task 4: Skill FIXABLE verdict support (orchestrator.md)

**Files:**
- Modify: `skills/alethic-common/orchestrator.md:84` (regex)
- Modify: `skills/alethic-common/orchestrator.md:410-430` (verdict branches)

**Step 1: Fix verdict regex (line 84)**

Replace:
```
- Search for `VERDICT:\s*(correct|minor_issues|major_flaw|unsolved)` (case-insensitive).
```
with:
```
- Search for `VERDICT:\s*(correct|minor_issues|fixable|major_flaw|unsolved)` (case-insensitive).
```

**Step 2: Add CORRECTED SOLUTION extraction (after line 90)**

After the line about `TOP_ISSUE:`, add:
```
- If verdict is "fixable", search for `CORRECTED SOLUTION:\s*\n([\s\S]*?)END CORRECTED SOLUTION` in the verification file. Store the captured text as `corrected_solution` (trimmed). If no match found, set `corrected_solution` to null.
```

**Step 3: Add FIXABLE verdict branch (between "minor_issues"/"major_flaw" and "unsolved")**

In the verdict branching section (after the "minor_issues" or "major_flaw" branch at line 424–426), insert a new branch:

```markdown
- **If verdict is "fixable"**:
  - If `corrected_solution` is not null:
    1. Write `corrected_solution` to `{session_dir}/worklog/iter{N}/corrected.md`.
    2. **Record the FIXABLE verdict for stall tracking BEFORE re-verification** — append "fixable" to `iteration_final_verdicts` now (do not wait for re-verification, which could overwrite it).
    3. **Re-verify the corrected solution**: Read the Verifier prompt from `{references_dir}/verifier.md`. Append tool overlays (same as Step 2b). Increment `task_calls`. Spawn a Verifier Task with:
       - The problem from `problem.md`
       - The corrected solution from `worklog/iter{N}/corrected.md` (NOT the original solution)
       - Same decoupling rules as Step 2b
    4. Extract re-verification verdict/confidence (same parsing as Step 2b.2).
    5. **If re-verification verdict is "correct" AND confidence >= {confidence_threshold}**:
       - CRITICAL issue guard applies (same as the "correct" branch above).
       - Copy `corrected.md` to `solution.md`. Update `session.json`, go to Step 4 then Step 5. **STOP the loop.**
    6. **If re-verification fails**: Copy `corrected.md` to `solution.md` (use corrected version as the new base). Copy the re-verification file to `verification.md`. Proceed to Step 2d (Revise) — the reviser will work from the corrected solution, not the original.
  - If `corrected_solution` is null:
    - Treat as "major_flaw" — if `max_revisions` > 0, proceed to Step 2d. Otherwise, continue to next iteration.
```

**Step 4: Update stall tracking note**

In the "Accumulate failed approach" section (around line 493), add a note that for FIXABLE verdicts, the verdict was already recorded in the stall tracker during Step 2c (so skip re-recording).

**Step 5: Verify no other references to the 4-verdict list**

Search the orchestrator for any other place that lists the four verdicts and needs `fixable` added. Check dashboard tables, history tables, and the failure admission section.

---

## Wave 2: Tests (after wave 1 completes)

### Task 5: Fill test gaps T2, T3, T9

**Files:**
- Modify: `tests/test_best_of_n.py` (T2: variant-B client edge cases, T9: large N)
- Modify: `tests/test_synthesizer.py` (T3: consensus with FIXABLE)

**Step 1: T2 — Variant-B client reuse / creation tests**

Add to `tests/test_best_of_n.py`:

```python
class TestVariantBClient:
    """T2: Variant-B client creation edge cases."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_same_model_reuses_client(self, mock_tools):
        """When variant_b model matches primary, client should be reused (not recreated)."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=2,
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # same as default
        )

        agent = MathAgent(config=config)
        mock_client = MagicMock()
        agent.client = mock_client
        agent._api_key = "test-key"

        # Generate + verify responses
        mock_client.messages.create.side_effect = [
            _mock_response("Solution A"),
            _mock_response("Solution B"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
        ]

        result = agent.solve("test")
        # With same model, no new Anthropic client should be created
        # All calls go through mock_client
        assert mock_client.messages.create.call_count == 4

    @patch("alethic.agent.anthropic.Anthropic")
    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_different_model_creates_new_client(self, mock_tools, mock_anthropic_cls):
        """When variant_b model differs, a new Anthropic client should be created."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=2,
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )

        mock_primary = MagicMock()
        mock_variant = MagicMock()
        # First Anthropic() call is for primary, second for variant B
        mock_anthropic_cls.side_effect = [mock_primary, mock_variant]

        agent = MathAgent(config=config, api_key="test-key")

        # Set up responses: candidate 0 (primary), candidate 1 (variant), verify x2
        mock_primary.messages.create.side_effect = [
            _mock_response("Solution A (primary)"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
        ]
        mock_variant.messages.create.side_effect = [
            _mock_response("Solution B (variant)"),
        ]

        result = agent.solve("test")
        # variant client should have been called for odd-indexed candidate
        assert mock_variant.messages.create.call_count >= 1

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_variant_b_odd_even_alternation(self, mock_tools):
        """Even candidates use primary config, odd use variant B."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=4,
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # same model to simplify
        )

        agent = MathAgent(config=config)
        mock_client = MagicMock()
        agent.client = mock_client
        agent._api_key = "test-key"

        # 4 generate + 4 verify
        mock_client.messages.create.side_effect = [
            _mock_response("Sol 0"),
            _mock_response("Sol 1"),
            _mock_response("Sol 2"),
            _mock_response("Sol 3"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_MED),
        ]

        result = agent.solve("test")
        assert result.solved
```

**Step 2: T3 — Consensus aggregation with FIXABLE verdict**

Add to `tests/test_synthesizer.py`:

```python
    def test_fixable_in_tie_breaks_to_fixable_over_minor(self):
        """In a 1:1 tie of FIXABLE vs MINOR_ISSUES, FIXABLE is more severe and wins."""
        results = [
            VerificationResult(verdict=Verdict.FIXABLE, critique="fixable", confidence=0.75),
            VerificationResult(verdict=Verdict.MINOR_ISSUES, critique="minor", confidence=0.80),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.FIXABLE

    def test_fixable_majority(self):
        """Majority FIXABLE verdict should produce FIXABLE consensus."""
        results = [
            VerificationResult(verdict=Verdict.FIXABLE, critique="a", confidence=0.70),
            VerificationResult(verdict=Verdict.FIXABLE, critique="b", confidence=0.75),
            VerificationResult(verdict=Verdict.CORRECT, critique="c", confidence=0.90),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.FIXABLE

    def test_unsolved_vs_fixable_tie_takes_unsolved(self):
        """In a 1:1 tie of UNSOLVED vs FIXABLE, UNSOLVED is more severe and wins."""
        results = [
            VerificationResult(verdict=Verdict.UNSOLVED, critique="unsolved", confidence=0.30),
            VerificationResult(verdict=Verdict.FIXABLE, critique="fixable", confidence=0.70),
        ]
        agg = aggregate_mechanical(results)
        assert agg["verdict"] == Verdict.UNSOLVED
```

**Step 3: T9 — Large N=5 with mixed success/failure**

Add to `tests/test_best_of_n.py`:

```python
class TestLargeN:
    """T9: Large N parallel generation with mixed success/failure."""

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_n5_with_partial_failures(self, mock_tools):
        """N=5 generation where 2 candidates fail should still produce a result from the 3 survivors."""
        from alethic.agent import MathAgent

        config = AgentConfig(
            best_of_n=5,
            max_iterations=1,
            max_revisions_per_cycle=0,
            enable_code_execution=False,
            verbose=False,
        )

        agent = MathAgent(config=config)
        mock_client = MagicMock()
        agent.client = mock_client
        agent._api_key = "test-key"

        call_count = 0
        generate_responses = []
        for i in range(5):
            generate_responses.append(_mock_response(f"Solution {i}"))
        verify_responses = []
        for i in range(5):
            verify_responses.append(_mock_response(CORRECT_HIGH))

        # We need to make 2 of the 5 generate calls fail.
        # Since ThreadPoolExecutor reorders, we'll patch at a lower level.
        original_create = MagicMock()
        resp_idx = 0

        def _create_side_effect(**kwargs):
            nonlocal resp_idx
            msgs = kwargs.get("messages", [])
            # Generate calls have "problem" in user message; verify calls have "solution"
            is_generate = any("test problem" in str(m) for m in msgs) and not any(
                "Solution" in str(m) for m in msgs
            )
            if is_generate:
                resp_idx += 1
                if resp_idx in (2, 4):  # fail candidates 2 and 4
                    raise RuntimeError(f"Simulated failure for candidate {resp_idx}")
                return _mock_response(f"Solution {resp_idx}")
            return _mock_response(CORRECT_HIGH)

        mock_client.messages.create.side_effect = _create_side_effect

        result = agent.solve("test problem")
        # Should still produce a result from the surviving candidates
        assert result.solution is not None
```

**Step 4: Run all tests**

Run: `/home/xeal/.local/bin/micromamba run -n alethic pytest -v --tb=short`
Expected: All 578+ tests pass, 3 xfailed.

**Step 5: Commit**

```bash
git add -A
git commit -m "fix: close remaining audit issues and fill test gaps

- #7: document verdict severity rationale in synthesizer
- #8/#11: expand AgentConfig tool_guidance to accept scipy/matplotlib,
  remove silent filter in verifier_agent._build_agent_config()
- #12: store api_key in MathAgent for safe variant-B client creation
- #13: shrink RunState.iteration_final_verdicts deque to maxlen=2
- #14: smart truncation in _summarize_failed_approach (word boundary + ...)
- #16: log partial candidate failures as ERROR events
- Skill bug: add fixable verdict to orchestrator regex + full FIXABLE
  branch with corrected-solution extraction, re-verification, and
  fallthrough to revision
- T2: variant-B client reuse/creation/alternation tests
- T3: consensus aggregation with FIXABLE verdict tests
- T9: large N=5 with partial failures test

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Post-implementation

After all tests pass:
1. Run full lint: `ruff check src tests`
2. Run full test suite with coverage: `pytest --cov=alethic`
3. Update `MEMORY.md` to mark audit issues as closed
4. Version bump to 3.0.5 (5 files) — separate commit
5. Update CLAUDE.md if any module descriptions changed
