# Skill–Library Parity Boundary

The Python library (`src/alethic/`) and the Claude Code skills (`skills/`) implement
the same Generate → Verify → Revise loop, but they are not — and cannot be — feature-identical.
This document defines what **must** stay synced and what is **intentionally library-only**,
so parity audits are mechanical instead of archaeological.

## Must stay synced (semantics that affect outcomes)

When any of these change in the library, port the change to the skill orchestrator
(`skills/alethic-common/orchestrator.md`) and/or the reference prompts in the same PR:

| Semantic | Library source of truth | Skill location |
|----------|------------------------|----------------|
| Candidate selection rule (verdict-aware: CORRECT < MINOR_ISSUES < FIXABLE < MAJOR_FLAW < UNSOLVED, confidence breaks ties) | `oracle_router.rank_candidates()` | orchestrator.md, Step "select the best candidate" |
| Acceptance gate (verdict CORRECT **and** confidence ≥ threshold) | `agent.py` main loop | orchestrator.md termination check |
| FIXABLE shortcut (re-verify corrected solution; accept if passing, else fall through to revision) | `agent.py` | orchestrator.md FIXABLE step |
| Stall detection parameters (window, epsilon, cooldown, reset cap, N boost) | `oracle_router.check_stall()` + `AgentConfig` | orchestrator.md Step 2-pre |
| Preset table (iters, revisions, threshold, best-of-N) | `models.py PRESETS` | SKILL.md configurators |
| Generator / verifier / reviser prompt content (FIXABLE verdict, ATOM annotations, citations, backward pass, verify_step_N, interpretation check, balanced addendum, strategy-reset addendum) | `prompts.py` / `physics_prompts.py` | `skills/*/references/*.md` |
| Error-category keyword classifier (all 9 categories incl. `false_premise`, `wrong_method`; hierarchical priority order) + `error_category` in VERIFY events | `error_taxonomy.classify_errors()` / `_TREE` | orchestrator.md Step 2-pre-b + Step 2d 1b |
| Disproof escalation (on stall reset: error category ∈ {false_premise, interpretation, counterexample} OR 2 consecutive UNSOLVED → append disproof addendum) | `oracle_router._should_disproof()` + `DISPROOF_STRATEGY_ADDENDUM` | orchestrator.md Step 2-pre item 4 + SKILL.md `disproof_addendum` |
| Verification output format (VERDICT / CONFIDENCE / CRITIQUE / ISSUES / ATOM CONFIDENCES / CORRECTED SOLUTION) | `subagents._parse_verification()` | verifier reference prompts |

## Intentionally library-only

These have no skill counterpart. The skill model (sequential Task sub-agents,
file-based state, no threads, no provider control) makes them infeasible or pointless
to port. Do **not** flag these in parity audits:

- **Atom-guided verification** (v3.6) — architectural mismatch, decided 2026-03.
- **Confidence calibration** (v3.6) — requires persistent calibration store.
- **OracleRouter internals** (v3.7) — skills hardcode equivalent routing inline; only
  the *outcomes* (selection rule, stall behavior) must match, not the structure.
- **Hierarchical inconsistency classifier** (v3.7.2) — skills approximate the 5-level
  tree with a flat first-match-wins list in the library's tree-traversal order;
  category names and priority order must remain compatible, the
  `InconsistencyResult` diagnostics stay library-only.
- **OpenRouter adapter / client factory** (v3.7.2) — skills are Anthropic-only by nature.
- **Proof-graph tree search** (v3.8: `proof_graph.py`, `microkernel.py`, `explorer.py`,
  `search.py`) — recursive tree search does not map onto the sequential skill orchestrator.

## Parity audit checklist

1. Diff the "must stay synced" table rows against the skill files.
2. Check version strings agree: `pyproject.toml`, `.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json`, `src/alethic/__init__.py`.
3. Anything new in the library since the last audit: classify it into one of the two
   sections above and update this document.

Last full audit: 2026-06-10 (found and fixed candidate-selection drift; ported disproof
escalation and the v3.7.2 classifier categories to the skills; all prompts at parity).
