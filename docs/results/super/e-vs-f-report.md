# E vs F Monte Carlo Experiment Report

**Trials:** 5000 paired | **Traced:** 2000

## Summary

| Metric | Model E | Model F |
|--------|---------|---------|
| Solve rate | 0.0214 | 0.0404 |
| Mean confidence | 0.6535 | 0.7573 |
| Mean iterations | 7.88 | 7.80 |
| Mean cost (tokens) | 4275397 | 4263779 |

## Bayesian Posterior Analysis

- **Mean delta (F - E):** 0.0190
- **95% CI:** [0.0122, 0.0259]
- P(F better by >1pp): 0.9956
- P(F better by >3pp): 0.0009
- P(F better by >5pp): 0.0000
- P(F better by >10pp): 0.0000

## McNemar's Test

- E solves, F doesn't: 16
- F solves, E doesn't: 111
- Discordant pairs: 127
- Chi-squared: 69.5748
- p-value: 0.000000

## Number Needed to Treat (NNT)

- **Winner:** Model F
- **NNT:** 52.6
- **95% CI:** [38.6, 82.0]

## Per-Archetype Breakdown

| Archetype | E solve rate | F solve rate | N | Winner |
|-----------|-------------|-------------|---|--------|
| smooth | 0.0227 | 0.0346 | 2023 | F |
| insight | 0.0217 | 0.0447 | 2484 | F |
| adversarial | 0.0142 | 0.0426 | 493 | F |

## Diagnostics (from traced trials)

| Metric | Model E | Model F |
|--------|---------|---------|
| approach_discovery_rate | 1.3330 | 2.5920 |
| wasted_iterations | 0.9974 | 0.9952 |
| cost_per_solve | 1103170.5157 | 1289563.8724 |
| candidate_diversity | 1.3330 | 2.5920 |
| false_acceptance | 0.0000 | 0.0000 |
| stall_recovery_success | 0.0190 | N/A |

## Crossover Analysis

| Smooth % | Insight % | Winner | Margin |
|----------|-----------|--------|--------|
| 0% | 90% | F | +0.0235 |
| 5% | 85% | F | +0.0229 |
| 10% | 80% | F | +0.0224 |
| 15% | 75% | F | +0.0218 |
| 20% | 70% | F | +0.0213 |
| 25% | 65% | F | +0.0207 |
| 30% | 60% | F | +0.0202 |
| 35% | 55% | F | +0.0196 |
| 40% | 50% | F | +0.0191 |
| 45% | 45% | F | +0.0185 |
| 50% | 40% | F | +0.0180 |
| 55% | 35% | F | +0.0174 |
| 60% | 30% | F | +0.0168 |
| 65% | 25% | F | +0.0163 |
| 70% | 20% | F | +0.0157 |
| 75% | 15% | F | +0.0152 |
| 80% | 10% | F | +0.0146 |
| 85% | 5% | F | +0.0141 |
| 90% | 0% | F | +0.0135 |

## Tier 2 Parameter Sensitivity

### Model F (cpuct sweep)

| cpuct | Solve rate |
|-------|-----------|
| 0.250 | 0.0350 |
| 0.500 | 0.0350 |
| 1.000 | 0.0350 |
| 1.414 | 0.0350 |
| 2.000 | 0.0350 |
| 3.000 | 0.0350 |

**Best:** cpuct=0.250 → 0.0350

### Model E (stall_window sweep)

| stall_window | Solve rate |
|-------------|-----------|
| 2 | 0.0310 |
| 3 | 0.0220 |
| 4 | 0.0150 |
| 5 | 0.0160 |

**Best:** stall_window=2 → 0.0310

## Decision Recommendation

**Recommendation: Implement Model F (PUCT + progressive widening)**
P(F better by >1pp) = 0.9956 exceeds 0.95 threshold.
