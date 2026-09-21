# JEV as a fast advisory layer for Alethic

*Design note, 2026-09-21. Status: proposed; no JEV integration or paid experiment
is implemented by this note. Repository observations refer to commit
`3792adb1090b96e6cbef34e935e93f23885c54ba`.*

The useful hypothesis is that a small, bounded semantic judgment could help
Alethic avoid an unproductive expensive iteration or select a better next check.
The target is time to an independently verified result. Low latency on an
individual decision does not establish an end-to-end improvement.

Start with **semantic repeated-failure detection plus next-check recommendation**,
recorded in shadow mode alongside the existing behavior. Treat JEV outputs as
advisory search priors. They are neither proof certificates nor calibrated
probabilities that a mathematical claim is true.

## What already exists

These are observed mechanisms, not proposed JEV features:

| Existing mechanism | Source | Implication |
|---|---|---|
| A deterministic, state-reading `OracleRouter` bundles pre-iteration choices and exposes stall, ranking, revision-budget and atom-focus methods. | [oracle_router.py](../../src/alethic/oracle_router.py), especially `route`, `check_stall`, `rank_candidates` and `revision_budget` | Preserve these cheap rules as the baseline. A semantic assessor would advise the orchestration layer; it need not replace the router. |
| Hierarchical keyword classification maps critiques to error categories and a fixed oracle-routing table. | [error_taxonomy.py](../../src/alethic/error_taxonomy.py), `classify_inconsistency`, `classify_errors`, `_ORACLE_ROUTING` | Test whether meaning-sensitive classification helps on ambiguous critiques before replacing any keyword behavior. The current taxonomy already has nine specific categories plus a general fallback. |
| Atom hashes normalize whitespace and strip embedded verification-function blocks. Stability uses those hashes and iteration confidence. | [atoms.py](../../src/alethic/atoms.py), `content_hash` and `classify_atom_stability` | These detect specified textual repetitions and trajectories. They do not establish semantic equivalence or proof correctness. |
| The router already observes repeated critique categories and builds saturation-awareness guidance. | [oracle_router.py](../../src/alethic/oracle_router.py), `saturation_signal` and `_build_saturation_block` | The proposed detector must add something beyond repeated labels or confidence plateaus. |
| Proof atoms have dependency information; tree search and its microkernel are separate consumers of routing information. | [proof_graph.py](../../src/alethic/proof_graph.py), [search.py](../../src/alethic/search.py), [microkernel.py](../../src/alethic/microkernel.py) | Evaluate flat and tree modes separately. `RoutingDecision.next_oracle` and `force_adversarial` are explicitly scaffolding for the flat path; tree search consumes `_ORACLE_ROUTING` directly. |
| Session and event data provide a starting point for profiling, with different persistence formats in the skill and Python library. | [agent.py](../../src/alethic/agent.py), [models.py](../../src/alethic/models.py), [session.py](../../src/alethic/session.py), [skill orchestrator](../../skills/alethic-common/orchestrator.md) | Inspect actual saved data before deciding which measurements are available. |

Do not insert a network judgment where a numeric threshold, exact identity,
resource limit or existing deterministic rule already answers the question well.

## First pilot: detect repetition and recommend a check

At a completed verification or revision event, construct a bounded record from
material the runner already has: the original obligation, current and immediately
preceding proposed solution, applicable conventions, relevant critique, available
test results, and the last few failed approaches. Retain source paths, atom IDs,
revision IDs and hashes. Do not expose future outcomes or evaluation labels.

Ask independent typed questions against that shared record:

| Question | Example allowed choices |
|---|---|
| Does the revision address the specific objection? | `addresses`, `partly_addresses`, `does_not_address`, `insufficient_information` |
| Is the proposed repair materially different from the preceding failed repair? | `different`, `substantially_repeated`, `insufficient_information` |
| Does it introduce or strengthen an assumption relevant to the obligation? | `yes`, `no_detected_change`, `insufficient_information` |
| Which available check would most usefully test the unresolved issue? | A runner-supplied subset of `dimensional_analysis`, `limiting_case`, `symbolic_check`, `numerical_counterexample`, `source_inspection`, `independent_review`, plus `insufficient_information` |

