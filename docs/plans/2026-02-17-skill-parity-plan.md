# Skill Feature Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract a shared orchestrator from the duplicated `/alethic-solve` and `/alethic-derive` skills, then enhance it with verification features, CLI flags, and session metadata to achieve feature parity with the Python library.

**Architecture:** Both ~1030-line SKILL.md files become ~75-line thin configurators that define domain variables (math/physics terminology), then dynamically find and Read a shared `orchestrator.md` (~625 lines). The orchestrator reads prompt templates from per-skill `references/*.md` files just-in-time. New features (severity parsing, CLI flags, event logging) are added to the shared orchestrator only.

**Tech Stack:** Claude Code skills (markdown instruction files), Bash, plugin-dev:plugin-validator

**Design document:** `docs/plans/2026-02-17-skill-parity-design.md`

**Run commands in:** Project root `/home/xeal/dev/alethic`

**Validation command:** Use the `plugin-dev:plugin-validator` agent

---

## Commit 1: Extract Shared Orchestrator (Pure Refactor)

This is the largest commit. It extracts the shared orchestrator logic, creates thin SKILL.md configurators, and makes reference files authoritative. No behavioral changes.

### Task 1.1: Update reference files to be authoritative

**Files:**
- Modify: `skills/alethic-solve/references/generator.md` (line 1-2)
- Modify: `skills/alethic-solve/references/verifier.md` (line 1-2)
- Modify: `skills/alethic-solve/references/reviser.md` (line 1-2)
- Modify: `skills/alethic-solve/references/beautifier.md` (line 1-2)
- Modify: `skills/alethic-solve/references/textbook_planner.md` (line 1-2)
- Modify: `skills/alethic-solve/references/textbook_writer.md` (line 1-2)
- Modify: `skills/alethic-solve/references/fidelity_verifier.md` (line 1-2)
- Modify: `skills/alethic-derive/references/generator.md` (line 1-2)
- Modify: `skills/alethic-derive/references/verifier.md` (line 1-2)
- Modify: `skills/alethic-derive/references/reviser.md` (line 1-2)
- Modify: `skills/alethic-derive/references/beautifier.md` (line 1-2)
- Modify: `skills/alethic-derive/references/textbook_planner.md` (line 1-2)
- Modify: `skills/alethic-derive/references/textbook_writer.md` (line 1-2)
- Modify: `skills/alethic-derive/references/fidelity_verifier.md` (line 1-2)

**Step 1: Read all 14 reference files**

Read each reference file. Every file starts with a header like:
```
# Verifier System Prompt

> **Note:** The authoritative version of this prompt is embedded in `skills/alethic-solve/SKILL.md`. This file is kept as a standalone reference.
```

**Step 2: Update headers to mark reference files as authoritative**

In each reference file, replace the note with:
```
> **Authoritative prompt.** Read by the orchestrator at runtime via `skills/alethic-common/orchestrator.md`.
```

**Step 3: Verify reference files have v2.0 features**

Run:
```bash
grep -c "CRITICAL" skills/alethic-solve/references/verifier.md skills/alethic-derive/references/verifier.md
grep -c "SECTION CONFIDENCES" skills/alethic-solve/references/verifier.md skills/alethic-derive/references/verifier.md
```

Expected: Both verifier.md files return count >= 1 for both patterns. (They already have these features from the v2.0 update.)

---

### Task 1.2: Create the shared orchestrator

**Files:**
- Create: `skills/alethic-common/orchestrator.md`

**Step 1: Create the directory**

```bash
mkdir -p skills/alethic-common
```

**Step 2: Write the orchestrator**

Create `skills/alethic-common/orchestrator.md`. This is the largest file (~625 lines). Its content is derived from `skills/alethic-solve/SKILL.md` with these transformations:

1. Remove the YAML frontmatter (lives in thin SKILL.md)
2. Remove all `<*_prompt>...</*_prompt>` embedded prompt blocks (replaced with Read instructions)
3. Replace domain-specific terms with variables:
   - `"mathematical"` / `"physics"` → use `{domain}` context
   - `"solution"` / `"derivation"` → `{noun}`
   - `"solve"` / `"derive"` → `{verb}`
   - `"alethic-solve"` / `"alethic-derive"` → `alethic-{command}`
   - `"Mathematical Reasoning"` / `"Physics Derivation"` → `{agent_title}`
   - `"math"` / `"physics"` (in session.json) → `{domain}`
