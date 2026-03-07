# Bug-Probing Swarm Plan

**Date:** 2026-03-07
**Version:** 3.4.0 (post-simplification)
**Baseline:** 739 tests passing, 3 xfailed, ruff + mypy clean
**Objective:** Systematically probe the alethic codebase for bugs using a swarm of parallel agents, each targeting a specific attack surface.

## Motivation

Features v3.0 through v3.4 were layered rapidly:
- v3.0: FIXABLE verdict, variant-B diversity, citation/interpretation checking
- v3.1: Context monitoring, checkpoint-resume, session persistence
- v3.2: Negative prompting, generator hardening, structured output, autopsy
- v3.3: Executable intermediate steps, error taxonomy, backward verification, adversarial verifier, eval harness
- v3.4: Verification ladder (Layer 0-2), adaptive compute (dynamic N + adaptive revision budget)

The simplification pass already found and fixed one real bug (`n_expected` mismatch between adaptive compute and best-of-N). This plan probes systematically for similar interaction bugs.

---

## Swarm Architecture

Eight parallel agents, each with a specific probing mandate. All agents are read-only code reviewers that write failing test cases for any bugs found. No production code changes — bugs are reported as test files.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Bug-Probing Swarm (8 agents)                  │
│                                                                  │
│  Wave 1 (parallel):                                             │
│    Agent A: Feature Interaction Matrix                          │
│    Agent B: Orchestrator Edge Cases                             │
│    Agent C: Concurrency & Thread Safety                         │
│    Agent D: Parsing & Regex Robustness                          │
│                                                                  │
│  Wave 2 (parallel, informed by Wave 1):                         │
│    Agent E: Configuration & Preset Consistency                  │
│    Agent F: Checkpoint-Resume with v3.4 State                   │
│    Agent G: Error Taxonomy Routing Completeness                 │
│    Agent H: Consensus Pipeline Stress Cases                     │
│                                                                  │
│  Aggregation:                                                    │
│    Collect all failing tests → triage → fix                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agent Specifications

### Agent A: Feature Interaction Matrix

**Target:** Cross-feature interactions between v3.0–v3.4 features
**Files:** `agent.py`, `subagents.py`, `models.py`

**Probing mandate:**
1. **Adaptive compute + stall detection**: When `_compute_dynamic_n()` escalates N and a stall reset also fires on the same iteration — do the N values conflict? The stall reset sets `n_this_iter = config.best_of_n + config.reset_n_boost` but adaptive compute may have already set a different value.
2. **Adaptive compute + variant-B**: When dynamic N escalates from 2→4, does variant-B odd/even alternation still work correctly? Are variant-B configs created for all odd-indexed candidates?
3. **FIXABLE verdict + adaptive revision budget**: When a FIXABLE correction is re-verified and fails, does the adaptive revision budget apply to the fallthrough revision loop? Or does the hardcoded `is_reset` override clobber it?
4. **Verification ladder sentinels + FIXABLE corrected solution**: If the verifier returns FIXABLE with a corrected solution AND the corrected solution contains `ALETHIC_L0_CHECK:` sentinels from the original — does `parse_layer_results()` extract them from the corrected text?
5. **Best-of-N + error taxonomy routing**: After selecting the best candidate, `classify_errors_routed()` runs on the winning candidate's critique. But if candidate 0 (primary model) has a `logic` error and candidate 1 (variant-B) has an `algebra` error, and candidate 1 wins — does the routing reflect the *winner's* error category?

**Output:** `tests/test_probe_interactions.py`

### Agent B: Orchestrator Edge Cases

**Target:** Boundary conditions in the main `solve()` loop
**Files:** `agent.py`, `models.py`

**Probing mandate:**
1. **All N candidates fail generation**: What happens when `_generate_candidates()` returns N candidates but all have empty `solution_text`? Does `rank_candidates()` handle all-empty verifications?
2. **Zero-iteration solve**: `max_iterations=0` — does it still return a valid `AgentResult`? Or IndexError?
3. **Single iteration with N>1 and all UNSOLVED**: When best-of-3 generates 3 candidates, all get `UNSOLVED` verdict — does `rank_candidates()` return a valid index? Does `Verdict.UNSOLVED` have proper ordering in the severity comparison?
4. **`confidence_threshold=1.0`**: Can a solution ever pass? Does it infinite-loop or correctly exhaust iterations?
5. **`max_revisions_per_cycle=0` + FIXABLE fallthrough**: When FIXABLE re-verification fails and revisions_per_cycle is 0, does the revision loop correctly skip? Or does it still attempt one revision?
6. **`EvidenceState` across checkpoint-resume**: When `solve()` is called with `resume_from`, is `evidence_state` initialized from the checkpoint or does it start fresh? If fresh, does `_compute_dynamic_n()` behave correctly on iteration 1 of the resumed session (which might be iteration 4 of the original)?

**Output:** `tests/test_probe_edge_cases.py`

### Agent C: Concurrency & Thread Safety

