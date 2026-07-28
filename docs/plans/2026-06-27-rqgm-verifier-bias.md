# RQGM (arXiv:2606.26294) → Alethic: what to adapt

*Design note. Produced by a 26-agent adversarial workflow (4 parallel readers → 5 design
lenses → 3-skeptic panel each → synthesis → completeness critic), 2026-06-27. Paper: "The Red
Queen Gödel Machine: Co-Evolving Agents and Their Evaluators," Iacob, Jovanović, Shen et al.,
24 Jun 2026. Every repo claim below was verified against code by a skeptic; faithfulness claims
were checked against the paper's **demonstrated** results, not its framing.*

**Implementation of §3 lives in [`scripts/self_preference_probe.py`](../../scripts/self_preference_probe.py).**

## 1. The spine (confirmed + sharpened)

Alethic's Verifier **is** the stationary criterion RQGM critiques: `VERIFIER_SYSTEM` is fixed at
design time, and Generator and Verifier are the same base model (Opus), so "decoupled verification"
hides the generator's *traces* but not its *correlated blind spots*. The sharpening from the code
analysis: **Alethic has no labeled `(problem, solution, true_verdict)` set anywhere.** Every
accuracy-like signal is anchored to the verifier's own output — `calibration.py` calibrates
confidence against `solved = (verdict == CORRECT)` (agent.py:1268), and the eval harness scores
`correct_prediction = result.solved == expected_solvable` (harness.py:240), a *solvability* flag,
not per-solution correctness. K-verifier consensus runs K copies of one model + one prompt (variance
reduction, not bias reduction); `variant_b` diversifies generators only; the breaker is a
one-directional Anthropic-on-Anthropic attack. The only verdict-level ground truth is the handful of
false-claim problems, and they test the *reject* direction only — they structurally cannot catch the
failure RQGM targets: **a verifier accepting a wrong proof.** RQGM's lever is a frozen ground-truth
anchor; the cheap, honest moves for a fixed orchestrator are (a) measure the bias and (b) decorrelate
the evaluator — not import any Gödel-machine machinery.

## 2. Ranked adoptables

Ordered by (faithfulness × value ÷ cost). "Faithfulness" = backed by a *demonstrated* RQGM result;
**3 of 4 substantive proposals' faithfulness skeptics returned FAIL** — stated plainly.

