# Host verification and collaboration assessment

Version 0.6.1 addresses the result-presentation gap found in the
[2CarsAI observation](2CARSAI_OBSERVATION.md). A host had recorded a successful
bounded retest, but `complete_repair()` correctly refused an unknown assessment.
The old projection only showed receipts attached to completed plans, so it kept
asking for a result that was already recorded.

## Read-only behavior

`task_view()` and `task_queue()` now account for strictly bound recorded results
of a started plan. No new assessment, execution, authorization, retry or ledger
write occurs during projection. Existing incident counts and closure rules remain
unchanged, including the requirement for an assessed result to resolve an incident.

| Evidence | Handling and next step |
|---|---|
| No matching result | `awaiting_result`: inspect the original host outcome |
| Host explicitly reports success and verification passed; assessment unknown | `awaiting_assessment`: inspect the collaboration assessment |
| One matching result without that combination | `result_recorded`: review the retained result and assessment |
| Multiple matching receipts | `needs_reconciliation`: inspect all results without choosing the latest |
| New adverse evidence invalidates the plan version | `needs_reconciliation`, retaining the earlier host result as evidence |
| Missing referenced evidence | `evidence_missing`, with priority over an earlier success |

Task-level `recovery_status` can remain `needs_attention`: an unavailable
assessment is still unresolved. When every open issue concerns a passed host
retest and its missing assessment, the summary says
“宿主复测已通过，协作评估待核对” and the action is `inspect_assessment`.
The host retains control of the actual original task and must not translate this
presentation into permission or an execution command.

Each incident card adds `host_verification`, either `null` or a detached summary:

```json
{
  "status": "passed",
  "plan_id": "original-plan-id",
  "incident_id": "original-incident-id",
  "event_id": "verification-event-id",
  "event_ids": ["verification-event-id"],
  "observation": {"event_id": "verification-event-id", "result": "success", "narrative": "Host retest passed"},
  "assessment": {"status": "unknown", "error_code": "assessment_not_configured", "reason": "Assessment unavailable", "source": "model", "goal_status": "unknown"},
  "source": "host_report",
  "independently_verified": false,
  "grants_permission": false
}
```

`passed` requires literal `result="success"` and Boolean
`verification_passed=True`. Success prose, `1`, `"true"` or a missing flag cannot
establish that state. `failed` preserves an explicit failure/error/timeout;
`reported` preserves a partial, unknown, completed or ok result, or success without
Boolean verification. Missing/malformed outcomes and lifecycle-only states such
as started/running/queued do not become result receipts. With multiple receipts,
`status="multiple"`, `event_id`, `observation` and `assessment` are null, and all
candidate IDs remain visible. The SDK does not fetch or authenticate references.

Matching requires the original task, pair, action and `repair_plan_id`, a record
timestamp at or after plan start, and a stored ingestion sequence strictly after
the plan's start sequence. `event()` exposes this derived `sequence` metadata;
it survives restart and is not inferred from timestamps, UUIDs, dictionary order
or user-supplied context. Older persisted checkpoints already contain this order,
so no state migration is needed. Missing sequence metadata in manually assembled
projection input cannot qualify a new result.

This proves SDK recording order, **not the occurrence time or authenticity of an
external operation**. Hosts must protect verification fields from agent input,
check the original external receipt, enforce its scope/freshness and record it
after the corresponding host-authorized operation. The 2CarsAI probe additionally
checks that the real test start time follows plan creation.

An `evidence_needed` incident receives `related_incident_id` only if all of its
observations belong to one such pending plan's results. Unrelated observations
and other task/pair issues stay independent. The HTML example groups only a
passed result's unavailable assessment into the original card: two underlying
incidents remain in the API, while the user sees one original issue, separate
execution/assessment lines and one next step. Raw event context is never rendered.

## Verification and limits

The original 2CarsAI SQLite checkpoint was read again with this projection. Both
samples now point to assessment review, while all four incidents remain open.
The checkpoint bytes and exported state were unchanged; the original business
tests and model calls were not repeated. See the retained
[read-only check](../evaluation/captures/2carsai-pr238/view-check.json).

Contract tests cover unknown assessments, strict Boolean handling, wrong task,
pair/action/plan, stale or missing sequence, equal timestamps, competing receipts,
new failures, goal violations, unrelated unknowns, cancellation, missing records,
restart, no-call/no-write reads, escaped content and non-duplicated preview cards.
The [HTML preview](task-review-preview.html) remains explicitly simulated and
read-only; visual browser/device acceptance and live multi-agent A/B remain pending.
