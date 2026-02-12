# Alethic

A mathematical reasoning agent inspired by [Google DeepMind's Aletheia](https://arxiv.org/abs/2602.10177), built on **Claude (Opus 4.6)**.

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
3. **Balanced prompting** — The Generator explores counterexamples before committing to a proof strategy (anti-confirmation-bias technique).
4. **Tool integration** — Sandboxed Python code execution for computational verification.
5. **Configurable compute budget** — `max_iterations` and `max_revisions_per_cycle` control the reasoning loop depth.

## Installation

```bash
pip install alethic
```

Or install from source:

```bash
git clone https://github.com/hyperion-git/alethic.git
cd alethic
pip install -e ".[dev]"
```

## Quick Start

### Python API

```python
from alethic import MathAgent, AgentConfig

# Uses ANTHROPIC_API_KEY env var
agent = MathAgent()
result = agent.solve("Prove that the square root of 2 is irrational.")

print(result)         # Full formatted output
print(result.solved)  # True/False
print(result.confidence)  # 0.0–1.0
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

# Use a different model
alethic --model claude-sonnet-4-5-20250929 "What is 17 * 23?"

# Disable code execution
alethic --no-code "Prove Euler's identity"
```

### Configuration

```python
from alethic import MathAgent, AgentConfig

config = AgentConfig(
    model="claude-opus-4-6",           # Model ID
    max_iterations=5,                   # Max generate-verify-revise cycles
    max_revisions_per_cycle=3,          # Max revisions before restarting
    enable_code_execution=True,         # Python sandbox for computation
    temperature_generator=1.0,          # Generator sampling temperature
    temperature_verifier=0.2,           # Verifier temperature (lower = stricter)
    temperature_reviser=0.7,            # Reviser temperature
    max_tokens=16384,                   # Max tokens per API call
    verbose=True,                       # Print progress
)

agent = MathAgent(config=config)
```

## How It Works

### Three-Subagent Architecture

| Subagent | Role | Temperature | Key Feature |
|----------|------|-------------|-------------|
| **Generator** | Produces candidate solutions | 1.0 (creative) | Balanced prompting explores counterexamples first |
| **Verifier** | Independently evaluates solutions | 0.2 (strict) | Decoupled — never sees Generator's reasoning chain |
| **Reviser** | Fixes issues based on critique | 0.7 (moderate) | Preserves correct parts, rewrites flawed sections |

### Verdict Types

| Verdict | Meaning | Action |
|---------|---------|--------|
| `CORRECT` | Solution verified as rigorous | Return solution |
| `MINOR_ISSUES` | Core is sound, needs small fixes | Send to Reviser |
| `MAJOR_FLAW` | Critical logical error | Revise or restart from Generator |
| `UNSOLVED` | Cannot solve reliably | Admit failure |

### The Loop

1. **Generator** produces a candidate solution with extended reasoning
2. **Verifier** evaluates it independently (no access to thinking traces)
3. If `CORRECT` → return the solution
4. If `MINOR_ISSUES` or `MAJOR_FLAW` → **Reviser** improves the solution
5. Re-verify the revision; repeat up to `max_revisions_per_cycle`
6. If still flawed, restart from the Generator (next iteration)
7. After `max_iterations` — admit failure with the best solution seen

## Examples

```bash
# List available examples
python -m alethic.examples --list

# Run a specific example
python -m alethic.examples --pick 1

# Run all examples
python -m alethic.examples
```

## Testing

```bash
# Run all tests (mocked API, no key needed)
pytest

# With coverage
pytest --cov=alethic

# Only live tests (requires ANTHROPIC_API_KEY)
pytest -m live
```

## Background: DeepMind's Aletheia

This project is inspired by Google DeepMind's Aletheia agent, announced February 2026:

- **Paper**: [Towards Autonomous Mathematics Research (arXiv:2602.10177)](https://arxiv.org/abs/2602.10177)
- **Companion**: [Accelerating Scientific Research with Gemini (arXiv:2602.03837)](https://arxiv.org/abs/2602.03837)
- **Erdős study**: [Semi-Autonomous Mathematics Discovery (arXiv:2601.22401)](https://arxiv.org/abs/2601.22401)

DeepMind's Aletheia achieved 95% on IMO-ProofBench Advanced and autonomously solved open Erdős conjectures. The key architectural insight — decoupling verification from generation — translates directly to Claude's API, which is what this project implements.

## License

MIT
