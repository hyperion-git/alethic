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
| strategy_reset_addendum | (from "Strategy Reset Addendum" section below) |
| disproof_addendum | (from "Disproof Escalation Addendum" section below) |
| adversarial_verifier | true for thorough/extreme presets; false otherwise — enables 5-round adversarial self-correction in all verifier Task calls |
| adversarial_breaker | true for thorough/extreme presets; false otherwise — enables adversarial breaker probe on CORRECT verdicts |

## Balanced Approach Addendum

> Append this to the Generator's user message (unless `--no-balanced` is set):

7. **Explore counterexamples first (balanced approach).** Before committing to a proof strategy, spend at least a few sentences considering whether the statement might be FALSE. Try small cases (n = 0, 1, 2, 3), constant/linear functions, boundary conditions, and degenerate cases (empty sets, zero vectors, identity matrices). If you find a counterexample, present it as your solution. If you cannot find one, explain why and then proceed with the proof.

## Strategy Reset Addendum

> Injected into the Generator prompt (replacing the standard failed_approaches block) when a stall reset is triggered. The `{failed_approaches}` placeholder is filled by the orchestrator.

## STRATEGY RESET — Previous approaches exhausted

The methods listed below have been attempted and FAILED. You are FORBIDDEN from using them.

{failed_approaches}

For each method above: DO NOT use it. DO NOT adapt it. DO NOT build on it.
If your plan uses any of these methods, you MUST change your plan.
Reflect on your approach — if it resembles any of the above, choose a different one.

You MUST use a categorically different proof technique.
Start from a completely different mathematical foundation.
Consider approaches from a different branch of mathematics entirely.

## Disproof Escalation Addendum

> Appended to the reset context by the orchestrator when accumulated evidence suggests the claim may be false (error category `false_premise`/`interpretation`/`counterexample`, or two consecutive UNSOLVED verdicts).

## DISPROOF ESCALATION — Consider that the claim may be false

Previous iterations have repeatedly failed to prove this statement. Based on accumulated evidence, there is a meaningful probability that the claim is FALSE.

In addition to exploring categorically different proof techniques, you MUST also:

1. **Systematically search for counterexamples**: Test small cases exhaustively (n=0,1,2,3,...). For claims about real numbers, test rationals, irrationals (sqrt(2), pi, e), negative numbers, zero, and boundary cases. Use Python/SymPy to automate the search.

2. **Identify necessary conditions**: What would NEED to be true for the claim to hold? Can you show one of those necessary conditions fails?

3. **Check known results**: Does this claim conflict with known theorems? Would it imply something known to be false?

If you find evidence the claim is false, present a clear disproof:
- State the counterexample or contradiction explicitly
- Verify it computationally using Python code
- Explain why it violates the original claim

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
- `/alethic-solve --no-balanced "Prove sqrt(2) is irrational"` — skip counterexample check
- `/alethic-solve --resume .alethic/session-id/ "original problem"` — resume from checkpoint
- `/alethic-solve --file problem.md` — read problem from file
- `/alethic-solve -q -p thorough "Prove the Cayley-Hamilton theorem"` — quiet mode (no dashboard)
- `/alethic-solve --json "Is 17 prime?"` — JSON output
- `/alethic-solve --model sonnet "Prove Fermat's little theorem"` — use Sonnet for sub-agents

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