4. Add "Domain Variables" section referencing the thin SKILL.md
5. Add "Prompt Loading" section with Read instructions for references
6. Add balanced addendum handling (conditionally append `{balanced_addendum}` to Generator prompt)

The orchestrator structure (copy from design doc):

```
# Alethic Orchestrator — Shared GVR Loop

## Domain Variables
## Argument Parsing
## Critical Architecture Rules
## Error Handling Protocol
## Prompt Loading
## Step 1: Setup
## Step 2: Main Loop (2a Generate, 2b Verify, 2c Check Verdict, 2d Revise, 2e Update State)
## Step 3: Failure Admission
## Step 4: Format Output (4a Beautifier, 4b Textbook Pipeline)
## Step 5: Present Results
## Step 6: Session Finalization
## Orchestrator Context Management
## Known Limitations
```

When writing the orchestrator, use the `skills/alethic-solve/SKILL.md` as the source. Read it, then apply the transformations above. Key sections to parameterize:

- Line 14: "Alethic {agent_title} Agent" — use `{agent_title}` from domain config
- Line 16: "a {domain} reasoning agent" — use variables
- Step 1 session.json: `"domain": "{domain}"`, `"skill": "alethic-{command}"`
- Step 2a Task descriptions: `"Generate {noun} iter {N} candidate {C}"`
- Step 2a file instructions: `"Write your complete {noun} to..."`
- Step 2b Task descriptions: `"Verify {noun} iter {N} candidate {C}"`
- Step 2d Task descriptions: `"Revise {noun} iter {N} rev {M}"`
- Step 4a/4b: `"Read the raw {noun} from..."`
- Step 5: `"This {noun} was not approved..."`, `"No {noun} was produced."`
- Step 6: `"domain":"{domain}"`

For prompt loading, replace each embedded `<*_prompt>...</*_prompt>` block with:
```
Read the {Role} prompt from `{references_dir}/{filename}.md` and include it at the beginning of the Task prompt.
```

For the balanced addendum, add after the Generator prompt loading:
```
If `--no-balanced` is NOT set, append the balanced approach addendum (from the thin SKILL.md's
"Balanced Approach Addendum" section) to the Generator's user message.
```

**Step 3: Verify the file was created**

```bash
wc -l skills/alethic-common/orchestrator.md
```

Expected: ~600-650 lines.

**Step 4: Verify key markers exist**

```bash
grep -c "{noun}" skills/alethic-common/orchestrator.md
grep -c "{domain}" skills/alethic-common/orchestrator.md
grep -c "{references_dir}" skills/alethic-common/orchestrator.md
grep -c "Step 2a" skills/alethic-common/orchestrator.md
```

Expected: All counts > 0.

---

### Task 1.3: Create the thin alethic-solve SKILL.md

**Files:**
- Modify: `skills/alethic-solve/SKILL.md` (rewrite from ~1031 lines to ~75 lines)

**Step 1: Back up the original**

```bash
cp skills/alethic-solve/SKILL.md skills/alethic-solve/SKILL.md.bak
```

**Step 2: Write the thin SKILL.md**

Rewrite `skills/alethic-solve/SKILL.md` with:

