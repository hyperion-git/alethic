# E vs F Monte Carlo Experiment Report

**Trials:** 5000 paired | **Traced:** 2000

## Summary

| Metric | Model E | Model F |
|--------|---------|---------|
| Solve rate | 0.0216 | 0.0394 |
| Mean confidence | 0.6538 | 0.7584 |
| Mean iterations | 7.88 | 7.79 |
| Mean cost (tokens) | 6558989 | 6540969 |

## Bayesian Posterior Analysis

- **Mean delta (F - E):** 0.0178
- **95% CI:** [0.0110, 0.0246]
- P(F better by >1pp): 0.9888
- P(F better by >3pp): 0.0002
- P(F better by >5pp): 0.0000
- P(F better by >10pp): 0.0000

## McNemar's Test

- E solves, F doesn't: 17
- F solves, E doesn't: 106
- Discordant pairs: 123
- Chi-squared: 62.9593
- p-value: 0.000000

## Number Needed to Treat (NNT)

- **Winner:** Model F
- **NNT:** 56.2
- **95% CI:** [40.6, 90.6]

## Per-Archetype Breakdown

| Archetype | E solve rate | F solve rate | N | Winner |
|-----------|-------------|-------------|---|--------|
| smooth | 0.0232 | 0.0356 | 2023 | F |
| insight | 0.0209 | 0.0423 | 2484 | F |
| adversarial | 0.0183 | 0.0406 | 493 | F |

## Diagnostics (from traced trials)

| Metric | Model E | Model F |
|--------|---------|---------|
| approach_discovery_rate | 1.3450 | 2.5580 |
| wasted_iterations | 0.9974 | 0.9953 |
| cost_per_solve | 1542475.1088 | 1781347.9582 |
| candidate_diversity | 1.3450 | 2.5580 |
| false_acceptance | 0.0000 | 0.0000 |
| stall_recovery_success | 0.0185 | N/A |

## Crossover Analysis

| Smooth % | Insight % | Winner | Margin |
|----------|-----------|--------|--------|
| 0% | 90% | F | +0.0214 |
| 5% | 85% | F | +0.0210 |
| 10% | 80% | F | +0.0205 |
| 15% | 75% | F | +0.0201 |
| 20% | 70% | F | +0.0196 |
| 25% | 65% | F | +0.0192 |
| 30% | 60% | F | +0.0187 |
| 35% | 55% | F | +0.0183 |
| 40% | 50% | F | +0.0178 |
| 45% | 45% | F | +0.0174 |
| 50% | 40% | F | +0.0169 |
| 55% | 35% | F | +0.0165 |
| 60% | 30% | F | +0.0160 |
| 65% | 25% | F | +0.0156 |
| 70% | 20% | F | +0.0151 |
| 75% | 15% | F | +0.0147 |
| 80% | 10% | F | +0.0143 |
| 85% | 5% | F | +0.0138 |
| 90% | 0% | F | +0.0134 |

## Tier 2 Parameter Sensitivity

### Model F (cpuct sweep)

| cpuct | Solve rate |
|-------|-----------|
| 0.250 | 0.0330 |
| 0.500 | 0.0330 |
| 1.000 | 0.0330 |
| 1.414 | 0.0330 |
| 2.000 | 0.0330 |
| 3.000 | 0.0330 |

**Best:** cpuct=0.250 → 0.0330

### Model E (stall_window sweep)

| stall_window | Solve rate |
|-------------|-----------|
| 2 | 0.0270 |
| 3 | 0.0180 |
| 4 | 0.0160 |
| 5 | 0.0150 |

**Best:** stall_window=2 → 0.0270

## Decision Recommendation

**Recommendation: Implement Model F (PUCT + progressive widening)**
P(F better by >1pp) = 0.9888 exceeds 0.95 threshold.
