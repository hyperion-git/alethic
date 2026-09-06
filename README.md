# Alethic

A model-agnostic reasoning agent for mathematics and physics. Alethic generates a
candidate, verifies it in a separate context, and revises it using the critique.
It can return an unsolved result when its budget runs out. The design is inspired
by [DeepMind's Aletheia](https://arxiv.org/abs/2602.10177).

The Python library and CLI support **Anthropic, OpenAI, OpenRouter, and custom
OpenAI-compatible endpoints**. A small client interface also supports injected
backends. Model IDs are passed through; there is no model allowlist.

## Install

Python 3.10 or newer. Install the backend you use:

```bash
git clone https://github.com/hyperion-git/alethic.git
cd alethic
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[openai,scientific]'
```

Use `.[anthropic,scientific]` for Anthropic or `.[openrouter,scientific]` for
OpenRouter. The `scientific` extra provides NumPy, SymPy, SciPy, mpmath and
Matplotlib for computational checks. The base package requires no provider SDK;
use `--no-code --tools none` when running without scientific dependencies.

## Run

Set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY` for the selected
provider. `--api-key` overrides that provider's environment variable.

```bash
alethic solve --provider openai --model YOUR_MODEL --preset quick \
  'Prove that sqrt(2) is irrational'

alethic derive --provider openrouter --model PROVIDER/MODEL --preset quick \
  'Derive the energy levels of a quantum harmonic oscillator'

alethic check derivation.md --provider anthropic --model YOUR_MODEL
alethic verify solution.md --problem-file problem.md \
  --provider openai --model YOUR_MODEL

# A locally served model (the SDK requires a non-empty key, even for a no-auth server)
alethic solve --provider openai --base-url http://localhost:8000/v1 \
  --api-key local --model LOCAL_MODEL --context-window 32768 \
  --token-parameter max_tokens --max-tokens 4096 --preset quick \
  'Prove that sqrt(2) is irrational'
```

`ALETHIC_PROVIDER` and `ALETHIC_MODEL` set CLI defaults. Omitting both preserves
the original Anthropic/`claude-opus-4-6` default. A model must support the selected
endpoint's protocol; compatibility does not imply sufficient reasoning ability.
For models without tool calling, use `--no-code --tools none`.

Some reasoning models reject temperature. Use
`--request-options '{"temperature":null,"reasoning_effort":"high"}'` when those
settings are supported by your model. See [backend configuration](docs/providers.md)
for reasoning budgets, custom clients, endpoint limitations and migration notes.

## Python API

```python
from alethic import AgentConfig, PhysicsAgent, VerifierAgent, VerifierConfig

config = AgentConfig.from_preset(
    "quick",
    provider="openai",
    model="YOUR_MODEL",
    context_window=32768,
    max_tokens=4096,
)
result = PhysicsAgent(config).solve("Derive the harmonic oscillator spectrum.")
print(result)

# A separate model/provider can audit the written result.
if result.solution is not None:
    audit = VerifierAgent(VerifierConfig(
        provider="openrouter", model="PROVIDER/VERIFIER_MODEL", num_verifiers=3,
    )).verify(result.problem, result.solution)
    print(audit)
```

`MathAgent` and `PhysicsAgent` share the orchestrator; only their prompts differ.
`VerifierAgent` assesses a solution against a problem. `CheckerAgent` audits
internal consistency without a problem statement. All four accept `client=...`
for per-instance backend injection, without changing process-global state.

## How it works

1. Generate one or more candidates (`--best-of N`).
2. Verify each candidate in an independent conversation. Generator reasoning
   traces and private tool-continuation metadata never enter that conversation.
3. Rank candidates, revise the best one using the critique, and verify again.
4. Return an accepted solution, a false-premise finding, a checkpoint, or the
   best unverified attempt when the budget is exhausted.

Best-of-N and consensus calls can run concurrently. Optional stall resets,
adversarial checking and tree search widen the search when progress stalls.
They remain controlled by presets and explicit overrides.

| Preset | Iterations | Revisions per cycle | Candidates | Acceptance threshold |
| --- | ---: | ---: | ---: | ---: |
| `quick` | 2 | 1 | 1 | 0.85 |
| `default` | 5 | 3 | 2 | 0.90 |
| `thorough` | 8 | 5 | 3 | 0.95 |
| `extreme` | 12 | 5 | 5 | 0.97 |

Presets control effort, not model selection. Alternate generation models require
`--variant-b-model`; an alternate adversarial checker requires `--breaker-model`.
Both use the configured backend. In Python, `variant_b` may also override the
provider and endpoint; credentials then come from the new provider's environment.

Use `--search tree` for hierarchical proof search. Sessions and checkpoints are
written under `.alethic/` in a repository, with a temporary-directory fallback.
Resume with `--resume SESSION_DIR`. `--json` emits structured output.

## Limits

An accepted LLM answer is **not a formal proof certificate**. Verifier confidence
is a model-produced score, not an established probability of correctness.
Independent contexts and multiple samples can share systematic errors. Audit
important results with external evidence, direct calculations or a proof assistant.

Context monitoring uses a character-count estimate, not a model-specific tokenizer.
Set `--context-window` to your server's actual limit; truncation causes a checkpoint
rather than acceptance of a partial answer. Reasoning and sampling parameters vary
by API and model. Live provider behavior is not covered by the offline suite.

The Python execution subprocess has restricted imports and a timeout; it is not
an operating-system security boundary for adversarial code. Run untrusted problems
in an isolated environment, or disable execution.

## Claude Code integration

The existing `skills/` plugin remains a **Claude Code host integration**. Its Task
orchestration is specific to that host; the Python library and CLI provide the
portable execution path.

```bash
claude plugins add hyperion-git/alethic
```

Commands include `/alethic-solve`, `/alethic-derive`, `/alethic-verify`,
`/alethic-check`, `/alethic-textbook`, and `/alethic-scientific-figure`.
See [skill parity](docs/skill-parity.md) for differences from the Python runtime.

## Development and evaluation

```bash
python -m pip install -e '.[dev]'
python -m pytest -q -m 'not live and not integration'
python -m mypy src/alethic
python -m ruff check src tests

# Requires a live model and incurs provider usage.
alethic eval run data/benchmarks/math-sample.json \
  --provider openai --model YOUR_MODEL --preset quick --output results.json
```

Offline tests reject unmocked provider calls. Regression fixtures are deterministic
and require no sampling seed or API key. Live model outputs are not reproducible
across providers; record the model, endpoint, configuration and returned usage.

The main extension points are `llm.py` (client/response contract), `providers.py`
(API translation), `models.py` (configuration), `subagents.py` (role calls), and
`agent.py` (orchestration). `openrouter.py` is a compatibility wrapper.

[Apache 2.0 license](LICENSE).
