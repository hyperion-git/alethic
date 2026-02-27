# v3.0.5 Polish Design

## Goal

Close out all remaining audit issues (#7, #8, #11–14, #16), fix the skill FIXABLE bug, and fill test coverage gaps T1–T9. Ship as v3.0.5.

## Execution Strategy

5 agents, grouped by file boundaries. Agents 1–4 run in parallel (no file overlap). Agent 5 runs after to write tests against the fixed code.

## Agent 1: `agent.py` (issues #12, #13, #14, #16)

### Fix #12: Variant-B client fragility
- Store `api_key` parameter in `MathAgent.__init__` as `self._api_key`
- Replace `api_key=self.client.api_key` (line ~289) with `api_key=self._api_key`
- If `api_key` was `None` (env var fallback), pass `api_key=None` to let the new client also read env

### Fix #13: Deque maxlen mismatch
- Change `deque(maxlen=3)` → `deque(maxlen=2)` in `RunState.iteration_final_verdicts` (line ~67)
- Behavior stays the same (trigger on 2 consecutive MAJOR_FLAWs), just removes the unused third slot

### Fix #14: Smart truncation in `_summarize_failed_approach`
- Replace the hard `[:200]` with truncation at last space before 200 chars + "..." suffix
- Keep the first-sentence + top-issue structure

### Fix #16: Error path event logging
- In the `ThreadPoolExecutor` exception handler (~line 305), add `log.emit(EventType.ERROR, iteration, candidate=idx, error=str(e))`
- The iteration-level `APIError` handler already logs adequately (no candidate context needed since it's outside the candidate loop)

## Agent 2: `verifier_agent.py` + `models.py` (issues #8, #11)

### Fix #11: scipy/matplotlib guidance propagation
- In `_build_agent_config()` (~line 55-57), remove the `if t in {"sympy", "numpy"}` filter — pass all tool_guidance through
- In `AgentConfig.__post_init__`, expand valid_tools from `{"sympy", "numpy"}` to `{"sympy", "numpy", "scipy", "matplotlib"}`
- This is safe because `_build_system_prompt()` looks up guidance from a map — if the map doesn't have a key for scipy/matplotlib (as in solve/derive's `TOOL_GUIDANCE`), no guidance is appended. For verify/check, `CHECK_TOOL_GUIDANCE` has all four keys.

### Fix #8: Clearer error message
- With the expanded valid set, this is partially resolved (scipy/matplotlib are now valid)
- Keep the validation but update the error message to list all valid tools

## Agent 3: `synthesizer.py` (issue #7)

### Fix #7: Document verdict severity rationale
- Add a comment block above `_VERDICT_SEVERITY` explaining: UNSOLVED (no solution) > FIXABLE (flawed but recoverable) in severity. Tie-breaks favor the more severe interpretation.
- No behavioral change.

## Agent 4: `orchestrator.md` (skill FIXABLE bug)

### Regex fix (line 84)
- Add `fixable` to: `VERDICT:\s*(correct|minor_issues|fixable|major_flaw|unsolved)`

### New extraction step
- After extracting verdict, if verdict is "fixable", also extract corrected solution:
  - Search for text between `CORRECTED SOLUTION:` and `END CORRECTED SOLUTION` in the verification file

### New verdict branch (between minor_issues/major_flaw and unsolved)
- **If verdict is "fixable" AND corrected solution found**:
  1. Write corrected solution to `worklog/iter{N}/corrected.md`
  2. Re-verify: spawn a fresh Verifier Task with the corrected solution
  3. Extract re-verification verdict/confidence
  4. If re-verification passes (correct + confidence >= threshold): accept → Step 4
  5. If re-verification fails: use corrected solution as the new solution, fall through to revision
- **If verdict is "fixable" WITHOUT corrected solution**:
  - Treat as "major_flaw", proceed to revision

### Stall tracking note
- Record the original FIXABLE verdict in the stall tracker BEFORE re-verification can overwrite it (mirrors Python library behavior from fix #4 in the audit)

## Agent 5: Tests (T1–T9, runs after agents 1–4)

| Gap | Test | File |
|-----|------|------|
| T1 | FIXABLE correction fails re-verification → falls through to reviser | `test_alethic.py` or `test_new_types.py` |
| T2 | Variant-B: same model reuses client, different model creates new client, odd/even alternation | `test_best_of_n.py` |
| T3 | Consensus aggregation when FIXABLE is one of K verdicts | `test_synthesizer.py` |
| T4 | Stall tracking after FIXABLE fallthrough produces correct state | `test_stall_reset.py` |
| T5 | Problem text with curly braces `{x | x > 0}` doesn't crash `.format()` | `test_alethic.py` |
| T6 | CORRECTED SOLUTION regex: solution containing `STEP ONE:`, empty correction, multiple blocks | `test_alethic.py` |
| T7 | Single verifier crash in consensus pipeline — pipeline continues with remaining results | `test_verify_check.py` |
| T8 | CLI `--no-variant-b` + `--variant-b-model` produces warning | `test_alethic.py` (CLI tests section) |
| T9 | Large N=5 generation with mixed success/failure candidates | `test_best_of_n.py` |

## Non-goals

- No version bump yet (do that after all fixes land and tests pass)
- No CLAUDE.md updates (defer to after implementation)
- No changes to the Python library's prompts or physics_prompts (already correct)