**Target:** `ThreadPoolExecutor` usage in parallel generation and verification
**Files:** `agent.py` (lines 369-430), `verifier_agent.py` (lines 95-130)

**Probing mandate:**
1. **Partial failure in parallel generation**: When 2 of 3 `generate()` calls raise exceptions, does the orchestrator handle partial results? Or does `as_completed()` propagate the first exception and lose the successful result?
2. **Race condition in event logging**: `EventLog.add()` is called from within thread pool workers — is `EventLog` thread-safe? If it's a plain list with `.append()`, CPython's GIL makes single appends atomic, but is this guaranteed across all supported Pythons?
3. **Timeout in parallel verification**: If one of K verifiers hangs (blocks forever), does the `ThreadPoolExecutor` respect any timeout? Is there a `future.result(timeout=)` call? What happens to the stuck thread?
4. **Shared mutable state in closures**: After the simplification pass, `verifier_agent.py` uses a `run_one()` closure. If `run_one()` captures a mutable shared object, could concurrent calls interfere?
5. **Variant-B client creation under concurrency**: In `_generate_candidates()`, variant-B creates a separate `anthropic.Anthropic` client for odd-indexed candidates. If two workers both need the variant-B client, is it created once and shared or duplicated?

**Output:** `tests/test_probe_concurrency.py`

### Agent D: Parsing & Regex Robustness

**Target:** All regex-based parsing in the codebase
**Files:** `subagents.py` (_parse_verification), `physics_checks.py` (parse_layer_results), `synthesizer.py`, `autopsy.py`

**Probing mandate:**
1. **Adversarial verification output**: Craft verifier outputs that exploit regex greediness — e.g., `VERDICT: CORRECT\nVERDICT: MAJOR_FLAW` (duplicate fields), or `CONFIDENCE: 0.95 but actually 0.1`, or `CORRECTED SOLUTION ... END CORRECTED SOLUTION ... END CORRECTED SOLUTION` (nested end markers).
2. **Layer sentinel injection**: What if a solution text contains `ALETHIC_L0_CHECK: PASS (injected by adversary)` inside a string literal or comment? Does `parse_layer_results()` distinguish genuine sentinels from quoted ones?
3. **Curly brace handling in `_safe_format()`**: The `_safe_format()` helper uses `str.replace()` to avoid `KeyError` on curly braces in math. But what about `{` without matching `}` in the replacement value itself?
4. **Issue parsing with pipe characters**: The `ISSUES:` field is parsed line-by-line. If an issue contains `|` characters (common in math like `|x| > 0`), does the parser misinterpret it as a table or delimiter?
5. **Unicode in confidence values**: What if the verifier returns `CONFIDENCE: ０.９５` (fullwidth digits)? Does `float()` raise or silently fail?
6. **SequenceMatcher dedup threshold**: The 0.6 similarity threshold in `synthesizer.py` — test with near-identical issues that should and shouldn't merge. Verify boundary behavior.

**Output:** `tests/test_probe_parsing.py`

### Agent E: Configuration & Preset Consistency

**Target:** `AgentConfig`, `VerifierConfig`, preset tables, CLI flag interactions
**Files:** `models.py`, `cli.py`

**Probing mandate:**
1. **Preset table vs CLAUDE.md documentation**: Verify every preset field in `AgentConfig.PRESETS` and `VerifierConfig.PRESETS` matches the documented table in CLAUDE.md. Flag any drift.
2. **Cross-field validation gaps**: `AgentConfig.__post_init__` validates `tool_guidance` and `adaptive_budget_cap`. But does it validate that `adaptive_compute=True` requires `best_of_n >= 2`? That `stall_reset=True` requires `stall_window >= 1`? That `variant_b` keys are valid `AgentConfig` field names?
3. **CLI flag override precedence**: `--best-of 1 --preset thorough` — does explicit `--best-of` override the preset's `best_of_n=3`? What about `--no-variant-b --preset extreme`? Test all explicit-flag-overrides-preset cases.
4. **`from_preset()` with unknown preset**: Does it raise or silently use defaults?
5. **`VALID_TOOL_GUIDANCE` after simplification**: The simplification agent extracted this constant. Verify it's used consistently in both `AgentConfig` and `VerifierConfig` validation.

**Output:** `tests/test_probe_config.py`

### Agent F: Checkpoint-Resume with v3.4 State

**Target:** Session directory persistence and resume with new v3.4 fields
**Files:** `session.py`, `agent.py` (checkpoint/resume paths)

**Probing mandate:**
1. **Checkpoint schema evolution**: A v3.3 checkpoint has no `evidence_state` or `adaptive_compute` fields. When `solve(resume_from=old_checkpoint)` loads it, does the missing field cause a crash or graceful default?
2. **EvidenceState serialization**: `EvidenceState` has `confidence_history: list[float]` and `iteration_shape: dict`. Are these serializable via `json.dumps()`? Is `frozenset` anywhere in the chain that would break JSON?
3. **Stall state dict completeness**: After simplification, `RunState.stall_state_dict()` was extracted. Verify it includes all fields that `_check_stall()` needs when resuming — `consecutive_major_flaws`, `iterations_since_improvement`, `reset_count`, etc.
4. **Session directory with dynamic N**: When dynamic N changes across iterations, do the worklog files (`candidate_0.md` through `candidate_N.md`) reflect the actual N used per iteration? Or do they assume a fixed N?

