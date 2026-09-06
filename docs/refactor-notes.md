# Model backend refactor

Reviewed against commit `3792adb` on `main` (6 September 2026).

The original project already had an OpenRouter shim, but its request method
unconditionally replaced the requested model with the constructor's model.
Alternate generators and the adversarial breaker could therefore run on the wrong
model. Core imports required Anthropic even when another backend was selected,
and thorough presets selected Claude Sonnet implicitly.

The refactor introduces an SDK-independent client/response contract, shared model
configuration and two backend adapters. OpenRouter is now a thin wrapper around
the OpenAI-compatible adapter. Provider, endpoint, model, token-limit dialect,
context window and request options propagate through solve, derive, verify, check
and evaluation. Clients may be injected per agent. Failure analysis reuses the
selected client, and consensus synthesis uses the shared retry path.

Presets now select effort only. Secondary models must be chosen explicitly. The
breaker now honors its configured temperature. Private reasoning/signature data
survives tool continuation without entering an independent verifier's prompt.
Truncated tool calls are not executed, malformed tool arguments become recoverable
tool errors, and empty choices/refusals raise explicit response errors. Provider
exceptions propagate instead of exiting the application.

The README has been reduced from 568 to 170 lines. Backend configuration,
limitations, migration and an executable SDK-free example are in
[providers.md](providers.md). The Claude Code skill integration remains specific
to its host; the Python runtime is the portable path.

## Validation

The upstream baseline passed 1,707 tests after installing an offline guard. The
guard was necessary because a failure-analysis path in the original suite made
an unmocked provider call, which automatic approval review blocked. The guard now
rejects unmocked SDK calls locally for every non-live test and is included in CI.

The final suite passed **1,754 tests**, with **3 existing expected failures** and
**2 live integration tests deselected**. Mypy passed all 43 source files. Ruff
findings decreased from 114 to 100, with no new diagnostics; remaining findings
are pre-existing debt. `git diff --check` passed.

New tests exercise model routing, credential separation, reasoning translation,
continuation metadata and verifier isolation, token limits, malformed responses,
CLI propagation, configuration round trips and a custom client in a Python
interpreter with site packages disabled.

Reproduction environment: Python 3.12.13, Anthropic SDK 0.125.0, OpenAI SDK 2.54.0,
pytest 9.1.1, mypy 2.3.1 and Ruff 0.16.6. Follow the development commands in the
README. Fixtures are deterministic; no model sampling or seed is involved.

Live provider compatibility and mathematical solve-rate improvements were not
measured. Confidence scores remain model assessments, not proof certificates.
