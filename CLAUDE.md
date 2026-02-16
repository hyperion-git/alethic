# CLAUDE.md

## Overview

**Alethic** is a reasoning agent for mathematics and physics inspired by [Google DeepMind's Aletheia](https://arxiv.org/abs/2602.10177), built on Claude (Opus 4.6). It implements the Aletheia Generate → Verify → Revise loop with two key techniques from the paper: **decoupled verification** (the Verifier evaluates solutions independently, without access to the Generator's reasoning traces) and **best-of-N sampling** (each iteration generates N candidate solutions in parallel, verifies all, selects the best, and revises only the winner). The orchestrator logic is domain-neutral; only the prompt templates differ between math (`MathAgent`, `/alethic-solve`) and physics (`PhysicsAgent`, `/alethic-derive`).

The Python library generates candidates in parallel via `ThreadPoolExecutor` when N > 1; Claude Code skills generate sequentially. Both support configurable N through presets (quick=1, default=2, thorough=3, extreme=5) or the `--best-of` flag.

Available as Claude Code skills (`/alethic-solve` for math, `/alethic-derive` for physics, `/alethic-scientific-figure` for scientific figures) or as a standalone **Python library** with CLI.

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
alethic solve "Prove that sqrt(2) is irrational"
alethic derive "Derive the energy levels of the quantum harmonic oscillator"
alethic --preset quick "Is 17 prime?"
alethic --preset thorough "Prove the Cayley-Hamilton theorem"
alethic derive --preset thorough "Derive the hydrogen atom energy spectrum"
alethic --thinking "Prove the Basel problem"
alethic --best-of 3 "Prove the Cayley-Hamilton theorem"
alethic --preset thorough -B 5 "Prove the Riemann mapping theorem"
alethic derive -B 3 "Derive the hydrogen atom energy spectrum"

# Run examples
python -m alethic.examples --list
python -m alethic.examples --pick 1
```

## Skill Installation

### Via marketplace (recommended)

```bash
claude plugins add hyperion-git/alethic
```

### Manual installation (development)

```bash
DEST=~/.claude/plugins/cache/local/alethic/0.2.0
mkdir -p "$DEST"
cp -r .claude-plugin skills "$DEST/"

