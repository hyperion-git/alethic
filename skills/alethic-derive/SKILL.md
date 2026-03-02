---
name: alethic-derive
description: "Derive a physics result using Generate-Verify-Revise loop with decoupled verification"
argument-hint: '[-p preset] [-i iters] [-r revs] [-b budget] [-B N] "<problem>"'
allowed-tools:
  - Bash
  - Read
  - Write
  - Task
  - WebSearch
  - WebFetch
---

# /alethic-derive — Alethic Physics Derivation Agent

The user's input is: $ARGUMENTS

## Domain Configuration

| Key | Value |
|-----|-------|
| domain | physics |
| command | derive |
| noun | derivation |
| verb | derive |
| agent_title | Physics Derivation |
| session_skill | alethic-derive |
| strategy_reset_addendum | (from "Strategy Reset Addendum" section below) |

## Balanced Approach Addendum

> Append this to the Generator's user message (unless `--no-balanced` is set):

7. **Check limiting cases and dimensions (balanced approach).** Before committing to a derivation approach, check dimensional consistency of the expected result and verify at least one known limiting case (e.g., ħ→0 classical limit, c→∞ non-relativistic limit, weak-coupling limit). Also consider whether the problem's premise might be flawed — does it contradict known physical principles? If so, present the contradiction. Otherwise, proceed with the derivation.

## Strategy Reset Addendum

> Injected into the Generator prompt (replacing the standard failed_approaches block) when a stall reset is triggered. The `{failed_approaches}` placeholder is filled by the orchestrator.

## STRATEGY RESET — Previous approaches exhausted

The following high-level derivation strategies have been tried and failed:
{failed_approaches}

You MUST use a categorically different derivation technique.
Do NOT refine, extend, or repair any previous approach.
Start from a completely different physical or mathematical foundation.
Consider approaches from a different formalism entirely (e.g., if Lagrangian methods failed, try Hamiltonian; if perturbation theory failed, try exact methods or symmetry arguments).

## Examples

- `/alethic-derive "Derive the energy spectrum of the quantum harmonic oscillator"` — defaults (5 iter, 3 rev, 50 budget)
- `/alethic-derive -p quick "Derive the classical period of a simple pendulum"` — quick preset
- `/alethic-derive -p thorough "Derive the hydrogen atom energy spectrum from the Schrodinger equation"` — thorough preset
- `/alethic-derive -p quick -i 4 "Show that F=ma follows from the Lagrangian"` — quick preset with iteration override
- `/alethic-derive -i 8 -r 5 "Derive the Dirac equation from relativistic quantum mechanics"` — extended
- `/alethic-derive -t 0.95 "Derive Maxwell's equations from the electromagnetic action"` — stricter threshold
- `/alethic-derive -B 3 "Derive the hydrogen atom energy spectrum"` — 3 candidates per iteration
- `/alethic-derive --textbook "Derive the energy spectrum of the quantum harmonic oscillator"` — textbook-style output
- `/alethic-derive -p thorough --textbook "Derive the hydrogen atom energy spectrum"` — thorough + textbook
- `/alethic-derive --no-balanced "Derive the Euler-Lagrange equations"` — skip dimensional check
- `/alethic-derive --resume .alethic/session-id/ "original problem"` — resume from checkpoint
- `/alethic-derive --file problem.md` — read problem from file
- `/alethic-derive -q -p thorough "Derive the hydrogen atom energy spectrum"` — quiet mode (no dashboard)
- `/alethic-derive --json "Derive the period of a simple pendulum"` — JSON output
- `/alethic-derive --model sonnet "Derive Maxwell's equations"` — use Sonnet for sub-agents

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
   REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/orchestrator.md|alethic-derive/references|")
   echo "REFERENCES: $REF_DIR"
   ```

3. Read the orchestrator file at the path found above.

4. Follow the orchestrator instructions exactly, using this skill's Domain Configuration, Balanced Approach Addendum, and references directory.