```markdown
---
name: alethic-solve
description: "Solve a mathematical problem using Generate-Verify-Revise loop with decoupled verification"
argument-hint: '[-p preset] [-i iters] [-r revs] [-b budget] [-B N] "<problem>"'
allowed-tools:
  - Bash
  - Read
  - Write
  - Task
  - WebSearch
  - WebFetch
---

# /alethic-solve — Alethic Mathematical Reasoning Agent

The user's input is: $ARGUMENTS

## Domain Configuration

| Key | Value |
|-----|-------|
| domain | math |
| command | solve |
| noun | solution |
| verb | solve |
| agent_title | Mathematical Reasoning |
| session_skill | alethic-solve |

## Balanced Approach Addendum

> Append this to the Generator's user message (unless `--no-balanced` is set):

7. **Explore counterexamples first (balanced approach).** Before committing to a proof strategy, spend at least a few sentences considering whether the statement might be FALSE. Try small cases (n = 0, 1, 2, 3), constant/linear functions, boundary conditions, and degenerate cases (empty sets, zero vectors, identity matrices). If you find a counterexample, present it as your solution. If you cannot find one, explain why and then proceed with the proof.

## Examples

- `/alethic-solve "Prove sqrt(2) is irrational"` — defaults (5 iter, 3 rev, 50 budget)
- `/alethic-solve -p quick "Is 17 prime?"` — quick preset (2 iter, 1 rev, threshold 0.85)
- `/alethic-solve -p thorough "Prove the Cayley-Hamilton theorem"` — thorough preset
- `/alethic-solve -p quick -i 4 "Solve x^2=2"` — quick preset with iteration override
- `/alethic-solve -i 8 -r 5 "Prove the Cayley-Hamilton theorem"` — extended
- `/alethic-solve -t 0.95 "Prove Fermat's little theorem"` — stricter threshold
- `/alethic-solve -B 3 "Prove the Cayley-Hamilton theorem"` — 3 candidates per iteration
- `/alethic-solve --textbook "Prove sqrt(2) is irrational"` — textbook-style output
- `/alethic-solve -p thorough --textbook "Prove the Cayley-Hamilton theorem"` — thorough + textbook

## Load Orchestrator

1. Find the orchestrator:
   ```bash
   ORCH=$(find ~/.claude/plugins -name "orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
   echo "ORCHESTRATOR: $ORCH"
   ```
   If not found, check the local development path:
   ```bash
   ORCH=$(find /home -maxdepth 6 -name "orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
   echo "ORCHESTRATOR: $ORCH"
   ```

2. Derive the references directory:
   ```bash
   REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/orchestrator.md|alethic-solve/references|")
   echo "REFERENCES: $REF_DIR"
   ```

3. Read the orchestrator file at the path found above.

4. Follow the orchestrator instructions exactly, using this skill's Domain Configuration, Balanced Approach Addendum, and references directory.
```

**Step 3: Verify the new file size**

```bash
wc -l skills/alethic-solve/SKILL.md
```

Expected: ~70-80 lines.

---

### Task 1.4: Create the thin alethic-derive SKILL.md

**Files:**
- Modify: `skills/alethic-derive/SKILL.md` (rewrite from ~1035 lines to ~75 lines)

**Step 1: Back up the original**

```bash
cp skills/alethic-derive/SKILL.md skills/alethic-derive/SKILL.md.bak
```

**Step 2: Write the thin SKILL.md**

Same structure as alethic-solve but with physics domain values:

| Key | Value |
|-----|-------|
| domain | physics |
| command | derive |
| noun | derivation |
| verb | derive |
| agent_title | Physics Derivation |
| session_skill | alethic-derive |

Balanced approach addendum (physics version):
```
7. **Check limiting cases and dimensions (balanced approach).** Before committing to a derivation approach, check dimensional consistency of the expected result and verify at least one known limiting case (e.g., ħ→0 classical limit, c→∞ non-relativistic limit, weak-coupling limit). Also consider whether the problem's premise might be flawed — does it contradict known physical principles? If so, present the contradiction. Otherwise, proceed with the derivation.
```

Examples use physics problems (quantum harmonic oscillator, simple pendulum, hydrogen atom, Euler-Lagrange, Dirac equation, etc.).

Fix the textbook bug: use `{textbook}` variable (not hardcoded `false`) in session.json — but this is in the orchestrator, not the thin SKILL.md, so it's already fixed by the orchestrator parameterization.

**Step 3: Verify the new file size**

```bash
wc -l skills/alethic-derive/SKILL.md
```

Expected: ~70-80 lines.

---

### Task 1.5: Validate and commit

**Step 1: Verify file structure**

```bash
ls -la skills/alethic-common/orchestrator.md
ls -la skills/alethic-solve/SKILL.md skills/alethic-derive/SKILL.md
for skill in solve derive; do
  for ref in generator verifier reviser beautifier textbook_planner textbook_writer fidelity_verifier; do
    test -f "skills/alethic-$skill/references/$ref.md" && echo "OK: $skill/$ref.md" || echo "MISSING: $skill/$ref.md"
  done
done
```

Expected: All files exist, all references OK.

**Step 2: Run plugin validator**

Use the `plugin-dev:plugin-validator` agent to validate the plugin structure.

**Step 3: Verify key patterns**

```bash
# Orchestrator has domain variables
grep -c "{noun}" skills/alethic-common/orchestrator.md
# Thin SKILL.md files have domain config
grep -c "domain.*math" skills/alethic-solve/SKILL.md
grep -c "domain.*physics" skills/alethic-derive/SKILL.md
# Reference files are authoritative
grep -c "Authoritative" skills/alethic-solve/references/verifier.md
# Verifier has v2.0 features
grep -c "CRITICAL" skills/alethic-solve/references/verifier.md
grep -c "SECTION CONFIDENCES" skills/alethic-derive/references/verifier.md
```

Expected: All counts > 0.

**Step 4: Remove backups**

```bash
rm skills/alethic-solve/SKILL.md.bak skills/alethic-derive/SKILL.md.bak
```

**Step 5: Commit**

```bash
git add skills/alethic-common/ skills/alethic-solve/ skills/alethic-derive/
git commit -m "$(cat <<'EOF'
Extract shared orchestrator from duplicated skill files

Both /alethic-solve (1031 lines) and /alethic-derive (1035 lines) now
delegate to a shared orchestrator.md (~625 lines) that handles the
full GVR loop, session management, dashboard, and textbook pipeline.
Each SKILL.md is reduced to ~75 lines of domain configuration.

Prompt templates are now read from references/*.md files at runtime
(just-in-time, one Read per sub-agent call). Reference files become
the authoritative source of truth (previously they were standalone
copies of embedded prompts).

Fixes: alethic-derive textbook flag hardcoded as false in session.json.

No behavioral changes — pure refactor.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Commit 2: Add Verification Features

### Task 2.1: Extend Verifier return line format

**Files:**
- Modify: `skills/alethic-solve/references/verifier.md`
- Modify: `skills/alethic-derive/references/verifier.md`

**Step 1: Update the return line instruction in both verifier references**

Both files currently end with:
```
After writing the verification file, return ONLY this single line:
VERDICT: {verdict} | CONFIDENCE: {confidence}
```

Change to:
```
After writing the verification file, return ONLY this single line:
VERDICT: {verdict} | CONFIDENCE: {confidence} | HAS_CRITICAL: {yes|no} | TOP_ISSUE: {first issue text, or "none"}

- HAS_CRITICAL: "yes" if ANY issue is tagged [CRITICAL], "no" otherwise.
- TOP_ISSUE: The text of the first issue listed (without the severity tag), or "none" if no issues.
```

**Step 2: Verify**

```bash
grep -c "HAS_CRITICAL" skills/alethic-solve/references/verifier.md skills/alethic-derive/references/verifier.md
```

Expected: Both return 1+.

---

### Task 2.2: Add CRITICAL-blocks-acceptance to orchestrator

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Update Error Handling Protocol**

In the verdict parsing section, add parsing for the new fields:
```
- Search for `HAS_CRITICAL:\s*(yes|no)` (case-insensitive). Default: "no" if missing.
- Search for `TOP_ISSUE:\s*(.+?)(?:\s*\||\s*$)`. Default: "none" if missing.
```

**Step 2: Update Step 2c verdict checking**

After the current check "If verdict is 'correct' AND confidence >= threshold", add:
```
**CRITICAL issue guard**: Before accepting, also check HAS_CRITICAL. If "yes":
- Log: `[Iter {N}] CRITICAL issue detected — forcing revision`
- Treat as "major_flaw" regardless of verdict and confidence — proceed to Step 2d.
```

**Step 3: Verify**

```bash
grep -c "HAS_CRITICAL" skills/alethic-common/orchestrator.md
grep -c "CRITICAL issue guard" skills/alethic-common/orchestrator.md
```

---

### Task 2.3: Add section-targeted revision to Reviser prompts

**Files:**
- Modify: `skills/alethic-solve/references/reviser.md`
- Modify: `skills/alethic-derive/references/reviser.md`

**Step 1: Add section targeting instruction**

At the end of the Instructions section in both reviser references, add:

```
6. **Target low-confidence sections.** If the verification includes a SECTION CONFIDENCES block, focus your revision effort on sections with confidence below 0.70. These are the weakest parts and should receive the most attention.
```

**Step 2: Verify**

```bash
grep -c "SECTION CONFIDENCES" skills/alethic-solve/references/reviser.md skills/alethic-derive/references/reviser.md
```

---

### Task 2.4: Commit

```bash
git add skills/
git commit -m "$(cat <<'EOF'
Add CRITICAL-blocks-acceptance and section-targeted revision

Verifier return line now includes HAS_CRITICAL and TOP_ISSUE fields.
Orchestrator blocks acceptance when any CRITICAL issue exists, forcing
revision regardless of verdict/confidence. Reviser prompts now instruct
focusing on low-confidence sections from SECTION CONFIDENCES.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Commit 3: Add CLI Flag Parity

### Task 3.1: Add new flags to orchestrator argument parsing

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Add flags to the argument parsing table**

Add these rows:

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--no-balanced` | `-n` | off | Disable balanced prompting addendum in Generator |
| `--file` | `-f` | — | Read problem text from a file path |
| `--quiet` | `-q` | off | Suppress monitoring dashboard |
| `--json` | `-j` | off | Output structured JSON summary |
| `--model` | `-m` | opus | Model tier for sub-agents (haiku/sonnet/opus) |

**Step 2: Add validation rules**

```
If `--model` is not one of "haiku", "sonnet", "opus", default to "opus" and warn.
If `--file` is set, Read the file. If it doesn't exist, ask the user to provide a valid path.
```

**Step 3: Add `--file` handling to Step 1**

After argument parsing and before writing problem.md:
```
If `--file` is set, Read the specified file path and use its content as the problem statement.
```

**Step 4: Add `--model` to all Task calls**

Replace all `model: "opus"` with `model: "{model}"` (where `{model}` defaults to "opus").

**Step 5: Add `--quiet` gating**

In Step 2b.4 (monitoring dashboard), Step 2b (cumulative history table), and Step 2d.7 (re-verification print), add:
```
(Skip this output if `--quiet` is set.)
```

**Step 6: Add `--no-balanced` gating**

In the prompt loading section, the balanced addendum append instruction already says "unless --no-balanced is set." Verify this is present.

**Step 7: Add `--json` output mode to Step 5**

Add before the existing Step 5 presentation:
```
**If `--json` is set**, output only a JSON object and skip the markdown presentation:

{json structure with problem, solved, verdict, confidence, iterations_used, total_revisions,
 task_calls, elapsed_seconds, solution_path, output_path, session_id, failed_approaches}
```

**Step 8: Verify**

```bash
grep -c "\-\-no-balanced" skills/alethic-common/orchestrator.md
grep -c "\-\-file" skills/alethic-common/orchestrator.md
grep -c "\-\-quiet" skills/alethic-common/orchestrator.md
grep -c "\-\-json" skills/alethic-common/orchestrator.md
grep -c "\-\-model" skills/alethic-common/orchestrator.md
```

---

### Task 3.2: Update thin SKILL.md examples

**Files:**
- Modify: `skills/alethic-solve/SKILL.md`
- Modify: `skills/alethic-derive/SKILL.md`

**Step 1: Add flag examples to both thin SKILL.md files**

Add to the Examples section:
```
- `/alethic-{command} --no-balanced "..."` — skip counterexample/dimensional check
- `/alethic-{command} --file problem.md` — read problem from file
- `/alethic-{command} -q -p thorough "..."` — quiet mode (no dashboard)
- `/alethic-{command} --json "..."` — JSON output
- `/alethic-{command} --model sonnet "..."` — use Sonnet for sub-agents
```

---

### Task 3.3: Commit

```bash
git add skills/
git commit -m "$(cat <<'EOF'
Add CLI flag parity: --no-balanced, --file, --quiet, --json, --model

Five new flags bring skill CLI closer to the Python library:
- --no-balanced/-n: skip balanced prompting addendum
- --file/-f: read problem from file instead of inline
- --quiet/-q: suppress monitoring dashboard output
- --json/-j: output structured JSON summary
- --model/-m: select model tier (haiku/sonnet/opus)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Commit 4: Add Session Metadata Enrichment

### Task 4.1: Add events.jsonl logging to orchestrator

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Add event logging after each Task call**

After each Task sub-agent call in Steps 2a, 2b, 2d, 4a, 4b, add:
```
Log event: echo '{"type":"{role}","iteration":{N},...,"timestamp":"'$(date -Iseconds)'"}' >> {session_dir}/worklog/events.jsonl
```

Event types: `generate`, `verify`, `revise`, `beautify`, `plan_textbook`, `write_textbook`, `verify_fidelity`, `accept`, `fail`.

---

### Task 4.2: Add failed_approaches to orchestrator

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Add failed approach accumulation**

At the end of Step 2e (after a failed iteration, before continuing to next):
```
Accumulate failed approach: append to the running list:
{"iteration": {N}, "strategy": "{generator return summary}", "verdict": "{verdict}", "confidence": {confidence}, "top_issue": "{TOP_ISSUE from verifier}"}
```

**Step 2: Add failed_approaches to session.json**

In the session.json schema, add:
```json
"failed_approaches": []
```

Update Step 2e to write accumulated approaches to session.json.

**Step 3: Update strategy history prompt for Generator**

The Generator strategy history in Step 2a already says "Previous attempts used the following strategies..." Update to include top issues:
```
Previous attempts:
- Iter 1: {strategy} → {verdict} ({confidence}): {top_issue}
- Iter 2: {strategy} → {verdict} ({confidence}): {top_issue}
Try a DIFFERENT approach.
```

---

### Task 4.3: Add elapsed_seconds tracking

**Files:**
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Capture start time in Step 1**

Add to Step 1 setup:
```bash
START_TIME=$(date +%s)
```

**Step 2: Compute elapsed in Step 6**

Add to Step 6 finalization:
```bash
ELAPSED=$(($(date +%s) - START_TIME))
```

Write `"elapsed_seconds": {ELAPSED}` to session.json.

---

### Task 4.4: Update CLAUDE.md and commit

**Files:**
- Modify: `CLAUDE.md` (module map, session directory layout)
- Modify: `skills/alethic-common/orchestrator.md`

**Step 1: Update CLAUDE.md**

Add `alethic-common/orchestrator.md` to the skill file table:

```
| `skills/alethic-common/orchestrator.md` | Shared GVR loop orchestrator — parameterized by domain, reads prompts from references/*.md |
```

Update the skill SKILL.md descriptions to note they are thin configurators.

**Step 2: Commit**

```bash
git add skills/ CLAUDE.md
git commit -m "$(cat <<'EOF'
Add session metadata: events.jsonl, failed_approaches, elapsed_seconds

Event logging writes one JSON line per Task call to worklog/events.jsonl
for post-hoc analysis. Failed approaches accumulate strategy summaries
with top issues from the Verifier, passed to subsequent Generators for
strategy diversity. Elapsed wall-clock time tracked in session.json.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Post-Implementation Checklist

After all 4 commits:

1. Run plugin validator: `plugin-dev:plugin-validator` agent
2. Verify file structure:
   ```bash
   find skills/ -name "*.md" | sort
   ```
3. Verify key markers:
   ```bash
   grep -l "HAS_CRITICAL" skills/alethic-common/orchestrator.md
   grep -l "events.jsonl" skills/alethic-common/orchestrator.md
   grep -l "elapsed_seconds" skills/alethic-common/orchestrator.md
   grep -l "--json" skills/alethic-common/orchestrator.md
   grep -l "CRITICAL" skills/alethic-*/references/verifier.md
   ```
4. Verify git log shows 4 clean commits:
   ```bash
   git log --oneline -4
   ```
5. Manual smoke test: `/alethic-solve -p quick "Is 17 prime?"`
6. Update CLAUDE.md if any additional module map changes are needed
