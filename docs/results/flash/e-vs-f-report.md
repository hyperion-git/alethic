# E vs F Monte Carlo Experiment Report

**Trials:** 5000 paired | **Traced:** 2000

## Summary

| Metric | Model E | Model F |
|--------|---------|---------|
| Solve rate | 0.0212 | 0.0392 |
| Mean confidence | 0.6542 | 0.7590 |
| Mean iterations | 7.88 | 7.80 |
| Mean cost (tokens) | 4172507 | 4162413 |

## Bayesian Posterior Analysis

- **Mean delta (F - E):** 0.0180
- **95% CI:** [0.0113, 0.0248]
- P(F better by >1pp): 0.9907
- P(F better by >3pp): 0.0002
- P(F better by >5pp): 0.0000
- P(F better by >10pp): 0.0000

## McNemar's Test

- E solves, F doesn't: 12
- F solves, E doesn't: 102
- Discordant pairs: 114
- Chi-squared: 69.4825
- p-value: 0.000000

## Number Needed to Treat (NNT)

- **Winner:** Model F
- **NNT:** 55.6
- **95% CI:** [40.3, 88.5]

## Per-Archetype Breakdown

| Archetype | E solve rate | F solve rate | N | Winner |
|-----------|-------------|-------------|---|--------|
| smooth | 0.0222 | 0.0341 | 2023 | F |
| insight | 0.0213 | 0.0431 | 2484 | F |
| adversarial | 0.0162 | 0.0406 | 493 | F |

## Diagnostics (from traced trials)

| Metric | Model E | Model F |
|--------|---------|---------|
| approach_discovery_rate | 1.3530 | 2.5600 |
| wasted_iterations | 0.9974 | 0.9950 |
| cost_per_solve | 1053805.0280 | 1300300.6819 |
| candidate_diversity | 1.3530 | 2.5600 |
| false_acceptance | 0.0000 | 0.0000 |
| stall_recovery_success | 0.0181 | N/A |

## Crossover Analysis

| Smooth % | Insight % | Winner | Margin |
|----------|-----------|--------|--------|
| 0% | 90% | F | +0.0220 |
| 5% | 85% | F | +0.0215 |
| 10% | 80% | F | +0.0210 |
| 15% | 75% | F | +0.0205 |
| 20% | 70% | F | +0.0200 |
| 25% | 65% | F | +0.0195 |
| 30% | 60% | F | +0.0190 |
| 35% | 55% | F | +0.0185 |
| 40% | 50% | F | +0.0180 |
| 45% | 45% | F | +0.0176 |
| 50% | 40% | F | +0.0171 |
| 55% | 35% | F | +0.0166 |
| 60% | 30% | F | +0.0161 |
| 65% | 25% | F | +0.0156 |
| 70% | 20% | F | +0.0151 |
| 75% | 15% | F | +0.0146 |
| 80% | 10% | F | +0.0141 |
| 85% | 5% | F | +0.0136 |
| 90% | 0% | F | +0.0131 |

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
| 2 | 0.0290 |
| 3 | 0.0180 |
| 4 | 0.0150 |
| 5 | 0.0160 |

**Best:** stall_window=2 → 0.0290

## Decision Recommendation

**Recommendation: Implement Model F (PUCT + progressive widening)**
P(F better by >1pp) = 0.9907 exceeds 0.95 threshold.
