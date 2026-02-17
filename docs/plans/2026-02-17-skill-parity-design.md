# Skill Feature Parity — Design Document

> **Status**: Complete — ready for implementation plan.

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

### Path Resolution

The thin SKILL.md needs to find and Read `orchestrator.md` at runtime. Since the Read tool requires absolute paths and the plugin installation directory varies (development vs marketplace), use Bash `find` for self-discovery:

```bash
ORCH=$(find ~/.claude/plugins -name "orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
echo "$ORCH"
```

From the orchestrator path, derive the references directory:
```bash
REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/orchestrator.md|alethic-{command}/references|")
```

Cost: 1 Bash call + 1 Read call. Comparable to the current approach (which loads all ~1030 lines inline).

### Context Window Budget

| What | Lines | When loaded |
|------|-------|-------------|
| Thin SKILL.md | ~70 | System loads it |
| Orchestrator.md | ~700 | Read once at start |
| Generator prompt | ~60 | Just-in-time per generate |
| Verifier prompt | ~70 | Just-in-time per verify |
| Reviser prompt | ~50 | Just-in-time per revise |
| Beautifier prompt | ~50 | Just-in-time (end) |
| Textbook prompts (3 files) | ~180 | Only if --textbook |

Typical run total: ~1000 lines (comparable to current ~1030). Textbook runs: ~1130 lines.

### Reference File Status

The reference files are currently documented as "standalone references" with the SKILL.md embed being authoritative. The extraction **reverses this** — reference files become authoritative, and the orchestrator reads them at runtime.

**Current discrepancies** (reference files have v2.0 features that embedded prompts lack):

| Feature | Reference files | Embedded in SKILL.md |
|---------|----------------|---------------------|
| Severity tags (`[CRITICAL]`/`[MAJOR]`/`[MINOR]`) | Present | Absent — uses generic `[Issue 1, if any]` |
| SECTION CONFIDENCES block | Present (3 lines) | Absent |
| Code fences around output format | Present | Absent (bare text) |

The extraction naturally resolves these discrepancies — the orchestrator reads the reference files, which already have v2.0 features.

### Bug Found

`skills/alethic-derive/SKILL.md` line 577: `"textbook": false` is hardcoded instead of using `{textbook}` variable (correctly parameterized in alethic-solve). Fixed as part of commit 1.

---

## New Features Design (Detailed)

### Verification Features (Commit 2)

**Extended Verifier return line**: The orchestrator's design principle is "never read solution/verification files into context." To support CRITICAL-blocks-acceptance without reading the verification file, extend the Verifier's return line:

```
VERDICT: {verdict} | CONFIDENCE: {confidence} | HAS_CRITICAL: {yes|no} | TOP_ISSUE: {first issue or "none"}
```

- `HAS_CRITICAL`: Parsed by orchestrator to block acceptance. Fallback: if missing, assume "no" (backward compat).
- `TOP_ISSUE`: Stored in `failed_approaches` for strategy history. Not used for control flow.

**CRITICAL-blocks-acceptance** (Step 2c change): After extracting verdict and confidence, also extract `HAS_CRITICAL`. If "yes", skip directly to revision regardless of verdict/confidence. Log: `[Iter {N}] CRITICAL issue detected — forcing revision`.

**Section-targeted revision** (prompt-level only): The Reviser already reads the verification file. Add to the Reviser prompt reference files:

```
If the verification includes a SECTION CONFIDENCES block, focus your revision
effort on sections with confidence below 0.70. These are the weakest parts of
the solution and should receive the most attention.
```

No orchestrator logic changes needed — purely a prompt enhancement.

**Severity in verifier prompts**: Already present in reference files. The extraction in commit 1 brings these into the skill automatically.

### CLI Flag Parity (Commit 3)

| Flag | Short | Default | Orchestrator logic |
|------|-------|---------|-------------------|
| `--no-balanced` | `-n` | off | Skip balanced addendum paragraph (instruction 7) in Generator prompt. The orchestrator appends a "Balanced approach" addendum after reading the Generator reference file. When `--no-balanced` is set, skip the append. |
| `--file` | `-f` | — | After argument parsing, Read the specified file path and use its content as the problem statement. Error if file doesn't exist. |
| `--quiet` | `-q` | off | Skip all monitoring dashboard printing (Step 2b.4 candidate table, Step 2b cumulative history, Step 2d.7 re-verification lines). Keep essential logs (`[Iter N] Verifier: VERDICT...`). |
| `--json` | `-j` | off | In Step 5, output a JSON object instead of formatted markdown. Structure: `{"problem", "solved", "verdict", "confidence", "iterations_used", "total_revisions", "task_calls", "elapsed_seconds", "solution_path", "output_path", "session_id", "failed_approaches"}` |
| `--model` | `-m` | opus | Pass `model: "{value}"` to each Task call. Valid: haiku, sonnet, opus. Replaces hardcoded `model: "opus"`. |