| Change | Verdict | Why (citing skeptics) | Pkg/Skill | Cost | Prerequisite |
|---|---|---|---|---|---|
| **Verifier-bias FPR diagnostic** (`scripts/self_preference_probe.py`), descoped to Arm A + K=1-vs-K=5 | **Adopt** | Cheapest high-signal item; deliverable is a *number*. Faithfulness skeptic FAILED the headline (Opus-vs-other-model is intra-AI self-preference, not RQGM's AI-vs-human 1.91×) but PASSED the K=1-vs-K=5 secondary as a faithful port of the explicit "variance ≠ bias" claim. Scope-cost FAILED Arm B (matched-quality injection needs unverifiable scaffolds) → dropped. Redundancy passed. | Package | Low | OpenRouter key; reframe as "self-preference analogue," not the 1.91× |
| **Metric split** `correct_prediction` → `solve_rate` + `false_claim_reject_rate`; freeze gate with `anchor_sha256` + `gate_epoch` | **Adopt — ✅ SHIPPED 2026-07-28** | **Only proposal whose faithfulness skeptic PASSED** — it *rejects* "evolve the benchmark" as anti-faithful and keeps RQGM's anchor *discipline*. ~30 lines, zero LLM circularity. The reject-rate is the only repo metric that responds to verifier *bias* not variance. | Package | Low | Human-authored false-claims to grow the (tiny) reject anchor |
| **Heterogeneous verifier roster** (`verifier_model(s)` config; round-robin a non-Anthropic slot into K-consensus + solve-loop verify) | **Adopt-with-changes** | Structural half of the spine fix. Faithfulness FAILED: *no* RQGM result varied the evaluator's base model (main runs all GPT-5.5; Nemotron only in ablations) — merit is classic ensemble decorrelation. Scope-cost FAILED on cost-realism: mean-confidence aggregation breaks across model scales (→ more revisions, *more* tokens). | Both (skill capped to Claude-family) | Medium | Fix mean-confidence aggregation (verdict-majority path); **gate on the diagnostic** |
| **Offline one-shot verifier-prompt selection vs planted-flaw labeled set** | **Defer** | Right ingredient (verdict-accuracy metric + ground-truth anchor = the real gap), correct RQGM result to borrow. Faithfulness FAILED: RQGM's +9% came from *co-evolution against the anchor*, which this strips; a best-of-5 sweep was never validated. Dataset is the dominant non-automatable cost. | Both | High | Build the 20-item labeled set + **run the existing breaker + a diverse verifier as an audit first** |
| **Heterogeneous "Auditor Slot"** (cheap different-model judge on citation/spec-gaming axes) | **Defer→merge** | Faithfulness FAILED: its axes (citation #16, spec-gaming #17) are *already in* `VERIFIER_SYSTEM`, so "complementary" is illusory — it's just model diversity (the roster row). Real bug found: `set_client_factory` is process-global → "make slot 0 heterogeneous" flips *all* K slots. Subsumed by the roster. | Both | Medium | Fold model-diversity intent into the roster |
| **Auto-generating / self-evolving benchmark problems** | **Reject** | Category creep + anti-faithful: RQGM *freezes* its anchor (App G warns a drifting anchor lets evaluators drift uncorrected). The proposal's own author kills it. | — | — | — |

## 3. The #1 recommended move — verifier-bias diagnostic (descoped)

Answer, with a number, "does the Opus verifier accept ground-truth-WRONG solutions, and does K=5
consensus help?" — **before** spending engineering on a fix. New script only; no orchestrator change.
**Implemented in `scripts/self_preference_probe.py`.**

- **Arm A (zero new ground truth, zero circularity):** the two `expected_solvable=false` anchors —
  `false-claim-even-odd` (math) and `false-drude-lorenz-number` (physics), loaded from the benchmark
  JSON. Every "proof" of a false claim is wrong by construction, so any `CORRECT` is a false positive.
  1. Generate R≥10 attempts/problem from two authors: `opus` (Anthropic) and `nonopus` (OpenRouter).
  2. Run the real verifier (`subagents.verify`, stock `VERIFIER_SYSTEM`) on every attempt.
  3. Record `verdict` per `(problem, host, rep)`.
- **Statistic:** `FPR_host = #CORRECT / #verified`; `FPR_opus / FPR_nonopus` with Fisher exact 2×2 +
  Wilson 95% CI (N small → exact). Framed as self-preference *analogue*, not the literal 1.91×.
- **Secondary (`--k5`, high faithfulness-per-token):** re-verify at K=1 vs K=5 (same model+prompt). If
  FPR survives K=5 you have empirically demonstrated RQGM's "consensus reduces variance, not bias."
- **`check`-path arm (`--check-arm`, the HIGH-gap addition — see §4):** also run the reference-less
  reviewer (`CheckerAgent.check`, `problem=""`).
- **Contingency:** the anchors were designed to be caught, so Arm A may give FPR=0 → ratio `0/0`. That
  null is itself decision-relevant: it cheaply *falsifies* the spine's worry. Reportable outcome.
- **Cost:** ~a day of code (done); real cost is API reps. **Either outcome gates the roster row.**

## 4. Completeness pass — gaps the synthesis missed

**[HIGH] The grader-vs-reviewer asymmetry — RQGM's load-bearing distinction — was omitted.** RQGM
showed the reference-conditioned *grader* improved by co-evolution alone, but the reference-less
*reviewer* REQUIRED an adversarial objective (it over-accepted at 1.91×). This maps precisely onto
Alethic:
- `verify` / `VerifierAgent` has a problem statement → **grader analogue** (reference-bearing).
- `check` / `CheckerAgent` runs with `problem=""` (verifier_agent.py:149-156) → **reference-less
  reviewer analogue** — the path with *no anchor at all*, the truest analogue of RQGM's over-accepting
  reviewer, and the component most exposed to self-preference bias.
- **Action (done):** the #1 diagnostic gained a `--check-arm`; treat the two evaluators differently —
  `verify` needs only *diversification*; `check` is the one that may need an *adversarial objective*.

**[MED] Calibration erasure.** `calibration.py` filters stored `(raw_conf, solved)` pairs by
`major.minor` only. After porting a package-optimized verifier prompt, the store stays anchored to the
*old* verifier's verdicts → the new verifier is calibrated against the displaced verifier's bias.
Extend the `anchor_sha256`/`gate_epoch` discipline to invalidate calibration pairs on any
verifier-prompt or verifier-model change.

**[MED] The only demonstrated *token* result (coding: 1.35–1.72× fewer) was never mapped.** That
saving came from an agent-as-judge queried *once per artifact* vs multi-turn execution. Alethic's
K-consensus is K full multi-turn Opus verify calls. Unexplored lever: a **single-query cheap-model
judge** partially displacing the K expensive verify calls — a decorrelated signal that *cuts* tokens.
Directly relevant to the v4.0 solve-rate-vs-token-cost gate.

**[LOW] Complementarity used only to reject, never to construct.** The most faithful, lowest-circularity
candidate is a **non-LLM execution signal**: did the generator's embedded `verify_step_N()` code
(design #19) actually run and pass? Mechanical, so `VERIFIER_SYSTEM` cannot duplicate it.

**[LOW] Writer co-evolution (1.78–1.86×) not scoped.** Alethic's generators are its "writers" and
`variant_b` already diversifies them; improving generators is in-scope but deliberately out of focus
here (the spine is the verifier).

## 5. What Alethic already does (don't reinvent)

Adversarial breaker (post-CORRECT, one-directional; supports `breaker_model` but never votes) ·
5-round adversarial verifier self-correction (same model) · `calibration.py` (confidence, not verdict
accuracy) · eval harness (`expected_solvable` = solvability) · `variant_b` (generators only) ·
K-consensus (K copies of one model+prompt) · OpenRouter plumbing (wireable but verify never uses it) ·
citation-checking #16 + anti-spec-gaming #17 (already in `VERIFIER_SYSTEM`).

## 6. Skill vs package

The skill is static markdown: it cannot run a diagnostic, an optimization loop, or persist a signal,
and Task sub-agents can't route to OpenRouter — so the diagnostic and any cross-family verifier are
**package-only**. The skill's only path to benefit is the parity workflow: port a package-validated
verifier prompt into `skills/*/references/verifier.md`, and at most round-robin Claude-family models
across verify Task calls. Only the persona/prompt half ports — not model-family diversity.

## 7. Link to the v4.0 benchmark gate

The gate is solve-rate-vs-token-cost (flat vs tree) measured against `expected_solvable` — so it can
show token-neutrality and solve-rate preservation but **cannot** show a verifier caught a false
positive. The **metric split + anchor hash** (row 2) is the minimal change that lets the same gate also
report the one number responding to verifier *bias*. The single-query cheap-judge lever (gap MED) is
the token-side complement.

## Pilot run via the skill route (subscription, 2026-06-27)

Ran the diagnostic's faithful core **in-session via Claude Code Task sub-agents** — the subscription
analogue of the skill's decoupled verification (no API key, no per-token cost; Claude-family only).

- **Author arm:** Opus *and* Haiku, each asked to "prove" both false-claim anchors, **refused and
  produced correct disproofs** — even Haiku caught the Drude/Lorenz subtlety (derived L=3⁄2(k_B/e)²,
  attributed π²/3 to Sommerfeld). → Claude-family authors will not manufacture the wrong-proof inputs
  the diagnostic needs; the weak/cross-provider author arm is genuinely **OpenRouter/package-only**.
  Empirically confirms the capability confound.
- **Verifier arm:** a planted trap (algebra correct, but Sommerfeld c_v=(π²/2)k_B(k_BT/E_F) and Fermi
  velocity mislabeled as "classical Drude") was checked by **5 independent decoupled Opus verifiers →
  5/5 INCORRECT, confidence 0.93–0.97**, all correctly flagging the quantum-inputs-under-classical-label
  premise error. K=1 verdict = K=5 majority = INCORRECT; unanimous, so consensus added nothing (no
  variance to reduce). **FPR = 0** on this trap.
- **Interpretation:** a reassuring **NULL** for these anchors — Claude is robust both as author and as
  verifier, so the "verifier accepts a wrong proof" failure does not manifest for Claude-family here.
  This cheaply de-risks the spine's worry *for these specific cases*. The trap was caught by the
  VERIFIER_SYSTEM false-premise (#16) and anti-spec-gaming (#17) features Alethic **already ships**.
- **Caveats:** (1) N=1 planted trap on a textbook-known subtlety — a real audit needs a **battery of
  subtler planted flaws** (the Deferred grader-improvement lens, doable on-subscription); (2) the skill
  route structurally cannot run the weak/cross-provider author arm, which is where a real false positive
  is likeliest → the full `scripts/self_preference_probe.py` (package + OpenRouter) remains the way to
  stress that dimension.

## Planted-flaw battery via the skill route (subscription, 2026-06-27)

A 10-item audit run in-session via Task sub-agents: **8 hand-authored planted flaws** (2 blatant,
2 moderate, 4 subtle — typed by failure mode: div-by-zero, dimensional, induction-overlap,
dropped-factor, conditional-convergence reorder, limit/integral interchange, L'Hôpital-out-of-hypotheses,
vibrational freeze-out) + **2 correct controls** (√2 irrational; the genuine classical-Drude L=3⁄2,
correct-but-surprising). Each item → **5 independent decoupled Opus verifiers + 1 Sonnet + 1 Haiku** (70
agents). Verifiers saw only problem+solution, never the ground-truth key.

| Metric | Result |
|---|---|
| Flaw catch-rate (K=5) | **8 / 8** |
| Flaw catch-rate (K=1, single verifier) | **8 / 8** |
| Control false-reject (K=5) | **0 / 2** |
| Items where K=1 ≠ K=5 (consensus changed verdict) | **0 / 10** (all unanimous) |
| Sonnet overall correct | **10 / 10** |
| Haiku overall correct | **10 / 10** |
| By tier (Opus / Sonnet / Haiku) | blatant 2/2, moderate 2/2, **subtle 4/4** — all three tiers |

The subtle catches had correct *reasons*, e.g. F6: "∫₀¹ n xⁿ dx = n/(n+1) → 1, not 0; no integrable
dominating function, mass concentrates near x=1"; F8: "at 300 K the vibrational mode is frozen out
(ℏω ≫ k_BT)". Both controls accepted (C2 explicitly: "premises consistently applied for a
Maxwell-Boltzmann gas").

**What a ceiling result can and cannot support.** Every item scored 100% with zero variance, so the
battery has **no discriminating power** — which *dissolves* the tempting reads rather than supporting
them:
- It does **not** weaken the verifier-bias case. The instrument could not have detected bias if present
  → *absence of evidence, not evidence of absence.* The bias question remains **untested**.
- It does **not** show a cheap judge "matches Opus." Haiku 10/10 beside Opus 10/10 means Haiku clears
  the *same easy bar*, not that it matches Opus at Opus's real operating difficulty. Also untested.
- K=5 = K=1 on every item is expected when items are clear-cut (no disagreement to resolve); it says
  nothing about consensus value on *hard* items.

**Why both experiments missed the target (the load-bearing point).** RQGM's concern is *correlated
blind spots* — the verifier misses an error because it shares the generator's blind spot. But you can
only plant an error you can *see*, and a blind spot is by definition an error the model *cannot* see.
Hand-authored flaws therefore sample the model's **articulable-error set — the complement of what we
want to measure.** This is the same wall the package's weak-author arm hit from the other side: asking
Claude to *generate* a wrong proof failed because it won't author errors it can see. One underlying
reason, two dead ends.

**The only valid read (kept, not inflated):** the verifier accepted both correct controls — including
the correct-but-surprising classical-Drude C2 — and caught the *premise*-type errors (F4/F6/F7) with
correct reasoning, not surface pattern-matching on "verify this." That is mild evidence it is **not
trigger-happy**. It is not a quality verdict.

**Consequence:** the only instrument that can test verifier blind spots is **harvested real errors with
ground truth** — wrong solutions the model produced *unawares*, then labeled. That is exactly the
Deferred labeled-set item (§2, row 4), now promoted: these two cheap probes do not substitute for it —
they demonstrate *why* it is necessary.

## Recommended sequence (revised after the pilot + battery)

The two cheap probes were instructive but **structurally cannot test the RQGM question** — one failed
by refusal, the other by construction. They leave the bias question *open*, not closed. Revised
priorities:

1. **Build a harvested real-error labeled set — the only valid test of verifier blind spots.** Collect
   `(problem, solution, true_verdict)` triples where the model produced a wrong solution *unawares*
   (run Alethic / raw Claude on hard problems and label outputs against known answers; or import a
   benchmark with gold solutions). This is the Deferred grader-audit item promoted to #1: it is the
   only design that reaches *correlated* blind spots, which planted flaws and self-generation cannot.
2. ~~**Ship the metric split + anchor hash**~~ — **IMPLEMENTED 2026-07-28** (see below), so the gate can
   *report* a verdict-accuracy number once the labeled set exists.
3. **Treat the cheap-judge token lever AND the heterogeneous verifier roster as UNTESTED hypotheses.**
   Gate both on the labeled set (which can finally show whether Haiku truly matches Opus at real
   difficulty, and whether same-model bias exists). Do **not** adopt either on the current evidence —
   the perfect battery cannot support them.
4. Reject auto-evolving benchmarks.

## Implemented: metric split + anchor freeze (rec #2, 2026-07-28)

Landed in `src/alethic/eval/harness.py` (shared) and consumed by both `alethic eval run` and
`scripts/run_gate.py`, so the v3.8 gate reports it without a second copy of the aggregation.

**`split_metrics(results)`** partitions the benchmark by `expected_solvable` — the two populations
answer different questions, and the old pooled `solve_rate` (solved / *all* problems) hid both:

| Key | Meaning |
|---|---|
| `solve_rate` | solved / **solvable** problems — capability. **Redefined**; not comparable to a pre-split number. |
| `false_claim_accept_rate` | accepted / **scored** anchors — **the primary number.** Every "proof" of a false claim is wrong by construction, so an accept is a false positive. The only metric in the repo responding to verifier *bias* rather than *variance*. |
| `false_claim_reject_rate` | its complement — **not** a false-premise *detection* rate (see caveat) |
| `false_claim_verdicts` | verdict histogram over the anchors, which does separate rejection from exhaustion |
| `n_errors`, `n_false_claim_scored` | observation accounting |

**Errors are handled asymmetrically, deliberately.** A solvable problem that errored *is* a failure to
solve and stays in the `solve_rate` denominator (dropping it would inflate the rate on a lossy run). An
anchor that errored produced *no verdict* — a non-observation — and leaves the anchor denominator. The
naive `not solved` test counted crashed anchors as correct rejections, biasing toward the reassuring
answer. On a 2-solvable/2-anchor fixture with one error each, the old logic reports 50% rejection where
the truth is that the single anchor which returned a verdict was **accepted** — 0% rejection.

**Caveat kept visible in the docstring:** `AgentResult.solved` is false whenever the agent missed a
CORRECT verdict, so "rejected" lumps genuine false-premise detection together with plain budget
exhaustion. `false_claim_verdicts` is what distinguishes them; `AgentResult` carries no detection flag.

**Anchor freeze** uses two independent keys, because either alone is insufficient:

- `anchor_sha256` — SHA-256 over `(id, domain, problem, expected_solvable)` sorted by id. Invariant to
  file ordering; changes if any problem's text, domain, or solvability flag is edited, or one is
  added/removed. Answers *which problem set was run*. (`domain` is included: it selects the agent class.)
- `GATE_EPOCH = 2` — a module constant, **not** a benchmark field. Answers *how outcomes were scored*.
  A verifier-prompt or verifier-model change never touches the benchmark JSON, so only an epoch bump can
  invalidate a comparison across one. Epoch 1 = the pooled pre-split `solve_rate`. Keeping the epoch in
  code also means the benchmark files stay untouched — which is what anchor-freeze discipline wants.

§4's extension (invalidating `calibration.py` pairs on a verifier change) is deliberately **not** in
scope here; it becomes a bump of this same constant rather than a redesign.

Tests: `tests/test_eval.py::TestAnchorHash` (5) and `::TestSplitMetrics` (6), each written to fail
against the naive implementation — hash discrimination, the errored-anchor case, the errored-solvable
case, and `None`-not-`0.0` when no anchors were scored.