# Register in installed_plugins.json (if not already present)
# Then restart Claude Code:
# /alethic-solve "Prove sqrt(2) is irrational"
# /alethic-derive "Derive the energy levels of the quantum harmonic oscillator"
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Orchestrator Loop                            │
│                                                                   │
│   ┌───────────┐                                                  │
│   │Generator 1│──┐                                               │
│   │  (T=1.0)  │  │   ┌──────────┐    ┌──────────┐              │
│   ├───────────┤  ├──▶│ Verifier │───▶│ Reviser  │──┐           │
│   │Generator 2│  │   │  (T=0.2) │    │  (T=0.7) │  │           │
│   │  (T=1.0)  │──┤   └──────────┘    └──────────┘  │           │
│   ├───────────┤  │   (verify all,     (best only)   │           │
│   │    ...    │──┘    select best)                   │           │
│   └───────────┘                                      │           │
│        ▲                  N candidates               │           │
│        └─────────────────────────────────────────────┘           │
│                                                                   │
│   Terminates when: CORRECT (≥ threshold) OR max iters            │
└──────────────────────────────────────────────────────────────────┘
```

Each iteration generates N candidates (best-of-N sampling), verifies all independently, selects the highest-confidence candidate, and revises only that winner. The Python library runs generation in parallel (`ThreadPoolExecutor`); Claude Code skills run sequentially. When N=1 (the `quick` preset default), the loop reduces to the classic single-candidate Generate → Verify → Revise cycle.

## Presets

Both the CLI (`--preset`) and the Python API (`AgentConfig.from_preset()`) support named presets. Explicit flags/kwargs override preset values.

| Preset | Iters | Revs | Threshold | Best-of | Thinking | Think budget | Max tokens |
|--------|-------|------|-----------|---------|----------|-------------|------------|
| `quick` | 2 | 1 | 0.85 | 1 | off | — | 16,384 |
| `default` | 5 | 3 | 0.90 | 2 | off | — | 16,384 |
| `thorough` | 8 | 5 | 0.95 | 3 | on | 15,000 | 32,768 |
| `extreme` | 12 | 5 | 0.97 | 5 | on | 40,000 | 65,536 |

Both `/alethic-solve` and `/alethic-derive` support presets via `-p`/`--preset`, controlling iterations, revisions, budget, confidence threshold, and best-of-N candidates. Temperature and extended thinking are API-only (Task sub-agent limitation). Explicit flags (e.g., `-B 5`) override preset values.

## Module Map

| Module | Purpose |
|--------|---------|
| `agent.py` | `MathAgent` orchestrator — runs the Generate N → Verify all → Select best → Revise loop with best-of-N sampling (parallel via `ThreadPoolExecutor`), false-premise detection, and candidate ranking |
| `physics_agent.py` | `PhysicsAgent` — thin subclass of `MathAgent` that injects physics-specific prompt templates |
| `subagents.py` | `generate()`, `verify()`, `revise()` — each wraps a Claude API call with role-specific prompts; accepts optional prompt kwargs for domain specialization; supports extended thinking |
| `models.py` | Dataclasses: `AgentConfig` (with `PRESETS`, `from_preset()`, and `best_of_n` field), `Solution`, `VerificationResult`, `Revision`, `AgentResult` (with `candidates_per_iteration`), `Verdict` enum |
| `prompts.py` | Math system/user prompt templates for all three subagents + balanced prompting addendum |
| `physics_prompts.py` | Physics-specific prompt templates: derivation strategies, physics error checklist, dimensional/limiting-case balanced addendum |
| `tools.py` | `execute_python()` sandbox, `PYTHON_TOOL` schema, `process_tool_calls()` for tool-use loop |
| `cli.py` | `argparse`-based CLI (`alethic` entry point) with `solve`/`derive` subcommands, `--preset`, `--thinking`, and `--best-of`/`-B` support |
| `examples.py` | Bundled example problems (`python -m alethic.examples`) |

| Skill file | Purpose |
|------------|---------|
| `skills/alethic-solve/SKILL.md` | `/alethic-solve` command orchestrator — spawns Opus Task sub-agents with file-based state, best-of-N candidate generation, and monitoring dashboard |
| `skills/alethic-derive/SKILL.md` | `/alethic-derive` command orchestrator — physics derivations with physics-specific prompts, best-of-N candidate generation, and monitoring dashboard |
| `skills/alethic-scientific-figure/SKILL.md` | `/alethic-scientific-figure` command — publication-quality scientific figures with AFP color palette and Tufte principles |
| `skills/alethic-scientific-figure/references/*.md` | Color palette reference, presentation/poster overrides |
| `skills/alethic-scientific-figure/scripts/register_colormaps.py` | Registers 56 CIELAB-linearized AFP colormaps with matplotlib |
| `skills/alethic-scientific-figure/evals.json` | Evaluation scenarios for the `/alethic-scientific-figure` skill |
| `.claude-plugin/plugin.json` | Plugin metadata |
| `.claude-plugin/marketplace.json` | Marketplace manifest for `hyperion-git/alethic` |
| `skills/alethic-solve/references/*.md` | Standalone math prompt references (generator, verifier, reviser, beautifier) |
| `skills/alethic-derive/references/*.md` | Standalone physics prompt references (generator, verifier, reviser, beautifier) |

## Key Design Decisions

1. **Decoupled verification**: The Verifier receives ONLY the final solution text, never the Generator's thinking traces. In the skill, this is enforced by architecture — Task sub-agents get fresh context windows.
2. **Domain-neutral orchestrator**: The Generate N → Verify all → Select best → Revise loop (including best-of-N sampling) is identical for math and physics. `PhysicsAgent` overrides only the prompt templates via optional kwargs to `generate()`, `verify()`, `revise()` in `subagents.py`. No orchestrator code is duplicated.
3. **Configurable confidence threshold**: Solutions require `CORRECT` verdict AND confidence ≥ `confidence_threshold` (default 0.90). Correct-but-uncertain solutions are treated as minor issues and sent for revision.
4. **False-premise detection**: The Verifier's `REASON:` field enables early exit when a problem's premise is false (e.g., contradicts Brouwer's fixed point theorem).
5. **Structured output parsing**: Verifier output is parsed via regex (`_parse_verification`) for `VERDICT:`, `CONFIDENCE:`, `CRITIQUE:`, `REASON:`, `ISSUES:` fields with independent extraction per field.
6. **Sandboxed code execution**: `execute_python()` uses restricted `__builtins__` and an allowlist of importable modules (math, sympy, numpy, scipy, mpmath). Timeout via `signal.SIGALRM`.
7. **Tool-use loop**: `_call_model()` in `subagents.py` handles multi-round tool calls (up to 5 rounds) before extracting the final text response.
8. **Strategic failure admission**: After exhausting `max_iterations`, the agent returns `Verdict.UNSOLVED` with the best solution seen, rather than hallucinating confidence.
9. **File-based state** (skill only): Session directories in `.alethic/` (project-local) prevent context window exhaustion — the orchestrator tracks only verdicts and confidence, full text lives in files. Falls back to `/tmp/alethic-*/` outside git repositories.
10. **Best-of-N sampling**: Each iteration generates N candidates (configurable via `--best-of` / `-B`), verifies all, selects the highest-confidence candidate, and revises only the winner. The Python library uses `ThreadPoolExecutor` for parallel generation when N>1; skills generate sequentially and display a monitoring dashboard with candidate rankings and cumulative iteration history. When N=1, behavior is identical to the pre-best-of-N code path (no thread pool, same log messages, same history shape). `AgentResult` includes `candidates_per_iteration` metadata. Preset defaults: quick=1, default=2, thorough=3, extreme=5.

### Session Directory Layout (skills only)

Skills persist sessions in `.alethic/` within the project directory (detected via `.git`). Each session gets a `{slug}-{YYYYMMDD}-{4hex}/` directory containing `session.json` (metadata), `problem.md`, `output.md` (final deliverable), and a `worklog/` subdirectory for intermediate files. When best-of-N > 1, each iteration's worklog contains `candidate_{C}.md` and `verification_c{C}.md` files for each candidate, with the best candidate copied to the standard `solution.md` / `verification.md` locations. An append-only `sessions.jsonl` index at the `.alethic/` root enables querying across sessions. Falls back to `/tmp/alethic-*` outside git repositories. The Python library (`MathAgent`, `PhysicsAgent`) is unaffected — it uses in-memory `AgentResult` objects.
