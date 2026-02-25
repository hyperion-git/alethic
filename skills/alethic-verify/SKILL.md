---
name: alethic-verify
description: "Verify a solution against a problem statement with multi-verifier consensus"
argument-hint: '[-p preset] [-K verifiers] [--problem "..."] <solution or --file path>'
allowed-tools:
  - Bash
  - Read
  - Write
  - Task
  - WebSearch
  - WebFetch
---

# /alethic-verify --- Alethic Multi-Verifier Consensus

The user's input is: $ARGUMENTS

## Domain Configuration

| Key | Value |
|-----|-------|
| mode | verify |
| domain | (auto-detected from solution, or `--domain` override) |
| prompt_template | verifier.md |
| requires_problem | true |
| default_tools | sympy,numpy,scipy,matplotlib |
| session_skill | alethic-verify |

## Examples

- `/alethic-verify --problem "Prove sqrt(2) is irrational" --file proof.md` --- verify a proof file against a problem
- `/alethic-verify -K 5 --problem "Solve x^2 = 2" solution.md` --- 5 verifiers
- `/alethic-verify -p thorough --problem-file problem.md --file solution.md` --- thorough preset, both from files
- `/alethic-verify -p quick --problem "Is 17 prime?" "Yes, 17 is prime because..."` --- quick inline verify
- `/alethic-verify --problem "Derive E=mc^2" .alethic/derive-emc2-20260225-a1b2/` --- verify a previous session's output
- `/alethic-verify -q --json --problem "..." --file sol.md` --- quiet JSON output
- `/alethic-verify --tools sympy --problem "..." --file proof.md` --- SymPy verification only
- `/alethic-verify -d physics --problem "..." --file derivation.md` --- force physics domain

## Load Orchestrator

1. Find the verify-orchestrator:
   ```bash
   ORCH=$(find ~/.claude/plugins -name "verify-orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
   echo "ORCHESTRATOR: $ORCH"
   ```
   If not found, check the local development path:
   ```bash
   ORCH=$(find /home -maxdepth 6 -name "verify-orchestrator.md" -path "*/alethic-common/*" 2>/dev/null | head -1)
   echo "ORCHESTRATOR: $ORCH"
   ```

2. Derive the references directory and common references directory:
   ```bash
   REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/verify-orchestrator.md|alethic-verify/references|")
   COMMON_REF_DIR=$(echo "$ORCH" | sed "s|verify-orchestrator.md|references|")
   echo "REFERENCES: $REF_DIR"
   echo "COMMON_REFERENCES: $COMMON_REF_DIR"
   ```

3. Read the orchestrator file at the path found above.

4. Follow the orchestrator instructions exactly, using this skill's Domain Configuration and references directories.
