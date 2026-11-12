# Agent Relationship development roadmap

This roadmap describes the 0.3.0 source changes and the remaining work. A completed SDK mechanism is not a claim of production readiness or real-model effectiveness.

## Implemented in this batch

| Area | Delivered behavior | Evidence to run |
|---|---|---|
| Assessment contract | One bilateral assessment; source, goal judgment, brief reason and explicit unknown errors; strict finite numeric validation | `test_assessment.py` |
| Scoring | No gain/loss cancellation; unilateral loss and goal violations remain unfavorable; unknown cannot increase trust; identity-aware asymmetry | `test_scoring.py` |
| Recording and diagnosis | Per-instance event deduplication, task-scoped profiles, first severe notification, callback isolation, evidence history, read-only report trends | Runtime regression tests |
| Recovery bookkeeping | Sticky task-linked incidents, bounded plans, host authorization declaration, same-task verification, stale evidence rejection, cancellation bookkeeping, failure cooldown | `test_recovery.py` |
| Developer experience | Offline end-to-end example, migration notes, observed versus inferred evidence, explicit operating limits | `PYTHONPATH=. python examples/task_recovery.py` |

The SDK records and diagnoses. It does not execute a proposed action, independently validate a host assertion or grant an agent additional permission. The example simulates the host and preserves the original task ID; it is not an integration with an external task system.

## Next: durable operation and integration

- Add an optional persistent store with versioned events, assessments, incidents and plans. Verify restart recovery and deduplication across crashes.
- Define replay behavior when model, prompt or scoring versions change. Preserve original evidence and distinguish new assessments from historical outcomes.
- Bound evidence retention and support export/archival without silently discarding unresolved issues.
- Define cross-process concurrency and event delivery semantics. Instance locks are insufficient for a multi-worker deployment.
- Add one tested host integration that resumes the original task after a verified result. Keep execution authority in the host.
- Add explicit workspace, role, agent-version and action-risk context where the use case needs it; task profiles currently aggregate both directions.

## Then: user-facing task experience

- Embed a concise exception card in the original task: observed issue, impact, current handling, one recommended action and expandable evidence.
- Provide an operator queue for unresolved tasks and waiting decisions. Keep relationship graphs and scores in a diagnostic detail view.
- Make verified resolution, partial recovery, failed recovery and corrected diagnoses visible without repeated alerts for the same issue.
- Add a correction workflow that preserves original evidence, records why an assessment was changed and recomputes affected diagnoses.
- Validate whether the user can understand the issue and next action without interpreting numerical scores.

These are planned interfaces; no web UI ships in 0.3.0.

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

Measure task success, false pauses, missed issues, repeated work, time to recovery, human intervention count and incremental model cost. Do not use average relationship score as the success criterion. Select intervention policies only after this comparison; the SDK's current mode remains observation.

## Known limits

- In-memory state only; restart loses history, incident status and event deduplication.
- Full evidence/deduplication ledgers grow with events despite the scoring-history cap.
- Model confidence is not calibrated; tracker confidence is only an evidence-count heuristic.
- Mock scenarios and HTTP request mocks do not establish real-model quality.
- Low-score connected regions do not prove competition escalation or intent.
- No automatic task execution, authentication layer, web interface or complete framework adapter.
- No measured token usage, dollar cost or empirical repair success probabilities yet.
