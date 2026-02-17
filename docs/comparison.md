# Alethfeld vs Vibefeld vs Alethic — Three-Way Comparison

## Context
Three AI-assisted mathematical reasoning systems by two authors:
- **Alethfeld** (`tobiasosborne/alethfeld`) — multi-agent adversarial proof system in Clojure
- **Vibefeld** (`tobiasosborne/vibefeld`) — adversarial proof framework in Go (same author, spiritual successor)
- **Alethic** (`hyperion-git/alethic`) — GVR reasoning agent in Python, inspired by DeepMind's Aletheia

---

## At a Glance

| Dimension | Alethfeld | Vibefeld | Alethic |
|-----------|-----------|----------|---------|
| **Goal** | Machine-checked proofs with adversarial rigor | Battle-tested natural-language proofs via adversarial collaboration | Iterative problem-solving with decoupled verification |
| **Tagline** | — | "Provers convince. Verifiers attack. Truth emerges." | Inspired by DeepMind's Aletheia |
| **Language** | Clojure 1.12 (JVM) | Go 1.25 | Python 3.13 |
| **AI models** | Claude, Gemini, Codex | AI-agnostic (framework for external agents) | Claude only (Anthropic API) |
| **Agent roles** | 7 (Adviser, Prover, Verifier, Lemma Decomposer, Reference Checker, Formalizer, Orchestrator) | 2 core (Prover, Verifier) + framework | 3 (Generator, Verifier, Reviser) |
| **Proof format** | Lamport notation → Lean 4 | Lamport-style hierarchical (1, 1.1, 1.1.1) | Free-form prose |
| **Output** | Lean 4 code + LaTeX | Natural-language proof with audit trail | Solution text, optional textbook conversion |
| **Verification** | Adversarial per-step + anti-sycophancy | Adversarial per-step with challenge severity levels | Decoupled holistic (no reasoning traces) |
| **State model** | Semantic proof graph (EDN DAG) | Event-sourced ledger (filesystem ACID) | In-memory dataclasses / file sessions |
| **CLI commands** | ~12 | 60+ | ~10 flags |
| **Dependencies** | Malli, tools.cli, data.json | Cobra only (minimal) | Anthropic SDK, argparse |
| **License** | MIT | MIT | MIT |

---

## Architecture

### Alethfeld: 7-Agent Orchestrator Prompt

```
Adviser → Prover ⇄ Verifier (adversarial loop per step)
              ↓
     Lemma Decomposer → Reference Checker → Formalizer
              ↑
         Orchestrator (state machine in prompt)
```

The orchestrator is a **single large prompt** that directs the AI model through phases (theorem audit → strategy → skeleton → expansion/verification → formalization). All 7 agent roles are encoded in the prompt — the AI switches roles based on the current phase. State is an EDN semantic graph loaded/saved between phases.

### Vibefeld: CLI Framework for External Agents

```
Prover ──claim──→ [af refine] ──→ Ledger
                                      ↑
Verifier ──claim──→ [af challenge] ───┘
                    [af accept]
```

Vibefeld is **not an AI agent itself** — it's a **proof infrastructure framework** (60+ CLI commands) that external agents (human or AI) interact with. The `af` binary manages the proof graph, ledger, locks, taint, and concurrency. Agents call CLI commands; the framework enforces invariants. This is fundamentally different from alethfeld, where the orchestrator prompt embeds the workflow.

