# Skill Feature Parity — Design Document (WIP)

> **Status**: In progress — architecture approved, remaining sections pending.

**Goal**: Bring Claude Code skills (`/alethic-solve`, `/alethic-derive`) to feature parity with the Python library CLI, and extract a shared orchestrator to eliminate maintenance duplication.

**Approach**: Extract-then-enhance (4 commits: refactor, verification, flags, metadata)

---

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | Everything achievable (~70% parity) | Remaining ~30% is impossible due to Task sub-agent limitations (temperature, thinking, max_tokens, api_key) |
| Maintenance | Shared orchestrator | Extract common logic to `alethic-common/orchestrator.md`; both skills become thin configurators |
| CRITICAL blocking | Block + force revision | Never accept solutions with unresolved CRITICAL issues, regardless of confidence score |
| Implementation strategy | Extract-then-enhance | 4 commits: (1) pure refactor, (2) verification features, (3) CLI flags, (4) session metadata |

## Features to Implement

### Verification Features (Commit 2)
- Severity tags `[CRITICAL]`/`[MAJOR]`/`[MINOR]` in inline verifier prompts
- Section confidences in verifier output format
- CRITICAL-blocks-acceptance logic (block + force revision)
- Section-targeted revision (reviser focuses on lowest-confidence sections)

### CLI Flag Parity (Commit 3)
- `--no-balanced` — disable balanced prompting addendum
- `--file` — read problem text from a file path
- `--quiet` — suppress monitoring dashboard
- `--json` — output structured JSON summary
- `--model` — select model tier (haiku/sonnet/opus via Task tool's model parameter)

### Session Metadata Enrichment (Commit 4)
- `failed_approaches` — persist verifier critiques across iterations
- `events` — timestamp-tagged event log in session.json
- `elapsed_seconds` — total wall-clock time

### Impossible Features (Task sub-agent limitations)
- `--temperature-*` — Task tool doesn't expose temperature control
- `--thinking` / `--thinking-budget` — Can't enable extended thinking on sub-agents
- `--max-tokens` — Can't set per-call token limits
- `--api-key` — Sub-agents use the session's credentials

---

## Architecture (Approved)

### Current Structure
```
skills/
  alethic-solve/
    SKILL.md          # ~1031 lines: flag parsing + GVR loop + dashboard + prompts
    references/       # 7 math-specific sub-agent prompt files
  alethic-derive/
    SKILL.md          # ~1035 lines: same orchestrator + physics prompts
    references/       # 7 physics-specific sub-agent prompt files
```

### Proposed Structure
```
skills/
  alethic-common/
    orchestrator.md       # ~700 lines: Steps 1-6, flags, GVR loop, dashboard, session
  alethic-solve/
    SKILL.md              # ~80 lines: sets domain=math, reads orchestrator + references
    references/           # Math-specific prompts (unchanged)
  alethic-derive/
    SKILL.md              # ~80 lines: sets domain=physics, reads orchestrator + references
    references/           # Physics-specific prompts (unchanged)
```

### Design Rationale
- Both SKILL.md files are ~1030 lines with 90%+ identical orchestrator logic
- Only prompt templates and domain terminology (`"math"` vs `"physics"`, `"proof"` vs `"derivation"`) differ
- All 7 prompt templates already live in separate `references/*.md` files
- The orchestrator (Steps 1-6: setup, GVR loop, failure admission, output formatting, presentation, finalization) is 100% domain-neutral

### How It Works
1. User invokes `/alethic-solve "problem"` or `/alethic-derive "problem"`
2. Thin SKILL.md defines domain configuration (name, terminology, reference file paths)
3. SKILL.md instructs Claude to Read `../alethic-common/orchestrator.md`
4. Orchestrator uses `{domain}` context and reads prompt templates from the skill's `references/` directory
5. All logic (flag parsing, GVR loop, dashboard, session management, textbook pipeline) lives in the shared orchestrator

### Structural Analysis

**Identical sections (shared in orchestrator.md):**
1. Argument parsing logic (flags, presets, validation)
2. Critical architecture rules
3. Error handling protocol
4. Main GVR loop (Step 2a-2e: generate, verify, check verdict, revise, update state)
5. Failure admission (Step 3)
6. Output formatting (Step 4: beautifier + textbook pipeline)
7. Session initialization and finalization (Step 1, Step 6)
8. Budget tracking and checking
9. Dashboard/monitoring display

**Domain-specific sections (remain in references/):**
1. Generator prompt — strategy lists (proof vs derivation techniques)
2. Verifier prompt — error checklists (math vs physics errors)
3. Reviser prompt — revision focus areas
4. Beautifier prompt — LaTeX macros, document structure
5. Textbook planner — classification types, section elements
6. Textbook writer — structural environments, connecting prose
7. Fidelity verifier — domain-specific checklist items

---

## Remaining Design Sections (TODO)

- [ ] New features design (verification, flags, metadata — detailed)
- [ ] Thin SKILL.md template design
- [ ] Orchestrator.md structure outline
- [ ] Commit plan with file-level changes
- [ ] Testing strategy