These labels are proposed evaluation targets, not existing Alethic API fields.
The next-check question must stand on the supplied evidence; it cannot depend on
another answer from the same independent question bundle. The runner combines
answers using an explicit, testable policy. Scores and thresholds need local
evaluation rather than being treated as scientific posterior probabilities.

An illustrative case is a revision that changes notation but still assumes the
boundary term criticized by the verifier vanishes. JEV could flag the repeated
assumption and recommend a boundary-condition or limiting-case check. It would
not settle whether that boundary term vanishes. Label this kind of invented case
as illustrative until a reviewed real trace supports it.

In the first pilot, store the recommendations and compare them with the current
router's decision; take no action from JEV. Unavailable tools, missing evidence,
uncertain outputs, malformed responses, timeouts and exhausted allowances all
retain the existing behavior. Record these cases as outcomes rather than silently
excluding them from evaluation.

Keep the independent verifier's information boundary: historical critiques and
failed approaches may inform the routing assessor, but do not feed JEV judgments
or generator reasoning traces into the verifier as evidence of correctness.
Current category-count guidance is a separate, documented baseline behavior.

## Further opportunities, after the pilot

| Opportunity | Proposed use | Boundary and evaluation concern |
|---|---|---|
| Edit impact | Distinguish wording changes from changes to domains, approximation orders, boundary conditions or scalar-product conventions; suggest additional affected obligations. | Exact dependency and changed-input rules still mandate rechecks. JEV may add suspected dependencies; it must not waive required re-verification or authorize proof reuse on semantic similarity alone. |
| Context selection | Select relevant definitions, conventions, lemmas, unresolved objections and source passages for the next task. | Preserve exact excerpts, source locations and versions. Mandatory assumptions and counterevidence must not disappear through relevance scoring. Compare completeness as well as token reduction. |
| Bounded prefetch | While a long reasoning step runs, identify a small set of likely-needed local definitions, papers or existing results. | Limit concurrency, bytes, time and allowed sources in code. Start with authorized read-only retrieval, record unused fetches, and include their cost. A recommendation does not authorize new external services. |
| Simulation and tool failure triage | Classify unfamiliar text logs as likely setup, precision, convergence, domain or unexpected-result problems; recommend an existing diagnostic. | Numeric convergence tests, tolerances and retry limits remain deterministic. JEV does not certify a simulation or operate laboratory equipment. Include false reruns and missed failures in evaluation. |
| Candidate similarity | Detect approaches that are textually different but repeat the same unproductive strategy. | Initially record an advisory similarity signal. Prematurely pruning a useful branch is a meaningful error, particularly in mathematical search. |

The latency opportunity is an event hook inside the runner: tool result or
revision, deterministic checks, bounded semantic questions, then an allowed next
step or a return to the main reasoning model. If a full host-model turn must
prepare and interpret every JEV call, much of the intended saving may disappear.
No such hook is installed by this note.