**9 Laws of Adversarial Proof:**
1. Adversarial verification (verifiers attack, don't rubber-stamp)
2. Agent isolation (provers and verifiers work independently)
3. Append-only ledgers (full audit trail)
4. Filesystem-based ACID (no database)
5. Hierarchical nodes (Lamport-style)
6. Three-category state tracking (workflow / epistemic / taint)
7. Challenge system with severity levels
8. Taint propagation
9. Filesystem concurrency (POSIX atomics)

### Alethic: 3-Subagent Python Library

```
Generator(s) → Verifier → Reviser → (loop)
   ×N             ×N          ×1
```

A **Python library + CLI** that makes Anthropic API calls directly. Each subagent is a function wrapping a Claude API call with role-specific prompts and temperature. The orchestrator (`MathAgent.solve()`) manages the loop in-process. Best-of-N uses `ThreadPoolExecutor` for parallel generation.

### Architectural Philosophy

| | Alethfeld | Vibefeld | Alethic |
|--|-----------|----------|---------|
| **What is the AI?** | The AI IS the system (prompt-orchestrated) | AI is a client of the system | AI is called by the system |
| **Where's the logic?** | In the orchestrator prompt | In the Go binary (2,400+ LOC service) | In Python orchestrator code |
| **State management** | EDN files, prompt-managed | Event-sourced ledger, CLI-managed | In-memory / file-based sessions |
| **Multi-agent** | Single model switching roles | True multi-agent (separate processes) | Separate API calls per role |

---

## Verification

### Alethfeld: Anti-Sycophancy Protocols
- Verifier told "your primary failure mode is accepting false theorems"
- Checks: could the theorem be false? Is the prover explaining away contradictions?
- Per-step verdicts: `accept`, `challenge`, `type-error`
- 7 rounds per step, 50 total budget
- Theorem audit phase catches false premises before proof work begins

### Vibefeld: Structured Challenge System
- **4 severity levels**: critical, major (blocking) / minor, note (advisory)
- Blocking challenges must be resolved before node acceptance
- Challenge targets: inference, scope, missing justification, context failure
- Verifier can withdraw challenges, challenges can be superseded
- Acceptance requires: all children validated + all blocking challenges resolved + dependencies validated
- **Escape hatches**: admit (with taint), refute (disproven), archive (abandon)

### Alethic: Decoupled Holistic
- Verifier receives ONLY problem statement + final solution text
- Never sees Generator's thinking traces or tool outputs
- Structured output: VERDICT, CONFIDENCE (0–1), CRITIQUE, REASON, ISSUES
- Confidence threshold gates acceptance (default 0.90)
- False-premise detection via REASON field → early exit

### Comparison

| Aspect | Alethfeld | Vibefeld | Alethic |
|--------|-----------|----------|---------|
| **Granularity** | Per inference step | Per inference step | Whole solution |
| **Adversarial?** | Yes (challenge/response) | Yes (structured challenges) | No (independent evaluation) |
| **Blocking mechanism** | Round limits (7/step) | Challenge severity (critical/major block) | Confidence threshold |
| **Admits gaps?** | Proof obligations | `admit` command (taint propagates) | `UNSOLVED` verdict |
| **Catches false premises?** | Theorem audit | Refute command | REASON field |

---

## State & Audit

### Alethfeld
- **EDN semantic graph** (Clojure data format)
- Nodes are claims/definitions/lemmas in a DAG
- Stable UUIDs, schema-validated via Malli
- Taint propagation tracks admitted dependencies
- No event history — only current state

### Vibefeld
- **Event-sourced ledger** (26 event types, append-only)
- State derived by replaying events (full audit trail)
- Filesystem ACID with POSIX atomics (no database)
- Three-category tracking: workflow (available/claimed/blocked), epistemic (pending/validated/admitted/refuted/archived/draft), taint (clean/self_admitted/tainted/unresolved)
- Amendment history with diffs, failed approach registry, evidence attachment with SHA256 hashing
- Lock system for multi-agent concurrency (claim/release/extend)

### Alethic
- **In-memory dataclasses** (library): `AgentResult` with `Solution`, `VerificationResult`, `Revision` history
- **File-based sessions** (skills): `.alethic/{slug}/` with `session.json`, `problem.md`, `solution.md`, worklog
- **Sessions index**: `.alethic/sessions.jsonl` for cross-session queries
- No event sourcing, no formal audit trail

### Comparison

Vibefeld has the richest state model by far — event sourcing means you can replay the entire proof construction history, see every challenge raised and resolved, track failed approaches, and audit agent behavior. Alethfeld has a clean graph model but no history. Alethic's state is optimized for the GVR loop, not for audit.

---

## Taint Propagation

Both Osborne projects implement taint — Alethic does not.

### Alethfeld
- Admitted nodes contaminate dependents
- Taint tracked in the semantic graph
- Visualization shows clean vs tainted nodes

### Vibefeld (more sophisticated)
- **5 taint rules**: archived/refuted → clean; pending/draft → unresolved; ancestor unresolved → unresolved; self admitted → self_admitted; ancestor tainted → tainted; else clean
- Auto-recomputes after every state change
- DFS-based cycle detection prevents circular reasoning
- Taint tracing planned (P2) for visualizing uncertainty propagation

### Alethic
- No taint concept
- Confidence scores serve a loosely analogous role (uncertain solutions get revised)
- No dependency graph between proof steps

---

## Sampling Strategy

| | Alethfeld | Vibefeld | Alethic |
|--|-----------|----------|---------|
| **Candidates per step** | 1 (single prover) | 1 (single prover per claim) | N (best-of-N) |
| **Diversity source** | Adversarial iteration + Adviser strategy | Adversarial iteration + failed approach registry | Parallel sampling + temperature |
| **Parallel generation** | No | No (but supports concurrent agents on different nodes) | Yes (ThreadPoolExecutor) |
| **Strategy tracking** | Adviser evaluates approaches | `approach-tried` + `approach-list` commands | Implicit in Generator temperature |

Alethic's best-of-N is unique among the three — it generates multiple complete solutions in parallel and picks the best. The Osborne projects get diversity through adversarial refinement instead.

---

## Domain Coverage

| Domain | Alethfeld | Vibefeld | Alethic |
|--------|-----------|----------|---------|
| Pure math proofs | Primary | Primary | Supported |
| Physics derivations | `:formal-physics` mode | Not documented | First-class (`PhysicsAgent`) |
| Problem solving | Not designed for this | Not designed for this | Primary focus |
| Scientific figures | No | No | Yes (`/alethic-scientific-figure`) |
| Formal verification | Lean 4 (0 `sorry`s achieved) | Planned (Lean/Coq/Isabelle roadmap) | No |

---

## Formal Verification Output

| | Alethfeld | Vibefeld | Alethic |
|--|-----------|----------|---------|
| **Lean 4** | Yes (6 theorems, 0 `sorry`s) | Roadmap | No |
| **LaTeX** | Yes | Export command | Textbook conversion (optional) |
| **Machine-checkable** | Yes | Not yet | No (LLM-verified only) |

---

## Tech Stack

| Aspect | Alethfeld | Vibefeld | Alethic |
|--------|-----------|----------|---------|
| Language | Clojure 1.12 | Go 1.25 | Python 3.13 |
| Runtime | JVM (Java 21+) | Native binary | CPython |
| External deps | Malli, tools.cli, data.json, Criterium | Cobra only | Anthropic SDK |
| Data format | EDN | Event ledger (custom) | JSON / dataclasses |
| CLI commands | ~12 | 60+ | ~10 flags |
| Test framework | test.check (property-based) | Go testing (27 packages) | pytest (mocked API) |
| LOC (approx) | Unknown | ~388 Go files, 2,400+ LOC service | ~1,750 Python LOC |
| Distribution | Git + uberjar | Git + go build | pip + Claude Code plugin |
| AI coupling | Model-agnostic prompts | AI-agnostic framework | Claude-specific SDK |

---

## Alethfeld → Vibefeld Evolution

Both are by Tobias Osborne. Vibefeld appears to be a **ground-up rewrite** with significant architectural evolution:

| Change | Alethfeld | Vibefeld |
|--------|-----------|---------|
| Language | Clojure (JVM, slow startup) | Go (fast native binary) |
| Architecture | Monolithic orchestrator prompt | CLI framework for external agents |
| State | EDN graph (snapshot) | Event-sourced ledger (full history) |
| Concurrency | Single-agent | Multi-agent with filesystem locks |
| Validation | Malli schemas | Go type system + service-layer invariants |
| Dependencies | Several JVM libs | Cobra only (minimal) |
| CLI surface | ~12 commands | 60+ commands |
| Maturity | v5.1/5.2 orchestrator | v0.1.1 (active development) |
| Lean 4 | Working (6 theorems) | Roadmap |

Key evolution: Alethfeld puts the orchestration logic *inside the AI prompt*. Vibefeld moves it *outside into compiled code*, making the framework AI-agnostic and the invariants machine-enforced rather than prompt-enforced.

---

## Complementary Strengths

**Alethfeld**: Proven Lean 4 formalization pipeline, anti-sycophancy protocols, theorem audit

**Vibefeld**: Richest infrastructure (event sourcing, taint, concurrency, 60+ commands), AI-agnostic, strongest invariant enforcement

**Alethic**: Best-of-N parallel sampling, physics domain support, decoupled verification, polished output (textbook, figures), easiest deployment (pip/plugin)

---

## Summary

The three projects represent different philosophies for the same goal (rigorous AI-assisted mathematical reasoning):

- **Alethfeld** = proof construction via **prompt-orchestrated AI role-switching** with Lean 4 as the ground truth
- **Vibefeld** = proof construction via **compiled infrastructure framework** that any AI (or human) can use as a client, with event-sourced audit trails
- **Alethic** = **solution-level reasoning agent** that generates, independently verifies, and refines answers with configurable compute budgets

The Osborne projects (alethfeld/vibefeld) operate at **proof-step granularity** with adversarial verification. Alethic operates at **whole-solution granularity** with decoupled verification. The Osborne projects target formal mathematical proofs. Alethic targets broader problem-solving (math + physics + figures).

A natural pipeline: **Alethic** for rapid exploration → **Vibefeld** for adversarial proof construction → **Alethfeld** for Lean 4 formalization.
