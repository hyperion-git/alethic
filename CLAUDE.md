# CLAUDE.md

## Overview

**Alethic** is a mathematical reasoning agent inspired by [Google DeepMind's Aletheia](https://arxiv.org/abs/2602.10177), built on Claude (Opus 4.6). It implements a Generate → Verify → Revise loop with decoupled verification — the Verifier evaluates solutions independently, without access to the Generator's intermediate reasoning traces.

## Dev Commands

```bash
# Create and activate environment
micromamba create -n alethic python=3.13 -y
micromamba activate alethic
pip install -e ".[dev]"

# Run tests (mocked API, no key needed)
pytest

# Run tests with coverage
pytest --cov=alethic

# Lint
ruff check src tests

# Format
ruff format src tests

# Type check
mypy src/alethic

# Run CLI (requires ANTHROPIC_API_KEY)
alethic "Prove that sqrt(2) is irrational"

# Run examples
python -m alethic.examples --list
python -m alethic.examples --pick 1
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestrator Loop                     │
│                                                         │
│   ┌───────────┐    ┌──────────┐    ┌──────────┐       │
│   │ Generator  │───▶│ Verifier │───▶│ Reviser  │──┐    │
│   │  (T=1.0)  │    │  (T=0.2) │    │  (T=0.7) │  │    │
│   └───────────┘    └──────────┘    └──────────┘  │    │
│        ▲                                          │    │
│        └──────────────────────────────────────────┘    │
│                                                         │
│   Terminates when: CORRECT verdict OR max iterations    │
└─────────────────────────────────────────────────────────┘
```

## Module Map

| Module | Purpose |
|--------|---------|
| `agent.py` | `MathAgent` orchestrator — runs the Generate → Verify → Revise loop |
| `subagents.py` | `generate()`, `verify()`, `revise()` — each wraps a Claude API call with role-specific prompts |
| `models.py` | Dataclasses: `AgentConfig`, `Solution`, `VerificationResult`, `Revision`, `AgentResult`, `Verdict` enum |
| `prompts.py` | System/user prompt templates for all three subagents + balanced prompting addendum |
| `tools.py` | `execute_python()` sandbox, `PYTHON_TOOL` schema, `process_tool_calls()` for tool-use loop |
| `cli.py` | `argparse`-based CLI (`alethic` entry point) |
| `examples.py` | Bundled example problems (`python -m alethic.examples`) |

## Key Design Decisions

1. **Decoupled verification**: The Verifier receives ONLY the final solution text, never the Generator's thinking traces. This prevents confidence inflation on erroneous solutions.
2. **Structured output parsing**: Verifier output is parsed via regex (`_parse_verification`) for `VERDICT:`, `CONFIDENCE:`, `CRITIQUE:`, `ISSUES:` fields rather than JSON, since natural-language output is more reliable from the model.
3. **Sandboxed code execution**: `execute_python()` uses restricted `__builtins__` and an allowlist of importable modules. Timeout via `signal.SIGALRM`.
4. **Tool-use loop**: `_call_model()` in `subagents.py` handles multi-round tool calls (up to 5 rounds) before extracting the final text response.
5. **Strategic failure admission**: After exhausting `max_iterations`, the agent returns `Verdict.UNSOLVED` with the best solution seen, rather than hallucinating confidence.