**Balanced addendum handling**: The current SKILL.md embeds the balanced approach as instruction 7 of the Generator prompt. With the extraction, the Generator prompt is in a reference file. Two options:

1. **Inline in reference file** (current): The balanced paragraph is part of generator.md. The `--no-balanced` flag would need the orchestrator to strip instruction 7 before passing to the Task.
2. **Separate addendum** (cleaner): Split instruction 7 into a separate paragraph appended by the orchestrator. The generator.md has instructions 1-6, 8. The orchestrator conditionally appends the balanced addendum.

**Choice**: Option 2. The orchestrator holds the balanced addendum text (it's domain-specific — counterexamples for math, dimensional analysis for physics). The thin SKILL.md provides the addendum text as part of domain config.

### Session Metadata Enrichment (Commit 4)

**`failed_approaches` in session.json**: After each failed iteration (before continuing to next), accumulate:

```json
{
  "iteration": 2,
  "strategy": "Proof by contradiction using infinite descent",
  "verdict": "major_flaw",
  "confidence": 0.3,
  "top_issue": "Division by zero in step 3"
}
```

- `strategy` comes from the Generator's one-line return
- `top_issue` comes from the Verifier's extended return line
- Written to `session.json` under `"failed_approaches": [...]`
- Passed to subsequent Generators in strategy history prompt

**`events` in worklog/events.jsonl**: After each Task sub-agent call, append one JSON line:

```jsonl
{"type":"generate","iteration":1,"candidate":1,"timestamp":"2026-02-17T10:00:00Z"}
{"type":"verify","iteration":1,"candidate":1,"verdict":"minor_issues","confidence":0.82,"has_critical":false,"timestamp":"2026-02-17T10:00:12Z"}
{"type":"revise","iteration":1,"revision":1,"timestamp":"2026-02-17T10:00:25Z"}
{"type":"verify","iteration":1,"revision":1,"verdict":"correct","confidence":0.94,"has_critical":false,"timestamp":"2026-02-17T10:00:38Z"}
{"type":"accept","iteration":1,"confidence":0.94,"timestamp":"2026-02-17T10:00:38Z"}
```

Written via Bash: `echo '{json}' >> {session_dir}/worklog/events.jsonl`

**`elapsed_seconds`**: Capture `START_TIME=$(date +%s)` at Step 1. Compute `ELAPSED=$(($(date +%s) - START_TIME))` at Step 6. Write to session.json.

---

## Thin SKILL.md Template

```markdown
---
name: alethic-{command}
description: "{description}"
argument-hint: '[-p preset] [-i iters] [-r revs] [-b budget] [-B N] "<problem>"'
allowed-tools:
  - Bash
  - Read
  - Write
  - Task
  - WebSearch
  - WebFetch
---

# /alethic-{command} — Alethic {agent_title} Agent

The user's input is: $ARGUMENTS

## Domain Configuration

| Key | Value |
|-----|-------|
| domain | {domain} |
| command | {command} |
| noun | {noun} |
| verb | {verb} |
| agent_title | {agent_title} |
| session_skill | alethic-{command} |

## Balanced Approach Addendum

{domain-specific balanced approach paragraph — counterexamples for math, dimensional analysis for physics}

## Domain-Specific Examples

{8-10 example invocations with domain-appropriate problems}

## Load Orchestrator

1. Find the orchestrator:
   ```bash
   ORCH=$(find ~/.claude/plugins -name "orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
   echo "ORCHESTRATOR: $ORCH"
   ```

2. Derive the references directory:
   ```bash
   REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/orchestrator.md|alethic-{command}/references|")
   echo "REFERENCES: $REF_DIR"
   ```

3. Read the orchestrator file at the path found above.

4. Follow the orchestrator instructions exactly, using this skill's Domain
   Configuration, Balanced Approach Addendum, and references directory.
```

**Estimated size**: ~75 lines per skill (including examples).

---

## Orchestrator.md Structure Outline

The orchestrator is a ~700-line markdown file with parameterized domain variables.

```
# Alethic Orchestrator — Shared GVR Loop

## Domain Variables
(Read from the thin SKILL.md that loaded this file.)
- {domain}, {command}, {noun}, {verb}, {agent_title}, {session_skill}
- {references_dir}: path to the skill's references/ directory
- {balanced_addendum}: the domain-specific balanced approach text

## Argument Parsing                               (~40 lines)
- Flag table (--preset, -i, -r, -b, -t, -B, --textbook, --no-balanced,
  --file, --quiet, --json, --model)
- Preset table (quick/default/thorough/extreme)
- Validation rules

## Critical Architecture Rules                    (~15 lines)
- 7 rules, using {noun} for domain-specific references

## Error Handling Protocol                        (~25 lines)
- Verdict parsing (extended: VERDICT | CONFIDENCE | HAS_CRITICAL | TOP_ISSUE)
- Confidence validation
- Sub-agent failure handling

## Prompt Loading                                 (~15 lines)
- Instructions to Read prompt templates from {references_dir}/*.md
- Just-in-time loading: read each prompt when spawning the corresponding sub-agent
- Balanced addendum: append {balanced_addendum} to Generator prompt unless --no-balanced

## Step 1: Setup                                  (~80 lines)
- Project detection, slug generation, session directory
- problem.md writing (with --file support)
- session.json initialization (with {domain}, {session_skill})
- START_TIME capture
- Resource estimate banner

## Step 2: Main Loop                              (~200 lines)
- 2a: Generate candidates (read generator.md, construct Task, track strategy)
- 2b: Verify candidates (read verifier.md, construct Task, parse extended return)
- 2c: Check verdict (CRITICAL blocking, best tracking, verdict branching)
- 2d: Revise loop (read reviser.md, construct Task, re-verify)
- 2e: Update state (session.json, events.jsonl, failed_approaches)
- Monitoring dashboard and cumulative history table (gated by --quiet)

## Step 3: Failure Admission                      (~15 lines)

## Step 4: Format Output                          (~130 lines)
- 4a: Simple Beautifier (read beautifier.md)
- 4b: Textbook Pipeline (read textbook_planner.md, textbook_writer.md, fidelity_verifier.md)

## Step 5: Present Results                        (~60 lines)
- Solved/unsolved presentation (gated by --json for JSON output)

## Step 6: Session Finalization                   (~20 lines)
- session.json update, elapsed_seconds, sessions.jsonl append

## Orchestrator Context Management                (~10 lines)

## Known Limitations                              (~15 lines)
```

**Total: ~625 lines** (lighter than the estimated ~700 because prompts are externalized).

---

## Commit Plan

| # | Commit | Files changed | Validation |
|---|--------|---------------|------------|
| 1 | Extract shared orchestrator (pure refactor) | Create `skills/alethic-common/orchestrator.md` (~625 lines), rewrite `skills/alethic-solve/SKILL.md` (~75 lines), rewrite `skills/alethic-derive/SKILL.md` (~75 lines), update 14 reference files (make authoritative, fix verifier discrepancies), fix derive textbook bug | Plugin validator, grep for required markers, manual smoke test |
| 2 | Add verification features | Update `orchestrator.md` (extended return line, CRITICAL blocking, HAS_CRITICAL parsing), update 2 verifier references (return line format), update 2 reviser references (section targeting) | Grep for `HAS_CRITICAL` in orchestrator, grep for `SECTION CONFIDENCES` in verifier refs |
| 3 | Add CLI flag parity | Update `orchestrator.md` (5 new flags in parsing, conditional logic), update 2 thin SKILL.md files (new examples), move balanced addendum to thin SKILL.md | Grep for each flag name in orchestrator |
| 4 | Add session metadata | Update `orchestrator.md` (events.jsonl, failed_approaches, elapsed_seconds), update session.json schema | Grep for `events.jsonl` and `elapsed_seconds` in orchestrator |

---

## Testing Strategy

Skills are markdown instruction files, not executable code. Testing approach:

1. **Plugin structure validation** (after each commit): Run the `plugin-dev:plugin-validator` agent.

2. **File existence validation** (after commit 1):
   ```bash
   # Verify all referenced files exist
   for f in orchestrator.md; do test -f "skills/alethic-common/$f" && echo "OK: $f"; done
   for skill in solve derive; do
     for ref in generator verifier reviser beautifier textbook_planner textbook_writer fidelity_verifier; do
       test -f "skills/alethic-$skill/references/$ref.md" && echo "OK: $skill/$ref"
     done
   done
   ```

3. **Marker validation** (after commit 2):
   ```bash
   # Verify v2.0 features in reference files
   grep -l "CRITICAL" skills/alethic-*/references/verifier.md
   grep -l "SECTION CONFIDENCES" skills/alethic-*/references/verifier.md
   grep -l "HAS_CRITICAL" skills/alethic-common/orchestrator.md
   ```

4. **Manual smoke test** (after commit 1): Run `/alethic-solve -p quick "Is 17 prime?"` and verify the orchestrator loads, generates, verifies, and presents results correctly.

5. **Behavioral diff** (after commit 1): Compare a session's worklog structure (file names, session.json schema) against a pre-extraction session to confirm identical behavior.
