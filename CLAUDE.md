# CLAUDE.md

## Overview

**Alethic** is a reasoning agent for mathematics and physics inspired by [Google DeepMind's Aletheia](https://arxiv.org/abs/2602.10177), built on Claude (Opus 4.6). It implements the Aletheia Generate → Verify → Revise loop with two key techniques from the paper: **decoupled verification** (the Verifier evaluates solutions independently, without access to the Generator's reasoning traces) and **best-of-N sampling** (each iteration generates N candidate solutions in parallel, verifies all, selects the best, and revises only the winner). The orchestrator logic is domain-neutral; only the prompt templates differ between math (`MathAgent`, `/alethic-solve`) and physics (`PhysicsAgent`, `/alethic-derive`).

The Python library generates candidates in parallel via `ThreadPoolExecutor` when N > 1; Claude Code skills generate sequentially. Both support configurable N through presets (quick=1, default=2, thorough=3, extreme=5) or the `--best-of` flag.

Additionally, `verify` and `check` commands provide standalone multi-verifier consensus: K independent verifiers evaluate a solution in parallel, results are mechanically aggregated (majority-vote verdict, mean confidence, issue union), then an LLM synthesizes a unified critique. `verify` assesses correctness against a problem statement; `check` audits internal consistency without one.

Available as Claude Code skills (`/alethic-solve` for math, `/alethic-derive` for physics, `/alethic-verify` and `/alethic-check` for consensus verification, `/alethic-scientific-figure` for scientific figures) or as a standalone **Python library** with CLI.

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
alethic --tools sympy "Prove the Basel problem"             # SymPy only (no NumPy)
alethic --tools none "Is 17 prime?"                         # no tool guidance
alethic derive --tools sympy,numpy "Derive the Lamb shift"  # both (default)
alethic --no-stall-reset "Is 17 prime?"                     # disable stall detection
alethic --stall-window 3 --stall-epsilon 0.05 "..."         # custom stall parameters
alethic --variant-b-model claude-sonnet-4-6 "..."           # explicit variant B model
alethic --no-variant-b "..."                                # disable variant B diversity

# Verify/check (standalone consensus verification)
alethic verify solution.md --problem-text "Is 1+1=2?"      # verify solution against problem
alethic verify .alethic/session-dir/ --verifiers 5          # verify from session dir, K=5
alethic verify solution.md -P problem.txt                   # problem from file
alethic check derivation.md                                 # internal consistency check
alethic check solution.md --domain physics                  # override domain detection
alethic check solution.md --json                            # JSON output
alethic verify solution.md --problem-text "..." --preset thorough  # preset controls K & thinking

# Textbook-style output (skills only)
# /alethic-solve --textbook "Prove sqrt(2) is irrational"
# /alethic-solve -p thorough --textbook "Prove the Cayley-Hamilton theorem"
# /alethic-derive --textbook "Derive harmonic oscillator energy levels"
# /alethic-textbook .alethic/{existing-session}/
# /alethic-textbook --domain physics derivation.md

# New skill CLI flags
# /alethic-solve --no-balanced "Prove sqrt(2) is irrational"  # skip counterexample check
# /alethic-solve --file problem.md                            # read problem from file
# /alethic-solve -q -p thorough "..."                         # quiet mode (no dashboard)
# /alethic-solve --json "Is 17 prime?"                        # JSON output
# /alethic-solve --model sonnet "..."                         # use Sonnet for sub-agents
# /alethic-solve --tools sympy "Prove the Basel problem"       # SymPy only (no NumPy)
# /alethic-solve --tools none "Is 17 prime?"                   # no tool guidance
# /alethic-solve --no-stall-reset "Is 17 prime?"               # disable stall detection
# /alethic-solve --stall-window 3 --stall-epsilon 0.05 "..."   # custom stall parameters

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
DEST=~/.claude/plugins/cache/local/alethic/3.0.3
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

