# Prepare and resume a measured comparison

The paired scorer accepts complete run artifacts. A live host also needs to know
which checkpoint it is about to run, the limits for that work, what has already
finished and what needs reconciliation after an interruption. The checkout's
`evaluation.session` module adds that execution record around a caller-owned
policy. It does not connect to a model provider or supply credentials.

The developer flow is **review the frozen plan → claim one checkpoint → let the
host execute within its limits → save its measured result → inspect disagreements**.
The original shared prompt, hooks, model and observed history remain identical
between arms. Jobs interleave native and SDK arms, alternating the first arm for
each checkpoint. The full authored suite has 19 checkpoints and 38 paired jobs;
this is a job count, not a model-call count. An SDK job may require several calls.

## Prepare a concrete plan

Supply a JSON spec with exactly these fields. The zero limits below describe an
offline example only; they neither select a live model nor authorize spending.

```json
{
  "schema_version": 1,
  "experiment": {
    "experiment_id": "offline-demo",
    "harness": "synthetic/no-harness",
    "model": "none",
    "prompt_sha256": "SHA256_OF_EXACT_SHARED_PROMPT_BYTES",
    "hooks_sha256": "SHA256_OF_EXACT_SHARED_HOOK_CONFIG_BYTES",
    "replay_mode": "prefix_isolated",
    "seed": 0,
    "budget": {
      "max_input_tokens": 0,
      "max_output_tokens": 0,
      "max_cost": 0,
      "currency": "USD"
    }
  },
  "sdk_revision": "exact-commit-and-assessment-configuration-manifest",
  "provenance": {
    "native": {"kind": "synthetic", "run_id": "demo-native", "source_ref": "offline-example"},
    "sdk": {"kind": "synthetic", "run_id": "demo-sdk", "source_ref": "offline-example"}
  },
  "total_budget": {
    "max_input_tokens": 0,
    "max_output_tokens": 0,
    "max_cost": 0,
    "currency": "USD"
  }
}
```

For a measured capture, replace the synthetic metadata with the exact host/model
configuration, configuration manifest and distinct retained capture references.
Set both provenance kinds to `captured`. Specify an agreed total budget for **both
arms together**, as well as the common per-checkpoint budget. Captured sessions
require monetary ceilings and a matching currency at both levels. The host must
meter assessment calls, final decisions and retries against those limits. Keep
the applicable pricing/usage source with the capture; the journal does not look
up prices or certify a supplied receipt.

Compute hashes from the actual shared prompt and hook configuration bytes, not
from a filename or placeholder. `prepare` checks both file hashes. The plan stores
blind inputs and configuration only, with a digest. Expected answers, explanatory
titles and gold rationales are excluded. Keep credentials in the host's protected
configuration; they do not belong in the spec, evidence, plan or results.

```bash
python -m evaluation prepare --spec /private/spec.json \
  --prompt /private/decision-prompt.txt --hooks /private/hooks.json \
  --out /private/plan.json
python -m evaluation session-create --plan /private/plan.json --db /private/run.sqlite3
python -m evaluation session-status --db /private/run.sqlite3
```

Preparation calls no callback or model. It prints the exact plan digest, model,
job count, per-checkpoint limits, total limits and worst-case reservations. A
total budget smaller than the sum of all maxima may still fund a run if actual
usage is lower, but completion is not guaranteed. The journal stops when it cannot
reserve the next full checkpoint ceiling. No budget or configuration may be
changed inside an existing plan; revise the experiment explicitly and keep the
previous record.

## Connect an existing host