**Output:** `tests/test_probe_checkpoint.py`

### Agent G: Error Taxonomy Routing Completeness

**Target:** `error_taxonomy.py`, `agent.py` (routing integration), `physics_checks.py`
**Files:** `error_taxonomy.py`, `agent.py`

**Probing mandate:**
1. **Routing table completeness**: `_ORACLE_ROUTING` maps 7 categories. After simplification, the `.get()` fallback was removed. Verify exhaustively that `classify_errors()` can only return these 7 categories — no edge case produces an 8th.
2. **Empty critique handling**: What does `classify_errors("")` return? What about `classify_errors_routed("")`? Does it crash or return `"general"`?
3. **Multi-category critique**: When a critique mentions both algebra AND logic errors, which category wins? Is the priority order documented and tested?
4. **Category → revision addendum content**: Each category produces a specific addendum string. Verify no addendum is empty or None. Verify addenda are distinct (no two categories produce identical guidance).
5. **Physics vs math error patterns**: Are there physics-specific error patterns (e.g., "dimensional mismatch", "gauge invariance violated") that should map to `units` or a new category but currently fall through to `general`?

**Output:** `tests/test_probe_taxonomy.py`

### Agent H: Consensus Pipeline Stress Cases

**Target:** `verifier_agent.py`, `synthesizer.py`, `check_prompts.py`
**Files:** `verifier_agent.py`, `synthesizer.py`

**Probing mandate:**
1. **All K verifiers crash**: After simplification, the closure-based `run_one()` replaced `_run_single_verify()`. Verify that when all K futures raise exceptions, the pipeline returns a meaningful error rather than an empty `ConsensusResult`.
2. **K=1 consensus**: With `num_verifiers=1`, does majority-vote logic still work? Is `mean()` of a single confidence value correct?
3. **Mixed FIXABLE verdicts in consensus**: If 2 of 3 verifiers return FIXABLE (majority) but with different corrected solutions — which corrected solution wins? Is this even handled?
4. **Issue dedup with empty issues**: When all verifiers return zero issues but different verdicts — does the aggregation handle empty issue lists without crashing?
5. **Synthesis API failure**: When the LLM synthesis call fails, the fallback concatenates raw critiques. Verify the fallback produces valid `ConsensusResult` with all fields populated.
6. **Domain auto-detection edge cases**: `detect_domain()` with text containing equal math and physics keywords. What's the tiebreaker? Is it deterministic?

**Output:** `tests/test_probe_consensus.py`

---

## Execution Protocol

### Wave 1 (Agents A-D, parallel)

```
Agent tool × 4, subagent_type="feature-dev:code-reviewer"

Each agent receives:
1. Its probing mandate (above)
2. Instruction: "For each probe point, read the relevant source code,
   analyze the logic, and write a test that would FAIL if the bug exists.
   Write all tests to the specified output file. If no bug exists for a
   probe point, write a passing test that documents the correct behavior.
   Run the test suite after writing to verify your tests are syntactically
   valid."
3. Test runner: /home/xeal/.local/bin/micromamba run -n alethic pytest {output_file} -v
```

### Wave 2 (Agents E-H, parallel)

Same protocol. Wave 2 agents may read Wave 1 output files for cross-referencing but should not depend on them.

### Aggregation

After all 8 agents complete:
1. Collect all new test files
2. Run full suite: `pytest tests/test_probe_*.py -v`
3. Triage: separate genuine bugs (failing tests) from documentation tests (passing)
4. Fix genuine bugs
5. Commit: `feat: bug-probing swarm — N bugs found and fixed, M probe tests added`

---

## Success Criteria

- [ ] All 8 agents complete without error
- [ ] Each agent produces a valid test file
- [ ] All test files are syntactically valid (importable)
- [ ] Genuine bugs are triaged and fixed
- [ ] Existing 739 tests still pass after fixes
- [ ] New probe tests are committed to the test suite
- [ ] Total test count increases by 40-80 new test cases

---

## Risk Assessment

| Probe Area | Likelihood of Bugs | Impact |
|-----------|-------------------|--------|
| Feature interactions (A) | **High** — already found n_expected bug | Critical — orchestrator correctness |
| Edge cases (B) | Medium — unusual configs | Medium — graceful degradation |
| Concurrency (C) | Low — CPython GIL helps | High — data corruption if hit |
| Parsing (D) | Medium — regex is fragile | Medium — misclassification |
| Config (E) | Low — well-tested | Low — user-facing errors |
| Checkpoint (F) | **High** — schema evolution | High — resume failures |
| Taxonomy (G) | Low — deterministic | Low — suboptimal revision |
| Consensus (H) | Medium — edge cases | Medium — incorrect verdicts |
