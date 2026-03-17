# E vs F Monte Carlo Experiment Design

**Date:** 2026-03-17
**Goal:** Determine whether Option E (atom-guided verification + stall-reset) or Option F (PUCT + progressive widening) should be the v3.8 architecture, using empirically-calibrated Monte Carlo simulation.
**Preset:** thorough (N=3, 8 iterations, breaker, variant-B, adversarial self-correction)
**Prior evidence:** 20 experiments (10 scientists + 10 adversarial reviewers) produced a nuanced picture — E dominates at large M (approach count), F dominates at small M (3-5), PUCT+widening outperforms flat UCB1.

---

## 1. Experiment Structure

Three phases, executed sequentially:

```
Phase 1: Calibration (real subagents)     ~59 problem-iterations
Phase 2: Simulation  (pure Python/NumPy)  5K paired trials + 2K traced
Phase 3: Validation  (real subagents)     50 trials on held-out problems
```

## 2. Phase 1: Calibration

### 2.1 Problem Selection

10 problems from the existing benchmarks (`math-sample.json`, `physics-sample.json`), stratified:

| Archetype | Count | Examples |
|-----------|-------|---------|
| Smooth refinement | 4 | prime-17, geometric-series, simple-pendulum-period, gauss-law-from-coulomb |
| Insight-required | 4 | sqrt2-irrational, cantor-diagonal, qho-energy-levels, lorentz-transformation |
| Adversarial | 2 | false-claim-even-odd, false-drude-lorenz-number |

### 2.2 Hybrid Depth Protocol

Calibration uses a hybrid full-depth strategy to capture late-game regime changes (stall-reset exhaustion, context pressure) without running all problems to full depth:

| Subset | Problems | Iterations | Purpose |
|--------|----------|------------|---------|
| Full-depth | 3 (1 easy, 1 medium, 1 hard) | 8 | Late-game distributions: stall-reset exhaustion, context pressure, failed_approaches accumulation |
| Broad | 7 remaining | 5 | Early-game distributions: verdict/confidence/revision/atom rates |

**Full-depth selection:**
- Easy: `prime-17` (expected solve by iter 2-3)
- Medium: `sqrt2-irrational` (expected solve by iter 5-6)
- Hard: `qho-energy-levels` (likely UNSOLVED at iter 8)

**Total:** (3 x 8) + (7 x 5) = 59 problem-iterations

### 2.3 Subagent Protocol Per Problem-Iteration

Each iteration of each problem runs through Claude subagents (Task tool with fresh context):

1. **Generator** (x3, independent): Solve the problem with ATOM[N] annotations
2. **Verifier** (x3, independent, decoupled): Verify each candidate — return VERDICT, CONFIDENCE, CRITIQUE, ISSUES, ATOM CONFIDENCES
3. **Reviser** (x1, best candidate): Revise given critique + error-category addendum
4. **Re-verify** (x1): Verify revised solution
5. **Breaker** (x1, if accepted): Adversarial attack on accepted solution

~10-13 subagent calls per problem-iteration. Total: ~600-770 subagent calls.

### 2.4 Measurements Collected

Per-iteration, per-candidate:

| Metric | Source | Calibrates |
|--------|--------|-----------|
| Verdict distribution | Verifier output | P(verdict \| iteration, archetype) |
| Confidence distribution | Verifier output | mu, sigma of confidence by verdict |
| Revision improvement rate | Pre/post revision confidence | P(improve \| error_category) |
| Revision regression rate | Confidence drop after revision | P(regress) |
| FIXABLE rate | FIXABLE verdicts with corrections | P(FIXABLE \| iteration) |
| FIXABLE success rate | Re-verify passes | P(accept \| FIXABLE re-verify) |
| Atom annotation count | ATOM[N] markers in solutions | Atoms-per-solution distribution |
| Atom targeting accuracy | Atom-flagged steps vs verifier errors | P(correct_target \| atom_flag) |
| Error category distribution | `classify_errors()` on critiques | P(category \| archetype) |
| Approach diversity | Atom-structure hash clustering + error-category proxy | M (distinct approaches in N=3) |
| Stall frequency | Consecutive delta_conf < epsilon | P(stall \| iteration) |
| Breaker demotion rate | FLAW_FOUND verdicts | P(demotion \| accepted) |
| Token usage | Subagent response metadata | Tokens per call, context utilization |

**Late-game specific (full-depth runs only):**
- Iteration at which stall resets fired
- Post-exhaustion dynamics (behavior after max_resets reached)
- Context exhaustion events (ContextExhaustedError)
- Failed approaches length vs generation confidence

