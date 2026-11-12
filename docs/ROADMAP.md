# Agent Relationship development roadmap

The 0.3.0 foundation was merged in [PR #1](https://github.com/blueken31/agent-relationship/pull/1) at `cee0e4d`. The 0.4.0 durable checkpoints were merged in [PR #2](https://github.com/blueken31/agent-relationship/pull/2) at `427719b`, with passing [main CI](https://github.com/blueken31/agent-relationship/actions/runs/43181777529). The 0.5.0 original-task continuity work was merged in [PR #3](https://github.com/blueken31/agent-relationship/pull/3) at `dc7e3dc`, with passing [main CI](https://github.com/blueken31/agent-relationship/actions/runs/43185929957). This roadmap follows the approved focus on multi-agent collaboration diagnosis, recovery verification and measured value alongside existing harnesses. A completed SDK mechanism is not a claim of production readiness or real-model effectiveness.

## Completed foundation: 0.3.0

| Area | Delivered behavior | Validation |
|---|---|---|
| Assessment contract | One bilateral assessment; source, goal judgment, brief reason and explicit unknown errors; strict finite numeric validation | `test_assessment.py` |
| Scoring | No gain/loss cancellation; unilateral loss and goal violations remain unfavorable; unknown cannot increase trust; identity-aware asymmetry | `test_scoring.py` |
| Recording and diagnosis | Event deduplication, task-scoped profiles, first severe notification, callback isolation, retained evidence, read-only report trends | `test_runtime.py` |
| Recovery bookkeeping | Sticky task-linked incidents, bounded plans, explicit host authorization, same-task verification, stale evidence rejection, cancellation and cooldown | `test_recovery.py` |
| Developer experience | Offline end-to-end example, migration notes, observed versus inferred evidence | `PYTHONPATH=. python examples/task_recovery.py` |

The SDK records and diagnoses. It does not execute a proposed action, independently validate a host assertion or grant additional permission. Host authorization and result verification remain outside the SDK.

## Completed persistence: 0.4.0

| Area | Implemented scope | Validation |
|---|---|---|
| Optional local persistence | Standard-library SQLite via `storage_path`; default in-memory behavior preserved; explicit close/context-manager lifecycle | Storage and persistent-tracker tests |
| Restart continuity | Restore scores, original evidence, assessment provenance, deduplication, incidents, plans, cooldowns and report trends without model calls | State round-trip and restart tests |
| Transactional updates | Load, mutate and save in one writer transaction; rollback on failure; independent instances refresh current committed state | Rollback, contention and concurrency tests |
| Checkpoint compatibility | Explicit snapshot/scoring versions and scoring-configuration checks; incompatible or corrupt state fails without silently resetting it | `test_state.py`, `test_storage.py` |
| Developer walkthrough | Start recovery, reopen, deduplicate a retry, record verified recovery, reopen again and confirm the same task outcome | `PYTHONPATH=. python examples/durable_recovery.py` |

This is restoration of the current checkpoint. It does not re-evaluate historical events, keep a history of checkpoints, resume external execution or guarantee notification delivery. An uncommitted operation may need to be retried after interruption; a committed event with the same ID is not counted again.

## Completed original-task continuity: 0.5.0

| Area | Implemented scope | Validation |
|---|---|---|
| Read-only task view | `task_view()` gathers all task incidents into compact cards with observed facts, separate assessments, handling state, evidence references and one next action | Task-view and public-query tests |
| Pending queue | `task_queue()` returns unresolved task summaries and keeps unlinked incidents visible | Multi-pair, multi-task and unlinked-incident cases |
| Plan inspection | `repair_plans()` and `repair_plan()` read existing plans without creating proposals, advancing cooldown state or invoking a model | Snapshot/revision and restart checks |
| Host continuation example | Explicit host authorization and current task checks; original task/step/revision identity; local simulated result and receipt committed together | `test_host_integration.py`, `examples/host_task_recovery.py` |
| User experience preview | Responsive read-only HTML cards, expandable evidence and simulated lifecycle states | `examples/task_review_preview.py --output /tmp/task-review.html` |

The intended experience is: find the original task → inspect one concise issue and recommendation → let the host perform its authorized check → see the recorded verification → let the host continue the original step. A started plan means its attempt was recorded, not that external execution is running. Resolved incidents do not mean the original task is complete.

The host example's local result and receipt share one transaction. Its SDK readiness check and host commit use separate databases and are not atomic. A real host must supply its own concurrency policy and reconcile ambiguous external outcomes using the external operation's stable ID. No external executor or exactly-once external delivery is supplied.

## Completed: 0.6.0 collaboration evaluation and harness boundary

[PR #4](https://github.com/blueken31/agent-relationship/pull/4) merged at `cfb8c7c`.
Its [main CI](https://github.com/blueken31/agent-relationship/actions/runs/43193347392)
passed. This validates the implementation mechanisms, not a live comparison.

The product remains a collaboration diagnosis and recovery-validation layer for multi-agent systems, delivered as an embeddable Python SDK. Native harnesses already provide orchestration, approvals, tracing and recovery. Reusable diagnosis and evidence rules are a candidate benefit to measure, not a claim of exclusive capabilities.

| Deliverable | Scope | Acceptance |
|---|---|---|
| Collaboration cases | Versioned authored cases cover legitimate rejection, goal violation despite bilateral benefit, shared dependency failure, persistent harm, identity/model changes, stale evidence, recovery and third-party impact | Validate all cases; blind exports omit gold labels, explanatory titles and future observations |
| Paired evaluation | Native harness with full prompt/hooks versus the same setup plus SDK context; identical history, policy and total budget | Reject incomplete or mismatched runs; report decision errors, attribution and measured usage without inventing unknown costs |
| SDK evaluation bridge | Fresh prefix-isolated tracker with caller-supplied backend, shared decision callback and explicit usage accounting | No gold-derived assessments, no automatic advice-to-hold conversion, no cross-case state or future evidence |
| Agents API observation adapter | Explicit original-task and ordered-pair binding, selected provider metadata, separate host evidence and stable source identity | Synthetic replay, SQLite reopen deduplication, changed bindings rejected, lifecycle completion never becomes business success |
| Position and workflow | README and this roadmap state what is implemented, what is tested and what remains unmeasured | Keep users in the original host task; preserve observation mode and host authority |

Run the [evaluation protocol](EVALUATION.md) and [adapter walkthrough](AGENTS_API_ADAPTER.md). The evaluator scores decisions on fixed histories; it does not measure live task success, actual pauses, recovery time or real group behavior. Scorer self-checks use explicitly synthetic arithmetic inputs and are not published as model results. The first event fixtures follow documented provider fields but are not a captured live run or comprehensive schema conformance suite.

The evaluator's `continue / investigate / hold` labels describe the **proposed next action** under the shared policy. `investigate` is an evidence request, not an actual execution pause. Tracker `review`, unknown evidence or a low event-count confidence must not be mechanically converted into `hold`. A task failure is a task observation, not proof that either agent caused it.

## Completed: prepare and resume the real comparison

The [prepared session workflow](EVALUATION_RUNS.md) makes the existing evaluation
usable by an actual host without losing earlier results or silently repeating an
uncertain invocation. It is checkout tooling; runtime SDK contracts and scoring
semantics stay at 0.6.0.

| Deliverable | Acceptance |
|---|---|
| Reviewable plan | Exact prompt/hooks hashes, blind inputs, shared experiment, paired job count and explicit total limits; preparation makes no calls |
| Work admission | Reserve the declared checkpoint maximum before host dispatch; stop before the next claim if the remaining budget is insufficient |
| Durable receipts | Completed results survive restart, duplicate receipts are idempotent, changed receipts fail, partial work remains inspectable |
| Uncertain-outcome handling | Callback errors, process exits and missing usage retain their reservation; no automatic retry or refund; reconcile from host records |
| Complete result export | Both arms must finish before emitting scorer-compatible runs; synthetic workflow checks make no quality claims |

The host must enforce limits at its provider boundary and report actual usage;
local reservations cannot reverse or prevent unobserved remote spending. The
remaining real-run inputs are an authorized accessible host session, redacted
evidence/receipts, exact model/assessment configuration and an agreed cost ceiling.
The next work should run that bounded comparison and inspect its disagreements,
before expanding generic infrastructure.

## Current evidence: first 2CarsAI project observation

The [2CarsAI #238 observation report](2CARSAI_OBSERVATION.md) records an actual
HTTP/PostgreSQL/Redis reproduction: the same two original tests fail before the
historical repair and pass afterward. Fresh host verification was then recorded
against SDK plans. Deduplication, SQLite reopen and task binding held. With no
assessment provider configured, the host's passing test did not resolve the SDK
incidents; each task also gained an `evidence_needed` incident. Runtime rules
were left unchanged and no mock positive assessment was substituted.

This is one known defect with two parameter variants and analysis-role identities,
not a captured multi-agent trace or a native-versus-SDK quality comparison. The
existing native reviewer found the defect before this study. Unknown usage remains
unknown, and no model-accuracy, token-saving or recovery-time uplift is claimed.

The immediate follow-up is to separate the presentation and treatment of verified
host results from unavailable collaboration assessments, preserving source and
task boundaries. An unavailable assessor must not turn a passing original task
into a request to repeat execution. Decide the bounded host-verification contract
before changing incident closure. Then capture an authorized real task's events
and run the capable native comparison; Railway is optional infrastructure.

## Next acceptance gate: one real host and a strong native baseline

1. **Capture and bind:** use one accessible real multi-agent development task with permitted observation access. Retain a redacted stream and host evidence; verify task/agent mappings and reconnect/replay behavior. Use the Agents API adapter only for actual provider events; other hosts need an explicit mapping and must not manufacture provider envelopes. Keep model credentials and execution authority in the host.
2. **Agree the rubric:** review the authored cases, action-specific labels and a held-out extension before comparing models. Freeze the native prompt, hooks, model version, sampling settings, harness version, total token/cost budget and SDK revision. Give the native arm full history and normal diagnostic/verification instructions.
3. **Run both arms:** use the same observations and budget, account for SDK assessments and retries, retain input/configuration digests and declared run provenance. Missing usage remains unknown. If extra budget is studied, report it as a separate experiment.
4. **Review disagreement:** inspect false holds, missed holds, evidence requests, unsupported pair attribution and cases where SDK state conflicts with host facts. Validate semantic labels with humans; do not optimize against these public fixtures alone.
5. **Test the actual workflow:** in the original task screen, inspect the issue and evidence, apply one host-authorized check, record its new verification, then confirm the original task outcome. Measure task success, actual pauses, repeated work, recovery time, human interventions and total cost from host receipts; the offline decision scorer cannot supply those outcomes.

Only after this gate should the project claim incremental benefit or choose any automated intervention policy. No live model invocation or production execution is required by the current offline checks. Live runs need an available host session, protected credentials, agreed model configuration and an explicit cost ceiling.

## Then: multi-agent structure and targeted reliability

- Use observed failures to define task dependencies, directed role/version identities and shared upstream evidence. The current bilateral score and connected low-score regions are insufficient for causal cluster diagnosis. Add a detector only with labeled cases and a measurable benefit over the native baseline.
- Preserve original assessments when adding corrections or deliberate model/prompt re-evaluation. Version interpretations and compare them without silently rewriting history.
- Measure checkpoint size, write latency and contention; implement retention/export when the measured workload requires it. Unresolved evidence and event deduplication must survive archival.
- Add a durable notification outbox only when a real host requires delivery guarantees beyond querying committed incidents. Current callbacks remain best-effort.
- Embed concise issue cards in the original host task before building any standalone UI. Validate that users understand the evidence and next step without interpreting a relationship score.
- Consider distributed storage only after local workload measurements justify it. The SDK does not currently coordinate a distributed agent cluster.

## User experience acceptance

The host connects events once. The user stays on the original task and sees: **what happened, what is supported by evidence, what remains uncertain, and one suggested next step**. Details expose the original observations and separate assessments. If the host performs a check, the same card shows its result and the original task's actual continuation state. No user is asked to score agents manually or navigate a second orchestration console.

The existing HTML preview is a local read-only demonstration. Browser visual acceptance in this environment remains incomplete because local file previews were blocked by browser policy. Synthetic content/escaping checks do not replace host usability testing.

## Known limits

- Persistence is opt-in; default in-memory state still disappears on restart.
- Full evidence, deduplication data, in-memory state and serialized checkpoints grow with events despite the scoring-history cap. Restoring validates scoring arithmetic against retained assessments, so read cost grows too.
- SQLite writes serialize model assessment and are intended for local storage on one machine, not network filesystems or distributed/high-throughput services.
- The checkpoint format is versioned but there is no historical replay, schema migration or automatic re-evaluation engine yet.
- API credentials, callbacks and backend objects are not serialized; event context is retained. The host must protect the unencrypted database and backups.
- Callbacks have no durable outbox or exactly-once delivery guarantee. Hosts can query committed incidents after restart.
- Mock fixtures and HTTP request mocks do not establish real-model quality. Tracker confidence is an event-count heuristic, not calibrated accuracy.
- Low-score connected regions do not prove competition escalation, intent or causality.
- No automatic external task execution, authentication layer, production web client or complete live harness integration. The initial Agents API adapter has synthetic contract checks; the local host example and read-only preview remain reference integrations.
- The evaluation format accepts measured usage, but no live comparative token usage, monetary cost, task-success improvement or empirical repair success probability has been established.