`EvaluationSession.run_next(dispatch)` reserves one job durably before invoking
the supplied function. `dispatch` receives the original blind checkpoint, arm,
stable `job_id`, plan digest, experiment configuration, SDK revision/provenance
and per-checkpoint budget. It returns the [usual policy result](EVALUATION.md#run-artifact-contract).
The native and SDK wrappers in `examples/harness_sdk_policy.py` can supply those
results after the host configures the common policy, assessment backend and usage
meter. A fresh policy context is required for each isolated prefix.

```python
from evaluation.session import EvaluationSession

# dispatch is host application code. Before making any call it must:
# - verify the requested model/prompt/hooks/assessment configuration;
# - enforce request['budget'] across all calls for this checkpoint;
# - bind request['job_id'] to host/provider receipts for outcome lookup;
# - return measured usage together with the decision and SDK evidence.
with EvaluationSession("/private/run.sqlite3") as session:
    while session.run_next(dispatch) is not None:
        print(session.status())
```

The journal enforces **work admission and reservation accounting**, not a remote
provider's billing boundary. A callback can spend more than it was asked to; a
post-call rejection cannot undo that charge. The host must constrain requests,
tool calls, subagents and retries before dispatch. Do not present this journal as
a provider-side spending cap or as permission to run an external task.

For a host in another process, use the explicit claim/receipt interface:

```bash
python -m evaluation session-claim --db /private/run.sqlite3 --out /private/request-001.json
# The configured host executes the request once and saves its result receipt.
python -m evaluation session-record --db /private/run.sqlite3 \
  --job-id JOB_ID_FROM_REQUEST --receipt /private/receipt-001.json
```

None of these CLI commands calls a model. Claiming changes the journal to a
reserved state. Use a new request filename for each claim; an existing file is
not overwritten. A write failure after claim leaves the request reserved for
inspection. Completed job receipts are immutable and identical resubmission is
idempotent. Distinct local sessions cannot claim another job while one outcome is
outstanding. This single-machine serialization is not distributed coordination
or exactly-once external execution.

## Resume and inspect without repeating work

| State | Next step |
| --- | --- |
| `ready` | Review and claim the next checkpoint |
| `needs_reconciliation` | Inspect the saved job, query the actual host and record its original result; the host may still be running |
| `budget_exhausted` | Inspect completed results; the remaining allowance cannot reserve the next checkpoint |
| `completed` | Export both full arms and score them |

```bash
python -m evaluation session-inspect --db /private/run.sqlite3 --job-id JOB_ID
python -m evaluation session-results --db /private/run.sqlite3
```

The inspection retrieves the same request and any committed receipt; it is not a
new dispatch instruction. Partial results are readable and explicitly marked as
an unscored set. The normal scorer still requires complete coverage of both arms.

A callback exception, abrupt process exit, invalid evidence, out-of-budget result
or missing usage keeps its full reservation and stops further claims. Exception
messages are not stored because providers may include sensitive text. The host
must reconcile the existing invocation using authoritative saved results and usage.
`record_result()` can settle that existing claim after restart; it never repeats
the callback. There is no automatic expiry, refund, retry or assumption that a
failed connection meant no cost. If an outcome cannot be established, retain the
blocked run as incomplete rather than silently rerunning it as a continuation.

Unlike the standalone scorer, which can report unknown usage, the controlled
session requires measured input/output token receipts to settle a job. A cost
ceiling additionally requires a cost receipt. `charged_or_reserved` combines
settled receipts and declared maxima for outstanding work; it is accounting, not
an invoice. `measured_usage` only aggregates completed receipts; a null cost remains
unknown. A provider charge exceeding its reservation is an invalid result requiring
host reconciliation and is not represented as a confirmed bounded spend.

## Export and validate

```bash
python -m evaluation session-export --db /private/run.sqlite3 --out-dir /private/completed-run
python -m evaluation score --native /private/completed-run/native.json \
  --sdk /private/completed-run/sdk.json --out /private/comparison.json
python -m examples.evaluation_session
```

The export directory must be new. Exports use the existing complete paired run
format. A later failure writing export files does not lose the journal receipts;
export to another new directory. The last command is an offline example of
claiming, reopening, reconciling and finishing 38 synthetic jobs with no provider
calls. It reports only that these mechanics worked, not diagnostic performance.

SQLite journals contain supplied evidence and are unencrypted. Use a private local
directory, protect exports/backups and redact before sharing. New journal files
use owner-only permissions. The repository ignores `evaluation-runs/` and SQLite
files to reduce accidental commits. Hashes/checksums detect inconsistent files;
they do not authenticate a host, prove fair model execution or prevent an operator
from rewriting a capture. The journal stores a full local checkpoint and is meant
for this small evaluation suite, not a large production service.

## Remaining inputs for the first real run

The next gate needs an accessible, authorized multi-agent host session and its
redacted observations/receipts, exact final-decision and assessment model settings,
the frozen shared prompt/hooks, and an agreed per-checkpoint and total cost ceiling.
No live credentials, paid call or actual native-versus-SDK result was produced by
the offline implementation checks. Use the configured host for credentials. The
following work should consume those real inputs and review outcomes before adding
more generic infrastructure.