### 2.5 Distribution Fitting

From the raw measurements, fit parametric distributions:

- **Verdict:** Categorical per (archetype, iteration_bucket) where buckets = {1-2, 3-5, 6-8}
- **Confidence:** Beta distribution per verdict (captures [0,1] support and skew)
- **Revision improvement:** Bernoulli per error_category
- **Atom count:** Poisson (count data)
- **Approach count M:** Empirical per archetype (no parametric assumption)
- **Stall rate:** Bernoulli per iteration bucket

Quality gate: coefficient of variation < 0.5 on all key metrics. If violated, expand calibration (+50% budget reserved for this contingency).

**Approach classification:** Approaches are classified using a two-signal method: (1) atom-structure hash from `parse_atoms()` — candidates with different atom DAG structures are different approaches; (2) error-category proxy — candidates receiving different error classifications (`classify_errors()`) are likely using different strategies. Two candidates are "same approach" if both signals match. M is the number of distinct clusters per problem.

**Approach ceiling derivation:** The ceiling for each approach is estimated as the maximum confidence observed across all iterations for candidates in that cluster. Per-archetype ceiling distributions are fitted as Beta distributions from these per-problem maxima.

**Late-game distribution fitting:** For the iteration bucket {6-8}, archetype dimensions are collapsed (all archetypes pooled) to avoid critically sparse cells. The 3 full-depth problems yield ~27 candidate-verifications in this bucket (3 problems x 3 iterations x 3 candidates), sufficient for pooled verdict/confidence fitting but not per-archetype breakdown.

## 3. Phase 2: Simulation

### 3.1 Two Mini-Models

Both share a common base (`AlethicSimulator`) and differ in candidate selection policy and within-approach optimization.

#### Shared Base

```python
class AlethicSimulator:
    # Calibrated distributions (fitted from Phase 1)
    verdict_dist: dict         # P(verdict | archetype, iter_bucket)
    confidence_dist: dict      # Beta(a,b) per verdict
    revision_rates: dict       # P(improve | error_category)
    regression_rate: float     # P(regress | revision)
    fixable_rate: float        # P(FIXABLE)
    fixable_success: float     # P(accept | FIXABLE re-verify)
    atom_count_dist: tuple     # Poisson(lambda)
    atom_targeting: float      # P(correct_target | atom_flag)
    error_cat_dist: dict       # P(category | archetype)
    approach_count: dict       # M per archetype (empirical)
    stall_rate: dict           # P(stall | iter_bucket)
    breaker_demotion: float    # P(demotion | accepted)

    # Fixed parameters (thorough preset)
    max_iterations = 8
    max_revisions = 5
    confidence_threshold = 0.95
    base_n = 3
    stall_window = 3
    stall_epsilon = 0.02
    max_resets = 2             # max(1, 8//4)
    reset_n_boost = 1
```

**Per-iteration shared logic:**
1. Draw problem archetype (weighted by benchmark mix)
2. Draw M (viable approaches) from `approach_count[archetype]`
3. Assign approach ceilings from archetype-specific Beta distribution
4. Generate N candidates — **models diverge here**
5. Verify each candidate (verdict + confidence from calibrated distributions)
6. Classify error category
7. Acceptance gate: verdict=CORRECT, confidence >= 0.95
8. If acceptable: breaker check (P(demotion) from calibration)
9. If FIXABLE: attempt shortcut (P(success) from calibration)
10. Revision sub-loop (up to max_revisions, per-category improvement rates)
11. Update stall tracking, atom history, token estimate

#### Model E: AtomGuidedSimulator

Overrides candidate selection and revision targeting:

- **Candidate selection:** Greedy — pick one approach, generate N=3 variants
- **Revision targeting:** Atom-guided. P(improve) = base_rate x targeting_boost, where targeting_boost is calibrated from atom targeting accuracy
- **Stall recovery:** Strategy reset — switch approach from remaining M-1, reduce revision budget to 1, boost N by reset_n_boost. Max 2 resets; once exhausted, no escape valve.
- **Atom stability:** Hash-based tracking. STABLE atoms get REDUCED verification attention (modeled as quality bonus on verifier accuracy)

#### Model F: PUCTWidenSimulator

Overrides candidate selection:

