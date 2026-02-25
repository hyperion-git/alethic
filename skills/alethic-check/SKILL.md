---
name: alethic-check
description: "Audit a mathematical or scientific document for internal correctness without a problem statement"
argument-hint: '[-p preset] [-K verifiers] <solution_file or --file path>'
allowed-tools:
  - Bash
  - Read
  - Write
  - Task
  - WebSearch
  - WebFetch
---

# /alethic-check --- Alethic Proof Auditor

The user's input is: $ARGUMENTS

## Domain Configuration

| Key | Value |
|-----|-------|
| mode | check |
| domain | (auto-detected from solution, or `--domain` override) |
| prompt_template | checker.md |
| requires_problem | false |
| default_tools | sympy,numpy,scipy,matplotlib |
| session_skill | alethic-check |

## Examples

- `/alethic-check proof.md` --- audit a proof file for internal correctness
- `/alethic-check --file derivation.md` --- same, explicit flag
- `/alethic-check -K 5 -p thorough paper_section3.md` --- thorough audit with 5 reviewers
- `/alethic-check -p quick "Let x=1. Then x^2=1. Therefore x=1. QED."` --- quick inline check
- `/alethic-check .alethic/solve-sqrt2-20260225-a1b2/` --- audit a previous session's output
- `/alethic-check -q --json --file proof.md` --- quiet JSON output
- `/alethic-check --tools sympy,numpy --file proof.md` --- SymPy + NumPy only
- `/alethic-check -d physics --file derivation.md` --- force physics domain

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
   REF_DIR=$(echo "$ORCH" | sed "s|alethic-common/verify-orchestrator.md|alethic-check/references|")
   COMMON_REF_DIR=$(echo "$ORCH" | sed "s|verify-orchestrator.md|references|")
   echo "REFERENCES: $REF_DIR"
   echo "COMMON_REFERENCES: $COMMON_REF_DIR"
   ```

3. Read the orchestrator file at the path found above.

4. Follow the orchestrator instructions exactly, using this skill's Domain Configuration and references directories.