| Preset | Iters | Revs | Threshold | Best-of | Thinking | Think budget | Max tokens | Stall reset | Window | Epsilon | N boost | Variant B |
|--------|-------|------|-----------|---------|----------|-------------|------------|-------------|--------|---------|---------|-----------|
| `quick` | 2 | 1 | 0.85 | 1 | off | — | 16,384 | off | — | — | 0 | — |
| `default` | 5 | 3 | 0.90 | 2 | off | — | 16,384 | on | 2 | 0.03 | 1 | — |
| `thorough` | 8 | 5 | 0.95 | 3 | on | 15,000 | 32,768 | on | 3 | 0.02 | 1 | Sonnet |
| `extreme` | 12 | 5 | 0.97 | 5 | on | 40,000 | 65,536 | on | 3 | 0.02 | 2 | Sonnet |

Both `/alethic-solve` and `/alethic-derive` support presets via `-p`/`--preset`, controlling iterations, revisions, budget, confidence threshold, and best-of-N candidates. Temperature and extended thinking are API-only (Task sub-agent limitation). Explicit flags (e.g., `-B 5`) override preset values.

### Verify/Check Presets

The `verify` and `check` commands use `VerifierConfig` presets controlling verifier count and thinking. Explicit flags (e.g., `-K 5`) override preset values.

| Preset | Verifiers (K) | Thinking | Think budget | Max tokens |
|--------|--------------|----------|-------------|------------|
| `quick` | 2 | off | — | 16,384 |
| `default` | 3 | off | — | 16,384 |
| `thorough` | 5 | on | 15,000 | 32,768 |
| `extreme` | 7 | on | 40,000 | 65,536 |

## Module Map

| Module | Purpose |
|--------|---------|
| `agent.py` | `MathAgent` orchestrator — runs the Generate N → Verify all → Select best → Revise loop with best-of-N sampling (parallel via `ThreadPoolExecutor`), variant-B model diversity (odd-indexed candidates use alternate config), FIXABLE verdict shortcut (re-verifies corrected solution, accepts if passing), false-premise detection, candidate ranking, failed approach tracking via `RunState`, structured event logging via `EventLog`, switchable tool guidance via `_build_system_prompt()`/`_get_tool_guidance_map()`, and stall detection with strategy reset via `_check_stall()`/`_build_reset_context()` |
| `physics_agent.py` | `PhysicsAgent` — thin subclass of `MathAgent` that injects physics-specific prompt templates and overrides `_get_tool_guidance_map()` to return `PHYSICS_TOOL_GUIDANCE` and `_reset_addendum()` to return `PHYSICS_STRATEGY_RESET_ADDENDUM` |
| `subagents.py` | `generate()`, `verify()`, `revise()` — each wraps a Claude API call with role-specific prompts; accepts optional prompt kwargs for domain specialization; supports extended thinking |
| `models.py` | Dataclasses: `AgentConfig` (with `PRESETS`, `from_preset()`, `build_variant_b_config()`, `best_of_n`, `tool_guidance: frozenset[str]`, `stall_window`, `stall_epsilon`, `stall_reset`, `reset_n_boost`, `variant_b: dict | None` fields), `VerifierConfig` (with `PRESETS`, `from_preset()`, `num_verifiers`, `domain`, `tool_guidance` fields), `ConsensusResult` (verdict, confidence, confidence_range, critique, issues, individual_results), `ConsensusIssue` (text, severity, flagged_by), `Solution`, `VerificationResult` (with `Issue`, `SectionConfidence`, `corrected_solution: str | None`, severity-aware `is_acceptable()`, `has_correction` property), `Revision`, `AgentResult` (with `AgentEvent` list, `failed_approaches`), `Verdict` enum (CORRECT, MINOR_ISSUES, FIXABLE, MAJOR_FLAW, UNSOLVED), `IssueSeverity` enum, `EventType` enum (incl. `STALL_RESET`) |
| `prompts.py` | Math system/user prompt templates for all three subagents + balanced prompting addendum + `STRATEGY_RESET_ADDENDUM` + `TOOL_GUIDANCE` map (SymPy/NumPy generator/verifier guidance strings); verifier includes FIXABLE verdict and citation-checking instructions |
| `physics_prompts.py` | Physics-specific prompt templates: derivation strategies, physics error checklist, dimensional/limiting-case balanced addendum + `PHYSICS_STRATEGY_RESET_ADDENDUM` + `PHYSICS_TOOL_GUIDANCE` map (physics-specific SymPy/NumPy guidance); verifier includes FIXABLE verdict and citation-checking instructions |
| `tools.py` | `execute_python()` sandbox, `PYTHON_TOOL` schema (highlights SymPy as `sp` and NumPy as `np`), `process_tool_calls()` for tool-use loop |
| `verifier_agent.py` | `VerifierAgent` and `CheckerAgent` — K-verifier consensus pipeline with `ThreadPoolExecutor` parallelism, domain auto-detection, mechanical aggregation + LLM critique synthesis; `VerifierAgent.verify(problem, solution)` and `CheckerAgent.check(solution)` |
| `synthesizer.py` | Two-stage consensus synthesis: `aggregate_mechanical()` (majority-vote verdict, mean confidence, issue dedup via `SequenceMatcher`) + `synthesize_critique()` (LLM cleanup) |
| `check_prompts.py` | Check-specific prompt templates (`CHECKER_SYSTEM`, `CHECKER_USER`) for solution-only internal consistency auditing + `CHECK_TOOL_GUIDANCE` map (SymPy/NumPy/SciPy/Matplotlib verifier guidance); includes FIXABLE verdict and citation-checking instructions |
| `domain.py` | `detect_domain(text, override=None)` — static keyword dictionary (~500 terms, 3 weighted tiers) classifying text as `"math"` or `"physics"` |
| `session_reader.py` | `resolve_session_input(path)` — extracts `(problem, solution)` from `.alethic/` session directories (tries `output.md`, falls back to `worklog/solution.md`) |
| `output.py` | `format_consensus(result, mode, command)` — formats `ConsensusResult` as box-drawing text, JSON, or single-line quiet output |
| `cli.py` | `argparse`-based CLI (`alethic` entry point) with `solve`/`derive`/`verify`/`check` subcommands, `--preset`, `--thinking`, `--best-of`/`-B`, `--tools`, `--no-stall-reset`, `--stall-window`, `--stall-epsilon`, `--variant-b-model`, `--no-variant-b`, `--verifiers`/`-K`, `--problem-text`, `--problem-file`/`-P`, `--domain` support |
| `examples.py` | Bundled example problems (`python -m alethic.examples`) |