- **Candidate selection:** PUCT with progressive widening and uniform prior. At iteration t, considers min(M, ceil(t^0.5)) approaches. Score = Q(a) + cpuct * P(a) * sqrt(total_visits) / (1 + visits(a)), where P(a) = 1/M (uniform prior). Note: with uniform priors, effective exploration constant is cpuct/M in the low-visit regime; after ~4+ visits per approach, the denominator (1 + visits(a)) dominates and effective exploration decays naturally. The Tier 1 default cpuct=1.414 is a starting point without theoretical guarantee for this PUCT variant; the Tier 2 sweep range (0.25-3.0) brackets the effective optimum for M=3-8. **Limitation:** progressive widening with t^0.5 limits exploration to ceil(sqrt(8))=3 approaches within 8 iterations; problems requiring approach 4+ are disadvantaged under Model F
- **Revision targeting:** Uniform (no atom guidance) — targets random step
- **Stall recovery:** No explicit detection. PUCT naturally shifts to under-explored approaches when current one plateaus.
- **No atom stability tracking**

#### Overhead Model

**Both models get 8 effective iterations.** No iteration-count penalty.

Neither model adds API calls beyond the baseline GVR loop. Model E adds ~3K prompt tokens total (atom focus directive + stability advisory) — negligible at 1.5% of context budget. Model F adds zero prompt tokens (PUCT is a local selection policy). Token overhead is recorded in cost metrics but does not reduce iterations.

### 3.2 Free Parameter Protocol (Three-Tier)

| Tier | Purpose | Model E params | Model F params |
|------|---------|---------------|---------------|
| **Tier 1 (primary)** | Defaults-vs-defaults, gate decision | thorough preset defaults | cpuct=1.414 (UCB1 theoretical) |
| **Tier 2 (sensitivity)** | Per-parameter sweep | stall_window in {2,3,4,5} | cpuct in {0.25, 0.5, 1.0, 1.414, 2.0, 3.0} |
| **Tier 3 (ceiling)** | Oracle-optimal upper bound | Best across Tier 2 sweep | Best across Tier 2 sweep |

Gate decision is based on Tier 1. If Tier 1 and Tier 3 disagree on winner, flag as "parameter-sensitive conclusion."

### 3.3 Trial Structure

| Trial type | Count | Detail level |
|-----------|-------|-------------|
| Paired (aggregate) | 5,000 | Solve/not-solve, confidence, iterations, cost |
| Traced (diagnostic) | 2,000 | Full per-iteration event log |

Each trial draws the same problem for both models (paired design). Traced trials additionally record: approach selected, visit counts, stall events, FIXABLE shortcuts, breaker interventions, error categories, atom counts/stability, revision outcomes, token estimates.

### 3.4 Problem Mix

Per-archetype breakdown reported as **primary** output. Weighted aggregate as secondary:

| Archetype | Benchmark fraction | Aggregate weight (research user) |
|-----------|-------------------|--------------------------------|
| Smooth | 40% | 30% |
| Insight | 50% | 50% |
| Adversarial | 10% | 20% (inflated for diagnostic value) |

A crossover table shows at what smooth/insight ratio the aggregate winner changes.

## 4. Phase 2 Statistical Analysis

### 4.1 Primary Decision Framework: Bayesian

The primary decision criterion is:

> **P(p_F - p_E > 3pp) > 0.95**

where p_E and p_F are the solve rates of Model E and Model F respectively.

Implementation:
- Uninformative priors: Beta(1,1) on each solve rate
- Posterior: Beta(1 + successes, 1 + failures) for each model
- P(delta > threshold) computed via 100K posterior samples
- Report full posterior distribution over the difference

The 3pp threshold is a default. The simulation also reports P(delta > 1pp), P(delta > 5pp), and P(delta > 10pp) to allow cost-sensitivity analysis.

### 4.2 Primary Effect Size: NNT

**Number Needed to Treat** with 95% credible interval:

> "Model F needs N problems [95% CI: lo-hi] to produce 1 additional solve over Model E."

NNT = 1 / (p_F - p_E). At 3pp: NNT = 33. Reported alongside cost-per-incremental-solve = NNT x cost_per_problem.

### 4.3 Secondary Test: McNemar's

McNemar's test on the 2x2 discordant-pair table (paired binary outcomes). Reports:
- Discordant pair count (minimum 15 for the result to be informative)
- Risk difference with exact binomial CI on b/(b+c)

McNemar's is the correct frequentist test for paired binary data (not Wilcoxon, which assumes continuous/ordinal differences).

### 4.4 What Is NOT Reported

- **Cohen's d** — wrong for binary outcomes (SD is a deterministic function of the mean)
- **p-values as decision input** — at N=5K, even 0.8pp is significant; the Bayesian threshold does the work
- **Single weighted aggregate without per-archetype breakdown** — the archetype weights are decision-sensitive