TypeSafe documents that multiple independent questions can share state and be
evaluated in parallel; its introduction describes minimal additional response
time from adding questions. This motivates one bounded bundle per event rather
than several sequential calls. It does not make dependent judgments independent,
or guarantee the same latency under arbitrary batch size or input length.
[TypeSafe introduction](https://docs.typesafe.ai/introduction)

## Profile existing runs before building the hook

The companion Farcaster change adds a read-only host-agent command:

```text
/fc:jev:profile <trace paths or project scope>
$jev profile <scope>
```

This is part of the [Farcaster research and JEV skill change](https://github.com/hyperion-git/farcaster/pull/1);
availability depends on that change being included in the installed version.
Profiling reads existing traces and returns evidence-backed candidates. It does
not instrument the runner, start a watcher, invoke JEV, run integrations, or
change project files. The audit command finds possible decision tasks in code;
the profile command investigates whether saved runs show a worthwhile bottleneck.

Useful Alethic inputs, when they exist:

- Skill sessions: `.alethic/<session>/session.json`,
  `worklog/events.jsonl`, `worklog/iterN/candidate_C.md`,
  `verification_cC.md`, `revision_M.md`, `verification_revM.md`,
  `changelog_revM.md`, and `worklog/autopsy.md`.
- Python result exports: `AgentResult.to_dict()` contains `events`,
  `elapsed_seconds`, and optional `token_usage`. `EventLog` is an
  in-memory list; do not assume every Python run persisted a JSONL trace.
- Checkpoints: `session.json` and, for tree search, `tree_state.json`;
  `worklog/best_solution.md` supplies the saved candidate when present.

The skill appends events after Task calls. A gap between consecutive completion
timestamps includes intervening work and is not an isolated model-response
measurement. Missing starts, end times, usage or model identities stay unknown.
Inspect timestamp units and run boundaries, including resumes, before comparing
durations. Do not sum overlapping candidate spans into elapsed wall time or
double-count an aggregate session record and its children.

The profile should identify repeated decision types and failures, distinguish
observed repetitions from inferred semantic repetitions, and recommend one
bounded pilot with the source events that motivated it. It can report measured
time spent and hypothetical opportunities separately. It cannot establish that
a different decision would have prevented a later failed iteration.

## Evaluation and promotion criteria

1. **Freeze cases and labels.** Select reviewed real cases covering genuine
   progress, repeated errors, paraphrases, changed assumptions, contradictory or
   missing references, productive repeated attempts, and useful unconventional
   repairs. Hold out entire related problems or trajectories to reduce leakage.
   Reviewer labels and future outcomes must be separate from model inputs.
   Record disagreement and adjudication rather than manufacturing certainty.
2. **Compare with the existing process.** Include the deterministic router,
   keyword classifier, hashes, category saturation and current verification
   behavior. Keep policy, case set, conventions, tool availability and scoring
   version fixed for each comparison. Do not use agreement with the current
   verifier as the sole correctness label; see the
   [verifier-bias note](2026-06-27-rqgm-verifier-bias.md).
3. **Measure decision quality.** Report per-question confusion, false progress,
   missed useful progress, false repetition, changed-assumption misses,
   uncertainty/abstention coverage, failure rate and calibration against reviewed
   labels. Distinguish factual judgment error from a plausible recommendation
   whose benefit remains unobserved.
4. **Measure total work.** Capture p50/p95 request latency with sample counts and
   input/bundle sizes, host preparation, retrieval, parsing, fallback, retries
   and queueing. Include all paid model calls and provider-confirmed charges when
   available; mark token-rate estimates and unknown costs explicitly.
5. **Measure research outcomes in a later controlled trial.** Shadow observations
   cannot demonstrate saved iterations. After satisfactory offline and shadow
   results, a separately authorized controlled comparison can measure time and
   cost per independently verified result, wasted iterations, missed solutions
   and false acceptance, with matched tasks and budgets. Freeze the acceptance
   criteria before that trial.

A lower median request latency is insufficient if tail latency, wrong
recommendations or fallback overhead worsen the research run. Retain the
deterministic baseline unless a worthwhile improvement survives the quality
checks. No speedup is predicted quantitatively here.

## Provider and operational assumptions

For a future implementation, the user's requested default is direct OpenRouter
with `~typesafe/jev-latest`, sharing the credential source already used by the
installed JEV MCP. Read credentials at runtime through the configured loader;
do not copy secrets into this repository, policies, traces or reports. Farcaster
already has a shared decision client; decide an explicit dependency or reuse
boundary before coupling Alethic to its filesystem layout. Do not assume
Alethic's existing chat-model OpenRouter adapter accepts the JEV decisions API.

Use the moving latest alias as requested, recording the requested alias,
returned model/provider identity where supplied, timestamps, policy/input
hashes and accounting metadata. A changed underlying model is an evaluation
confound: report and rerun the held-out checks rather than silently comparing it
with an older sample as though the treatment were unchanged.

JEV currently accepts text, not images, audio or video. Plot or experimental-image
triage therefore needs computed diagnostics or an upstream description; include
the cost, delay and errors of that preparation.
[TypeSafe state documentation](https://docs.typesafe.ai/concepts/state)

TypeSafe reports 70–500 ms response times and says its published evaluations
were generally run from laptops on the US West Coast, near its service.
Those are vendor measurements. Actual latency from this Berlin workflow through
OpenRouter, especially p95 under realistic context and question bundles, is
unknown. So are local scientific decision quality and end-to-end savings.
[TypeSafe launch article](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

The decision for now is to preserve these hypotheses, profile existing traces,
and prepare the first shadow evaluation. Runtime integration, paid runs,
autonomous routing changes and scientific acceptance changes remain future work.
