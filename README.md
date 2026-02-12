# Alethic

A mathematical reasoning agent inspired by [Google DeepMind's Aletheia](https://arxiv.org/abs/2602.10177), built on **Claude (Opus 4.6)**.

Available as a **Claude Code `/solve` skill** (recommended) or as a standalone **Python library** with CLI.

## Architecture

Alethic implements a **Generate → Verify → Revise** loop with **decoupled verification** — the core insight from DeepMind's design where the Verifier evaluates solutions independently, without access to the Generator's intermediate reasoning traces.

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestrator Loop                     │
│                                                         │
│   ┌───────────┐    ┌──────────┐    ┌──────────┐       │
│   │ Generator  │───▶│ Verifier │───▶│ Reviser  │──┐    │
│   └───────────┘    └──────────┘    └──────────┘  │    │
│        ▲                                          │    │
│        └──────────────────────────────────────────┘    │
│                                                         │
│   Terminates when: CORRECT verdict OR max iterations    │
└─────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Decoupled verification** — The Verifier sees only the final output, never the Generator's thinking traces. This prevents confidence inflation on erroneous solutions.
2. **Strategic failure admission** — The agent can declare "unsolved" rather than hallucinating an incorrect answer.
3. **False-premise detection** — The Verifier can identify when a problem's premise is false (e.g., asking to prove something that contradicts a known theorem) and halt early.
4. **Balanced prompting** — The Generator explores counterexamples before committing to a proof strategy (anti-confirmation-bias technique from DeepMind).
5. **Confidence threshold** — Solutions require both a `CORRECT` verdict and ≥90% confidence to be accepted. Correct-but-uncertain solutions are sent back for revision.
6. **Configurable compute budget** — `max_iterations`, `max_revisions_per_cycle`, and total sub-agent budget control the reasoning loop depth.

## Claude Code Skill (Recommended)

The `/solve` command runs Alethic natively inside Claude Code, using Task sub-agents for true architectural decoupling — each Verifier gets a fresh context window and literally cannot see Generator reasoning.

### Install

```bash
# Clone the repo
git clone https://github.com/hyperion-git/alethic.git

# Copy the skill into your Claude Code skills directory
mkdir -p ~/.claude/skills/solve
cp alethic/skill/skills/solve/SKILL.md ~/.claude/skills/solve/SKILL.md
```

Restart Claude Code. The `/solve` command is now available.

### Usage

```
/solve "Prove that sqrt(2) is irrational"

/solve -i 3 "Prove the AM-GM inequality for n variables"

/solve -i 5 -r 4 -b 80 "Prove the Fundamental Theorem of Algebra"
```

**Flags:**
| Flag | Default | Description |
|------|---------|-------------|
| `-i` | 5 | Max generate-verify-revise iterations |
| `-r` | 3 | Max revisions per iteration |
| `-b` | 50 | Total sub-agent call budget |

### How It Works

1. **Generator** (Opus Task agent) — reads `problem.md`, uses Bash/WebSearch, writes `solution.md`
2. **Verifier** (Opus Task agent, fresh context) — reads ONLY `problem.md` + `solution.md`, writes `verification.md`
3. **Reviser** (Opus Task agent) — reads solution + critique, writes `revision_{N}.md`
4. **Beautifier** (Opus Task agent) — formats the accepted solution into clean LaTeX/markdown

All state lives in `/tmp/alethic-{timestamp}/` — the orchestrator tracks only verdicts and confidence scores in its own context, preventing context window exhaustion across iterations.

## Python Library

For programmatic use or batch benchmarking. Requires an `ANTHROPIC_API_KEY`.

### Install

```bash
pip install -e ".[dev]"  # from source
```

### Quick Start

```python
from alethic import MathAgent, AgentConfig

agent = MathAgent()  # uses ANTHROPIC_API_KEY env var
result = agent.solve("Prove that the square root of 2 is irrational.")

print(result)            # Full formatted output
print(result.solved)     # True/False
print(result.confidence) # 0.0-1.0
```

### CLI

```bash
# Inline problem
alethic "Prove that there are infinitely many primes"

# From file
alethic --file problem.txt

# JSON output
alethic --json "Solve x^2 - 5x + 6 = 0"

# Control iterations
alethic --iterations 3 "Prove the AM-GM inequality"

# Extended thinking (deeper reasoning, more tokens)
alethic --thinking --thinking-budget 20000 "Prove the Basel problem"

# Disable code execution
alethic --no-code "Prove Euler's identity"
```

### Configuration

```python
config = AgentConfig(
    model="claude-opus-4-6",           # Model ID
    max_iterations=5,                   # Max generate-verify-revise cycles
    max_revisions_per_cycle=3,          # Max revisions before restarting
    enable_code_execution=True,         # Python sandbox for computation
    temperature_generator=1.0,          # Generator sampling temperature
    temperature_verifier=0.2,           # Verifier temperature (lower = stricter)
    temperature_reviser=0.7,            # Reviser temperature
    max_tokens=16384,                   # Max tokens per API call
    extended_thinking=False,            # Enable extended thinking
    thinking_budget=10000,              # Token budget for extended thinking
    verbose=True,                       # Print progress
)

agent = MathAgent(config=config)
```

## Three-Subagent Architecture

| Subagent | Role | Temperature | Key Feature |
|----------|------|-------------|-------------|
| **Generator** | Produces candidate solutions | 1.0 (creative) | Balanced prompting explores counterexamples first |
| **Verifier** | Independently evaluates solutions | 0.2 (strict) | Decoupled — never sees Generator's reasoning chain |
| **Reviser** | Fixes issues based on critique | 0.7 (moderate) | Preserves correct parts, rewrites flawed sections |

### Verdict Types

| Verdict | Meaning | Action |
|---------|---------|--------|
| `CORRECT` (≥90% confidence) | Solution verified as rigorous | Accept and return |
| `CORRECT` (<90% confidence) | Likely correct but uncertain | Send to Reviser |
| `MINOR_ISSUES` | Core is sound, needs small fixes | Send to Reviser |
| `MAJOR_FLAW` | Critical logical error | Revise or restart from Generator |
| `UNSOLVED` (with reason) | Problem premise is false | Return with explanation |
| `UNSOLVED` | Cannot solve reliably | Admit failure |

### Verifier Output Format

```
VERDICT: correct | minor_issues | major_flaw | unsolved
CONFIDENCE: 0.0 to 1.0

CRITIQUE:
[Step-by-step evaluation]

REASON: [Why the premise is false, or "N/A"]

ISSUES:
- [Issue 1]
- [Issue 2]
```

## Project Structure

```
alethic/
├── skill/                          # Claude Code skill plugin
│   ├── .claude-plugin/
│   │   └── plugin.json             # Plugin metadata (v0.2.0)
│   └── skills/solve/
│       └── SKILL.md                # /solve command orchestrator
├── src/alethic/                    # Python library
│   ├── agent.py                    # MathAgent orchestrator
│   ├── subagents.py                # generate(), verify(), revise()
│   ├── models.py                   # Data models + Verdict enum
│   ├── prompts.py                  # System/user prompt templates
│   ├── tools.py                    # Python sandbox + tool-use loop
│   ├── cli.py                      # CLI entry point
│   └── examples.py                 # Bundled example problems
├── docs/prompts/                   # Standalone prompt references
│   ├── generator.md
│   ├── verifier.md
│   ├── reviser.md
│   └── beautifier.md
└── tests/
    └── test_alethic.py             # 34 tests (mocked API)
```

## Testing

```bash
# Run all tests (mocked API, no key needed)
pytest

# With coverage
pytest --cov=alethic

# Lint
ruff check src tests

# Format
ruff format src tests
```

## Background: DeepMind's Aletheia

This project is inspired by Google DeepMind's Aletheia agent, announced February 2026:

- **Paper**: [Towards Autonomous Mathematics Research (arXiv:2602.10177)](https://arxiv.org/abs/2602.10177)
- **Companion**: [Accelerating Scientific Research with Gemini (arXiv:2602.03837)](https://arxiv.org/abs/2602.03837)
- **Erdős study**: [Semi-Autonomous Mathematics Discovery (arXiv:2601.22401)](https://arxiv.org/abs/2601.22401)

DeepMind's Aletheia achieved 95% on IMO-ProofBench Advanced and autonomously solved open Erdős conjectures. The key architectural insight — decoupling verification from generation — translates directly to Claude's API, which is what this project implements.

## License

MIT
