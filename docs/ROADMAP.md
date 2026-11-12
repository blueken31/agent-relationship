# Agent Relationship development roadmap

The 0.3.0 foundation was merged in [PR #1](https://github.com/blueken31/agent-relationship/pull/1), with merge commit `cee0e4d` and passing main CI. This roadmap describes the subsequent 0.4.0 implementation and the remaining work. A completed SDK mechanism is not a claim of production readiness or real-model effectiveness.

## Completed foundation: 0.3.0

| Area | Delivered behavior | Validation |
|---|---|---|
| Assessment contract | One bilateral assessment; source, goal judgment, brief reason and explicit unknown errors; strict finite numeric validation | `test_assessment.py` |
| Scoring | No gain/loss cancellation; unilateral loss and goal violations remain unfavorable; unknown cannot increase trust; identity-aware asymmetry | `test_scoring.py` |
| Recording and diagnosis | Event deduplication, task-scoped profiles, first severe notification, callback isolation, retained evidence, read-only report trends | `test_runtime.py` |
| Recovery bookkeeping | Sticky task-linked incidents, bounded plans, explicit host authorization, same-task verification, stale evidence rejection, cancellation and cooldown | `test_recovery.py` |
| Developer experience | Offline end-to-end example, migration notes, observed versus inferred evidence | `PYTHONPATH=. python examples/task_recovery.py` |

The SDK records and diagnoses. It does not execute a proposed action, independently validate a host assertion or grant additional permission. Host authorization and result verification remain outside the SDK.

## Current batch: 0.4.0 durable checkpoints

| Area | Implemented scope | Validation |
|---|---|---|
| Optional local persistence | Standard-library SQLite via `storage_path`; default in-memory behavior preserved; explicit close/context-manager lifecycle | Storage and persistent-tracker tests |
| Restart continuity | Restore scores, original evidence, assessment provenance, deduplication, incidents, plans, cooldowns and report trends without model calls | State round-trip and restart tests |
| Transactional updates | Load, mutate and save in one writer transaction; rollback on failure; independent instances refresh current committed state | Rollback, contention and concurrency tests |
| Checkpoint compatibility | Explicit snapshot/scoring versions and scoring-configuration checks; incompatible or corrupt state fails without silently resetting it | `test_state.py`, `test_storage.py` |
| Developer walkthrough | Start recovery, reopen, deduplicate a retry, record verified recovery, reopen again and confirm the same task outcome | `PYTHONPATH=. python examples/durable_recovery.py` |

This is restoration of the current checkpoint. It does not re-evaluate historical events, keep a history of checkpoints, resume external execution or guarantee notification delivery. An uncommitted operation may need to be retried after interruption; a committed event with the same ID is not counted again.

## Next: operational integration and evidence lifecycle

- Add one tested host integration that reconnects to the original task after a verified result. Define how the host determines whether an external action already ran before a crash.
- Add a durable notification outbox with stable delivery IDs and host deduplication. Current callbacks are best-effort and run after commit; a crash in that interval can lose a notification.
- Define versioned historical replay and deliberate re-evaluation when model, prompt or scoring policy changes. Preserve original assessments and distinguish them from later interpretations.
- Bound evidence retention and support export/archival without dropping unresolved incidents or losing event deduplication guarantees.
- Measure snapshot size, write latency and lock contention. The initial whole-state checkpoint serializes writers, including model evaluation, and is intended for a local file on one machine.
- Design a distributed store and coordination protocol only if measured workloads require it; network filesystems and distributed/high-throughput deployment are outside the SQLite scope.
- Add workspace, role, agent-version and proposed-action context where needed. Current task profiles aggregate both interaction directions.

## Then: user-facing task experience

- Embed a concise exception card in the original task: observed issue, impact, current handling, one recommended action and expandable evidence.
- Provide an operator queue for unresolved tasks and waiting decisions. Keep relationship graphs and scores in a diagnostic detail view.
- Make verified resolution, partial recovery, failed recovery and corrected diagnoses visible without repeated alerts for the same issue.
- Add an evidence-preserving correction workflow that records why an assessment changed and recomputes affected diagnoses.
- Validate whether the user understands the issue and next action without interpreting numerical scores.

These are planned interfaces; no web UI ships in 0.4.0.

## Validate before enabling automatic intervention

Compare a baseline without this SDK, deterministic rules, and rules plus model assessment on the same labeled tasks. Include:

| Scenario | Expected outcome |
|---|---|
| A reviewer correctly rejects an incorrect proposal | Necessary review is not punished as harmful cooperation |
| Both agents benefit while violating the user goal | Bilateral benefit does not mask the goal violation |
| A shared upstream dependency fails | Facts distinguish infrastructure failure from unsupported blame |
| A severe unresolved incident is followed by small successes | The incident remains visible until directly verified |
| A new agent, changed model or stale evidence is introduced | Uncertainty and provenance remain visible |
| Recovery fails and a permitted reassignment later succeeds | Evidence and the original task remain connected |
| A process restarts while recovery is active | The existing plan and restrictions remain visible without repeating external execution |

Measure task success, false pauses, missed issues, repeated work, time to recovery, human intervention count and incremental model cost. Do not use average relationship score as the success criterion. Select intervention policies only after this comparison; the SDK remains in observation mode.

## Known limits

- Persistence is opt-in; default in-memory state still disappears on restart.
- Full evidence, deduplication data, in-memory state and serialized checkpoints grow with events despite the scoring-history cap. Restoring validates scoring arithmetic against retained assessments, so read cost grows too.
- SQLite writes serialize model assessment and are intended for local storage on one machine, not network filesystems or distributed/high-throughput services.
- The checkpoint format is versioned but there is no historical replay, schema migration or automatic re-evaluation engine yet.
- API credentials, callbacks and backend objects are not serialized; event context is retained. The host must protect the unencrypted database and backups.
- Callbacks have no durable outbox or exactly-once delivery guarantee. Hosts can query committed incidents after restart.
- Mock fixtures and HTTP request mocks do not establish real-model quality. Tracker confidence is an event-count heuristic, not calibrated accuracy.
- Low-score connected regions do not prove competition escalation, intent or causality.
- No automatic task execution, authentication layer, web interface or complete framework adapter.
- No measured token usage, monetary cost or empirical repair success probabilities yet.
