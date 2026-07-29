# E vs F Monte Carlo Experiment Report

**Trials:** 5000 paired | **Traced:** 2000

## Summary

| Metric | Model E | Model F |
|--------|---------|---------|
| Solve rate | 0.0312 | 0.0400 |
| Mean confidence | 0.7346 | 0.7631 |
| Mean iterations | 7.85 | 7.79 |
| Mean cost (tokens) | 9330592 | 9306375 |

## Bayesian Posterior Analysis

- **Mean delta (F - E):** 0.0088
- **95% CI:** [0.0015, 0.0161]
- P(F better by >1pp): 0.3699
- P(F better by >3pp): 0.0000
- P(F better by >5pp): 0.0000
- P(F better by >10pp): 0.0000

## McNemar's Test

- E solves, F doesn't: 44
- F solves, E doesn't: 88
- Discordant pairs: 132
- Chi-squared: 14.0076
- p-value: 0.000182

## Number Needed to Treat (NNT)

- **Winner:** Model F
- **NNT:** 113.6
- **95% CI:** [62.1, 608.7]

## Per-Archetype Breakdown

| Archetype | E solve rate | F solve rate | N | Winner |
|-----------|-------------|-------------|---|--------|
| smooth | 0.0331 | 0.0351 | 2023 | Tie |
| insight | 0.0294 | 0.0439 | 2484 | F |
| adversarial | 0.0325 | 0.0406 | 493 | Tie |

## Diagnostics (from traced trials)

| Metric | Model E | Model F |
|--------|---------|---------|
| approach_discovery_rate | 2.3435 | 2.5520 |
| wasted_iterations | 0.9963 | 0.9951 |
| cost_per_solve | 3294118.5086 | 2523082.4370 |
| candidate_diversity | 2.3435 | 2.5520 |
| false_acceptance | 0.0000 | 0.0000 |
| stall_recovery_success | 0.0157 | N/A |

## Crossover Analysis

| Smooth % | Insight % | Winner | Margin |
|----------|-----------|--------|--------|
| 0% | 90% | F | +0.0139 |
| 5% | 85% | F | +0.0132 |
| 10% | 80% | F | +0.0126 |
| 15% | 75% | F | +0.0120 |
| 20% | 70% | F | +0.0114 |
| 25% | 65% | F | +0.0107 |
| 30% | 60% | F | +0.0101 |
| 35% | 55% | F | +0.0095 |
| 40% | 50% | F | +0.0088 |
| 45% | 45% | F | +0.0082 |
| 50% | 40% | F | +0.0076 |
| 55% | 35% | F | +0.0070 |
| 60% | 30% | F | +0.0063 |
| 65% | 25% | F | +0.0057 |
| 70% | 20% | F | +0.0051 |
| 75% | 15% | F | +0.0045 |
| 80% | 10% | F | +0.0038 |
| 85% | 5% | F | +0.0032 |
| 90% | 0% | F | +0.0026 |

## Tier 2 Parameter Sensitivity

### Model F (cpuct sweep)

| cpuct | Solve rate |
|-------|-----------|
| 0.250 | 0.0360 |
| 0.500 | 0.0360 |
| 1.000 | 0.0360 |
| 1.414 | 0.0360 |
| 2.000 | 0.0360 |
| 3.000 | 0.0360 |

**Best:** cpuct=0.250 → 0.0360

### Model E (stall_window sweep)

| stall_window | Solve rate |
|-------------|-----------|
| 2 | 0.0360 |
| 3 | 0.0280 |
| 4 | 0.0240 |
| 5 | 0.0170 |

**Best:** stall_window=2 → 0.0360

> **WARNING:** Tier 1 and Tier 3 disagree on winner. Conclusion is parameter-sensitive.

## Decision Recommendation

**Recommendation: Indecisive — consult per-archetype breakdown**
P(F better by >1pp) = 0.3699 — insufficient evidence for either model.
