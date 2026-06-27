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
| **Metric split** `correct_prediction` → `solve_rate` + `false_claim_reject_rate`; freeze gate with `anchor_sha256` + `gate_epoch` | **Adopt** | **Only proposal whose faithfulness skeptic PASSED** — it *rejects* "evolve the benchmark" as anti-faithful and keeps RQGM's anchor *discipline*. ~30 lines, zero LLM circularity. The reject-rate is the only repo metric that responds to verifier *bias* not variance. | Package | Low | Human-authored false-claims to grow the (tiny) reject anchor |
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

**Three actionable findings:**
1. **Cheap judges suffice (token lever, the completeness-critic gap):** Haiku and Sonnet matched the
   5×Opus panel 10/10. A single Haiku verifier would have caught every flaw at a fraction of the cost —
   direct evidence for a cheap complementary/replacement judge, the v4.0 solve-rate-vs-token-cost lever.
2. **K=5 never beat K=1 here:** zero verifier disagreement → on clear-cut items, consensus is wasted
   compute. K-consensus only earns its cost when verifiers *disagree*, which this battery never produced.
3. **The battery was too easy to find the failure boundary:** 100%/0% with cheap models matching
   expensive ones means the test lacks discriminating power. These are *classic, in-distribution* flaws
   (textbook fallacies a strong LLM has seen). The result says "the verifier's floor is above this
   battery," NOT "the verifier has no ceiling." A real stress test needs **novel** flaws (not in the
   training distribution) and/or errors **buried in long multi-step derivations** (where attention, not
   knowledge, is the bottleneck).

**Net effect on the recommendations:** two null-ish experiments (pilot + battery) now **weaken** the
verifier-bias / heterogeneous-roster case for Alethic and **strengthen** the cheap-judge token lever.
The genuine open question is no longer "is the verifier biased toward accepting wrong proofs?" (no
evidence yet) but "does it hold on *novel* / *long-context* flaws?" — and "can a Haiku verifier replace
Opus to cut verification tokens?"

## Recommended sequence (revised after the pilot + battery)

The two experiments shifted the evidence: no sign of verifier over-acceptance, but strong evidence
that a *cheap* judge matches Opus. Revised priorities:

1. **Probe the cheap-judge token lever (now the best-supported move).** Re-run a benchmark slice with a
   Haiku/Sonnet verifier vs Opus and compare solve-rate + tokens. The battery says a cheap verifier
   catches the same flaws; confirm at scale → it directly serves the v4.0 cost gate. (Package; can also
   prototype via the skill's verify Task model.)
2. **Ship the metric split + anchor hash** (~30 lines; the only faithfulness-passing change) — still
   worth it so the gate can *report* `false_claim_reject_rate`, even though it's currently ~perfect.
3. **Harden the audit before trusting the null:** extend the battery with **novel** flaws (not textbook
   fallacies) and flaws **buried in long multi-step derivations** (attention-limited, not knowledge-
   limited). 100%/0% on classic flaws ≠ a robust verifier on the cases that matter.
4. **De-prioritised (was #1/#3):** the self-preference diagnostic's weak/cross-provider author arm
   (`scripts/self_preference_probe.py`) and the heterogeneous verifier roster — two null-ish results
   weaken the bias case; gate any roster work on the harder battery (step 3) surfacing a real miss.
5. Defer the labeled-set grader optimization; reject auto-evolving benchmarks.