### 4.5 Diagnostic Metrics (from 2K traced trials)

| Metric | What it reveals |
|--------|----------------|
| Approach discovery rate | How quickly each model finds the best approach |
| Stall recovery success | When stalled, how often does the model escape? |
| Wasted iterations | Iterations with no confidence improvement and no information gain |
| PUCT exploration profile | Visit distribution across approaches over time (Model F only) |
| Cost per solve | Total tokens / number of solves (asymmetric between models) |
| Peak context utilization | Max fraction of context budget consumed (risk of ContextExhaustedError) |
| Candidate diversity | Atom-structure hash diversity within N=3 candidates per iteration |
| Verifier false acceptance rate | Post-hoc ground-truth check on benchmark problems with known answers |

### 4.6 Power Analysis

At N=5K paired trials with McNemar's test (p_d = 0.20 discordant rate):

| Metric | Value |
|--------|-------|
| Power for 3pp effect | 99.8% |
| 95% CI half-width | +/- 1.24pp |
| Minimum detectable effect (80% power) | 1.77pp |

5K is sufficient. 100K would be 20-50x overkill.

**Bayesian sensitivity note:** For the primary Bayesian criterion P(delta > 3pp) > 0.95 to fire, the observed effect must be approximately 4.6pp or larger (3pp threshold + 1.645 x ~0.95pp posterior SD at N=5K). The frequentist MDE of 1.77pp represents the limit of detection, not the Bayesian decision boundary. If finer discrimination is needed, lower the practical threshold to 1-2pp.

## 5. Phase 3: Validation

### 5.1 Protocol

Run 50 single-iteration probes on 10 held-out problems (5 per problem), alternating Model E and Model F protocols. Each probe runs one GVR iteration (generate 3 → verify 3 → revise 1 → re-verify 1 = ~8 calls), not a full 8-iteration solve. This validates the per-iteration distributions, not end-to-end solve rate.

**Held-out problems:** 10 problems from `gate-v38.json` (the 100-problem benchmark), excluding the 20 calibration problems. Stratified: 4 smooth, 4 insight, 2 adversarial/false-claim. Selected by difficulty tag to cover easy/medium/hard within each archetype.

Total: 50 probes x ~8 calls = ~400 subagent calls.

### 5.2 Validation Criteria

Per-problem validation at N=5 has no statistical power (95% CI width ~80pp). Instead:

| Criterion | Threshold | What it tests |
|-----------|-----------|--------------|
| **Aggregate solve rate** | Simulation within +/-15pp of observed | Absolute calibration (50 trials gives this precision) |
| **Spearman rank-order correlation** | rho > 0.3 across 10 problems | Relative difficulty ordering (rho > 0.5 has only 42% power at n=10; 0.3 gives 78% power to detect rho=0.7) |
| **Difficulty-bin ordering** | Easy-bin solve rate > Hard-bin solve rate | Minimal sanity check with pooled samples |

Per-problem +/-5pp comparisons are reported but not gated on (no statistical power at N=5).

### 5.3 Fallback

If validation fails (criterion 1 or 2 not met): expand calibration to all 20 problems, drop the validation phase. Trade external validity for statistical power.

## 6. New Metrics (from R6 Confounder Review)

Four metrics identified as critical to avoid a "confident but wrong" decision:

| Metric | Why critical | Implementation |
|--------|-------------|---------------|
| **Cost per solve** | Model F may cost 2-3x more in tokens | Plumb `token_ledger` into simulation; compute NNT x cost |
| **Peak context utilization** | Model F carries tree state; may exhaust context earlier | Track cumulative token estimate per iteration |
| **Candidate diversity hash** | Current approach-hash (verdict:conf) conflates different strategies | Use atom-structure hash from `parse_atoms()` |
| **Verifier false acceptance** | Binary solve rate doesn't distinguish correct from verifier-fooled | Post-hoc ground-truth check on known-answer problems |

## 7. Success Criteria

The experiment produces a decision if ALL of:

1. Calibration distributions are stable (CV < 0.5 on key metrics)
2. Validation passes (aggregate within +/-15pp AND Spearman rho > 0.3)
3. Bayesian posterior is decisive: P(delta > 3pp) > 0.95 OR P(delta < -3pp) > 0.95

If the posterior is indecisive (neither > 0.95), the per-archetype breakdown determines the recommendation:
- E wins on smooth AND F wins on insight → recommend E for default preset, prototype F for thorough/extreme
- One model wins on all archetypes → recommend that model
- Mixed results → recommend keeping E (status quo, lower engineering risk)