| Skill file | Purpose |
|------------|---------|
| `skills/alethic-common/orchestrator.md` | Shared GVR loop orchestrator (~805 lines) — parameterized by domain, reads prompts from references/*.md, handles session management, dashboard, textbook pipeline, event logging, stall detection with strategy reset, and all CLI flags |
| `skills/alethic-solve/SKILL.md` | `/alethic-solve` thin configurator — sets math domain variables, balanced approach addendum, strategy reset addendum, loads shared orchestrator |
| `skills/alethic-derive/SKILL.md` | `/alethic-derive` thin configurator — sets physics domain variables, balanced approach addendum, strategy reset addendum, loads shared orchestrator |
| `skills/alethic-verify/SKILL.md` | `/alethic-verify` thin configurator — sets mode=verify, requires_problem=true, loads shared verify-orchestrator |
| `skills/alethic-verify/references/verifier.md` | Standalone verifier prompt for correctness assessment against a problem statement |
| `skills/alethic-verify/references/tools/*.md` | Tool guidance overlays (sympy, numpy, scipy, matplotlib) for verification |
| `skills/alethic-check/SKILL.md` | `/alethic-check` thin configurator — sets mode=check, requires_problem=false, loads shared verify-orchestrator |
| `skills/alethic-check/references/checker.md` | Proof auditor prompt for internal consistency checking (6 evaluation criteria) |
| `skills/alethic-check/references/tools/*.md` | Tool guidance overlays (sympy, numpy, scipy, matplotlib) for checking |
| `skills/alethic-common/verify-orchestrator.md` | Shared verify/check orchestrator — K parallel verifier Task calls → mechanical aggregation → synthesizer Task call → formatted output |
| `skills/alethic-common/references/synthesizer.md` | Critique cleanup prompt for LLM-based synthesis of individual verifier critiques |
| `skills/alethic-common/references/domain-keywords.json` | Static keyword dictionary (~500 terms, 3 weighted tiers) for domain auto-detection |
| `skills/alethic-textbook/SKILL.md` | `/alethic-textbook` command — standalone textbook-style converter for existing sessions or raw .md files |
| `skills/alethic-scientific-figure/SKILL.md` | `/alethic-scientific-figure` command — publication-quality scientific figures with AFP color palette and Tufte principles |
| `skills/alethic-scientific-figure/references/*.md` | Color palette reference, presentation/poster overrides |
| `skills/alethic-scientific-figure/scripts/register_colormaps.py` | Registers 56 CIELAB-linearized AFP colormaps with matplotlib |
| `skills/alethic-scientific-figure/evals.json` | Evaluation scenarios for the `/alethic-scientific-figure` skill |
| `.claude-plugin/plugin.json` | Plugin metadata |
| `.claude-plugin/marketplace.json` | Marketplace manifest for `hyperion-git/alethic` |
| `skills/alethic-solve/references/*.md` | Authoritative math prompt templates (generator, verifier, reviser, beautifier, textbook planner/writer/fidelity) — read by orchestrator at runtime |
| `skills/alethic-solve/references/tools/*.md` | Switchable tool guidance overlays for math (sympy-generator, sympy-verifier, numpy-generator, numpy-verifier) — conditionally loaded by orchestrator based on `--tools` flag |
| `skills/alethic-derive/references/*.md` | Authoritative physics prompt templates (generator, verifier, reviser, beautifier, textbook planner/writer/fidelity) — read by orchestrator at runtime |
| `skills/alethic-derive/references/tools/*.md` | Switchable tool guidance overlays for physics (sympy-generator, sympy-verifier, numpy-generator, numpy-verifier) with `sympy.physics.*`, `scipy.constants`, etc. — conditionally loaded by orchestrator based on `--tools` flag |

## Key Design Decisions

1. **Decoupled verification**: The Verifier receives ONLY the final solution text, never the Generator's thinking traces. In the skill, this is enforced by architecture — Task sub-agents get fresh context windows.
2. **Domain-neutral orchestrator**: The Generate N → Verify all → Select best → Revise loop (including best-of-N sampling) is identical for math and physics. In the Python library, `PhysicsAgent` overrides only the prompt templates via optional kwargs to `generate()`, `verify()`, `revise()` in `subagents.py`. In the skills, both `/alethic-solve` and `/alethic-derive` are thin ~85-line configurators that define domain variables and load a shared `skills/alethic-common/orchestrator.md` (~805 lines). The orchestrator uses placeholders (`{noun}`, `{domain}`, `{verb}`, `{command}`, `{agent_title}`, `{references_dir}`, `{balanced_addendum}`, `{strategy_reset_addendum}`) and reads prompts from the skill's `references/*.md` at runtime. No orchestrator code is duplicated at either level.
3. **Configurable confidence threshold**: Solutions require `CORRECT` verdict AND confidence ≥ `confidence_threshold` (default 0.90). Correct-but-uncertain solutions are treated as minor issues and sent for revision.
4. **False-premise detection**: The Verifier's `REASON:` field enables early exit when a problem's premise is false (e.g., contradicts Brouwer's fixed point theorem).
5. **Structured output parsing**: Verifier output is parsed via regex (`_parse_verification`) for `VERDICT:`, `CONFIDENCE:`, `CRITIQUE:`, `REASON:`, `ISSUES:` fields with independent extraction per field.
6. **Sandboxed code execution with switchable tool guidance**: `execute_python()` runs code in a child subprocess for process-level isolation. Restricted `__builtins__` and an allowlist of importable modules (math, sympy, numpy, scipy, mpmath) provide defense-in-depth. SymPy is pre-imported as `sp` and NumPy as `np` in the sandbox. Tool-specific guidance (SymPy symbolic verification, NumPy/SciPy numerical verification) is modular and switchable via `--tools` (CLI/skill) or `AgentConfig.tool_guidance` (Python API). In the skills, guidance lives in overlay files (`references/tools/{tool}-{role}.md`) loaded by the orchestrator; in the Python library, guidance strings are stored in `TOOL_GUIDANCE` / `PHYSICS_TOOL_GUIDANCE` maps and appended to system prompts by `_build_system_prompt()`. Generators get advisory toolkits; Verifiers get mandatory re-derivation/spot-check requirements with RED FLAG escalation. Physics overlays additionally reference `sympy.physics.units`, `sympy.physics.quantum`, `scipy.constants`, and `scipy.integrate.solve_ivp`. Default: `sympy,numpy`; set to `none` to disable. Timeouts enforced at two levels: `signal.SIGALRM` in the child process and `subprocess.run(timeout=)` in the parent. Thread-safe.
7. **Tool-use loop**: `_call_model()` in `subagents.py` handles multi-round tool calls (up to 5 rounds) before extracting the final text response.
8. **Strategic failure admission**: After exhausting `max_iterations`, the agent returns `Verdict.UNSOLVED` with the best solution seen, rather than hallucinating confidence.
9. **File-based state** (skill only): Session directories in `.alethic/` (project-local) prevent context window exhaustion — the orchestrator tracks only verdicts and confidence, full text lives in files. Falls back to `/tmp/alethic-*/` outside git repositories.
10. **Best-of-N sampling**: Each iteration generates N candidates (configurable via `--best-of` / `-B`), verifies all, selects the highest-confidence candidate, and revises only the winner. The Python library uses `ThreadPoolExecutor` for parallel generation when N>1; skills generate sequentially and display a monitoring dashboard with candidate rankings and cumulative iteration history. When N=1, behavior is identical to the pre-best-of-N code path (no thread pool, same log messages, same history shape). `AgentResult` includes `candidates_per_iteration` metadata. Preset defaults: quick=1, default=2, thorough=3, extreme=5.
11. **Textbook-style converter** (skill only): The `--textbook` flag on `/alethic-solve` and `/alethic-derive` (or standalone `/alethic-textbook`) runs a staged sub-agent pipeline — Planner → Writer × N → Fidelity Verifier — that converts raw solutions into textbook-quality documents with theorem/definition/lemma environments (math) or setup/derivation/result environments (physics), pedagogical motivation, numbered equations with back-references, and connecting prose. The Planner adaptively decides section count based on solution length (1–8 sections), keeping each Writer's context bounded. The Fidelity Verifier compares the textbook version against the original on a 6-point checklist; MAJOR_ALTERATION triggers fallback to the simple beautifier. The orchestrator never reads solution text — only file paths and one-line summaries.

12. **Multi-verifier consensus (verify/check)**: K independent verifiers run in parallel via `ThreadPoolExecutor`, each producing a `VerificationResult`. Results are mechanically aggregated: majority-vote verdict (ties broken by severity), mean confidence with min/max range, and issue dedup via `SequenceMatcher` (0.6 similarity threshold) with vote counts. An LLM then synthesizes a unified critique from all individual critiques. `verify` requires a problem statement and assesses correctness; `check` audits internal consistency without one. Domain auto-detection uses a static keyword dictionary (~500 terms across 3 weighted tiers: strong=3, moderate=2, weak=1) to classify text as math or physics. Tool guidance defaults to `sympy,numpy,scipy,matplotlib` (broader than solve/derive). Both the Python library (`VerifierAgent`, `CheckerAgent`) and the skills (`/alethic-verify`, `/alethic-check`) implement the same pipeline; skills use a shared `verify-orchestrator.md` with thin configurators.

13. **Stall detection with strategy reset**: A lightweight monitoring layer detects when confidence stops improving (plateau: `iterations_since_meaningful_improvement >= stall_window`) or when `MAJOR_FLAW` verdicts repeat consecutively. On trigger, the next iteration widens best-of-N by `reset_n_boost`, injects a `STRATEGY_RESET_ADDENDUM` prompt (forcing a categorically different approach), and reduces revision budget to 1. All overrides are iteration-scoped (auto-revert). A cooldown of 1 iteration prevents back-to-back resets; total resets capped at `max(1, max_iterations // 4)`. Detection is deterministic; stochasticity comes from the LLM response. Disabled in the `quick` preset (too few iterations). Configurable via `--no-stall-reset`, `--stall-window`, `--stall-epsilon` (CLI/skill) or `AgentConfig` fields (Python API). `STALL_RESET` / `stall_reset` events are logged for post-hoc analysis. Both the Python library (`MathAgent._check_stall()`, `_build_reset_context()`) and the skill orchestrator (Step 2-pre) implement identical logic; domain-specific reset addenda live in `prompts.py`/`physics_prompts.py` (Python) and the thin SKILL.md configurators (skills).

14. **FIXABLE verdict with corrected-solution shortcut**: Verifiers can return a `FIXABLE` verdict (between `MINOR_ISSUES` and `MAJOR_FLAW`) with a complete corrected solution in a `CORRECTED SOLUTION` block. When the agent receives a FIXABLE verdict with a correction, it re-verifies the corrected solution immediately. If re-verification passes (acceptable confidence), the corrected solution is accepted directly — bypassing the revision cycle entirely. If re-verification fails, the corrected solution replaces the original and falls through to normal revision. This avoids burning revision budget on mechanical errors (sign mistakes, algebraic slips) that the verifier already knows how to fix. `VerificationResult` gains a `corrected_solution: str | None` field and `has_correction` property. The `FIXABLE` verdict is supported in all verifier/checker prompts (solve, derive, verify, check) and in the `synthesizer.py` consensus aggregation. Inspired by [Aletheia's FirstProof report](https://arxiv.org/abs/2602.21201).

15. **Variant-B model diversity**: In best-of-N sampling, odd-indexed candidates (1, 3, 5...) can use an alternate model configuration ("variant B") while even-indexed candidates (0, 2, 4...) use the primary config. This diversifies the solution pool by generating candidates from different model capabilities. Configured via `AgentConfig.variant_b: dict | None` — a dict of field overrides (e.g., `{"model": "claude-sonnet-4-6"}`) applied via `build_variant_b_config()`. Enabled by default in `thorough` and `extreme` presets (Sonnet variant); disabled in `quick` and `default`. CLI: `--variant-b-model MODEL` or `--no-variant-b`. When N=1, variant B has no effect (only candidate 0 is generated). The variant B client is reused if the model matches the primary; a separate `anthropic.Anthropic` client is created only when models differ. Inspired by Aletheia A/B dual-model approach in [arXiv:2602.21201](https://arxiv.org/abs/2602.21201).

16. **Citation and reference checking**: All verifier and checker prompts now include explicit instructions to verify citations and references. For every theorem, identity, lemma, or known result invoked in a solution: the verifier confirms it is either (a) proved within the solution itself, or (b) cited by specific name. Vague appeals ("it is well known", "by a standard result", "it can be shown that") are flagged as `[MINOR]` if the claim is independently verifiable, or `[MAJOR]` if it cannot be confirmed. This applies to all four prompt sets: solve verifier, derive verifier, standalone verify, and check.

17. **Problem-interpretation checking (anti-specification-gaming)**: All verifier prompts (except check, which has no problem statement) now explicitly verify that the solution addresses the intended, non-trivial interpretation of the problem. Flags as `[MAJOR]` if the solution reinterprets the problem to make it trivially solvable, answers a weaker/different question than asked, or exploits ambiguity to avoid the core difficulty. Motivated by DeepMind's Aletheia paper ([arXiv:2602.10177](https://arxiv.org/abs/2602.10177)) §4.2, which found that 50 of 63 technically correct Erdos solutions were vacuous due to the model reinterpreting ambiguous problems in the easiest-to-answer way.

### Session Directory Layout (skills only)

Skills persist sessions in `.alethic/` within the project directory (detected via `.git`). Each session gets a `{slug}-{YYYYMMDD}-{4hex}/` directory containing `session.json` (metadata with `failed_approaches`, `elapsed_seconds`), `problem.md`, `output.md` (final deliverable), and a `worklog/` subdirectory for intermediate files. The worklog contains `events.jsonl` (one JSON line per Task call for post-hoc analysis). When best-of-N > 1, each iteration's worklog contains `candidate_{C}.md` and `verification_c{C}.md` files for each candidate, with the best candidate copied to the standard `solution.md` / `verification.md` locations. When `--textbook` is used, the worklog additionally contains `textbook_plan.md` (Planner output), `textbook_section_{K}.md` (Writer outputs), `textbook_context.md` (rolling context for Writer continuity), `textbook_draft.md` (assembled sections), and `fidelity_check.md` (Fidelity Verifier output). An append-only `sessions.jsonl` index at the `.alethic/` root enables querying across sessions. Falls back to `/tmp/alethic-*` outside git repositories. The Python library (`MathAgent`, `PhysicsAgent`) is unaffected — it uses in-memory `AgentResult` objects.