## 8. Cost Estimate

Extended thinking (15K budget) on thorough preset means each Opus subagent call takes 30-120 seconds. With N=3 parallel generation and sequential verification, one iteration takes ~5-8 minutes. Estimates are conservative — generation is parallelized via ThreadPoolExecutor in the Python library, but verification is sequential.

| Phase | Subagent calls | Subscription tokens | Wall clock |
|-------|---------------|-------------------|-----------|
| Calibration | ~600-770 | ~15-23M tokens | 5-8 hours |
| Simulation | 0 (pure Python) | 0 | 5-10 min |
| Validation | ~400 | ~5-8M tokens | 2-3 hours |
| **Total** | **~1000-1170** | **~20-31M tokens** | **~8-12 hours** |

**Execution path:** Phase 1 and 3 use the Python library with `ANTHROPIC_API_KEY` (not Claude Code Task tool), matching the gate runner approach. The gate runner already demonstrates this works (`scripts/run_gate.py`). Subscription-only execution via Task tool is not feasible at this call volume due to sequential execution overhead.

**Contingency:** If CV quality gate fails, expand calibration to all 20 problems (+50% Phase 1 cost, ~3-4 additional hours).

## 9. Limitations and Known Confounds

### 9.1 Policy-Dependence Confound (Critical)

Phase 1 calibrates distributions from the real Alethic agent, which uses the current policy: greedy selection + stall reset — essentially Model E's policy. Model F (PUCT) would make different approach selections, encounter different error distributions, and stall differently. Using E-calibrated distributions to simulate F assumes **policy-independence**, which is an approximation.

Specifically, these distributions are policy-dependent:
- **Error category distribution:** Depends on which approach was selected (PUCT may select approaches that produce different error profiles)
- **Stall frequency:** PUCT's deliberate exploration of low-confidence branches would register as "stalls" under the current detector
- **Approach diversity dynamics:** Calibration sees N=3 variants of one approach; Model F would produce N=3 candidates from potentially different approaches

**Mitigation:** In Phase 3 validation, specifically compare F's real error-category distribution against the Phase 1 calibration. If they diverge significantly (chi-squared test, p < 0.05), flag the simulation results as potentially biased toward E and expand calibration with F-protocol runs.

### 9.2 Progressive Widening Ceiling

PUCT with t^0.5 widening caps exploration at ceil(sqrt(8)) = 3 approaches within 8 iterations. If the optimal approach is approach 4 or 5, Model F will never discover it. This is a structural limitation of the widening exponent, not a parameter tuning issue. The simulation reports how often the optimal approach falls outside F's exploration window.

### 9.3 Subscription vs API Execution

The original design assumed Claude Code subscription execution. Revised estimates require `ANTHROPIC_API_KEY` and the Python library. At thorough preset pricing (~$7.50/problem for a full 8-iteration run), Phase 1 costs ~$45-60 and Phase 3 costs ~$30-40, totaling ~$75-100 in API spend.

## 10. Deliverables

1. `scripts/e_vs_f_calibrate.py` — Phase 1 calibration runner
2. `scripts/e_vs_f_simulate.py` — Phase 2 Monte Carlo simulation (Model E + Model F)
3. `scripts/e_vs_f_validate.py` — Phase 3 validation runner
4. `data/calibration/e-vs-f-distributions.json` — Fitted distributions from Phase 1
5. `data/calibration/e-vs-f-traces.jsonl` — Raw calibration event traces
6. `docs/results/e-vs-f-report.md` — Final report with decision recommendation

## 11. Appendix: Prior Evidence Summary

20 experiments across two rounds produced these key findings:

**Round 1 (10 scientists):** E wins 7-2 on aggregate. E dominates convergence (+21-29pp), false-premise detection (+63pp TPR), noise robustness (+7-8%). F wins marginally on approach selection (+2-4pp) and stall recovery (+7pp).

**Round 2 (10 adversarial reviewers):** Several Round 1 findings overturned:
- PUCT+widening (0.833) dramatically outperforms flat UCB1 (0.662) tested in Round 1
- F wins at M=3-5 approaches (most real problems)
- Atom targeting needs 85%+ accuracy to beat F (at K=6)
- False-premise detection advantage was based on unrealistic atom observations
- Sticky optima favor F; regression dynamics favor E
- Hybrid E+F worse than E alone (no synergy)

The simulation uses PUCT+widening (not flat UCB1) and empirically-calibrated parameters (not guessed values) to resolve these ambiguities.
